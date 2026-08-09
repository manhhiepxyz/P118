"""Tests cho Connector layer.

Owner: Mạnh Hiệp (Executor layer)
File: tests/test_connectors.py
"""

from unittest.mock import MagicMock

import httpx
import pytest

from src.common.enums import ErrorCode
from src.connectors.payment import PaymentConnector
from src.connectors.resident import ResidentConnector
from src.connectors.transport import TransportConnector


@pytest.fixture
def mock_httpx_client():
    class MockClient:
        def __init__(self):
            self.post_mock = MagicMock()
            self.is_closed = False

        async def post(self, *args, **kwargs):
            return self.post_mock(*args, **kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            self.is_closed = True

    return MockClient()


# ---------------------------------------------------------------------------
# ResidentConnector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resident_connector_success(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"resident_id": "RES-123", "extra_field": "ignore_me"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = ResidentConnector(client=mock_httpx_client)
    result = await connector.execute("register_resident", {"name": "Test"})

    assert result.success is True
    assert result.data == {"resident_id": "RES-123"}
    assert "extra_field" not in result.data
    assert not mock_httpx_client.is_closed, "Injected client must not be closed"


@pytest.mark.asyncio
async def test_resident_connector_url_and_payload(mock_httpx_client):
    """ResidentConnector phải POST đúng URL /api/residents và truyền nguyên payload."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"resident_id": "RES-999"}
    mock_httpx_client.post_mock.return_value = mock_response

    payload = {"full_name": "Nguyễn Văn A", "apartment_code": "A101", "residential_area": "VH-SGV"}
    connector = ResidentConnector(base_url="http://localhost:8001", client=mock_httpx_client)
    await connector.execute("register_resident", payload)

    mock_httpx_client.post_mock.assert_called_once_with(
        "http://localhost:8001/api/residents",
        json=payload,
        timeout=30.0,
    )


@pytest.mark.asyncio
async def test_resident_connector_missing_output(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"something": "else"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = ResidentConnector(client=mock_httpx_client)
    result = await connector.execute("register_resident", {"name": "Test"})

    assert result.success is False
    assert result.error_code == ErrorCode.UNKNOWN_EXTERNAL_ERROR


# ---------------------------------------------------------------------------
# TransportConnector – register_vehicle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_connector_register_vehicle_success(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"vehicle_id": "VEH-123", "color": "red"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = TransportConnector(client=mock_httpx_client)
    result = await connector.execute("register_vehicle", {"plate": "29A-123"})

    assert result.success is True
    assert result.data == {"vehicle_id": "VEH-123"}


@pytest.mark.asyncio
async def test_transport_connector_register_vehicle_url_and_payload(mock_httpx_client):
    """TransportConnector phải POST đúng URL /api/vehicles và truyền nguyên payload."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"vehicle_id": "VEH-888"}
    mock_httpx_client.post_mock.return_value = mock_response

    payload = {"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"}
    connector = TransportConnector(base_url="http://localhost:8002", client=mock_httpx_client)
    await connector.execute("register_vehicle", payload)

    mock_httpx_client.post_mock.assert_called_once_with(
        "http://localhost:8002/api/vehicles",
        json=payload,
        timeout=30.0,
    )


# ---------------------------------------------------------------------------
# TransportConnector – book_parking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_connector_book_parking_success(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "booking_id": "BOOK-1",
        "parking_zone": "A1",
        "booking_date": "2026-08-10",
        "amount": 100,
        "currency": "VND",
        "ignore_me": "yes",
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = TransportConnector(client=mock_httpx_client)
    result = await connector.execute("book_parking", {"vehicle_id": "VEH-123"})

    assert result.success is True
    assert result.data == {
        "booking_id": "BOOK-1",
        "parking_zone": "A1",
        "booking_date": "2026-08-10",
        "amount": 100,
        "currency": "VND",
    }


@pytest.mark.asyncio
async def test_transport_connector_book_parking_url_and_payload(mock_httpx_client):
    """TransportConnector phải POST đúng URL /api/parking/bookings và truyền nguyên payload."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "booking_id": "BOOK-777",
        "parking_zone": "B2",
        "booking_date": "2026-08-10",
        "amount": 200000,
        "currency": "VND",
    }
    mock_httpx_client.post_mock.return_value = mock_response

    payload = {"vehicle_id": "VEH-001", "booking_date": "2026-08-10", "parking_zone": "B2"}
    connector = TransportConnector(base_url="http://localhost:8002", client=mock_httpx_client)
    await connector.execute("book_parking", payload)

    mock_httpx_client.post_mock.assert_called_once_with(
        "http://localhost:8002/api/parking/bookings",
        json=payload,
        timeout=30.0,
    )


# ---------------------------------------------------------------------------
# PaymentConnector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_connector_paid_status(mock_httpx_client):
    """Happy path: Mock API trả PAID → kết quả PAID."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"payment_id": "PAY-1", "payment_status": "PAID"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = PaymentConnector(client=mock_httpx_client)
    result = await connector.execute("pay_fee", {"booking_id": "BOOK-1"})

    assert result.success is True
    assert result.data == {"payment_id": "PAY-1", "payment_status": "PAID"}


@pytest.mark.asyncio
async def test_payment_connector_maps_success_to_paid(mock_httpx_client):
    """Legacy provider trả SUCCESS → phải map thành PAID."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"payment_id": "PAY-2", "payment_status": "SUCCESS"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = PaymentConnector(client=mock_httpx_client)
    result = await connector.execute("pay_fee", {"booking_id": "BOOK-2"})

    assert result.success is True
    assert result.data["payment_status"] == "PAID", "SUCCESS phải được map sang PAID"


@pytest.mark.asyncio
async def test_payment_connector_unknown_status_returns_error(mock_httpx_client):
    """payment_status không nằm trong allowlist → UNKNOWN_EXTERNAL_ERROR."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"payment_id": "PAY-3", "payment_status": "WEIRD_STATUS"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = PaymentConnector(client=mock_httpx_client)
    result = await connector.execute("pay_fee", {"booking_id": "BOOK-3"})

    assert result.success is False
    assert result.error_code == ErrorCode.UNKNOWN_EXTERNAL_ERROR


@pytest.mark.asyncio
async def test_payment_connector_url_and_payload(mock_httpx_client):
    """PaymentConnector phải POST đúng URL /api/payments và truyền nguyên payload."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"payment_id": "PAY-999", "payment_status": "PAID"}
    mock_httpx_client.post_mock.return_value = mock_response

    payload = {"booking_id": "BOOK-001", "amount": 150000, "currency": "VND"}
    connector = PaymentConnector(base_url="http://localhost:8003", client=mock_httpx_client)
    await connector.execute("pay_fee", payload)

    mock_httpx_client.post_mock.assert_called_once_with(
        "http://localhost:8003/api/payments",
        json=payload,
        timeout=30.0,
    )


# ---------------------------------------------------------------------------
# Generic error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_not_supported():
    connector = ResidentConnector()
    result = await connector.execute("unsupported_tool", {})
    assert result.success is False
    assert result.error_code == ErrorCode.INVALID_INPUT


@pytest.mark.asyncio
async def test_service_timeout(mock_httpx_client):
    mock_httpx_client.post_mock.side_effect = httpx.TimeoutException("Timeout")
    connector = ResidentConnector(client=mock_httpx_client)
    result = await connector.execute("register_resident", {})

    assert result.success is False
    assert result.error_code == ErrorCode.SERVICE_TIMEOUT
    assert result.retryable is True


@pytest.mark.asyncio
async def test_service_unavailable(mock_httpx_client):
    mock_httpx_client.post_mock.side_effect = httpx.ConnectError("No connection")
    connector = TransportConnector(client=mock_httpx_client)
    result = await connector.execute("register_vehicle", {})

    assert result.success is False
    assert result.error_code == ErrorCode.SERVICE_UNAVAILABLE
    assert result.retryable is True


@pytest.mark.asyncio
async def test_no_availability_mapping(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.json.return_value = {"error_code": "NO_AVAILABILITY", "message": "Full"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = TransportConnector(client=mock_httpx_client)
    result = await connector.execute("book_parking", {})

    assert result.success is False
    assert result.error_code == ErrorCode.NO_AVAILABILITY
    assert result.retryable is False


@pytest.mark.asyncio
async def test_unknown_external_error_mapping(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.json.return_value = {"error_code": "WEIRD_ERROR", "message": "???"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = TransportConnector(client=mock_httpx_client)
    result = await connector.execute("book_parking", {})

    assert result.success is False
    assert result.error_code == ErrorCode.UNKNOWN_EXTERNAL_ERROR


def test_create_invalid_input_response_helper():
    """Kiểm tra trực tiếp helper create_invalid_input_response trả về ErrorCode.INVALID_INPUT."""
    from tests.fakes.fake_connector import create_invalid_input_response

    result = create_invalid_input_response("Test invalid input")
    assert result.success is False
    assert result.error_code == ErrorCode.INVALID_INPUT
    assert result.message == "Test invalid input"
    assert result.retryable is False
