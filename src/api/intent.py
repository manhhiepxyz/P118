"""Intent lane — câu vừa gõ là yêu cầu MỚI hay là SỬA yêu cầu vừa rồi.

Owner: Thành Bảo (Decision layer)
File: src/api/intent.py

Vì sao tầng này tồn tại
-----------------------
Planner chỉ bao giờ được hỏi đúng một câu: "đây là dịch vụ nào". Nó chưa bao
giờ được hỏi "đây là việc mới hay là sửa việc vừa rồi". Nên mọi câu người dùng
gõ khi không có workflow nào đang chờ đều đi thẳng vào `/workflows/demo/start`
như một yêu cầu mới — kể cả ngay sau khi họ bấm Dừng.

Đo được trên chuỗi thật của người dùng, sau khi bấm Dừng một lượt tham quan:

    Bạn:    đổi lịch tham quan sang ngày 30
    P-118:  Mục tiêu của bạn có phần nằm ngoài các dịch vụ mình hỗ trợ...

Hai lỗi chồng lên nhau trong một câu trả lời đó:

  - Cổng `_amends_a_previous_request` chỉ mở ký ức đã huỷ khi câu mới mang một
    giá trị RÚT RA ĐƯỢC. Mẫu ngày của nó cần `30/08`, `2026-08-30` hoặc
    `09:30`; **"ngày 30" trơn thì không khớp**. Cổng đóng → Planner không thấy
    yêu cầu cũ → phân loại là ngoài phạm vi.
  - Kể cả khi cổng MỞ, đường đi vẫn là lập lại kế hoạch từ đầu. Người dùng sửa
    đúng một ô mà cả kế hoạch chạy lại — và ba lượt chạy cùng một câu từng cho
    ba kết quả khác nhau. Xem `rerun_with_answers`.

Đường đúng đã có sẵn: `amend_and_rerun` đọc giá trị cũ từ `workflow_tasks` (một
kế hoạch ĐÃ qua Validator), vá ô được sửa, và chỉ chạy lại những bước chưa
SUCCESS. Thứ còn thiếu chỉ là người dẫn đường từ ngôn ngữ tự nhiên tới nó —
trước đó nó chỉ với tới được bằng một nút bấm trên trang chi tiết, nên người
dùng gõ thì không bao giờ chạm vào.

Bảng định tuyến bốn nhánh
-------------------------
    TIEP_TUC    trả lời câu đang hỏi dở      → `/workflows/demo/{id}/continue`
    SUA_TRUOC   sửa yêu cầu vừa dừng         → `amend_and_rerun`   (nhánh MỚI)
    HOI_DAP     chào hỏi / hỏi năng lực      → speech lane
    YEU_CAU_MOI còn lại                      → Planner

Ba nhánh kia đã có đường đi từ trước (`/continue` khi có workflow đang chờ,
`src.api.small_talk.classify` cho lời xã giao); module này chỉ quyết định
`SUA_TRUOC` và trả phần còn lại về `YEU_CAU_MOI`. Enum giữ đủ bốn tên vì tên là
thứ đọc được ở chỗ định tuyến — một nhánh không tên thì lần sau lại có người
đoán.

Nguyên tắc: deterministic, 0 LLM call. Cùng lý do với speech lane — quyết định
"câu này có sửa việc cũ không" mà phải hỏi model thì nó không lặp lại được, và
một quyết định sai ở đây có nghĩa là chạy lại một việc người dùng đã chủ động
dừng.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum


class Intent(StrEnum):
    TIEP_TUC = "tiep_tuc"
    SUA_TRUOC = "sua_truoc"
    HOI_DAP = "hoi_dap"
    YEU_CAU_MOI = "yeu_cau_moi"


# Động từ NÓI RÕ ý sửa. Danh sách đóng, và cố ý hẹp.
#
# Hẹp vì lỗi hai phía không cân nhau. Bỏ sót một cách nói thì người dùng gõ lại
# đầy đủ — phiền, nhưng đúng. Nhận nhầm một yêu cầu mới thành "sửa" thì hệ
# thống lặng lẽ chạy lại kế hoạch cũ với một ô bị thay, và cái người dùng nhận
# được không phải cái họ vừa xin.
#
# "lại" một mình KHÔNG nằm đây: "đặt lại chỗ đỗ xe cho tháng sau" là việc mới.
_CHANGE_VERB = re.compile(
    r"\b(?:đổi|đôỉ|dổi|thay|sửa|sữa|chuyển|dời|rời|chỉnh|cập\s*nhật|update|change)\b",
    re.IGNORECASE,
)

# Hai cách người dùng thực tế dùng để NÓI LẠI một thay đổi trong chuỗi
# đăng ký xe + chỗ đỗ. Không mở rộng thành ``lại`` hay ``chỉ muốn`` đơn lẻ:
# chúng cũng xuất hiện trong yêu cầu mới. Điều kiện đủ vẫn nằm ở caller — phải
# có workflow cùng session đang sửa được và parser phải rút ra một giá trị mới.
_PARKING_RESTATEMENT = re.compile(
    r"\b(?:đăng\s*ký\s+lại|chỉ\s+muốn\s+đăng\s*ký\s+phương\s+tiện\s+và\s+chỗ\s+đỗ\s+xe)\b",
    re.IGNORECASE,
)

# Câu xin BỎ, không phải xin sửa. Bắt riêng để không nuốt: "đổi" trong "đổi ý,
# thôi không đặt nữa" là đổi ý chứ không phải đổi giá trị một ô.
_CANCEL_WORD = re.compile(r"\b(?:huỷ|hủy|bỏ|thôi|dừng|cancel)\b", re.IGNORECASE)


def wants_to_amend(text: str | None) -> bool:
    """Câu này có NÓI RÕ là muốn sửa yêu cầu trước không.

    Chỉ là điều kiện CẦN. Điều kiện đủ nằm ở chỗ gọi: phải có một yêu cầu đang
    sửa được, và câu phải rút ra được ít nhất một giá trị KHÁC giá trị cũ. Hai
    vế tách rời có chủ ý — "đổi sang khu B" khi khu hiện tại đã là B thì không
    có gì để sửa, và chạy lại một kế hoạch y nguyên là một lần gọi provider
    thừa chứ không phải một lần sửa.
    """
    said = (text or "").strip()
    if not said:
        return False
    if _CANCEL_WORD.search(said):
        return False
    return bool(_CHANGE_VERB.search(said) or _PARKING_RESTATEMENT.search(said))


# --- Ngày nói tắt ------------------------------------------------------------
#
# `_extract_date` chỉ đọc `2026-08-30` và `30/08/2026`. Người dùng thì viết
# "ngày 30", "30/8", "ngày 30 tháng 8" — cả ba đều thiếu thông tin, và thiếu
# đúng phần mà yêu cầu CŨ đang giữ. Nên neo vào giá trị cũ thay vì đoán.

_DAY_MONTH_YEAR = re.compile(r"(?<![\d/-])(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{4})(?![\d/-])")
_ISO = re.compile(r"(?<!\d)\d{4}-\d{1,2}-\d{1,2}(?!\d)")
_DAY_THANG_MONTH = re.compile(
    r"\b(?:ngày|ngay|mùng|mồng|hôm)\s*(\d{1,2})\s*(?:tháng|thang|/|-)\s*(\d{1,2})(?![\d/:-])",
    re.IGNORECASE,
)
_DAY_MONTH = re.compile(r"(?<![\d/:-])(\d{1,2})\s*[/-]\s*(\d{1,2})(?![\d/:-])")
_BARE_DAY = re.compile(r"\b(?:ngày|ngay|mùng|mồng|hôm)\s*(\d{1,2})(?![\d/:h-])", re.IGNORECASE)


def _iso_or_none(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _not_in_the_past(iso: str, today: date, *, step_months: int) -> str:
    """Ngày đã trôi qua thì hiểu là kỳ kế tiếp, không phải năm ngoái.

    "ngày 5" gõ vào ngày 21 tháng 8 nghĩa là 5 tháng 9 — người nói đang đặt
    lịch, và không ai đặt lịch cho một ngày đã qua. Nhưng chỉ nhích MỘT kỳ:
    nhích tới khi hợp lệ thì một lỗi gõ ("ngày 45") thành một vòng lặp.
    """
    when = date.fromisoformat(iso)
    if when >= today:
        return iso
    if step_months:
        month = when.month + step_months
        year = when.year + (month - 1) // 12
        rolled = _iso_or_none(year, (month - 1) % 12 + 1, when.day)
        return rolled or iso
    rolled = _iso_or_none(when.year + 1, when.month, when.day)
    return rolled or iso


def rewrite_relative_dates(
    text: str,
    *,
    anchor: str | None,
    today: date | None = None,
) -> str:
    """Viết đủ những ngày nói tắt, neo vào `anchor` — ngày của yêu cầu CŨ.

    Trả về câu đã thay, để bộ phân tích ngày đang dùng cho form đọc được mà
    không phải học thêm cú pháp nào. Không có gì mới được phân tích ở đây; đây
    chỉ là điền vào phần người dùng bỏ trống, và điền bằng dữ liệu có thật chứ
    không bằng suy đoán.

        anchor 2026-08-29 + "đổi sang ngày 30"          → 2026-08-30
        anchor 2026-08-29 + "đổi sang ngày 3 tháng 9"   → 2026-09-03
        anchor 2026-08-29 + "đổi sang 30/9"             → 2026-09-30

    Không có `anchor` thì không viết lại gì: đoán năm/tháng từ hôm nay cho một
    yêu cầu không rõ neo vào đâu là tự bịa ra một cam kết.
    """
    if not anchor:
        return text
    try:
        base = date.fromisoformat(anchor)
    except (TypeError, ValueError):
        return text
    now = today or date.today()

    # Ngày đã viết đủ thì để nguyên — cả câu, không riêng phần khớp. Một câu có
    # "30/08/2026" mà vẫn đem "30/08" đi neo lại là ghi đè lên thứ đã rõ.
    if _ISO.search(text) or _DAY_MONTH_YEAR.search(text):
        return text

    def day_and_month(match: re.Match[str]) -> str:
        iso = _iso_or_none(base.year, int(match.group(2)), int(match.group(1)))
        return _not_in_the_past(iso, now, step_months=0) if iso else match.group(0)

    def bare_day(match: re.Match[str]) -> str:
        iso = _iso_or_none(base.year, base.month, int(match.group(1)))
        return _not_in_the_past(iso, now, step_months=1) if iso else match.group(0)

    rewritten, changed = _DAY_THANG_MONTH.subn(day_and_month, text)
    if changed:
        return rewritten
    rewritten, changed = _DAY_MONTH.subn(day_and_month, text)
    if changed:
        return rewritten
    return _BARE_DAY.sub(bare_day, text)


# Ô mà một câu nói tự do đọc ra được KHÔNG CẦN ngữ cảnh.
#
# Đây là hàng rào của nhánh sửa. `_extract_follow_up_answers` có một nhánh
# cuối: khi chỉ còn đúng một field đang hỏi, nó lấy NGUYÊN câu làm giá trị. Ở
# chỗ đang hỏi thì đúng — người dùng vừa được hỏi ô đó. Ở đây thì sai: không ai
# hỏi gì cả, nên "đổi lịch tham quan sang ngày 30" sẽ được ghi thẳng vào một ô
# mô tả, và bước đó chạy với một câu tiếng Việt ở chỗ đáng lẽ là dữ liệu.
#
# Chỉ những ô có bộ phân tích RIÊNG, trả về giá trị chuẩn hoá, mới nằm đây.
AMENDABLE_FROM_TEXT = frozenset(
    {
        "viewing_date",
        "booking_date",
        "preferred_date",
        "move_date",
        "tour_date",
        "viewing_time",
        "preferred_time",
        "move_time",
        "project_id",
        "project_name",
        "parking_zone",
        "plate_number",
        "vehicle_type",
    }
)

# `passenger_count` cố ý KHÔNG nằm trong danh sách trên.
#
# Bộ phân tích của nó có nhánh cuối nhận một con số ĐỨNG RIÊNG, vì ở chỗ nó
# được dùng, người dùng vừa được hỏi thẳng "mấy người". Ở nhánh sửa không ai
# hỏi gì, nên "đổi sang ngày 30" — khi yêu cầu cũ không có ô ngày nào để neo —
# sẽ được đọc thành "đổi thành 30 người". Một con số trần trong câu tự do
# không đủ để suy ra nó thuộc ô nào.


def amend_summary(labels_and_values: list[tuple[str, str]]) -> str:
    """Nói ra ĐÃ ĐỔI GÌ, không chỉ "đã cập nhật".

    Nhánh này chạy lại một kế hoạch mà không hỏi lại câu nào, nên câu trả lời
    phải đủ để người đọc bắt được nếu hệ thống hiểu sai — họ còn kịp bấm Dừng.
    """
    parts = ", ".join(f"{label} thành {value}" for label, value in labels_and_values)
    return f"Mình đã đổi {parts} rồi chạy lại chính yêu cầu trước, không tạo yêu cầu mới."
