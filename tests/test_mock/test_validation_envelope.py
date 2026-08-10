"""Test 422 (RequestValidationError) trả đúng envelope contract + không lộ PII.

FastAPI mặc định trả ``{"detail": [...]}`` cho lỗi validation — không khớp
envelope ``{success, data, error_code, message, retryable}`` nên Connector
fallback về ``UNKNOWN_EXTERNAL_ERROR``. Handler chung trong
``src/mock/errors.install_error_handler`` sửa việc này cho MỌI app.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.mock.main import app as monolith_app
from src.services.mock.apartment_ownership import apartment_ownership_app
from src.services.mock.payment import payment_app
from src.services.mock.resident import resident_app
from src.services.mock.transport import transport_app

PII_VALUE = "Nguyen Van PII-079123456789"


def _assert_invalid_input_envelope(response) -> dict:
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "INVALID_INPUT"
    assert body["retryable"] is False
    assert isinstance(body["message"], str) and body["message"]
    assert "detail" not in body
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_app", "url", "payload"),
    [
        pytest.param(
            transport_app,
            "/api/parking/bookings",
            {"vehicle_id": "VEH-1", "booking_date": "2026-08-10", "parking_zone": "NOT_A_ZONE"},
            id="transport",
        ),
        pytest.param(
            resident_app,
            "/api/residents",
            {"full_name": "", "apartment_code": "A1201", "residential_area": "VH-SGV"},
            id="resident",
        ),
        pytest.param(
            payment_app,
            "/api/payments",
            {"booking_id": "BOOK-1", "amount": -5, "currency": "VND"},
            id="payment",
        ),
        pytest.param(
            apartment_ownership_app,
            "/api/apartment-owners/verify-ownership",
            {"full_name": "A", "apartment_code": "A1201"},
            id="apartment_ownership",
        ),
    ],
)
async def test_provider_app_validation_error_returns_envelope(target_app, url, payload):
    """Mỗi provider app độc lập → 422 theo envelope với error_code=INVALID_INPUT."""
    async with AsyncClient(transport=ASGITransport(app=target_app), base_url="http://test") as ac:
        response = await ac.post(url, json=payload)

    _assert_invalid_input_envelope(response)


@pytest.mark.asyncio
async def test_monolith_app_validation_error_returns_envelope():
    """Monolith (src/mock/main.py) cũng phải trả cùng envelope."""
    async with AsyncClient(transport=ASGITransport(app=monolith_app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/parking/bookings",
            json={"vehicle_id": "VEH-1", "booking_date": "2026-08-10", "parking_zone": "NOT_A_ZONE"},
        )

    body = _assert_invalid_input_envelope(response)
    assert "parking_zone" in body["message"]


@pytest.mark.asyncio
async def test_validation_message_does_not_leak_submitted_input():
    """REGRESSION BẢO MẬT: giá trị caller submit (PII) KHÔNG được lọt vào response.

    ``exc.errors()`` có key ``"input"`` chứa nguyên giá trị người dùng gửi lên.
    """
    payload = {
        "full_name": PII_VALUE,
        "apartment_code": "A1201",
        "residential_area": "VH-SGV",
        "parking_zone": PII_VALUE,
    }

    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/parking/bookings",
            json={"vehicle_id": "VEH-1", "booking_date": PII_VALUE, "parking_zone": PII_VALUE, **payload},
        )

    _assert_invalid_input_envelope(response)
    raw_body = response.text
    assert PII_VALUE not in raw_body
    assert "PII-079123456789" not in raw_body
    assert "Nguyen Van" not in raw_body


@pytest.mark.asyncio
async def test_domain_error_handler_behaviour_unchanged():
    """Handler cũ cho MockApiError giữ nguyên (404, envelope, error_code domain)."""
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/parking/bookings",
            json={"vehicle_id": "VEH-KHONG-TON-TAI", "booking_date": "2026-08-10", "parking_zone": "ZONE_A"},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "VEHICLE_NOT_FOUND"
    assert body["retryable"] is False
