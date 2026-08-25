"""Suy diễn trạng thái hiển thị của một hồ sơ xác minh. MỘT chỗ duy nhất.

Vì sao là một module riêng, thuần, không I/O
--------------------------------------------
Trạng thái người dùng nhìn thấy được ghép từ hai nguồn ở hai hệ thống:

    provider_status         Ownership Provider quyết định gì
    materialization_status  main app đã ghi xong chưa

Ba endpoint cùng phải trả lời câu hỏi ấy — hồ sơ của tôi, hàng đợi của đơn vị,
màn giám sát của admin. Viết phép ghép ba lần nghĩa là ba bản sẽ lệch, và bản
lệch sẽ nói "Đã xác minh" cho một hồ sơ chưa mở quyền.

Không đọc database, không gọi provider, không side effect: nhờ vậy toàn bộ ma
trận trạng thái kiểm được mà không cần dựng gì, và ba endpoint không thể có ba
cách hiểu.

Nguyên tắc trung tâm
--------------------
**Chỉ `APPROVED` + `SUCCESS` mới được nói "Đã xác minh".**

Mọi tổ hợp khác — kể cả `APPROVED` không biên lai — đều chưa mở quyền. Suy
"đơn vị đã duyệt nên chắc xong rồi" là đúng thứ đã tạo ra split-brain: quyết
định và kết quả là hai chuyện.

Provider luôn thắng
-------------------
Biên lai là bằng chứng vận hành của main app, không phải nguồn sự thật về quyết
định. Biên lai `SUCCESS` trên một hồ sơ provider nói `REJECTED` là dấu hiệu
lệch dữ liệu — không phải lý do để mở quyền.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# --- trạng thái của ĐƠN VỊ ---------------------------------------------------
PROVIDER_PENDING: Final = "PENDING"
PROVIDER_APPROVED: Final = "APPROVED"
PROVIDER_REJECTED: Final = "REJECTED"
PROVIDER_UNKNOWN: Final = "UNKNOWN"
_PROVIDER_HIEU_DUOC = frozenset({PROVIDER_PENDING, PROVIDER_APPROVED, PROVIDER_REJECTED})

# --- trạng thái materialize CÔNG KHAI ---------------------------------------
MAT_NOT_STARTED: Final = "NOT_STARTED"
MAT_PENDING: Final = "PENDING"
MAT_SUCCESS: Final = "SUCCESS"
MAT_FAILED: Final = "FAILED"
MAT_NOT_REQUIRED: Final = "NOT_REQUIRED"
MAT_UNKNOWN: Final = "UNKNOWN"
_MAT_HIEU_DUOC = frozenset({MAT_NOT_STARTED, MAT_PENDING, MAT_SUCCESS, MAT_FAILED, MAT_NOT_REQUIRED, MAT_UNKNOWN})

# --- trạng thái HIỆU DỤNG ----------------------------------------------------
EFF_WAITING_PROVIDER: Final = "WAITING_PROVIDER"
EFF_REJECTED: Final = "REJECTED"
EFF_APPROVED_PROCESSING: Final = "APPROVED_PROCESSING"
EFF_APPROVED_NEEDS_RETRY: Final = "APPROVED_NEEDS_RETRY"
EFF_APPROVED_BLOCKED: Final = "APPROVED_BLOCKED"
EFF_VERIFIED: Final = "VERIFIED"
EFF_NEEDS_RECONCILIATION: Final = "APPROVED_NEEDS_RECONCILIATION"
# Lệch dữ liệu trên hồ sơ CHƯA được duyệt.
#
# Tách khỏi `APPROVED_NEEDS_RECONCILIATION` vì tên kia khẳng định hồ sơ đã được
# duyệt — sai khi đơn vị còn đang cân nhắc hoặc đã từ chối. Và tách khỏi
# `REJECTED`/`WAITING_PROVIDER` vì hai cái đó đọc như kết cục BÌNH THƯỜNG, nên
# không ai đi soát tại sao main app lại ghi SUCCESS cho một hồ sơ như vậy.
EFF_NEEDS_RECONCILIATION_NEUTRAL: Final = "NEEDS_RECONCILIATION"
EFF_UNKNOWN: Final = "UNKNOWN"

CONSISTENT: Final = "CONSISTENT"
NEEDS_RECONCILIATION: Final = "NEEDS_RECONCILIATION"

# Mã lỗi nghiệp vụ — khác hẳn lỗi hạ tầng. Hạ tầng thì thử lại được; nghiệp vụ
# thì thử lại bao nhiêu lần cũng hỏng y hệt, và nói "đang thử lại" là hứa suông.
BUSINESS_REFUSED: Final = "BUSINESS_REFUSED"

_CAU_CHU: Final[dict[str, str]] = {
    EFF_WAITING_PROVIDER: "Đang chờ đơn vị xác minh",
    EFF_REJECTED: "Đã từ chối",
    EFF_APPROVED_PROCESSING: "Đã duyệt, hệ thống đang hoàn tất cập nhật",
    EFF_APPROVED_NEEDS_RETRY: "Đã duyệt, hệ thống chưa hoàn tất cập nhật",
    EFF_APPROVED_BLOCKED: "Đã duyệt nhưng chưa thể mở quyền",
    EFF_VERIFIED: "Đã xác minh",
    EFF_NEEDS_RECONCILIATION: "Đã duyệt, đang kiểm tra trạng thái cập nhật",
    EFF_NEEDS_RECONCILIATION_NEUTRAL: "Đang kiểm tra lại trạng thái hồ sơ",
    EFF_UNKNOWN: "Chưa xác định được trạng thái",
}


@dataclass(frozen=True)
class VerificationView:
    provider_status: str
    materialization_status: str
    effective_status: str
    display_status: str
    consistency_status: str

    @property
    def recovery_required(self) -> bool:
        """Đơn vị đã ký, main app chưa xong. Việc cần làm là HOÀN TẤT, không phải duyệt lại."""
        return self.effective_status in (
            EFF_APPROVED_NEEDS_RETRY,
            EFF_NEEDS_RECONCILIATION,
            EFF_NEEDS_RECONCILIATION_NEUTRAL,
            EFF_APPROVED_PROCESSING,
        )


def derive(
    provider_status: str | None,
    materialization_status: str | None = None,
    safe_error_code: str | None = None,
) -> VerificationView:
    """Ghép hai nguồn thành một trạng thái người đọc hiểu được.

    `materialization_status=None` nghĩa là KHÔNG có biên lai — khác hẳn với
    "có biên lai và nó nói NOT_STARTED".
    """
    nha_cung_cap = provider_status if provider_status in _PROVIDER_HIEU_DUOC else PROVIDER_UNKNOWN
    bien_lai = materialization_status if materialization_status in _MAT_HIEU_DUOC else None

    # Trạng thái đơn vị lạ: không đoán, và không echo giá trị thô ra ngoài.
    if nha_cung_cap == PROVIDER_UNKNOWN:
        return _view(PROVIDER_UNKNOWN, bien_lai or MAT_UNKNOWN, EFF_UNKNOWN, NEEDS_RECONCILIATION)

    if nha_cung_cap == PROVIDER_PENDING:
        # Biên lai SUCCESS trong khi đơn vị chưa quyết định là dữ liệu lệch.
        # Nó KHÔNG được thành "đã xác minh" — quyết định chưa tồn tại.
        # `PENDING + PENDING` là HỢP LỆ, và điều đó được chứng minh bằng
        # production lifecycle chứ không phải suy đoán: `run_decision` mở biên
        # lai TRƯỚC khi hỏi đơn vị, nên có một khoảng thật trong đó biên lai đã
        # PENDING còn quyết định thì chưa có. Xem
        # `test_a_pending_receipt_before_the_provider_decides_is_a_valid_moment`
        # — nó chụp trạng thái database tại đúng ranh giới ấy.
        if bien_lai in (None, MAT_PENDING):
            return _view(nha_cung_cap, bien_lai or MAT_NOT_STARTED, EFF_WAITING_PROVIDER, CONSISTENT)
        # Mọi trạng thái khác nghĩa là materialization đã chạy tới đâu đó trong
        # khi đơn vị chưa quyết định. Không luồng thật nào dẫn tới đó — dữ liệu
        # lệch, không phải một pha bình thường.
        return _view(nha_cung_cap, bien_lai, EFF_NEEDS_RECONCILIATION_NEUTRAL, NEEDS_RECONCILIATION)

    if nha_cung_cap == PROVIDER_REJECTED:
        # Từ chối là terminal. Biên lai SUCCESS ở đây nghĩa là main app đã ghi
        # một thứ mà đơn vị không đồng ý — cần soát, và tuyệt đối không mở quyền.
        # Từ chối rồi thì main app KHÔNG có việc gì để làm. Bất kỳ biên lai
        # nào ngoài `NOT_REQUIRED` nghĩa là nó đã bắt đầu một việc đơn vị không
        # đồng ý — cần soát, và tuyệt đối không hiện như kết cục bình thường.
        if bien_lai in (None, MAT_NOT_REQUIRED):
            return _view(nha_cung_cap, bien_lai or MAT_NOT_REQUIRED, EFF_REJECTED, CONSISTENT)
        return _view(nha_cung_cap, bien_lai, EFF_NEEDS_RECONCILIATION_NEUTRAL, NEEDS_RECONCILIATION)

    # --- đơn vị đã DUYỆT ----------------------------------------------------
    if bien_lai is None:
        # Không biên lai: không biết main app đã làm gì. Đây là dữ liệu cũ hoặc
        # một cú chết trước khi kịp ghi dòng đầu tiên. Không suy là xong.
        return _view(nha_cung_cap, MAT_UNKNOWN, EFF_NEEDS_RECONCILIATION, NEEDS_RECONCILIATION)
    if bien_lai == MAT_SUCCESS:
        return _view(nha_cung_cap, bien_lai, EFF_VERIFIED, CONSISTENT)
    if bien_lai in (MAT_PENDING, MAT_NOT_STARTED):
        return _view(nha_cung_cap, bien_lai, EFF_APPROVED_PROCESSING, CONSISTENT)
    if bien_lai == MAT_FAILED:
        if safe_error_code == BUSINESS_REFUSED:
            # Điều kiện nghiệp vụ chưa thoả. Thử lại không sửa được gì; nói
            # "đang thử lại" là hứa một lượt phục hồi không bao giờ tới.
            return _view(nha_cung_cap, bien_lai, EFF_APPROVED_BLOCKED, CONSISTENT)
        return _view(nha_cung_cap, bien_lai, EFF_APPROVED_NEEDS_RETRY, CONSISTENT)
    # APPROVED + NOT_REQUIRED / UNKNOWN: tổ hợp không hợp lệ.
    return _view(nha_cung_cap, bien_lai, EFF_NEEDS_RECONCILIATION, NEEDS_RECONCILIATION)


def _view(provider: str, mat: str, effective: str, consistency: str) -> VerificationView:
    return VerificationView(
        provider_status=provider,
        materialization_status=mat,
        effective_status=effective,
        display_status=_CAU_CHU[effective],
        consistency_status=consistency,
    )
