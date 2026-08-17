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
    """
    words = _normalize(message).split()
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
