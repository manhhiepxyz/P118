"""Nối quyết định của ĐƠN VỊ với kết quả nghiệp vụ ở main app — an toàn khi hỏng.

Vấn đề mà file này tồn tại để giải
----------------------------------
Duyệt một hồ sơ xác minh gồm hai bước ở hai hệ thống:

    1. Ownership Provider ghi APPROVED        (HTTP, hệ thống khác)
    2. main app materialize liên kết/xe       (PostgreSQL của main app)

Không transaction nào bao được cả hai. Ép lỗi vào đúng khe giữa chúng, đo được
nguyên văn trước khi có file này:

    provider            APPROVED
    user_resident_links 0 dòng
    lần đầu   http=500
    lần hai   http=409   (ALREADY_DECIDED)

Người dùng kẹt vĩnh viễn. Lý do không phải "thiếu retry" — mà là **điểm vào duy
nhất để thử lại là `decide`**, và `decide` bắt đầu bằng việc hỏi provider quyết
định lần nữa. Provider trả 409 và nó ĐÚNG: nó đã quyết định rồi.

Cách sửa: tách hai trạng thái ra làm hai
----------------------------------------
    provider_decision_status   đơn vị đã quyết định gì   (nguồn: provider)
    materialization_status     main app đã ghi xong chưa (nguồn: bảng này)

"APPROVED" không còn đồng nghĩa với "quyền đã mở". Nhờ vậy retry trả lời được
câu hỏi đúng: provider đã ký rồi thì **chỉ chạy nốt bước 2**, không hỏi lại
bước 1.

Biên lai là bằng chứng VẬN HÀNH, không phải bản sao nguồn sự thật: nó không
mang `claimed_data`, ảnh, họ tên, token hay payload provider.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException as BusinessRefusal

from src.db.verification_receipt_repository import (
    ReceiptMissingError,
    VerificationRecoveryUnavailableError,
)

logger = logging.getLogger(__name__)

UNKNOWN = "UNKNOWN"
PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

NOT_REQUIRED = "NOT_REQUIRED"
MAT_PENDING = "PENDING"
SUCCESS = "SUCCESS"
FAILED = "FAILED"

# Trạng thái provider mà mình HIỂU. Bất kỳ giá trị nào khác — provider nâng cấp,
# thêm trạng thái mới — đều fail-closed: không materialize, không đoán.
_HIEU_DUOC = frozenset({PENDING, APPROVED, REJECTED})


# Tập mã lỗi HỮU HẠN. `type(exc).__name__` không phải contract: nó là tên class
# Python của lần cài đặt hôm nay, đổi khi ai đó đổi thư viện, và nó rò ra chi
# tiết triển khai qua một bảng mà admin đọc được.
#
# Ánh xạ fail-safe: thứ không nhận ra thành UNKNOWN, không thành "tạm thời" —
# gọi một lỗi lạ là tạm thời nghĩa là hứa một lượt phục hồi có thể không tới.
ERR_DATABASE = "DATABASE_UNAVAILABLE"
ERR_TRANSPORT = "TRANSPORT_UNAVAILABLE"
ERR_TEMPORARY = "MATERIALIZATION_TEMPORARY_FAILURE"
ERR_BUSINESS = "BUSINESS_REFUSED"
ERR_UNKNOWN = "UNKNOWN_MATERIALIZATION_FAILURE"

SAFE_ERROR_CODES = frozenset({ERR_DATABASE, ERR_TRANSPORT, ERR_TEMPORARY, ERR_BUSINESS, ERR_UNKNOWN})


def safe_error_code(exc: BaseException) -> str:
    """Mã ổn định cho một lỗi materialize. KHÔNG bao giờ mang message.

    Nhận diện theo LOẠI, không theo chuỗi: so khớp message là cách chắc chắn
    để một bản nâng cấp provider đổi câu chữ và mọi nhánh ở đây im lặng rơi
    xuống UNKNOWN.
    """
    import asyncpg
    import httpx

    if isinstance(exc, asyncpg.PostgresError | ConnectionError):
        return ERR_DATABASE
    if isinstance(exc, httpx.HTTPError):
        return ERR_TRANSPORT
    if isinstance(exc, TimeoutError):
        return ERR_TEMPORARY
    return ERR_UNKNOWN


def idempotency_key_for(record_id: str) -> str:
    """Khoá ổn định theo hồ sơ.

    Theo `record_id` chứ không theo lần thử: cùng một hồ sơ, retry từ tiến
    trình nào, sau bao nhiêu lần restart, vẫn ra cùng một khoá. Khoá theo lần
    thử thì mỗi retry là một "giao dịch mới" — đúng thứ khoá idempotency sinh
    ra để chặn.
    """
    return f"verif:{record_id}"


class DecisionConflictError(RuntimeError):
    """Quyết định đi ngược một trạng thái đã chốt. Không đổi gì, trả 409."""


class ProviderStateUnknownError(RuntimeError):
    """Không đọc được provider, hoặc provider ở trạng thái lạ. Fail-closed."""


@dataclass(frozen=True)
class Outcome:
    """Kết quả một lượt quyết định/resume, đủ để route map sang HTTP."""

    record: dict[str, Any]
    provider_decision_status: str
    materialization_status: str
    called_provider_decide: bool
    safe_error_code: str | None = None

    @property
    def finished(self) -> bool:
        return self.materialization_status in (SUCCESS, NOT_REQUIRED)


async def run_decision(
    *,
    record_id: str,
    requested_decision: str,
    decided_by: str,
    reject_reason: str | None,
    ownership: Any,
    receipts: Any,
    materialize: Any,
) -> Outcome:
    """Chạy một lượt quyết định — hoặc chạy nốt một lượt dở dang.

    `materialize` là callable async nhận `record` và ném khi hỏng. Tách ra làm
    tham số để lớp này không biết gì về căn hộ hay xe: nó chỉ biết "có một việc
    phải làm sau khi đơn vị đồng ý, và việc ấy có thể hỏng".
    """
    # 1. Ghi biên lai TRƯỚC khi gọi provider.
    #
    #    Thứ tự này là điểm mấu chốt. Nếu tiến trình chết ngay sau khi provider
    #    commit, thứ duy nhất chứng minh "đã có người bấm duyệt hồ sơ này" là
    #    dòng đã ghi TRƯỚC đó. Ghi sau thì cú chết ấy không để lại dấu vết nào.
    # `record_type=None`: lúc này CHƯA đọc provider nên chưa biết đây là hồ sơ
    # căn hộ hay xe. Điền bừa "apartment" là ghi một sự kiện chưa biết vào
    # audit dưới dạng đã biết — và nếu tiến trình chết ngay sau dòng này, biên
    # lai của một hồ sơ XE sẽ vĩnh viễn nói nó là căn hộ.
    da_claim = await receipts.open_receipt(
        record_id=record_id,
        record_type=None,
        requested_decision=requested_decision,
        idempotency_key=idempotency_key_for(record_id),
    )
    # F: ý định của lượt ĐẦU được giữ. Một lượt sau gửi quyết định trái chiều
    # trong khi provider chưa chốt là hai người bấm hai nút ngược nhau; chọn
    # bừa một cái nghĩa là biên lai kể sai chuyện đã xảy ra.
    if da_claim is not None and da_claim != requested_decision:
        raise DecisionConflictError(da_claim)

    # 2. Trạng thái AUTHORITATIVE, đọc chứ không đoán.
    record = await ownership.get_record(record_id)
    trang_thai = str(record.get("status") or "")
    loai = str(record.get("record_type") or "")
    if trang_thai not in _HIEU_DUOC:
        raise ProviderStateUnknownError(trang_thai)
    await receipts.set_record_type(record_id, loai)

    # 3. So quyết định muốn làm với trạng thái đã có.
    da_goi_decide = False
    if trang_thai == PENDING:
        record = await ownership.decide_record(
            record_id,
            decision=requested_decision,
            reject_reason=reject_reason,
            decided_by=decided_by,
        )
        da_goi_decide = True
        trang_thai = str(record.get("status") or "")
    else:
        # Provider ĐÃ chốt. Quyết định trùng khớp thì đây là một lượt chạy nốt,
        # không phải một quyết định mới — và tuyệt đối không gọi `decide` lần
        # hai. Đó chính là chỗ bản cũ kẹt.
        muon = APPROVED if requested_decision == "approve" else REJECTED
        if trang_thai != muon:
            raise DecisionConflictError(trang_thai)

    await receipts.set_provider_status(record_id, trang_thai)

    # 4. Từ chối thì không có gì để materialize.
    if trang_thai == REJECTED:
        await receipts.finish(record_id, NOT_REQUIRED, None)
        return Outcome(record, trang_thai, NOT_REQUIRED, da_goi_decide)

    # 5. Đã duyệt: chạy bước hai. Ghi PENDING trước để một cú chết ở đây vẫn
    #    để lại "đang dở", không phải "chưa bắt đầu".
    # Biên lai có thể đã biến mất giữa chừng (dọn nhầm, khôi phục thiếu). Dựng
    # lại từ dữ kiện AUTHORITATIVE vừa đọc được — không bịa: loại lấy từ
    # provider, ý định lấy từ chính request này.
    try:
        await receipts.start_materialization(record_id)
    except ReceiptMissingError:
        # CHỈ "biên lai không còn ở đó" mới được dựng lại.
        #
        # `except Exception` ở đây gộp hai chuyện khác hẳn nhau: biên lai bị mất
        # (dựng lại được, dữ kiện authoritative vừa đọc xong) và database không
        # ghi được (dựng lại là chạy đúng câu lệnh vừa hỏng, rồi materialize
        # dựa trên một trạng thái chưa từng persist). Chuyện thứ hai phải nổi
        # lên, không được nuốt.
        await receipts.open_receipt(
            record_id=record_id,
            record_type=loai,
            requested_decision=requested_decision,
            idempotency_key=idempotency_key_for(record_id),
        )
        await receipts.set_provider_status(record_id, trang_thai)
        await receipts.start_materialization(record_id)
    try:
        extra = await materialize(record)
    except BusinessRefusal:
        # LỜI TỪ CHỐI NGHIỆP VỤ, không phải sự cố. Hai thứ này trông giống nhau
        # ở chỗ cùng là exception, và gộp chúng là sai theo cách tệ nhất.
        #
        # "Người nộp đơn không còn liên kết căn hộ hợp lệ" sẽ hỏng y hệt ở lần
        # thử thứ một nghìn — biến nó thành `FAILED` + 202 là hứa với người
        # dùng một lượt phục hồi không bao giờ tới, và để hồ sơ treo mãi trong
        # hàng đợi "đang hoàn tất".
        #
        # Đo được: `test_approve_vehicle_fails_when_link_revoked` đổi từ 409
        # sang 202 ngay khi bản đầu của hàm này bắt mọi Exception như nhau.
        await receipts.finish(record_id, FAILED, ERR_BUSINESS)
        raise
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        # KHÔNG lưu message: cả provider lẫn PostgreSQL đều từng trả nguyên
        # payload trong message, và biên lai là thứ bị dump vào issue.
        ma = safe_error_code(exc)
        await receipts.finish(record_id, FAILED, ma)
        logger.warning("materialize hồ sơ %s hỏng (%s)", record_id, ma)
        return Outcome(record, trang_thai, FAILED, da_goi_decide, ma)

    if isinstance(extra, dict):
        record = {**record, "materialized": extra}

    # Ghi nghiệp vụ ĐÃ commit. Ghi biên lai thì chưa — và nó có thể hỏng.
    #
    # Thứ tự bắt buộc là: commit nghiệp vụ → biên lai SUCCESS → trả 200. Đảo
    # lại thành "commit → trả 200 → biên lai best-effort" nghĩa là orchestration
    # tuyên bố xong một việc mà nó không lưu được bằng chứng nào; sau restart,
    # lượt phục hồi đọc biên lai và thấy một việc dở dang không có thật.
    #
    # Ở đây kết cục là: nghiệp vụ đúng, xác nhận chưa xong → PENDING, và route
    # trả 202. KHÔNG cố ghi FAILED bằng chính repository vừa hỏng rồi coi như
    # nó thành công.
    try:
        await receipts.finish(record_id, SUCCESS, None)
    except (VerificationRecoveryUnavailableError, ReceiptMissingError) as exc:
        # CHỈ hai lỗi này. `except Exception` ở đây biến một `TypeError` trong
        # chính code này thành 202 "đang hoàn tất" — người dùng chờ một lượt
        # phục hồi không bao giờ tới, và bug thì không ai thấy vì nó trông
        # giống thứ tự khỏi.
        logger.warning("ghi biên lai SUCCESS hỏng cho hồ sơ %s (%s)", record_id, safe_error_code(exc))
        return Outcome(record, trang_thai, MAT_PENDING, da_goi_decide, safe_error_code(exc))
    return Outcome(record, trang_thai, SUCCESS, da_goi_decide)


def as_uuid(value: str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
