"""Test Payment provider độc lập (src/services/mock/payment.py) — envelope format.

Deviation có chủ đích so với src/mock/: KHÔNG check booking_id/amount
(cross-provider, HUB inject đã verify) → POST trả 201 với mọi booking_id/amount.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.mock.payment import payment_app

PAYMENT = {"booking_id": "BOOK-999", "amount": 150000, "currency": "VND"}


@pytest.mark.asyncio
async def test_pay_fee_success_ignores_booking():
    """Cross-provider: booking_id/amount tùy ý vẫn 201 (HUB inject đã verify)."""
    async with AsyncClient(transport=ASGITransport(app=payment_app), base_url="http://test") as ac:
        response = await ac.post("/api/payments", json=PAYMENT)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["payment_id"].startswith("PAY-")
    assert body["data"]["payment_status"] == "PAID"
    assert body["error_code"] is None
    assert body["message"] == "Created"


@pytest.mark.asyncio
async def test_fail_injection_payment_failed():
    async with AsyncClient(transport=ASGITransport(app=payment_app), base_url="http://test") as ac:
        response = await ac.post("/api/payments?fail=PAYMENT_FAILED", json=PAYMENT)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "PAYMENT_FAILED"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_fail_injection_service_unavailable():
    async with AsyncClient(transport=ASGITransport(app=payment_app), base_url="http://test") as ac:
        response = await ac.post("/api/payments?fail=SERVICE_UNAVAILABLE", json=PAYMENT)
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "SERVICE_UNAVAILABLE"
    assert body["retryable"] is True


@pytest.mark.asyncio
async def test_get_payment_not_found():
    async with AsyncClient(transport=ASGITransport(app=payment_app), base_url="http://test") as ac:
        response = await ac.get("/api/payments/PAY-999")
    assert response.status_code == 404
    assert response.json()["error_code"] == "PAYMENT_NOT_FOUND"
