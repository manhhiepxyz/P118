"""Thư viện ký và xác minh cho cổng thanh toán VNPay (sandbox).

Owner: Mạnh Hiệp (Executor layer / Connector boundary)
File: src/connectors/vnpay.py

Vai trò trong kiến trúc:
  Đây là NỬA "ranh giới Connector" phía gateway thật: mọi tiếp xúc với đặc tả
  riêng của VNPay — tham số vnp_*, nhân 100 số tiền, HMAC-SHA512, múi giờ
  GMT+7, format ngày yyyyMMddHHmmss — nằm Ở ĐÂY và CHỈ ở đây.

  Executor/Orchestration không bao giờ nhìn thấy một chuỗi "vnp_*" nào:
    /payment-decision (vnpay branch)
      → build_payment_url()            # mở phiên, trả payment_redirect_url
    GET /webhooks/vnpay/ipn
      → verify_signature()             # chặn callback giả trước mọi thứ khác
      → parse_ipn_result()             # đọc kết quả thành/thất bại
      → repository confirm PENDING→PAID

Quy tắc đặc tả được cài đúng trong file này (nguồn: devdocs sandbox):
  1. Số tiền gửi đi là VND × 100 (`vnp_Amount`); IPN trả về cũng ×100.
  2. Chuỗi ký = các tham số vnp_* (bỏ rỗng) sắp theo alphabet,
     urlencode kiểu quote_plus, nối bằng '&'.
  3. Chữ ký = HMAC-SHA512(secret, chuỗi ký), viết thường hex.
  4. Khi XÁC MINH callback: loại `vnp_SecureHash` và `vnp_SecureHashType`
     khỏi dữ liệu ký; so khớp bằng compare_digest (chống timing attack).
  5. vnp_CreateDate/vnp_ExpireDate/vnp_PayDate theo GMT+7.
  6. IPN thành công khi CẢ vnp_ResponseCode lẫn vnp_TransactionStatus = '00'.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import unicodedata
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.connectors.base import Connector, ProviderCallContext

logger = logging.getLogger(__name__)

# Múi giờ chính thức của tham số thời gian VNPay: GMT+7 (Asia/Ho_Chi_Minh).
_VNPAY_TZ = timezone(timedelta(hours=7))

# Mã phản hồi IPN mà merchant phải trả cho VNPay (đặc tả IPN).
IPN_RSP_SUCCESS = "00"  # Confirm Success
IPN_RSP_ORDER_NOT_FOUND = "01"  # Order Not Found
IPN_RSP_ALREADY_CONFIRMED = "02"  # Order Already Confirmed
IPN_RSP_INVALID_AMOUNT = "04"  # Invalid Amount
IPN_RSP_UNKNOWN = "99"  # Unknown error (gồm cả chữ ký sai)

_IPN_MESSAGES = {
    IPN_RSP_SUCCESS: "Confirm Success",
    IPN_RSP_ORDER_NOT_FOUND: "Order Not Found",
    IPN_RSP_ALREADY_CONFIRMED: "Order Already Confirmed",
    IPN_RSP_INVALID_AMOUNT: "Invalid Amount",
    IPN_RSP_UNKNOWN: "Unknown Error",
}

# Ký tự hợp lệ của vnp_OrderInfo: chữ không dấu, số, khoảng trắng.
_ORDER_INFO_SAFE = re.compile(r"[^A-Za-z0-9 ]+")


def format_vnpay_date(dt: datetime) -> str:
    """Chuẩn hoá thời gian về GMT+7 rồi format yyyyMMddHHmmss."""
    return dt.astimezone(_VNPAY_TZ).strftime("%Y%m%d%H%M%S")


def sanitize_order_info(text: str) -> str:
    """vnp_OrderInfo chỉ nhận chữ không dấu — chuyển đổi chứ không xoá ký tự.

    'Thanh toán phí' phải thành 'Thanh toan phi', KHÔNG phải 'Thanh to n phi':
    xoá thẳng combining mark làm câu mất nghĩa trên trang VNPay user nhìn vào.
    Bước 1 map thủ công đ/Đ (không tách được bằng NFD), bước 2 NFD rồi bỏ
    combining mark cho các dấu thanh còn lại.
    """
    replaced = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", replaced)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    cleaned = _ORDER_INFO_SAFE.sub(" ", unicodedata.normalize("NFC", stripped))
    return re.sub(r"\s+", " ", cleaned).strip()


def _sign_params(params: Mapping[str, str]) -> list[tuple[str, str]]:
    """Lọc giá trị rỗng và sắp alphabet — bước bắt buộc trước khi ký."""
    return sorted(
        ((str(k), str(v)) for k, v in params.items() if v is not None and str(v) != ""),
        key=lambda kv: kv[0],
    )


def build_sign_data(params: Mapping[str, str]) -> str:
    """Chuỗi dữ liệu để ký: key=value&key=value theo alphabet, quote_plus."""
    return urllib.parse.urlencode(_sign_params(params))


def sign(hash_secret: str, params: Mapping[str, str]) -> str:
    """HMAC-SHA512 hexdigest của chuỗi dữ liệu với khóa bí mật merchant."""
    data = build_sign_data(params)
    return hmac.new(hash_secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha512).hexdigest()


def verify_signature(hash_secret: str, params: Mapping[str, str]) -> bool:
    """Xác minh vnp_SecureHash của callback (Return URL hoặc IPN).

    Loại đúng hai field chữ ký khỏi dữ liệu ký rồi tính lại. So sánh hằng
    thời gian: callback đến từ internet công cộng, không cho đoán khóa dần.
    """
    received = str(params.get("vnp_SecureHash", "") or "")
    if not received or not hash_secret:
        return False
    clean = {k: v for k, v in params.items() if k not in ("vnp_SecureHash", "vnp_SecureHashType")}
    expected = sign(hash_secret, clean)
    return hmac.compare_digest(expected.lower(), received.lower())


@dataclass(frozen=True)
class VnPaySessionConfig:
    """Bộ định danh merchant do VNPay cấp + endpoint môi trường."""

    tmn_code: str
    hash_secret: str
    payment_url: str = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
    ttl_minutes: int = 30
    protocol_version: str = "2.1.0"


def build_payment_url(
    config: VnPaySessionConfig,
    *,
    txn_ref: str,
    amount_vnd: int,
    order_info: str,
    ip_addr: str,
    return_url: str,
    now: datetime | None = None,
) -> str:
    """Dựng URL chuyển hướng sang trang thanh toán VNPay, có chữ ký cuối URL.

    Args:
        txn_ref    : mã tham chiếu giao dịch PHÍA merchant (không trùng trong
                     ngày) — P-118 dùng `payment_id` (PAY-xxx).
        amount_vnd : số tiền NGUYÊN theo VND; hàm tự nhân 100 theo đặc tả.
        order_info : mô tả hiển thị cho người trả tiền (được làm sạch ký tự).
        ip_addr    : IP của người thanh toán (VNPay bắt buộc).
        return_url : URL trình duyệt user quay về sau khi trả tiền.
        now        : điểm bấm giờ để test cố định; mặc định hiện tại GMT+7.

    Returns:
        URL đầy đủ dạng `<payment_url>?<query>&vnp_SecureHash=<hex>`.
    """
    if not config.tmn_code or not config.hash_secret:
        raise ValueError("VnPaySessionConfig thiếu tmn_code/hash_secret — kiểm tra .env")
    moment = now or datetime.now(tz=_VNPAY_TZ)
    params: dict[str, str] = {
        "vnp_Version": config.protocol_version,
        "vnp_Command": "pay",
        "vnp_TmnCode": config.tmn_code,
        "vnp_Amount": str(int(amount_vnd) * 100),
        "vnp_CurrCode": "VND",
        "vnp_TxnRef": txn_ref,
        "vnp_OrderInfo": sanitize_order_info(order_info),
        "vnp_OrderType": "other",
        "vnp_Locale": "vn",
        "vnp_ReturnUrl": return_url,
        "vnp_IpAddr": ip_addr or "127.0.0.1",
        "vnp_CreateDate": format_vnpay_date(moment),
    }
    # Phiên có hạn cứng: quá vnp_ExpireDate thì chính VNPay chặn nhập OTP.
    if config.ttl_minutes > 0:
        params["vnp_ExpireDate"] = format_vnpay_date(moment + timedelta(minutes=config.ttl_minutes))

    secure_hash = sign(config.hash_secret, params)
    query = build_sign_data(params)
    separator = "&" if "?" in config.payment_url else "?"
    return f"{config.payment_url}{separator}{query}&vnp_SecureHash={secure_hash}"


@dataclass(frozen=True)
class VnPayIpnResult:
    """Kết quả giao dịch đã parse từ query IPN (hoặc Return URL).

    `amount_vnd` đã chia 100 về đơn vị nguyên — caller đối chiếu trực tiếp
    với bản đóng băng trong row payments, không đụng số liệu sống.
    """

    txn_ref: str
    amount_vnd: int
    response_code: str
    transaction_status: str
    transaction_no: str
    bank_code: str
    pay_date: str

    @property
    def success(self) -> bool:
        """Thành công chỉ khi CẢ mã phản hồi lẫn trạng thái giao dịch là '00'."""
        return self.response_code == "00" and self.transaction_status == "00"


_REQUIRED_IPN_FIELDS = (
    "vnp_TxnRef",
    "vnp_Amount",
    "vnp_ResponseCode",
    "vnp_TransactionStatus",
)


def parse_ipn_result(params: Mapping[str, str]) -> VnPayIpnResult | None:
    """Đọc kết quả từ query callback. Thiếu field bắt buộc → None (dữ liệu rác)."""
    missing = [f for f in _REQUIRED_IPN_FIELDS if not params.get(f)]
    if missing:
        logger.warning("VNPay callback thiếu field bắt buộc: %s", ",".join(missing))
        return None
    try:
        amount_vnd = int(str(params["vnp_Amount"])) // 100
    except ValueError:
        logger.warning("VNPay callback vnp_Amount không phải số: %s", params["vnp_Amount"])
        return None
    return VnPayIpnResult(
        txn_ref=str(params["vnp_TxnRef"]),
        amount_vnd=amount_vnd,
        response_code=str(params["vnp_ResponseCode"]),
        transaction_status=str(params["vnp_TransactionStatus"]),
        transaction_no=str(params.get("vnp_TransactionNo", "")),
        bank_code=str(params.get("vnp_BankCode", "")),
        pay_date=str(params.get("vnp_PayDate", "")),
    )


def ipn_response(rsp_code: str) -> dict[str, str]:
    """Body JSON merchant trả VNPay sau khi xử lý IPN."""
    return {"RspCode": rsp_code, "Message": _IPN_MESSAGES.get(rsp_code, _IPN_MESSAGES[IPN_RSP_UNKNOWN])}


# ---------------------------------------------------------------------------
# Connector — nửa xác nhận của `pay_fee` khi PAYMENT_PROVIDER=vnpay
# ---------------------------------------------------------------------------


class VnPayPaymentConnector(Connector):
    """Connector `pay_fee` cho luồng gateway VNPay.

    Khác `PaymentConnector` ở chỗ KHÔNG gọi HTTP nào khi execute:

      * Mở phiên (build URL + row PENDING) là việc của đường duyệt tại
        `/payment-decision`, KHÔNG phải của Executor.
      * Tiền được xác nhận bởi callback IPN, ghi vào database trước khi
        connector chạy.
      * Nên `execute("pay_fee")` ở đây nghĩa là: ĐỌC phiên đã PAID của
        (workflow, booking) rồi chuẩn hoá thành `StandardResult` — đúng hợp
        đồng output {payment_id, payment_status}. Mọi hàng rào của Executor
        (prepare_submission, khoá idempotency, save result) giữ nguyên hiệu lực
        vì đường này vẫn đi qua `call_provider`.

    Phiên còn PENDING lúc execute là trạng thái KHÔNG BAO GIỜ nên xảy ra
    (resume chỉ được gọi sau khi IPN confirm). Trả failure không-retryable để
    fail-loud thay vì treo workflow trong vòng retry.
    """

    def __init__(
        self,
        workflow_id: str | None = None,
        pool: Any | None = None,
    ) -> None:
        self._workflow_id = workflow_id
        self._pool = pool

    @property
    def tool_names(self) -> list[str]:
        return ["pay_fee"]

    def idempotency_key_for(
        self, workflow_id: str, task_id: str, tool_name: str, resolved_input: dict[str, Any]
    ) -> str | None:
        # Cùng công thức với PaymentConnector: mọi đường trả tiền dùng chung
        # một dạng khoá — điều kiện để khoá đã lưu và khoá vừa tính so được.
        if tool_name != "pay_fee":
            return None
        booking_id = (resolved_input or {}).get("booking_id")
        if not workflow_id or not isinstance(booking_id, str) or not booking_id:
            return None
        from src.db.parking_payment_repository import payment_idempotency_key

        return payment_idempotency_key(str(workflow_id), booking_id)

    def is_retry_safe(self, tool_name: str) -> bool:
        # execute chỉ ĐỌC trạng thái đã commit của phiên — gọi lại vô hại.
        return tool_name == "pay_fee"

    async def _get_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        # Import lười: orchestration import connectors ở mức module, đi chiều
        # ngược lại ở mức module là tự tạo vòng import.
        from src.orchestration.runtime_provider import acquire_repository

        repository = await acquire_repository()
        return repository._pool  # noqa: SLF001 - composition root sở hữu pool

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        *,
        context: ProviderCallContext | None = None,
    ) -> StandardResult:
        if tool_name != "pay_fee":
            return StandardResult.fail(
                error_code=ErrorCode.INVALID_INPUT,
                message=f"Tool không được hỗ trợ: {tool_name}",
            )

        workflow_id = context.workflow_id if context is not None else self._workflow_id
        booking_id = input_data.get("booking_id")
        try:
            if not workflow_id or not isinstance(booking_id, str) or not booking_id:
                return StandardResult.fail(
                    error_code=ErrorCode.INVALID_INPUT,
                    message="Thiếu workflow_id hoặc booking_id để tra phiên thanh toán",
                )
            pool = await self._get_pool()
            from src.db.parking_payment_repository import get_vnpay_session_for_workflow

            session = await get_vnpay_session_for_workflow(pool, workflow_id=str(workflow_id), booking_id=booking_id)
            if session is None:
                return StandardResult.fail(
                    error_code=ErrorCode.PAYMENT_NOT_FOUND,
                    message="Không tìm thấy phiên thanh toán cho booking này",
                )
            if session.payment_status == "PAID":
                return StandardResult.ok(data={"payment_id": session.payment_id, "payment_status": "PAID"})
            return StandardResult.fail(
                error_code=ErrorCode.PAYMENT_FAILED,
                message="Phiên thanh toán chưa được gateway xác nhận",
                retryable=False,
            )
        except Exception as exc:  # noqa: BLE001 - Connector không raise ra ngoài
            return StandardResult.fail(
                error_code=ErrorCode.INTERNAL_SERVICE_ERROR,
                message=f"Lỗi không mong đợi: {exc}",
                retryable=False,
            )
