"""Speech lane — định tuyến tin nhắn xã giao mà không gọi LLM.

Owner: Thành Bảo (Decision layer)
File: src/api/small_talk.py

Nguyên tắc:
  - Xác định greeting/acknowledgement/capability/xã giao bằng deterministic
    matching, 0 LLM call.
  - Câu trả lời canned, KHÔNG nội suy query (không echo user input).
  - Capability query thì gọi endpoint `/capabilities` (data thật), không dùng
    chuỗi cứng.
  - Bảo toàn ngữ nghĩa acknowledgement: "ok thanh toán phí" là service goal,
    không phải acknowledgement.

Thứ tự classify (bắt buộc — giữ đúng để không nuốt service goal):
  repetition → service-intent → ack → greeting → about-agent → capability → social.

Service-intent kiểm TRƯỚC mọi canned route: một câu có ý định dịch vụ (đặt chỗ,
đăng ký, bảo trì...) dù lồng trong greeting/capability đều phải về planner, KHÔNG
bị speech lane chặn. Đây là đường an toàn cho mọi prompt tấn công xen kẽ.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx


class SpeechType(StrEnum):
    GREETING = "greeting"
    ACKNOWLEDGEMENT = "acknowledgement"
    CAPABILITY = "capability"
    # Hỏi CÁCH LÀM một việc cụ thể. Phải tách khỏi `CAPABILITY`: route xử lý
    # `CAPABILITY` bằng cách dựng lại danh mục dịch vụ và GHI ĐÈ `reply`, nên
    # dùng chung loại sẽ nuốt mất các bước cụ thể — đúng thứ người hỏi cần.
    HOW_TO = "how_to"


@dataclass(frozen=True)
class SmallTalk:
    speech_type: SpeechType
    reply: str


# --- Từ vựng canned ----------------------------------------------------------

_GREETINGS = frozenset(
    {
        "xin chào",
        "chào",
        "hello",
        "hi",
        "hey",
        "chào bạn",
        "chào p-118",
        "chào bạn ơi",
        "hello bạn",
        "xin chào bạn",
    }
)

_ACKNOWLEDGEMENTS = frozenset(
    {
        "ok",
        "okay",
        "oke",
        "được",
        "được rồi",
        "được ạ",
        "rõ rồi",
        "rõ ạ",
        "cảm ơn",
        "cảm ơn nhé",
        "cảm ơn bạn",
        "cảm ơn nhiều",
        "ok cảm ơn",
        "ok bạn",
        "ok luôn",
        "ok ok",
    }
)

_ABOUT_AGENT_MARKERS = (
    "ban la ai",
    "ai tao ra ban",
    "ban ten gi",
    "ban la gi",
    "gioi thieu ve ban",
    "gioi thieu ban than",
)

_SOCIAL_MARKERS = (
    "lam bai tho",
    "viet bai hat",
    "ke chuyen cuoi",
    "ban khoe khong",
    "hom nay the nao",
    "khoe khong",
    "giai thich",
)

_CAPABILITY_MARKERS = (
    "dich vu nao",
    "nhung dich vu",
    "co dich vu gi",
    "ho tro gi",
    "lam duoc gi",
    "ban lam duoc gi",
    "danh sach dich vu",
    "dich vu gi",
    "dich vu nao co",
    # Ba cách hỏi dưới đây đều rơi vào planner trước khi được thêm vào đây, và
    # planner trả `VALIDATION_ERROR` — người dùng hỏi một câu hoàn toàn hợp lý
    # rồi bị báo "thông tin bạn vừa gửi chưa hợp lệ".
    #
    # Đo được: "Bạn giúp được gì?" → VALIDATION_ERROR. "P-118 có thể làm gì" →
    # không khớp mẫu nào. "Hướng dẫn mình dùng với" → không khớp mẫu nào.
    "giup duoc gi",
    "giup gi duoc",
    "giup toi duoc gi",
    "co the lam gi",
    "co the giup gi",
    "lam nhung gi",
    "nhung gi",
    # Người dùng mới hay hỏi cách dùng chứ không hỏi danh mục. Câu trả lời họ
    # cần vẫn là danh sách năng lực theo đúng quyền của họ.
    # "the nao" một mình là rất rộng, nhưng `_asks_for_capabilities` đã trả
    # False cho mọi câu có ý định dịch vụ TRƯỚC khi tới đây — nên "đặt lịch thế
    # nào" vẫn về planner, chỉ "mình dùng cái này thế nào" mới thành capability.
    "the nao",
    "dung the nao",
    "dung nhu the nao",
    "su dung the nao",
    "huong dan",
    "bat dau tu dau",
    "lam sao de",
    # Hỏi về QUYỀN của chính mình. Cùng một câu trả lời với hỏi năng lực:
    # `_capability_reply` vốn đã tách "dùng ngay" khỏi "mở sau khi xác minh
    # căn hộ" dựa trên `account_state`, tức là nó chính là bản kê quyền.
    #
    # Trước khi thêm, "tôi có quyền gì" rơi xuống planner và nhận:
    #
    #   "Thông tin bạn cung cấp chưa hợp lệ nên mình chưa tra cứu được quyền
    #    lợi của bạn. Bạn vui lòng gửi lại (họ tên, số điện thoại) nhé."
    #
    # Hệ thống biết thừa quyền của họ — nó vừa dùng chính dữ liệu đó để khoá
    # ba dịch vụ trên màn hình — mà vẫn đi đòi họ khai lại danh tính.
    "quyen gi",
    "quyen loi",
    "duoc dung gi",
    "dung duoc gi",
    "duoc lam gi",
)

# --- Service intent (Phase C) -------------------------------------------------
#
# Phân biệt "hỏi về danh mục dịch vụ" (capability) với "yêu cầu làm" (service).
# Một câu là service intent nếu:
#   a) Chứa cụm động từ hành động (đặt chỗ, đăng ký, bảo trì...), HOẶC
#   b) Chứa từ biểu đạt nhu cầu ("cần", "muốn", "hãy"...) + danh từ dịch vụ.
# Ngược lại (chỉ hỏi "có dịch vụ gì", "làm được gì") là capability.

_ACTION_PHRASES = (
    "dat cho",
    "dang ky",
    "thanh toan",
    "chuyen nha",
    "dat lich",
    "bao tri",
    "sua",
    "thue",
    "mua",
    "dat coc",
    "hoan tien",
    "huy ve",
    "huy dang ky",
    "gia han",
)

_REQUEST_WORDS = ("can", "muon", "hay", "lam on", "giup toi", "giup")

_SERVICE_NOUNS = (
    "do xe",
    "xe",
    "can ho",
    "phong",
    "bao tri",
    "chuyen nha",
    "thang may",
    "cu dan",
    "phuong tien",
)

# --- Repetition detection (Phase 4a) -----------------------------------------

_REPETITION_THRESHOLD = 3


def _detect_repetition(message: str) -> bool:
    """Câu spam lặp từ (>= 3 lần) → chặn sớm, 0 LLM call.

    Chỉ chặn khi MỘT token chiếm >= 3 lần trong câu. Câu hợp lệ có 2 xe khác
    biển ("51A-12345 và 51A-12346") không bị bắt vì không token nào lặp 3 lần.

    Đếm trên chữ CÓ DẤU, không dùng `_normalize`.

    `_normalize` bỏ dấu để khớp mẫu — đúng cho việc khớp mẫu, sai hoàn toàn cho
    việc đếm lặp: "đó", "đỗ", "đó" đều thành `do`. Ba từ khác nghĩa gộp làm một
    và câu bị coi là spam.

    Đo được, nguyên văn một yêu cầu hợp lệ:

        "tôi muốn đặt lịch nhưng trước đó hãy đặt chỗ đỗ xe và tôi muốn biết
         hôm nay là ngày mấy trước khi làm 2 việc đó"

    → `do` đếm được 3 → "Bạn gõ lặp, mình chưa hiểu yêu cầu."

    Câu càng phức tạp thì càng dễ dính, vì tiếng Việt có rất nhiều cặp chỉ khác
    nhau ở dấu. Nói cách khác: bộ lọc spam mạnh tay nhất đúng với những yêu cầu
    đáng giá nhất.
    """
    lowered = message.casefold()
    words = [w.strip(" .,!?;:") for w in lowered.split()]
    words = [w for w in words if w]
    if len(words) < _REPETITION_THRESHOLD:
        return False
    most_common = Counter(words).most_common(1)
    return bool(most_common) and most_common[0][1] >= _REPETITION_THRESHOLD


def _normalize(message: str) -> str:
    """Normalize để match mà không thay đổi semantics.

    Casefold + strip punctuation + bỏ dấu tiếng Việt (NFD + loại combining marks)
    để match cả có dấu / không dấu.
    """
    lowered = message.casefold().strip(" .,!?")
    decomposed = unicodedata.normalize("NFD", lowered)
    unmarked = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    unmarked = unmarked.replace("đ", "d")
    return " ".join(unmarked.split())


def _is_acknowledgement(message: str) -> bool:
    """Chỉ nhận câu xã giao độc lập; không nuốt một goal có chữ 'ok'.

    "okok" / "ok ok" được chuẩn hoá về "ok ok" rồi so với set.
    """
    normalized = _normalize(message)
    candidates = {
        normalized,
        normalized.replace("okok", "ok ok"),
    }
    allowed = {_normalize(v) for v in _ACKNOWLEDGEMENTS}
    return any(candidate in allowed for candidate in candidates if candidate)


def _is_greeting(message: str) -> bool:
    """Chào thuần hoặc chào lặp ("xin chào xin chào", "hello hello").

    KHÔNG nuốt câu có ý định: "chào, đặt chỗ khu A" có thêm từ không phải
    greeting → trả False (sẽ về planner qua service-intent hoặc None).
    """
    normalized = _normalize(message)
    allowed = {_normalize(v) for v in _GREETINGS}
    if normalized in allowed:
        return True
    if len(normalized) < 2:
        return False
    # Greeting lặp: toàn bộ câu là lặp của đúng một greeting trong set.
    for greeting in allowed:
        if greeting and greeting in normalized:
            remainder = normalized.replace(greeting, "").strip()
            if not remainder and normalized.count(greeting) >= 2:
                return True
    return False


def _has_service_intent(message: str) -> bool:
    """Câu có ý định thực hiện dịch vụ → phải đi planner, KHÔNG chặn ở speech lane."""
    normalized = _normalize(message)
    if any(phrase in normalized for phrase in _ACTION_PHRASES):
        return True
    has_request = any(word in normalized for word in _REQUEST_WORDS)
    has_service_noun = any(noun in normalized for noun in _SERVICE_NOUNS)
    return has_request and has_service_noun


# --- Hỏi CÁCH LÀM, khác hẳn yêu cầu LÀM ---------------------------------------
#
# "liên kết căn hộ thế nào" và "liên kết căn hộ cho tôi" khác nhau hoàn toàn về
# ý định, nhưng `_has_service_intent` bắt cả hai (có động từ + danh từ dịch vụ).
# Vì service-intent được kiểm TRƯỚC mọi nhánh canned, câu hỏi cách làm rơi thẳng
# xuống planner — và planner không có gì để lập kế hoạch nên trả VALIDATION_ERROR:
#
#   người dùng: "liên kết căn hộ thế nào"
#   P-118:      "Hiện thông tin bạn cung cấp chưa hợp lệ, mình cần bạn kiểm tra
#                lại và gửi lại giúp mình nhé."
#
# Họ hỏi rất rõ ràng và bị nói là gõ sai. Trong thực tế người dùng còn hỏi mơ hồ
# hơn thế.
_HOWTO_MARKERS = (
    "the nao",
    "nhu the nao",
    "lam sao",
    "lam the nao",
    "cach ",
    "huong dan",
    "bat dau tu dau",
    "o dau",
    "can lam gi",
    "phai lam gi",
    "lam gi de",
)

# Các bước cho từng việc. Khoá là cụm đã chuẩn hoá xuất hiện trong câu hỏi.
#
# Thứ tự QUAN TRỌNG: cụm cụ thể đứng trước cụm chung. "đăng ký xe" chứa "xe",
# nên nếu "xe" đứng trước thì mọi câu hỏi về xe đều nhận hướng dẫn đỗ xe.
_HOWTO_STEPS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("can ho", "lien ket", "xac minh", "cu dan"),
        "Để xác minh căn hộ: mở mục “Xác minh căn hộ” ở thanh bên, nhập mã căn hộ và "
        "khu đô thị, đính kèm ảnh giấy tờ nhà rồi bấm gửi. Ban quản lý duyệt xong là "
        "các dịch vụ cư dân mở ra ngay.",
    ),
    (
        ("do xe", "dau xe", "phuong tien", "xe"),
        "Đăng ký xe và chỗ đỗ cần căn hộ đã xác minh trước. Bạn mở mục “Xác minh căn hộ”, "
        "gửi mã căn hộ kèm ảnh giấy tờ; duyệt xong thì chọn “Đăng ký phương tiện và chỗ đỗ xe” "
        "ở danh sách năng lực, điền biển số và khu đỗ là xong.",
    ),
    (
        ("tham quan", "xem nha", "xem can ho"),
        "Để đặt lịch tham quan: chọn “Đặt lịch tham quan dự án” ở danh sách năng lực, "
        "chọn dự án, ngày và giờ, cho biết có cần xe đưa đón không, rồi bấm Thực hiện. "
        "Việc này không cần xác minh căn hộ.",
    ),
    (
        ("tu van", "quan tam",),
        "Để nhận tư vấn: chọn “Đăng ký quan tâm / nhận tư vấn”, chọn dự án và hình thức "
        "bạn quan tâm, để lại giờ tiện liên hệ. Bộ phận tư vấn sẽ gọi lại.",
    ),
    (
        ("bao tri", "sua chua", "hong"),
        "Để báo bảo trì: cần căn hộ đã xác minh trước. Sau đó chọn “Báo bảo trì / sửa chữa”, "
        "mô tả sự cố và vị trí, chọn thời gian thuận tiện cho kỹ thuật viên.",
    ),
    (
        ("chuyen nha", "chuyen den", "chuyen di"),
        "Để đặt lịch chuyển nhà: cần căn hộ đã xác minh trước. Sau đó chọn “Đặt lịch chuyển nhà”, "
        "chọn ngày giờ, cho biết có dùng thang máy và cần hỗ trợ vận chuyển không.",
    ),
)


# --- Việc NẰM NGOÀI không gian tool của Agent ---------------------------------
#
# Agent có đúng 10 tool (`tool_contract.py`). Xác minh căn hộ KHÔNG nằm trong
# đó — nó là luồng giao diện cộng một lượt duyệt của ban quản lý.
#
# Vậy mà "giúp tôi liên kết căn hộ" vẫn xuống planner, vì nó có động từ và danh
# từ dịch vụ. Planner không có tool nào để lập kế hoạch, nên trả về:
#
#   "Mình chưa thể liên kết căn hộ vì thông tin bạn vừa cung cấp chưa hợp lệ.
#    Bạn vui lòng kiểm tra lại và gửi lại thông tin chính xác hơn."
#
# Ba thứ sai cùng lúc: câu của họ hoàn toàn hợp lệ; lý do thật không phải dữ
# liệu mà là Agent không làm được việc này; và không có một chỉ dẫn nào. Người
# dùng sẽ gửi lại "chính xác hơn" vài lần rồi bỏ cuộc.
#
# `_asks_how_to` không đỡ được vì nó đòi cụm "thế nào"/"làm sao" — câu này là
# câu sai khiến. Với việc Agent KHÔNG LÀM ĐƯỢC thì cách hỏi không quan trọng:
# hỏi hay sai khiến đều phải nhận cùng một chỉ dẫn.
_OUTSIDE_TOOLSPACE: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "lien ket can ho",
            "xac minh can ho",
            "xac thuc can ho",
            "dang ky can ho",
            "them can ho",
            "lien ket ho so",
            "xac minh ho so",
            "lien ket cu dan",
        ),
        "Việc xác minh căn hộ do một đơn vị độc lập đối chiếu giấy tờ rồi duyệt, nên mình "
        "không tự làm thay được. Bạn mở mục “Xác minh căn hộ” ở thanh bên rồi bấm "
        "“Xác thực với đơn vị” — sang cổng của họ, nhập mã căn hộ và khu đô thị, đính kèm "
        "ảnh giấy tờ nhà là xong. Duyệt xong thì các dịch vụ cư dân mở ra ngay.",
    ),
)


# --- Hỏi hôm nay là ngày mấy --------------------------------------------------
#
# Hệ thống biết chính xác (`date.today()`, cùng nguồn với Planner và Validator),
# nhưng câu hỏi này rơi xuống planner: mất ~12 giây rồi trả về "Mình không thể
# xem hôm nay là ngày mấy được." Sai, và lượt hỏi lại còn nhận "thông tin bạn
# gửi chưa hợp lệ".
#
# Người dùng hỏi ngày để ĐẶT LỊCH, nên câu trả lời nói luôn cả hai.
_DATE_QUESTION_MARKERS = (
    "hom nay la ngay may",
    "hom nay ngay may",
    "hom nay la thu may",
    "hom nay thu may",
    "ngay bao nhieu",
    "bay gio la ngay may",
    "hom nay ngay bao nhieu",
)

_WEEKDAY_VI = ("thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ nhật")


def _asks_todays_date(message: str) -> str | None:
    """Trả lời ngày hôm nay, hoặc None nếu câu không hỏi ngày."""
    normalized = _normalize(message)
    if not any(marker in normalized for marker in _DATE_QUESTION_MARKERS):
        return None
    from datetime import date

    today = date.today()
    return (
        f"Hôm nay là {_WEEKDAY_VI[today.weekday()]}, ngày {today.day:02d}/{today.month:02d}/{today.year}. "
        "Bạn cần đặt lịch vào ngày nào thì nói với mình nhé — mình hiểu được cả "
        "“ngày mai”, “ngày 29” hay “thứ Bảy này”."
    )


def _outside_toolspace(message: str) -> str | None:
    """Chỉ dẫn cho việc Agent không có tool để làm, hoặc None.

    Kiểm TRƯỚC service-intent và trước cả `_asks_how_to`: với việc nằm ngoài
    không gian tool thì cách diễn đạt không đổi được kết quả, nên không cần
    phân biệt câu hỏi với câu sai khiến.
    """
    normalized = _normalize(message)
    for phrases, guidance in _OUTSIDE_TOOLSPACE:
        if any(phrase in normalized for phrase in phrases):
            return guidance
    return None


def _asks_how_to(message: str) -> str | None:
    """Các bước cho câu hỏi "làm thế nào", hoặc None nếu không phải câu hỏi ấy.

    Trả về hướng dẫn CỤ THỂ cho việc được hỏi, không phải danh mục dịch vụ
    chung. Người hỏi "liên kết căn hộ thế nào" đã biết họ muốn gì rồi — đưa lại
    danh sách năm dịch vụ là bắt họ tự tìm câu trả lời trong đó một lần nữa.
    """
    normalized = _normalize(message)
    if not any(marker in normalized for marker in _HOWTO_MARKERS):
        return None
    for keywords, steps in _HOWTO_STEPS:
        if any(keyword in normalized for keyword in keywords):
            return steps
    # Hỏi cách làm nhưng không rõ việc gì — để `classify` rơi xuống capability,
    # nơi trả danh mục theo đúng quyền của tài khoản.
    return None


def _asks_for_capabilities(message: str) -> bool:
    """Chỉ là capability khi câu KHÔNG mang ý định dịch vụ.

    Fix false-positive: "cần hỗ trợ gì về bảo trì" có service intent → về planner.
    "có dịch vụ gì" / "bạn làm được gì" không có ý định thực hiện → capability.
    """
    if _has_service_intent(message):
        return False
    normalized = _normalize(message)
    return any(marker in normalized for marker in _CAPABILITY_MARKERS)


def _is_about_agent(message: str) -> bool:
    normalized = _normalize(message)
    return any(marker in normalized for marker in _ABOUT_AGENT_MARKERS)


def _is_social(message: str) -> bool:
    normalized = _normalize(message)
    return any(marker in normalized for marker in _SOCIAL_MARKERS)


def _capability_reply(capabilities: list[dict[str, Any]], account_state: str) -> str:
    available = [item for item in capabilities if account_state == "resident" or not item.get("requires_resident")]
    locked = [item for item in capabilities if account_state != "resident" and item.get("requires_resident")]

    lines = ["Các dịch vụ bạn có thể dùng ngay:"]
    for item in available:
        lines.append(f"• {item.get('name')}: {item.get('description')}")
    if locked:
        lines.append("Sau khi liên kết căn hộ, bạn có thể dùng thêm:")
        for item in locked:
            lines.append(f"• {item.get('name')}")
    lines.append("Hãy nói mục tiêu của bạn hoặc chọn một dịch vụ để bắt đầu.")
    return "\n".join(lines)


def classify(message: str) -> SmallTalk | None:
    """Deterministic pre-classifier. Trả SmallTalk hoặc None (service goal).

    Thứ tự cố định — KHÔNG đổi: repetition → service-intent → ack → greeting →
    about-agent → capability → social. Service-intent luôn trước canned route
    để không bao giờ nuốt một goal thật (kể cả khi lồng trong câu xã giao).
    """
    text = message.strip()
    if not text:
        return None

    if _detect_repetition(text):
        return SmallTalk(
            speech_type=SpeechType.ACKNOWLEDGEMENT,
            reply="Bạn gõ lặp, mình chưa hiểu yêu cầu. Mô tả giúp mình mục tiêu cụ thể nhé.",
        )

    # Hỏi ngày hôm nay — trả lời được ngay, 0 lượt gọi model.
    #
    # Kiểm trước service-intent: "hôm nay là ngày mấy cho việc đặt lịch" có danh
    # từ dịch vụ nên nếu để service-intent thắng thì nó lại xuống planner.
    date_reply = _asks_todays_date(text)
    if date_reply is not None:
        return SmallTalk(speech_type=SpeechType.HOW_TO, reply=date_reply)

    # Việc Agent KHÔNG có tool để làm — kiểm trước tất cả.
    outside = _outside_toolspace(text)
    if outside is not None:
        return SmallTalk(speech_type=SpeechType.HOW_TO, reply=outside)

    # Hỏi CÁCH LÀM kiểm trước service-intent.
    #
    # Đây là ngoại lệ có chủ ý với quy tắc "service-intent luôn trước": câu hỏi
    # cách làm mang đủ dấu hiệu của một yêu cầu dịch vụ (động từ + danh từ), nên
    # nếu để service-intent thắng thì nó rơi xuống planner và người dùng nhận
    # "thông tin bạn cung cấp chưa hợp lệ" cho một câu hỏi hoàn toàn rõ ràng.
    #
    # Không nuốt yêu cầu thật: chỉ khớp khi câu CHỨA cụm hỏi cách làm ("thế
    # nào", "làm sao", "cách…"). "Đặt chỗ đỗ xe khu A" không có cụm nào trong đó.
    howto = _asks_how_to(text)
    if howto is not None:
        return SmallTalk(speech_type=SpeechType.HOW_TO, reply=howto)

    # Service intent kiểm TRƯỚC mọi canned route.
    if _has_service_intent(text):
        return None

    if _is_acknowledgement(text):
        return SmallTalk(
            speech_type=SpeechType.ACKNOWLEDGEMENT,
            reply="Đã rõ. Bạn cứ cho mình biết mục tiêu tiếp theo nhé.",
        )

    if _is_greeting(text):
        return SmallTalk(
            speech_type=SpeechType.GREETING,
            reply="Xin chào! Mình là P-118, trợ lý bất động sản và dịch vụ cư dân. Bạn cần hỗ trợ gì hôm nay?",
        )

    if _is_about_agent(text):
        return SmallTalk(
            speech_type=SpeechType.GREETING,
            reply=(
                "Mình là P-118, trợ lý bất động sản và dịch vụ cư dân. "
                "Mình giúp tìm nhà, đặt lịch xem, đăng ký cư dân/xe, đặt chỗ đỗ "
                "và thanh toán phí. Bạn cần mình hỗ trợ gì nhé?"
            ),
        )

    if _asks_for_capabilities(text):
        return SmallTalk(speech_type=SpeechType.CAPABILITY, reply="")

    if _is_social(text):
        return SmallTalk(
            speech_type=SpeechType.GREETING,
            reply=(
                "Mình là trợ lý dịch vụ nhà ở — mình tập trung vào đặt chỗ, "
                "đăng ký, bảo trì và thanh toán. Bạn cần mình hỗ trợ gì nhé?"
            ),
        )

    return None


async def answer_capability_question(
    message: str,
    *,
    account_state: str,
    capabilities: list[dict[str, Any]] | None = None,
) -> SmallTalk | None:
    """Trả lời capability bằng danh mục dịch vụ, đọc TRONG TIẾN TRÌNH.

    Trả SmallTalk nếu message là capability query, ngược lại None để caller
    xử lý như service goal.

    Trước đây hàm này tự gọi `GET /api/v1/capabilities` qua HTTP vào chính app
    đang chạy. Endpoint đó BẮT BUỘC token — và lời gọi nội bộ không mang token
    nào — nên nó luôn nhận 401, `raise_for_status()` ném lỗi, và người dùng
    luôn nhận đúng một câu: "Hiện chưa lấy được danh sách dịch vụ."

    Đo được trên stack sạch: gõ "Bạn giúp được gì?" trả về câu dự phòng ấy;
    `httpx` gọi thẳng `localhost:8000/api/v1/capabilities` trong container cũng
    trả 401. Đường này chưa từng chạy được.

    Một app gọi HTTP vào chính nó để đọc một hằng số của chính nó thì phải trả
    giá bằng: một vòng mạng, một `base_url` phải đoán, và một bộ credential nó
    không có. Nhận danh mục qua tham số thì mất cả ba.
    """
    if not _asks_for_capabilities(message):
        return None
    if not capabilities:
        return SmallTalk(
            speech_type=SpeechType.CAPABILITY,
            reply="Hiện chưa lấy được danh sách dịch vụ. Bạn thử lại sau nhé.",
        )
    return SmallTalk(
        speech_type=SpeechType.CAPABILITY,
        reply=_capability_reply(capabilities, account_state),
    )
