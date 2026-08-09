"""Chuẩn hóa lỗi mock API — response envelope đồng nhất.

Từ v0.2.0: lỗi cũng trả ``ApiEnvelope`` ở body gốc (không bọc trong ``detail``
như ``HTTPException`` mặc định). Custom exception handler ``mock_error_handler``
đảm bảo mọi lỗi đều có dạng ``{success, data, error_code, message, retryable}``.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse


class MockApiError(Exception):
    """Lỗi nghiệp vụ mock API.

    Được ném từ router thay vì ``HTTPException``.
    Exception handler ``mock_api_error_handler`` map sang JSONResponse.
    """

    def __init__(self, status_code: int, code: str, message: str, retryable: bool = False):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable


# ---- Tiện ích tạo lỗi (giữ lại signature cũ cho tương thích) ----


def conflict(code: str, message: str, retryable: bool = False) -> MockApiError:
    """409 — trùng lặp hoặc không còn chỗ."""
    return MockApiError(409, code, message, retryable)


def not_found(code: str, message: str) -> MockApiError:
    """404 — không tìm thấy tài nguyên."""
    return MockApiError(404, code, message)


def forbidden(code: str, message: str) -> MockApiError:
    """403 — quyền truy cập bị từ chối (vd: không phải chủ sở hữu)."""
    return MockApiError(403, code, message)


def input_invalid(message: str = "Invalid input") -> MockApiError:
    """400 — input không hợp lệ."""
    return MockApiError(400, "INVALID_INPUT", message)


def input_missing(message: str = "Missing required information") -> MockApiError:
    """400 — thiếu thông tin bắt buộc."""
    return MockApiError(400, "MISSING_INFORMATION", message)


# ---- Exception handler đăng ký vào FastAPI app ----


def _build_envelope(code: str, message: str, retryable: bool) -> dict:
    return {
        "success": False,
        "data": None,
        "error_code": code,
        "message": message,
        "retryable": retryable,
    }


async def mock_api_error_handler(request, exc: MockApiError) -> JSONResponse:
    """Custom handler: mọi ``MockApiError`` → JSON envelope ở body gốc."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_build_envelope(exc.code, exc.message, exc.retryable),
    )


def install_error_handler(app: FastAPI) -> None:
    """Gắn exception handler cho MockApiError vào app."""
    app.add_exception_handler(MockApiError, mock_api_error_handler)


# ---- Failure injection (dùng chung cho cả 4 router) ----


def inject_failure(code: str) -> MockApiError:
    """Map failure injection code → MockApiError.

    Dùng cho query param ``?fail=NO_AVAILABILITY``, ``?fail=SERVICE_UNAVAILABLE``, ...
    """
    if code == "SERVICE_UNAVAILABLE":
        return MockApiError(status_code=503, code=code, message="[MOCK] Injected: service unavailable", retryable=True)
    if code == "SERVICE_TIMEOUT":
        return MockApiError(status_code=504, code=code, message="[MOCK] Injected: service timeout", retryable=True)
    if code == "INTERNAL_SERVICE_ERROR":
        return MockApiError(status_code=500, code=code, message="[MOCK] Injected: internal error")
    if code == "NO_AVAILABILITY":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: no availability")
    if code == "RESIDENT_ALREADY_EXISTS":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: resident already exists")
    if code == "RESIDENT_NOT_FOUND":
        return MockApiError(status_code=404, code=code, message="[MOCK] Injected: resident not found")
    if code == "VEHICLE_ALREADY_EXISTS":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: vehicle already exists")
    if code == "VEHICLE_NOT_FOUND":
        return MockApiError(status_code=404, code=code, message="[MOCK] Injected: vehicle not found")
    if code == "BOOKING_NOT_FOUND":
        return MockApiError(status_code=404, code=code, message="[MOCK] Injected: booking not found")
    if code == "BOOKING_ALREADY_EXISTS":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: booking already exists")
    if code == "PAYMENT_FAILED":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: payment failed")
    if code == "PAYMENT_AMOUNT_MISMATCH":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: payment amount mismatch")
    if code == "PAYMENT_NOT_FOUND":
        return MockApiError(status_code=404, code=code, message="[MOCK] Injected: payment not found")
    if code == "INSUFFICIENT_BALANCE":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: insufficient balance")
    if code == "OWNERSHIP_NOT_FOUND":
        return MockApiError(status_code=404, code=code, message="[MOCK] Injected: ownership not found")
    if code == "OWNERSHIP_MISMATCH":
        return MockApiError(status_code=403, code=code, message="[MOCK] Injected: ownership mismatch")
    return MockApiError(status_code=500, code="UNKNOWN_EXTERNAL_ERROR", message=f"[MOCK] Unknown fail code: {code}")
