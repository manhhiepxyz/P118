"""Bộ đọc giá trị chuẩn — chuỗi người dùng gõ → giá trị của contract.

Owner: Thành Bảo (Decision layer)
File: src/common/field_parsers.py

Đây KHÔNG phải tầng hiểu ý định. Nó không đoán người dùng muốn gì; nó trả lời
đúng một câu cho đúng một ô: "chuỗi này có phải giá trị hợp lệ của ô này không,
và nếu có thì giá trị chuẩn là gì". `None` nghĩa là không.

Tách khỏi `src/api/routes.py` vì Patch Validator (`src/orchestration/patch.py`)
phải dùng CHÍNH những bộ đọc này. Một đề xuất do model sinh ra và một ô người
dùng điền trên biểu mẫu phải đi qua cùng một luật; nếu không, đường nào lỏng
hơn sẽ thành đường thật. Mà `patch.py` không import được `routes.py`.

Không có nhánh dự phòng
-----------------------
Trước đây `_extract_follow_up_answers` có một nhánh cuối: khi chỉ còn ĐÚNG MỘT ô
đang hỏi, nó lấy NGUYÊN câu người dùng gõ làm giá trị. Nhánh ấy đã bị bỏ, và
KHÔNG được khôi phục dưới bất kỳ hình dạng nào — kể cả "chỉ cho field free
text". Ô nào đọc được thì phải có tên trong `FIELD_PARSERS`.

Luật LẤY TỪ CONTRACT, không chép tay
------------------------------------
Enum, chặn dưới của số, và kiểu của từng ô đều đọc từ `TOOL_CONTRACTS`. Chép
tay là cách hai bảng nói về cùng một luật rồi lệch nhau — đã xảy ra một lần với
khung giờ (`preferred_contact_time` bị thiếu trong bản chép tay, nên người dùng
trả lời "19:00" thì bộ lọc cho qua và Validator mới chặn).

`ENUM_SYNONYMS` là phần DUY NHẤT phải viết tay, vì contract chỉ biết token
(`air_conditioning`) còn người dùng gõ tiếng Việt ("điều hoà"). Nó chỉ MỞ RỘNG
đầu vào; giá trị đi ra luôn là token của contract.

Ô có thẩm quyền
---------------
`AUTHORITATIVE_FIELDS` không bao giờ có bộ đọc. Chúng là dữ liệu của provider
hoặc của phiên đăng nhập (`resident_id`, `vehicle_id`, `booking_id`,
`viewing_id`) hoặc là số tiền do hệ thống tính (`amount`, `currency`). Cho một
câu người dùng gõ trở thành nguồn của chúng là mở lại đúng lỗ hổng mà trust
boundary sinh ra để chặn.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from datetime import date, time, timedelta
from typing import Any

from src.common.agent_tool_policy import AGENT_REACHABLE_TOOLS
from src.common.projects import find_project_id, project_name, resolve_project_id
from src.common.schedule_policy import MAX_HORIZON_DAYS, TIME_WINDOWS
from src.common.tool_contract import TOOL_CONTRACTS

# Ô theo LOẠI. Giữ cạnh bộ đọc: hai bảng nói cùng một luật mà nằm hai nơi thì
# sớm muộn cũng lệch.
DATE_FIELDS = frozenset({"viewing_date", "booking_date", "preferred_date", "move_date", "tour_date"})
TIME_FIELDS = frozenset({"viewing_time", "preferred_time", "move_time", "preferred_contact_time"})
BOOLEAN_FIELDS = frozenset({"consent", "needs_elevator", "needs_loading_support"})

# Ô KHÔNG BAO GIỜ đọc từ câu người dùng. Xem ghi chú ở docstring module.
AUTHORITATIVE_FIELDS = frozenset({"resident_id", "vehicle_id", "booking_id", "viewing_id", "amount", "currency"})

# Mọi ô đầu vào của mọi tool, kể cả tool cũ. Dùng để ĐỐI CHIẾU, không phải để
# suy ra Agent hỏi được gì.
ALL_CONTRACT_FIELDS = frozenset(name for contract in TOOL_CONTRACTS.values() for name in contract.inputs)

# Tool Agent thật sự với tới được.
#
# `TOOL_CONTRACTS` là contract PROVIDER — nó liệt kê mọi thứ có connector, kể cả
# thứ đã bị loại khỏi Agent. `AGENT_REACHABLE_TOOLS` mới là không gian kế hoạch:
# `register_resident` (onboarding nằm ngoài Agent) và `search_properties` (tìm
# kiếm / listing là chức năng marketplace) đều không có ở đó.
#
# Phân biệt hai tập này là điểm chính: một ô CÒN TỒN TẠI trong contract cũ không
# có nghĩa Agent được hỏi nó, được vá nó, hay cần một bộ đọc cho nó.
REACHABLE_CONTRACT_FIELDS = frozenset(
    name for tool, contract in TOOL_CONTRACTS.items() if tool in AGENT_REACHABLE_TOOLS for name in contract.inputs
)

# Ô người dùng có thể trả lời TRONG AGENT HIỆN TẠI. Suy ra, không liệt kê: thêm
# một ô vào một tool với-tới-được mà quên bộ đọc thì test parity đỏ, không phải
# phát hiện lúc chạy thật.
USER_ANSWERABLE_FIELDS = REACHABLE_CONTRACT_FIELDS - AUTHORITATIVE_FIELDS

# Ô CHỈ tồn tại trong tool cũ. Agent không hỏi, không vá, và không có bộ đọc —
# viết một bộ đọc cho chúng là làm sống lại một capability đã bị loại.
LEGACY_ONLY_FIELDS = ALL_CONTRACT_FIELDS - REACHABLE_CONTRACT_FIELDS


def _spec_for(field: str) -> Any | None:
    """FieldSpec của một ô. `None` nếu hai tool khai báo khác nhau cho cùng tên.

    Khác nhau thì không có "một" luật để áp, và đoán bừa một bên là chọn hộ
    người dùng. Test parity khoá điều này lại.
    """
    specs = [c.inputs[field] for c in TOOL_CONTRACTS.values() if field in c.inputs]
    if not specs:
        return None
    first = specs[0]
    for other in specs[1:]:
        # Liệt kê từng thuộc tính, nên mỗi thuộc tính MỚI của `FieldSpec` phải
        # được thêm vào đây bằng tay. Quên một cái thì hai luật khác nhau trông
        # y hệt nhau, và bộ đọc câu trả lời áp bừa một trong hai.
        if (
            other.kind,
            other.enum,
            other.minimum,
            other.exclusive_minimum,
            other.maximum,
            other.must_be_true,
        ) != (
            first.kind,
            first.enum,
            first.minimum,
            first.exclusive_minimum,
            first.maximum,
            first.must_be_true,
        ):
            return None
    return first


def _extract_date(text: str) -> str | None:
    match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if match:
        year, month, day = map(int, match.groups())
    else:
        match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
        if not match:
            return None
        day, month, year = map(int, match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _extract_time(text: str) -> str | None:
    with_minutes = re.search(
        r"(?<!\d)([01]?\d|2[0-3])\s*[:;hH]\s*([0-5]\d)(?!\d)",
        text,
    )
    if with_minutes:
        return f"{int(with_minutes.group(1)):02d}:{with_minutes.group(2)}"

    # Trong tiếng Việt, "12h" và "12 giờ" có nghĩa chính xác là 12:00.
    # Negative lookahead ngăn 12h99 bị cắt thành 12h rồi chấp nhận nhầm.
    hour_only = re.search(
        r"(?<!\d)([01]?\d|2[0-3])\s*(?:h|giờ)(?!\s*\d)",
        text,
        re.IGNORECASE,
    )
    if hour_only:
        return f"{int(hour_only.group(1)):02d}:00"
    return None


# Cả hai cách viết ngày, gộp một mẫu để giữ ĐÚNG THỨ TỰ xuất hiện.
#
# Tách hai `finditer` rồi nối lại sẽ cho thứ tự sai khi một câu trộn cả hai
# dạng ("tham quan 2026-09-02 rồi đỗ xe 5/9/2026") — mọi ngày ISO sẽ đứng
# trước mọi ngày d/m/Y bất kể chúng nằm đâu trong câu.
_MOI_NGAY = re.compile(r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4})\b")


def extract_all_dates(text: str) -> list[str]:
    """Mọi ngày trong câu, THEO THỨ TỰ xuất hiện, dạng nguyên văn người dùng gõ.

    `_extract_date` dùng `re.search` nên chỉ thấy ngày ĐẦU TIÊN. Đúng khi
    chuỗi đưa vào đã được tách riêng cho một ô; sai khi một câu mang nhiều ô
    ngày cùng lúc — lúc đó MỌI ô đều nhận cùng một ngày.

    Đo được, câu hỏi gộp `viewing_date` + `booking_date` (luồng "tham quan và
    chỗ đỗ xe"), cả hai ngày đều hợp lệ:

        "…ngày 27/8/2026 … ngày 29/8/2026, khu A"
          → viewing_date = 2026-08-27
            booking_date = 2026-08-27   ← chỗ đỗ giữ SAI NGÀY, im lặng

    Trả về chuỗi NGUYÊN VĂN chứ không phải ISO đã chuẩn hoá: caller còn phải
    đưa từng chuỗi qua bộ đọc của đúng ô đó, để chính sách lịch (không quá
    khứ, không quá xa) vẫn chạy nguyên vẹn cho từng ô.
    """
    return _MOI_NGAY.findall(text)


def _extract_parking_zone(text: str) -> str | None:
    match = re.search(r"\b(?:zone|khu)[ _-]*([ab])\b", text, re.IGNORECASE)
    return f"ZONE_{match.group(1).upper()}" if match else None


# "khu D", "zone C", "khu 3" — người dùng NÊU TÊN một khu, và nó không phải
# A hay B. Khác hẳn "chưa nói khu nào".
_ZONE_MENTION = re.compile(r"\b(?:zone|khu)[ _-]*([a-z0-9])\b", re.IGNORECASE)


def _unknown_zone(text: str | None) -> str | None:
    """Ký tự của khu được nhắc tên nhưng không có thật. None nếu không có.

    `_extract_parking_zone` trả `None` cho CẢ HAI trường hợp — không nhắc khu
    nào, và nhắc một khu không tồn tại. Gộp hai thứ đó làm một tạo ra vòng lặp
    chết, y như đã xảy ra với tên dự án:

        Bạn:    khu D
        P-118:  Mình cần thêm thông tin: khu vực đỗ xe (Khu A hoặc Khu B)
        Bạn:    đúng đổi qua khu D
        P-118:  Bạn muốn đổi sang Khu D đúng không? Mình cần xác nhận lại...

    Người dùng ĐÃ trả lời, và được hỏi lại đúng câu vừa hỏi. Không có gì họ gõ
    thêm thoát ra được, vì hệ thống không bao giờ nói ra điều nó biết rõ: bãi
    xe chỉ có hai khu.

    Trả về đúng MỘT ký tự đã kiểm, không phải chuỗi người dùng gõ — câu trả lời
    ghép từ nó vẫn không có đường nào cho văn bản tự do lọt ra.
    """
    for match in _ZONE_MENTION.finditer(text or ""):
        token = match.group(1).upper()
        if token not in {"A", "B"}:
            return token
    return None


def _unknown_zone_message(zone: str) -> str:
    return f"Bãi xe chỉ có Khu A và Khu B, không có Khu {zone}. Bạn chọn giúp mình một trong hai khu đó nhé."


def extract_plate_number(text: str) -> str | None:
    """Bản công khai —  dùng CHÍNH luật này, không chép lại."""
    return _extract_plate_number(text)


def _extract_plate_number(text: str) -> str | None:
    """Chuẩn hoá biển số. `None` nếu không tìm thấy biển hợp lệ.

    Biển Việt Nam viết có DẤU CHẤM ở giữa phần số: `30A-123.45`. Mẫu cũ không
    nhận dấu ấy, nên nó khớp phần trước dấu chấm rồi dừng — và vì `re.search`
    tìm thấy một kết quả, không có lỗi nào được nêu.
    #
    Đo được, và đây là chính ví dụ mẫu in trong ô nhập của ứng dụng:

        30A-123.45  →  30A-123     ← mất hai chữ số cuối
        59A-123.45  →  59A-123
        51F-678.90  →  51F-678

    Xe được đăng ký dưới một biển KHÁC biển người dùng gõ, và không màn hình
    nào nói ra điều đó. Hỏng lặng lẽ ở đúng trường định danh chiếc xe.

    Chấp nhận 3–6 chữ số để không tự áp một chuẩn đăng kiểm cụ thể lên dữ liệu
    mock; đếm sau khi đã bỏ dấu phân cách, nên `30A-123.45` và `30A-12345` cho
    cùng một kết quả.
    """
    match = re.search(r"\b(\d{2}[a-z]{1,2})[ .-]?(\d{3,6}(?:[. ]\d{1,3})?)\b", text, re.IGNORECASE)
    if match is None:
        return None
    digits = re.sub(r"\D", "", match.group(2))
    if not 3 <= len(digits) <= 6:
        return None
    return f"{match.group(1).upper()}-{digits}"


def _extract_vehicle_type(text: str) -> str | None:
    """Chuẩn hoá cách gọi phương tiện phổ biến, không suy diễn từ câu mơ hồ."""
    lowered = text.casefold()
    motorcycle = r"\b(?:xe\s*máy|xemay|mô\s*tô|moto|motorcycle)\b"
    car = r"\b(?:xe\s*hơi|xe\s*ô\s*tô|ô\s*tô|ôto|oto|car)\b"
    if re.search(motorcycle, lowered):
        return "motorcycle"
    if re.search(car, lowered):
        return "car"
    return None


def _extract_passenger_count(text: str) -> int | None:
    """Số người đi xe tham quan: số đi kèm 'người'/'khách', hoặc số đứng riêng.

    Sức chứa xe là 1–30 (provider ép). Ngoài khoảng là không giải quyết được —
    trả None để backend hỏi lại thay vì đẩy giá trị vô lý xuống provider. Số
    trong ngày tháng (2026-08-20) bị loại bằng yêu cầu số đứng một mình.
    """
    with_unit = re.search(r"\b(\d{1,3})\s*(?:người|khách|nguoi|khach)\b", text, re.IGNORECASE)
    match = with_unit or re.search(r"(?<![-\d])(\d{1,2})(?![-\d])", text)
    if not match:
        return None
    count = int(match.group(1))
    return count if 1 <= count <= 30 else None


# Xa nhất người dùng được đặt trước. Không có trần thì "2199-12-31" là một ngày
# hợp lệ: nó không nằm trong quá khứ, nên mọi lớp kiểm đều cho qua. Chỗ đỗ xe
# năm 2199 vẫn được giữ thật trong database và chiếm capacity thật.
#
# 1825 ngày là NĂM năm, không phải hai. Ghi chú cũ ở đây nói "hai năm" và nó
# sai — một chú thích sai về một hằng số chính sách còn tệ hơn không có chú
# thích, vì người đọc tin nó mà không mở ra kiểm.
#
# LẤY TỪ `src/common/schedule_policy.py`, không chép trị số. Hai bảng nói
# cùng một luật mà nằm hai nơi thì sớm muộn cũng lệch, và bên lỏng hơn thành
# bên thật. Lý do chọn 5 năm — kể cả phần đánh đổi với fixture của bộ test —
# được ghi tại chính hằng số ấy trong `src/agents/validator.py`.
MAX_SCHEDULE_HORIZON_DAYS = MAX_HORIZON_DAYS


def _is_allowed_schedule_date(value: str) -> bool:
    parsed = date.fromisoformat(value)
    today = date.today()
    return today <= parsed <= today + timedelta(days=MAX_SCHEDULE_HORIZON_DAYS)


def _is_allowed_schedule_time(field: str, value: str) -> bool:
    parsed = time.fromisoformat(value)
    window = TIME_WINDOWS.get(field)
    return window is None or window[0] <= parsed <= window[1]


def _fold(text: str) -> str:
    """Bỏ dấu + hạ chữ thường, để "điều hoà" và "dieu hoa" là một.

    `đ` phải thay TRƯỚC khi chuẩn hoá. NFD tách dấu ra khỏi nguyên âm, nhưng `đ`
    là một CHỮ CÁI riêng trong bảng chữ cái tiếng Việt, không phải `d` cộng dấu
    — nên nó đi qua NFD nguyên vẹn. Đo được: "điều hoà" → "đieu hoa", và không
    từ đồng nghĩa nào khớp. Chữ `đ` mở đầu rất nhiều từ ("điều hoà", "điện",
    "đồng ý"), nên bỏ sót nó là bỏ sót cả một nhóm.
    """
    swapped = text.casefold().replace("đ", "d")
    stripped = unicodedata.normalize("NFD", swapped)
    return "".join(ch for ch in stripped if unicodedata.category(ch) != "Mn")


# Cách người Việt NÓI một giá trị enum → token của contract.
#
# Phần duy nhất viết tay trong file này, và cố ý: contract chỉ biết
# `air_conditioning`, người dùng gõ "điều hoà". Bảng này chỉ MỞ RỘNG đầu vào —
# giá trị đi ra luôn là token của contract, nên nó không nới được luật.
#
# Thứ tự trong mỗi danh sách có nghĩa: cụm DÀI hơn phải đứng trước, nếu không
# "xe tải nhỏ" bị "xe tải" nuốt mất.
ENUM_SYNONYMS: dict[str, list[tuple[str, str]]] = {
    "interest_type": [
        ("tu van", "consultation"),
        ("mua", "buy"),
        ("thue", "rent"),
    ],
    "issue_type": [
        ("dieu hoa", "air_conditioning"),
        ("may lanh", "air_conditioning"),
        ("dien nuoc", "other"),
        ("ong nuoc", "plumbing"),
        ("nuoc", "plumbing"),
        ("dien", "electrical"),
        ("khac", "other"),
    ],
    "move_vehicle": [
        ("xe tai nho", "van"),
        ("xe van", "van"),
        ("van", "van"),
        ("xe tai", "truck"),
        ("khong", "none"),
        ("tu chuyen", "none"),
    ],
    "vehicle_type": [("xe may", "motorcycle"), ("mo to", "motorcycle"), ("o to", "car"), ("xe hoi", "car")],
}

# Dài nhất một ô văn bản tự do được phép. Có trần vì một mô tả 50.000 ký tự
# không phải một mô tả — nó là một payload, và nó đi thẳng xuống provider.
MAX_FREE_TEXT = 500

# Ô văn bản tự do THẬT SỰ, theo contract (`kind == "string"`, không có bộ đọc
# chuyên biệt nào khác). Liệt kê tường minh: một ô rơi vào đây nghĩa là nó nhận
# gần như mọi chuỗi, nên việc thêm tên vào danh sách này phải hiện trong diff.
FREE_TEXT_FIELDS = frozenset({"description", "location", "residential_area", "full_name", "apartment_code"})


def _parse_free_text(text: str) -> str | None:
    """Chuỗi không rỗng, có trần độ dài, một dòng logic.

    Xuống dòng bị gộp: một ô mô tả nhiều dòng đi xuống provider thường là dấu
    hiệu người dùng dán nhầm, và nó cũng là hình dạng ưa thích của prompt lồng.
    """
    cleaned = " ".join(text.split())
    if not cleaned or len(cleaned) > MAX_FREE_TEXT:
        return None
    return cleaned


def _enum_parser(field: str, allowed: frozenset[str]) -> Callable[[str], str | None]:
    synonyms = ENUM_SYNONYMS.get(field, [])
    canonical = {value.casefold(): value for value in allowed}

    def parse(text: str) -> str | None:
        # Token của contract gõ thẳng vẫn phải nhận: biểu mẫu gửi `ZONE_B`, và
        # đưa nó qua bộ đọc tiếng Việt thì bộ ấy không nhận ra.
        exact = canonical.get(text.strip().casefold())
        if exact is not None:
            return exact
        folded = _fold(text)
        for phrase, value in synonyms:
            if re.search(rf"\b{re.escape(phrase)}\b", folded) and value in allowed:
                return value
        return None

    return parse


def _parse_project_id(text: str) -> str | None:
    """Câu trả lời thường gộp tên dự án + ngày + giờ. `resolve` chỉ nhận đúng
    toàn bộ tên; `find` tìm tên đóng nằm bên trong câu tự nhiên."""
    return find_project_id(text) or resolve_project_id(text)


def _parse_project_name(text: str) -> str | None:
    """Giữ lại TÊN công khai, không phải mã. Việc đổi sang `project_id` do
    adapter ở biên làm, và làm ở đúng một chỗ."""
    found = _parse_project_id(text)
    return project_name(found) if found else None


# Câu trả lời CÓ / KHÔNG.
#
# Bốn thứ khác nhau phải được phân biệt, và đảo thứ tự `if` không làm được điều
# đó. Cả ba câu dưới đây đều đã đo được trên bản trước:
#
#     "tôi không đồng ý"        → True    ← cụm khẳng định thắng phủ định
#     "có thang máy không?"     → False   ← câu HỎI bị đọc thành lời từ chối
#     "không gian phòng khách"  → False   ← "không" trong một từ ghép
#
# Câu đầu ghi NGƯỢC một lời từ chối. Câu thứ hai biến một câu hỏi thành một
# quyết định. Nên luật được viết theo bốn tầng, theo đúng thứ tự:
#
#   1. Bỏ TỪ GHÉP chứa "không" nhưng không phủ định gì.
#   2. CÂU HỎI ĐUÔI — "không?"/"chưa?" ở cuối một câu có nội dung đứng trước.
#      Người dùng đang hỏi lại, chưa trả lời. → None
#   3. PHỦ ĐỊNH — "không" ở bất kỳ đâu còn lại thì phủ định phạm vi cả câu, kể
#      cả khi sau nó là một cụm khẳng định ("không đồng ý").
#   4. XÁC NHẬN.

_NEGATION_COMPOUNDS = ("khong gian", "khong khi", "khong quan", "khong luc")

# Đuôi câu hỏi tiếng Việt: "... không?", "... chưa ạ?", "... không nhỉ".
_TAG_QUESTION = re.compile(r"\b(?:khong|chua)\b(?:\s+(?:a|ạ|vay|nhi|the|nhe))?\s*[?.!]*\s*$")

_NO_WORDS = frozenset({"khong", "no", "khoi", "chua"})
_YES_WORDS = frozenset({"co", "vang", "u", "um", "dung", "duoc", "can", "yes", "ok", "oke", "roi"})

# Cụm khẳng định nhiều từ. Kiểm SAU phủ định, không bao giờ trước: "không đồng
# ý" chứa "đồng ý", và cụm ấy thắng phủ định là cách một lời từ chối bị ghi
# thành một lời đồng ý.
_YES_PHRASES = ("dong y", "dung roi", "chap thuan")

# Chấp thuận RÕ RÀNG. Chỉ những cụm này mới bật `consent`.
_CONSENT_PHRASES = ("dong y", "chap thuan", "toi cho phep", "cho phep lien he", "dong ys")


def _strip_compounds(text: str) -> str:
    folded = _fold(text)
    for compound in _NEGATION_COMPOUNDS:
        folded = folded.replace(compound, " ")
    return folded


def _is_tag_question(folded: str) -> bool:
    """Câu này đang HỎI chứ không đang trả lời.

    Đuôi hỏi chỉ tính khi có NỘI DUNG đứng trước từ phủ định: "có thang máy
    không ạ" là câu hỏi, còn "không ạ" là một lời từ chối lịch sự. Đếm theo
    phần đứng trước, không theo tổng số từ — nếu không thì mọi tiểu từ lịch sự
    ("ạ", "nhé") đều biến một câu trả lời thành một câu hỏi.
    """
    match = _TAG_QUESTION.search(folded)
    if match is None:
        return False
    return bool(re.findall(r"[a-z0-9]+", folded[: match.start()]))


# Tiểu từ lịch sự và đệm câu. Chúng không mang nội dung, nên một câu chỉ gồm
# chúng cộng một tiếng có/không vẫn là một tiếng có/không.
_PARTICLES = frozenset({"a", "vay", "nhi", "the", "nhe", "minh", "ban", "ban oi", "di", "day", "ha"})


def is_bare_yes_no(text: str) -> bool:
    """Cả câu này có KHÔNG CÓ GÌ ngoài một tiếng có/không hay không.

    Một tiếng như vậy nói được GIÁ TRỊ nhưng không nói được nó thuộc về Ô NÀO.
    Khi nhiều ô cùng nhận được nó — `needs_loading_support` nhận `False`, còn
    `move_vehicle` có một giá trị đánh vần đúng bằng từ ấy (`"khong"` →
    `"none"`) — thì một tiếng đáp đóng luôn hai câu hỏi.

    Đo được trên stack demo, dịch vụ chuyển nhà:

        P-118: …có cần hỗ trợ bốc dỡ hay không và phương tiện chuyển nhà?
        Bạn:   không
        →      "needs_loading_support": false,
               "move_vehicle": "none"      ← chưa bao giờ được nói ra

    Câu CÓ nội dung thật ("đi xe tải", "ngày 31 lúc 8h") không rơi vào đây:
    nội dung ấy chỉ ra ô, nên rút nhiều ô từ một câu vẫn đúng.
    """
    folded = _strip_compounds(text)
    words = re.findall(r"[a-z0-9]+", folded)
    if not words:
        return False
    con_lai = [w for w in words if w not in _PARTICLES]
    if not con_lai:
        return False
    return all(w in _NO_WORDS or w in _YES_WORDS for w in con_lai)


def _parse_boolean(text: str) -> bool | None:
    """`True`/`False` cho một câu trả lời CÓ/KHÔNG rõ ràng, `None` nếu không rõ.

    `None` là câu trả lời đúng cho "câu này không nói có cũng không nói không".
    Đoán bừa ở một ô boolean là ghi một quyết định người dùng chưa đưa ra.
    """
    folded = _strip_compounds(text)
    if _is_tag_question(folded):
        return None
    words = set(re.findall(r"[a-z0-9]+", folded))
    if words & _NO_WORDS:
        return False
    if words & _YES_WORDS or any(phrase in folded for phrase in _YES_PHRASES):
        return True
    return None


def _parse_consent(text: str) -> bool | None:
    """`consent` chặt hơn mọi ô boolean khác, và cố ý.

    Đây là một CHẤP THUẬN cho phép liên hệ, không phải một tuỳ chọn tiện nghi.
    "ok", "được", "ừ" là lời đáp trôi chảy trong hội thoại — chúng có thể đang
    đáp lại câu trước đó chứ không phải đang cho phép gì. Suy `True` từ chúng là
    ghi một sự đồng ý người dùng chưa từng đưa ra, vào đúng ô mà việc đó có hệ
    quả ngoài hệ thống.

    Nên `True` chỉ đến từ một cụm xác nhận RÕ. `False` thì vẫn theo luật phủ
    định chung: từ chối phải dễ nói hơn đồng ý.

    Biểu mẫu gửi bool thật qua đường structured và không đi qua đây.
    """
    folded = _strip_compounds(text)
    if _is_tag_question(folded):
        return None
    words = set(re.findall(r"[a-z0-9]+", folded))
    if words & _NO_WORDS:
        return False
    if any(phrase in folded for phrase in _CONSENT_PHRASES):
        return True
    return None


def _date_parser() -> Callable[[str], str | None]:
    def parse(text: str) -> str | None:
        value = _extract_date(text)
        return value if value is not None and _is_allowed_schedule_date(value) else None

    return parse


def _time_parser(field: str) -> Callable[[str], str | None]:
    def parse(text: str) -> str | None:
        value = _extract_time(text)
        return value if value is not None and _is_allowed_schedule_time(field, value) else None

    return parse


def _build_registry() -> dict[str, Callable[[str], Any]]:
    """Bảng ô → bộ đọc, dựng TỪ contract.

    Ô chuyên biệt (`plate_number`, `parking_zone`, `project_id`...) khai báo
    thẳng vì bộ đọc của chúng biết những thứ contract không biết — hình dạng
    biển số Việt Nam, danh mục dự án. Còn lại suy ra từ `FieldSpec`.
    """
    registry: dict[str, Callable[[str], Any]] = {
        "plate_number": _extract_plate_number,
        "parking_zone": _extract_parking_zone,
        "vehicle_type": _extract_vehicle_type,
        "passenger_count": _extract_passenger_count,
        "project_id": _parse_project_id,
        # Không phải ô của contract — đây là tên CÔNG KHAI mà biên API dùng.
        "project_name": _parse_project_name,
    }
    for name in sorted(USER_ANSWERABLE_FIELDS):
        if name in registry:
            continue
        spec = _spec_for(name)
        if spec is None:
            continue
        if name in DATE_FIELDS or spec.kind == "date":
            registry[name] = _date_parser()
        elif name in TIME_FIELDS or spec.kind == "time":
            registry[name] = _time_parser(name)
        elif spec.kind == "boolean":
            registry[name] = _parse_consent if name == "consent" else _parse_boolean
        elif spec.kind == "enum" and spec.enum:
            registry[name] = _enum_parser(name, spec.enum)
        elif spec.kind == "string" and name in FREE_TEXT_FIELDS:
            registry[name] = _parse_free_text
    return registry


FIELD_PARSERS: dict[str, Callable[[str], Any]] = _build_registry()


def parse_field(field: str, text: str) -> Any | None:
    """Giá trị chuẩn của `field` đọc từ `text`, hoặc `None`.

    `None` gộp hai chuyện — ô không có bộ đọc, và ô có bộ đọc nhưng chuỗi không
    hợp lệ — vì với người gọi cả hai đều là "không dùng được". Muốn phân biệt
    thì tra `FIELD_PARSERS` trước.
    """
    parser = FIELD_PARSERS.get(field)
    if parser is None or not isinstance(text, str) or not text.strip():
        return None
    try:
        return parser(text)
    except (ValueError, TypeError):
        # Chuỗi đúng hình dạng nhưng giá trị vô nghĩa ("2026-02-31") — đó là
        # "không hợp lệ", không phải sự cố.
        return None
