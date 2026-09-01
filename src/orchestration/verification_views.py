"""Dựng view của hồ sơ xác minh cho BA VAI. Một chỗ duy nhất, thuần.

Vì sao ba hàm chứ không một schema dùng chung
---------------------------------------------
Ba vai cần ba thứ khác nhau, và khác nhau theo hướng đối nghịch:

    customer   biết hồ sơ của mình tới đâu. KHÔNG cần thấy lại ảnh giấy tờ.
    provider   PHẢI thấy `claimed_data` và `proof_image_urls` — đó là toàn bộ
               lý do bước xác minh tồn tại. Không có chúng thì không duyệt được.
    admin      biết hệ thống đang có việc gì, của ai. Không cần giấy tờ của ai.

Một schema dùng chung ép mọi quyết định privacy thành "có hay không có trường",
trong khi câu hỏi đúng là "vai nào được thấy trường nào". Chính vì dùng chung mà
bản thiết kế đầu của tôi từng đề xuất bỏ `claimed_data` khỏi cả ba — tức là lấy
mất công cụ làm việc của người duyệt.

Allowlist, không blocklist
--------------------------
Mỗi hàm DỰNG dict mới từ các trường được liệt kê, không copy dict của provider
rồi xoá bớt. Provider thêm một trường nhạy cảm ở bản sau thì blocklist im lặng
để nó đi tiếp; allowlist thì không.

Thuần: không I/O, không đọc database, không gọi provider. Snapshot biên lai được
truyền vào — việc tra chúng theo lô là của caller.
"""

from __future__ import annotations

from typing import Any

from src.db.verification_receipt_repository import ReceiptSnapshot, snapshot_or_missing
from src.orchestration.verification_status import (
    CONSISTENT,
    EFF_WAITING_PROVIDER,
    PROVIDER_PENDING,
    VerificationView,
    derive,
)

TEN_HO_SO: dict[str, str] = {
    "apartment": "Xác minh căn hộ",
    "vehicle": "Xác minh phương tiện",
}
_TEN_MAC_DINH = "Yêu cầu xác minh"


def _chung(record: dict[str, Any], view: VerificationView) -> dict[str, Any]:
    """Phần trạng thái mà cả ba vai đều thấy, và thấy GIỐNG NHAU.

    Ba màn hình kể một câu chuyện khác nhau về cùng một hồ sơ là cách chắc chắn
    để hai người tranh cãi về một việc mà cả hai đều "nhìn thấy".
    """
    return {
        "record_id": record.get("record_id"),
        "record_type": record.get("record_type"),
        "provider_status": view.provider_status,
        "materialization_status": view.materialization_status,
        "effective_status": view.effective_status,
        "display_status": view.display_status,
        "consistency_status": view.consistency_status,
        "created_at": record.get("created_at"),
        "decided_at": record.get("decided_at"),
    }


def customer_view(record: dict[str, Any], snapshot: ReceiptSnapshot) -> dict[str, Any]:
    """Hồ sơ của CHÍNH người dùng.

    `status` giữ lại cho tương thích, và nó là ALIAS của `provider_status` —
    không phải kết luận. Frontend hiện còn dùng nó để mở khoá màn hình; việc
    sửa nằm ở mục F, không phải ở đây. Giữ nguyên giá trị để lượt đổi frontend
    là một thay đổi độc lập, không phải một cú vá cùng lúc hai tầng.
    """
    view = derive(record.get("status"), snapshot.materialization_status, snapshot.safe_error_code)
    return {
        **_chung(record, view),
        "status": view.provider_status,
        "recovery_required": view.recovery_required,
        "reject_reason": record.get("reject_reason"),
    }


