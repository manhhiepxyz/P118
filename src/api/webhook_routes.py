"""Webhook cổng thanh toán VNPay — IPN (nguồn sự thật) và Return URL (chỉ hiển thị).

Owner: Hoàng Anh (API layer)
File: src/api/webhook_routes.py

Hai endpoint KHÔNG yêu cầu JWT — caller là máy chủ VNPay hoặc trình duyệt user
được VNPay redirect về, không phải phiên đăng nhập của ai. Bảo vệ thay thế:

  * `/ipn`    : chữ ký HMAC-SHA512 + đối chiếu số tiền với BẢN ĐÓNG BĂNG trong
                row payments PENDING. Đây là nơi duy nhất được phép chốt tiền.
  * `/return` : xác minh chữ ký rồi REDIRECT về frontend kèm workflow_id để
                trang kết quả poll. Không bao giờ ghi trạng thái tài chính ở
                đây — user có thể tự gõ URL này.

Quy ước phản hồi IPN theo đặc tả VNPay: HTTP 200 với body
`{"RspCode": "...", "Message": "..."}`. Mã khác "00" khiến VNPay gọi lại — mọi
nhánh xử lý phải idempotent (confirm có guard WHERE status='PENDING').

Nếu resume workflow lỗi SAU khi tiền đã ghi PAID, vẫn trả RspCode 00 (VNPay
không cần retry nữa) và sweeper sẽ hàn workflow qua
`_finalize_paid_vnpay_workflows` — tiền đã thật thì không bao giờ mất.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from src.config import get_settings
from src.connectors.vnpay import (
    IPN_RSP_ALREADY_CONFIRMED,
    IPN_RSP_INVALID_AMOUNT,
    IPN_RSP_ORDER_NOT_FOUND,
    IPN_RSP_SUCCESS,
    IPN_RSP_UNKNOWN,
    ipn_response,
    parse_ipn_result,
    verify_signature,
)
from src.db.parking_payment_repository import confirm_pending_payment, get_payment
from src.orchestration.demo_service import ResumeError, resume_vnpay_after_gateway
from src.orchestration.runtime_provider import acquire_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/vnpay", tags=["payment-webhook"])


async def _handle_gateway_callback(params: dict[str, str]) -> tuple[str, dict[str, Any] | None]:
    """Xử lý dùng chung cho Return URL và IPN: trả (outcome, payment_dict|None).

    outcome ∈ {"success", "failed", "invalid", "unknown"} — Return dùng để
    hiển thị, IPN dùng làm nhánh từ chối.
    """
    settings = get_settings()
    if not verify_signature(settings.vnpay_hash_secret, params):
        logger.warning("VNPay callback chữ ký sai — bỏ qua. txn_ref=%s", params.get("vnp_TxnRef"))
        return "invalid", None
    parsed = parse_ipn_result(params)
    if parsed is None:
        return "unknown", None

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        payment = await get_payment(pool, parsed.txn_ref)
        if payment is None or payment.provider != "vnpay":
            return "unknown", None
        if payment.amount != parsed.amount_vnd:
            logger.error(
                "VNPay callback SAI SỐ TIỀN: frozen=%s callback=%s payment=%s — từ chối",
                payment.amount,
                parsed.amount_vnd,
                payment.payment_id,
            )
            return "invalid", None
        return ("success" if parsed.success else "failed"), {
            "payment_id": payment.payment_id,
            "booking_id": payment.booking_id,
            "workflow_id": payment.workflow_id,
            "status": payment.payment_status,
        }
    finally:
        await pool.close()


@router.get("/return")
async def vnpay_return(http_request: Request) -> RedirectResponse:
    """Trình duyệt user quay về sau trang VNPay.
    
    Đóng vai trò dự phòng (fallback) để chốt thanh toán nếu IPN tới trễ hoặc
    chưa được cấu hình đúng trên Merchant Sandbox, vì chữ ký đã được xác thực.
    """
    params = {k: v for k, v in http_request.query_params.items()}
    outcome, payment = await _handle_gateway_callback(params)

    frontend = get_settings().frontend_base_url.rstrip("/")
    if payment is None or not payment.get("workflow_id"):
        return RedirectResponse(f"{frontend}/payment/result?vnp_status={outcome}")

    workflow_id = str(payment["workflow_id"])

    # Fallback xử lý giao dịch nếu IPN chưa kịp chạy
    if outcome == "success" and "vnp_Amount" in params:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001
        try:
            parsed_amount = int(params["vnp_Amount"]) // 100
            confirmation = await confirm_pending_payment(pool, payment_id=payment["payment_id"], amount_vnd=parsed_amount)
            
            if confirmation == "CONFIRMED":
                await resume_vnpay_after_gateway(workflow_id)
                from src.api.routes import _DEMO_JOBS, request_fresh_answer
                job = _DEMO_JOBS.get(workflow_id)
                request_fresh_answer(workflow_id, job=job)
        except Exception:
            logger.exception("Lỗi khi xử lý fallback payment tại Return URL: %s", payment["payment_id"])
        finally:
            await pool.close()

    return RedirectResponse(f"{frontend}/payment/result?workflow_id={workflow_id}&vnp_status={outcome}")


@router.get("/ipn")
async def vnpay_ipn(http_request: Request) -> dict[str, str]:
    """Callback máy-nhân-máy của VNPay. NƠI DUY NHẤT chốt PENDING → PAID."""
    params = {k: v for k, v in http_request.query_params.items()}
    outcome, payment = await _handle_gateway_callback(params)

    if payment is None:
        return ipn_response(IPN_RSP_ORDER_NOT_FOUND if outcome == "unknown" else IPN_RSP_UNKNOWN)

    # Gateway báo thất bại (OTP sai, user huỷ...) → không có tiền nào về,
    # phiên giữ nguyên PENDING cho tới khi hết hạn do sweeper đóng.
    if outcome != "success":
        logger.info("VNPay IPN báo thất bại: payment=%s outcome=%s", payment["payment_id"], outcome)
        return ipn_response(IPN_RSP_ORDER_NOT_FOUND)

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001
    try:
        parsed_amount = int(params["vnp_Amount"]) // 100
        confirmation = await confirm_pending_payment(pool, payment_id=payment["payment_id"], amount_vnd=parsed_amount)
    finally:
        await pool.close()

    if confirmation == "ALREADY_CONFIRMED":
        # IPN gọi lại sau khi đã PAID — vô hại, đúng nghĩa idempotent.
        return ipn_response(IPN_RSP_ALREADY_CONFIRMED)
    if confirmation != "CONFIRMED":
        logger.error(
            "VNPay IPN không confirm được: payment=%s outcome=%s — cần đối soát",
            payment["payment_id"],
            confirmation,
        )
        rsp = IPN_RSP_INVALID_AMOUNT if confirmation == "AMOUNT_MISMATCH" else IPN_RSP_UNKNOWN
        return ipn_response(rsp)

    try:
        await resume_vnpay_after_gateway(str(payment["workflow_id"]))
        
        # Gọi ResponseAgent để tạo thông báo hoàn tất và làm mới cache. Thiếu
        # bước này, giao diện polling sẽ mắc kẹt với response WAITING_APPROVAL cũ.
        from src.api.routes import _DEMO_JOBS, request_fresh_answer
        workflow_id = str(payment["workflow_id"])
        job = _DEMO_JOBS.get(workflow_id)
        request_fresh_answer(workflow_id, job=job)
    except ResumeError as exc:
        # Tiền ĐÃ VỀ nhưng workflow chưa chốt được — sweeper hàn lại. Trả 00
        # để VNPay ngừng retry: retry chỉ thấy ALREADY_CONFIRMED, vô ích.
        logger.error(
            "IPN confirmed payment=%s nhưng resume lỗi (%s): %s — sweeper sẽ hàn",
            payment["payment_id"],
            exc.code,
            exc,
        )
    except Exception:  # noqa: BLE001 - không để exception đổi RspCode sau khi tiền đã PAID
        logger.exception("IPN confirmed payment=%s nhưng resume ném ngoại lệ — sweeper sẽ hàn", payment["payment_id"])

    return ipn_response(IPN_RSP_SUCCESS)
