"""Test mock Payment API — envelope format + ?fail= injection."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.mock.main import app

RESIDENT = {"full_name": "Lâm Thành Bảo", "apartment_code": "A1201", "residential_area": "Vinhomes Ocean Park"}
VEHICLE = {"resident_id": None, "plate_number": "51A-12345", "vehicle_type": "car"}
BOOKING = {"vehicle_id": None, "booking_date": "2026-08-10", "parking_zone": "ZONE_B"}


async def _setup_booking(ac) -> tuple[str, int]:
    """Tạo resident → vehicle → booking, trả (booking_id, amount)."""
    resident = await ac.post("/api/residents", json=RESIDENT)
    vehicle = await ac.post("/api/vehicles", json={**VEHICLE, "resident_id": resident.json()["data"]["resident_id"]})
    booking = await ac.post(
        "/api/parking/bookings", json={**BOOKING, "vehicle_id": vehicle.json()["data"]["vehicle_id"]}
    )
    data = booking.json()["data"]
    return data["booking_id"], data["amount"]


@pytest.mark.asyncio
async def test_pay_fee_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        booking_id, amount = await _setup_booking(ac)
        response = await ac.post("/api/payments", json={"booking_id": booking_id, "amount": amount, "currency": "VND"})
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["payment_id"].startswith("PAY-")
    assert body["data"]["payment_status"] == "PAID"
    assert body["error_code"] is None
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_pay_fee_booking_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/payments", json={"booking_id": "BOOK-999", "amount": 100_000, "currency": "VND"})
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "BOOKING_NOT_FOUND"


@pytest.mark.asyncio
async def test_pay_fee_amount_mismatch():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        booking_id, _ = await _setup_booking(ac)
        response = await ac.post("/api/payments", json={"booking_id": booking_id, "amount": 1, "currency": "VND"})
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "PAYMENT_AMOUNT_MISMATCH"


@pytest.mark.asyncio
async def test_get_payment_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/payments/PAY-999")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "PAYMENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_fail_payment_failed_via_inject():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        booking_id, amount = await _setup_booking(ac)
        response = await ac.post(
            "/api/payments?fail=PAYMENT_FAILED",
            json={"booking_id": booking_id, "amount": amount, "currency": "VND"},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "PAYMENT_FAILED"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_fail_payment_service_unavailable():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        booking_id, amount = await _setup_booking(ac)
        response = await ac.post(
            "/api/payments?fail=SERVICE_UNAVAILABLE",
            json={"booking_id": booking_id, "amount": amount, "currency": "VND"},
        )
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "SERVICE_UNAVAILABLE"
    assert body["retryable"] is True
