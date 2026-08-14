"""Chuẩn hóa lỗi mock API — response envelope đồng nhất.

Từ v0.2.0: lỗi cũng trả ``ApiEnvelope`` ở body gốc (không bọc trong ``detail``
như ``HTTPException`` mặc định). Custom exception handler ``mock_error_handler``
đảm bảo mọi lỗi đều có dạng ``{success, data, error_code, message, retryable}``.
"""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
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


def _safe_validation_message(exc: RequestValidationError) -> str:
    """Dựng message từ RequestValidationError mà KHÔNG lộ giá trị caller gửi lên.

    BẢO MẬT: ``exc.errors()`` có key ``"input"`` chứa nguyên giá trị người dùng
    submit (có thể là PII: ``full_name``, ``id_number``, ...). Message chỉ được
    dựng từ **vị trí field** (``loc``) và **loại lỗi** (``type``) — không bao giờ
    nội suy ``err["input"]`` hay ``err["msg"]`` (msg của pydantic đôi khi echo
    lại giá trị đầu vào).
    """
    parts: list[str] = []
    seen: set[str] = set()
    for err in exc.errors():
        loc = ".".join(str(item) for item in err.get("loc", ())) or "body"
        err_type = str(err.get("type", "value_error"))
        part = f"{loc} ({err_type})"
        if part not in seen:
            seen.add(part)
            parts.append(part)
    if not parts:
        return "Invalid input"
    return "Invalid input for field(s): " + ", ".join(parts)


async def validation_error_handler(request, exc: RequestValidationError) -> JSONResponse:
    """422 của FastAPI → envelope chuẩn với ``error_code=INVALID_INPUT``.

    Mặc định FastAPI trả ``{"detail": [...]}`` — không khớp envelope contract nên
    ``Connector._handle_error_response()`` fallback về ``UNKNOWN_EXTERNAL_ERROR``.
    Handler này đảm bảo 422 cũng theo đúng ``{success, data, error_code, message,
    retryable}``.
    """
    return JSONResponse(
        status_code=422,
        content=_build_envelope("INVALID_INPUT", _safe_validation_message(exc), False),
    )


def install_error_handler(app: FastAPI) -> None:
    """Gắn exception handler cho MockApiError + RequestValidationError vào app."""
    app.add_exception_handler(MockApiError, mock_api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)


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
    # --- book_tour / book_shuttle / register_consultation (v0.5.0) ---
    if code == "TOUR_NOT_FOUND":
        return MockApiError(status_code=404, code=code, message="[MOCK] Injected: tour not found")
    if code == "TOUR_ALREADY_BOOKED":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: tour already booked")
    if code == "TOUR_SLOT_NOT_FOUND":
        return MockApiError(status_code=404, code=code, message="[MOCK] Injected: tour slot not found")
    if code == "SLOT_FULL":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: slot full")
    if code == "SHUTTLE_NOT_FOUND":
        return MockApiError(status_code=404, code=code, message="[MOCK] Injected: shuttle not found")
    if code == "SHUTTLE_ALREADY_BOOKED":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: shuttle already booked")
    if code == "CONSULTATION_NOT_FOUND":
        return MockApiError(status_code=404, code=code, message="[MOCK] Injected: consultation not found")
    if code == "CONSULTATION_ALREADY_EXISTS":
        return MockApiError(status_code=409, code=code, message="[MOCK] Injected: consultation already exists")
    return MockApiError(status_code=500, code="UNKNOWN_EXTERNAL_ERROR", message=f"[MOCK] Unknown fail code: {code}")
