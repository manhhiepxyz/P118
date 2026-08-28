"""Báo giá có DANH TÍNH — bằng chứng cho việc "đơn vị nào, giá nào, cho yêu cầu nào".

Vì sao báo giá phải là một bản ghi, không phải một con số
--------------------------------------------------------
Bước A khoá quyền sở hữu: mỗi dòng chờ duyệt thuộc một đơn vị cụ thể. Nhưng đơn
vị ấy đến từ `don_vi_mac_dinh(tool)` — một bảng cứng trong mã. Ngay khi P-118
bắt đầu CHỌN đơn vị theo giá, câu hỏi đổi: *lấy gì làm bằng chứng rằng đơn vị
này đã báo giá này cho yêu cầu này?*

Không có bằng chứng ấy thì `service_provider_id` chỉ là một chuỗi đi kèm
request — và mọi thứ người dùng gửi được thì người dùng sửa được. Một biểu mẫu
gửi `{"service_provider_id": "MOV-03", "amount": 1000}` sẽ được tin, vì không
có gì để đối chiếu.

Báo giá persist là thứ để đối chiếu. Nó trả lời được cả bốn câu:

    ai báo        service_provider_id + external_quote_id
    giá bao nhiêu amount + currency
    cho việc gì   request_fingerprint
    còn hiệu lực  valid_until + status

`request_fingerprint` — khoá của toàn bộ cơ chế
-----------------------------------------------
Vân tay tính từ input CANONICAL của `schedule_move`. Đổi ngày, đổi xe, đổi
thang máy hay đổi nhu cầu bốc xếp đều ra một vân tay khác — nên một báo giá cũ
KHÔNG dùng lại được cho một yêu cầu đã đổi. Thiếu vế này thì khách xin báo giá
cho xe van rồi đặt xe tải với đúng giá ấy, và đơn vị nhận một việc họ chưa bao
giờ đồng ý.

`max_price` KHÔNG nằm trong vân tay, và không bao giờ ra khỏi P-118. Ngân sách
là thông tin của KHÁCH. Gửi nó đi rồi nhận về một con số sát ngân sách là mời
đơn vị định giá theo túi tiền người hỏi thay vì theo công việc — và khi ấy
"chọn đơn vị rẻ nhất" đo một thứ do chính mình tạo ra. P-118 hỏi giá thật của
mọi đơn vị TRƯỚC, rồi mới tự lọc theo ngân sách.

Không lưu `goal`, prompt hay bất kỳ văn bản hội thoại nào vào báo giá. Báo giá
là chứng từ thương mại; nó chỉ cần các dữ kiện định giá.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Các field CANONICAL định nghĩa một yêu cầu chuyển nhà.
#
# Đúng bằng `inputs` của `schedule_move` trong `tool_contract`, và đó không
# phải trùng hợp: vân tay phải phủ HẾT thứ ảnh hưởng tới giá và tới việc đơn vị
# có nhận được hay không. Thiếu một field nghĩa là đổi field ấy mà vân tay
# không đổi — tức dùng lại được báo giá cũ cho một yêu cầu khác.
FIELD_CHUYEN_NHA: tuple[str, ...] = (
    "move_date",
    "move_time",
    "move_origin_id",
    "move_destination_id",
    "move_size",
    "move_vehicle",
    "needs_elevator",
    "needs_loading_support",
)

# Không bao giờ đi ra ngoài, và không bao giờ vào vân tay.
FIELD_KHONG_GUI_PROVIDER: frozenset[str] = frozenset({"max_price"})

# Đồng bộ với `_CURRENCY` ở `tool_contract`. MVP chỉ VND — đây là luật nghiệp
# vụ, không phải giới hạn kỹ thuật.
CURRENCY_CHO_PHEP: frozenset[str] = frozenset({"VND"})

TRANG_THAI = ("ACTIVE", "CONFIRMED", "EXPIRED", "SUPERSEDED")


class QuoteInvalidError(Exception):
    """Báo giá không dùng được. Mang theo LÝ DO ở dạng mã, không phải câu chữ.

    Mã để tầng trên quyết định (xin báo giá mới hay dừng hẳn); câu chữ để người
    đọc. Một `LIKE '%hết hạn%'` hỏng ngay lần đầu ai đó đổi chính tả.
    """

    def __init__(self, ma: str, thong_diep: str) -> None:
        super().__init__(thong_diep)
        self.ma = ma


def _chuan_hoa(value: Any) -> Any:
    """Đưa một giá trị về dạng so sánh được, ổn định giữa các lần chạy.

    `date`/`time` thành chuỗi ISO vì cùng một ngày có thể tới đây dưới dạng
    `date(2026, 9, 30)` (từ TaskPlan đã parse) hoặc `"2026-09-30"` (từ JSONB
    đọc lại). Hai đường vào cho hai vân tay khác nhau thì báo giá vừa persist
    xong đã không khớp chính yêu cầu sinh ra nó.

    `bool` giữ nguyên chứ KHÔNG hoá chuỗi: `False` và `"False"` phải khác nhau,
    nếu không một field boolean thiếu sẽ trùng vân tay với field ấy bằng False.
    """
    if isinstance(value, bool) or value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def van_tay_yeu_cau(input_data: dict[str, Any], *, fields: tuple[str, ...] = FIELD_CHUYEN_NHA) -> str:
    """Vân tay của MỘT yêu cầu. Cùng yêu cầu thì cùng vân tay, mọi lúc.

    Chỉ lấy các field canonical: thứ khác trong `input_data` (khoá nội bộ,
    `max_price`, dấu vết của lượt sửa) không được ảnh hưởng tới danh tính của
    yêu cầu. Nếu chúng ảnh hưởng thì thêm một khoá nội bộ vô hại sẽ vô hiệu hoá
    mọi báo giá đang sống.

    Field THIẾU cũng được ghi lại (`None`), không bị bỏ qua: "chưa khai
    `needs_elevator`" và "`needs_elevator=false`" là hai yêu cầu khác nhau, và
    một trong hai chưa đủ thông tin để báo giá.

    `sort_keys` + `separators` cố định: JSON không tất định thì hàm băm cũng
    không, và một vân tay nhảy số là một vân tay vô dụng.
    """
    thua = FIELD_KHONG_GUI_PROVIDER & set(fields)
    if thua:
        raise ValueError(f"field không được vào vân tay: {sorted(thua)}")
    canonical = {ten: _chuan_hoa(input_data.get(ten)) for ten in sorted(fields)}
    chuoi = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(chuoi.encode("utf-8")).hexdigest()


def payload_gui_provider(input_data: dict[str, Any]) -> dict[str, Any]:
    """Đúng những gì được phép rời khỏi P-118 khi đi xin báo giá.

    ALLOWLIST, không phải blocklist. Một blocklist quên `max_price` thì ngân
    sách rò ra ngoài và không ai thấy; một allowlist quên `max_price` thì...
    không có gì xảy ra, vì nó vốn không có tên trong danh sách. Field mới thêm
    sau này cũng vậy: mặc định là KHÔNG gửi.
    """
    return {ten: _chuan_hoa(input_data.get(ten)) for ten in FIELD_CHUYEN_NHA}


@dataclass(frozen=True)
class BaoGia:
    """Một báo giá đã persist. Đọc lại từ database ra đúng hình dạng này."""

    quote_id: str
    external_quote_id: str
    service_provider_id: str
    service_type: str
    amount: int
    currency: str
    request_fingerprint: str
    valid_until: datetime
    status: str
    # NEO BẮT BUỘC, không có mặc định. Vân tay tính từ input, nên hai workflow
    # khác nhau xin cùng một việc có CÙNG vân tay; không neo thì báo giá của
    # người này dùng được cho yêu cầu của người kia.
    #
    # Không mặc định `None` là cố ý: một tham số tuỳ chọn nghĩa là luật chỉ
    # tồn tại với những call site nhớ tới nó — tức không tồn tại.
    workflow_id: str
    task_id: str
    created_at: datetime | None = None
    confirmed_at: datetime | None = None

    @property
    def het_han(self) -> bool:
        return self.valid_until <= _bay_gio(self.valid_until)


def _bay_gio(mau: datetime) -> datetime:
    """`now()` cùng kiểu tz-aware với `mau`, để so sánh không nổ.

    asyncpg trả `TIMESTAMPTZ` dưới dạng tz-aware; một `datetime.now()` naive so
    với nó thì `TypeError`, và lỗi ấy chỉ hiện ra ở đường hết hạn — tức đúng
    nhánh ít được chạy nhất.
    """
    from datetime import timezone

    return datetime.now(timezone.utc) if mau.tzinfo is not None else datetime.now()  # noqa: UP017


def kiem_bao_gia(
    bao_gia: BaoGia | None,
    *,
    service_type: str,
    service_provider_id: str,
    request_fingerprint: str,
    amount: int,
    currency: str,
    workflow_id: str,
    task_id: str,
) -> BaoGia:
    """Bảy điều kiện, phải đúng ĐỒNG THỜI. Sai một cái là ném.

    Đây là cổng duy nhất trước khi một báo giá được dùng để ghim hàng đợi hoặc
    để thu tiền. Viết nó thành nhiều nhánh rải rác ở các call site là cách chắc
    chắn để một call site quên một điều kiện.

    Thứ tự kiểm là thứ tự từ THÔ tới TINH — tồn tại, rồi trạng thái, rồi hạn,
    rồi mới tới việc nó có mô tả đúng yêu cầu này không. Nó làm thông điệp lỗi
    nói đúng nguyên nhân đầu tiên chứ không phải nguyên nhân cuối cùng.

    `amount`/`currency` được kiểm lại với bản đã persist chứ không phải được
    ĐỌC ra: caller đưa vào thứ họ định dùng, và hàm này nói thứ ấy có khớp
    chứng từ không. Nếu chỉ đọc ra thì một `amount` bị sửa ở task sẽ lặng lẽ bị
    thay thế, và không ai biết đã có một lần thử.
    """
    if bao_gia is None:
        raise QuoteInvalidError("QUOTE_NOT_FOUND", "Không tìm thấy báo giá.")
    if bao_gia.status != "ACTIVE":
        raise QuoteInvalidError("QUOTE_NOT_ACTIVE", f"Báo giá đang ở trạng thái {bao_gia.status}.")
    if bao_gia.het_han:
        raise QuoteInvalidError("QUOTE_EXPIRED", "Báo giá đã hết hiệu lực.")
    if bao_gia.service_type != service_type:
        raise QuoteInvalidError("QUOTE_WRONG_SERVICE", "Báo giá không thuộc dịch vụ này.")
    if bao_gia.service_provider_id != service_provider_id:
        raise QuoteInvalidError("QUOTE_WRONG_PROVIDER", "Báo giá không thuộc đơn vị này.")
    if bao_gia.request_fingerprint != request_fingerprint:
        raise QuoteInvalidError("QUOTE_STALE_REQUEST", "Yêu cầu đã đổi so với lúc báo giá.")
    if bao_gia.amount != amount or bao_gia.currency != currency:
        raise QuoteInvalidError("QUOTE_AMOUNT_MISMATCH", "Số tiền không khớp báo giá đã lưu.")
    # Neo LUÔN được kiểm, không phụ thuộc caller có truyền hay không. Bản đầu
    # để hai điều kiện này là tuỳ chọn (`x is not None and ...`) — nghĩa là một
    # call site quên truyền sẽ đi qua cổng mà không có gì đỏ lên.
    if bao_gia.workflow_id != workflow_id:
        raise QuoteInvalidError("QUOTE_WRONG_WORKFLOW", "Báo giá thuộc một yêu cầu khác.")
    if bao_gia.task_id != task_id:
        raise QuoteInvalidError("QUOTE_WRONG_TASK", "Báo giá thuộc một bước khác.")
    return bao_gia


def loc_theo_ngan_sach(danh_sach: list[BaoGia], max_price: int | None) -> list[BaoGia]:
    """Lọc theo ngân sách VÀ theo hạn — Ở PHÍA P-118, sau khi đã có giá thật.

    Hạn được lọc ở đây chứ không chỉ ở SQL, vì một báo giá hết hạn GIỮA lúc đọc
    và lúc chọn vẫn phải rớt. Bản đầu bỏ hẳn vế này với lý do "hạn là dữ kiện,
    luật sống ở tầng trên" — nhưng rồi không tầng nào lọc, và một báo giá quá
    hạn vẫn thành đề xuất. Nó chỉ bị chặn tận lúc tiêu thụ, tức sau khi đã hiện
    lên màn hình như một lựa chọn có thật.

    Không ai vừa túi thì trả RỖNG. Không nới ngân sách hộ, không chọn "gần
    nhất": tầng trên có nhiệm vụ nói ra giá thật rẻ nhất và để khách quyết
    định, chứ không phải lặng lẽ đặt một đơn vị vượt ngân sách.
    """
    con_han = [q for q in danh_sach if not q.het_han]
    if max_price is None:
        return con_han
    return [q for q in con_han if q.amount <= max_price]