def provider_view(record: dict[str, Any], snapshot: ReceiptSnapshot) -> dict[str, Any]:
    """Hàng đợi của ĐƠN VỊ. Có đủ thứ để quyết định, và một cờ nói khi nào được quyết."""
    view = derive(record.get("status"), snapshot.materialization_status, snapshot.safe_error_code)
    return {
        **_chung(record, view),
        "status": view.provider_status,
        "applicant_user_id": record.get("applicant_user_id"),
        # Giấy tờ người nộp gửi lên. Bỏ chúng đi là lấy mất công cụ làm việc của
        # người duyệt — họ không còn gì để đối chiếu.
        "claimed_data": record.get("claimed_data"),
        "proof_image_urls": record.get("proof_image_urls"),
        "ownership_match": record.get("ownership_match"),
        "reject_reason": record.get("reject_reason"),
        "decided_by": record.get("decided_by"),
        # Kết quả nghiệp vụ vừa tạo (xe thì kèm `vehicle_id`). Người duyệt cần
        # nó để biết việc họ vừa ký đã thành hình ở hệ thống thật — bỏ đi là
        # bắt họ tin vào một dòng chữ.
        "materialized": record.get("materialized"),
        "can_decide": can_decide(view),
    }


def can_decide(view: VerificationView) -> bool:
    """Có được bấm Duyệt/Từ chối không.

    BA điều kiện, không một. `provider_status == PENDING` một mình là chưa đủ:
    một hồ sơ chờ duyệt mà biên lai đã nói materialization chạy xong là dữ liệu
    lệch, và quyết định trên dữ liệu lệch sẽ ghi đè thứ không ai hiểu.

    Sau khi đã quyết định thì việc còn lại là HOÀN TẤT, không phải duyệt lần
    hai — provider sẽ trả `ALREADY_DECIDED`, đúng chỗ split-brain cũ mắc kẹt.
    """
    return (
        view.provider_status == PROVIDER_PENDING
        and view.consistency_status == CONSISTENT
        and view.effective_status == EFF_WAITING_PROVIDER
    )


def admin_view(record: dict[str, Any], snapshot: ReceiptSnapshot, account: dict[str, Any] | None) -> dict[str, Any]:
    """Màn GIÁM SÁT. Biết ai đang xin gì và kẹt ở đâu — không cần giấy tờ của họ.

    `account.user_id` là định danh TÀI KHOẢN, không phải `resident_id`. Hai thứ
    khác nhau: cái đầu là ai đăng nhập, cái sau là chìa khoá tra hồ sơ cư dân.
    """
    view = derive(record.get("status"), snapshot.materialization_status, snapshot.safe_error_code)
    loai = str(record.get("record_type") or "")
    return {
        **_chung(record, view),
        "request_name": TEN_HO_SO.get(loai, _TEN_MAC_DINH),
        "account": _account_view(record.get("applicant_user_id"), account),
        "decided_by": record.get("decided_by"),
        "reject_reason": record.get("reject_reason"),
    }


# Tài khoản đã xoá/khoá vẫn phải hiện hồ sơ — audit không được có lỗ. Nhưng
# không dựng lại một cái tên không còn nữa.
TAI_KHOAN_KHONG_CON = "Tài khoản không còn hoạt động"


def _account_view(applicant_id: Any, account: dict[str, Any] | None) -> dict[str, Any]:
    if not account:
        return {
            "user_id": str(applicant_id) if applicant_id else None,
            "username": None,
            "display_name": TAI_KHOAN_KHONG_CON,
        }
    return {
        "user_id": str(account.get("user_id") or account.get("id") or applicant_id or "") or None,
        "username": account.get("username"),
        "display_name": account.get("full_name") or account.get("display_name") or account.get("username"),
    }


def enrich(
    records: list[dict[str, Any]],
    snapshots: dict[str, ReceiptSnapshot],
    *,
    kind: str,
    accounts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Đường DUY NHẤT từ record của provider tới response.

    Ba endpoint gọi hàm này, nên không endpoint nào tự viết bảng trạng thái
    riêng — và không endpoint nào có thể lệch khỏi hai cái kia mà suite vẫn xanh.
    """
    accounts = accounts or {}
    out = []
    for record in records:
        snapshot = snapshot_or_missing(snapshots, str(record.get("record_id")))
        if kind == "customer":
            out.append(customer_view(record, snapshot))
        elif kind == "provider":
            out.append(provider_view(record, snapshot))
        elif kind == "admin":
            out.append(admin_view(record, snapshot, accounts.get(str(record.get("applicant_user_id")))))
        else:
            raise ValueError("Vai không hợp lệ.")
    return out
