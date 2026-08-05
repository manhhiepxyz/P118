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
async def test_resident_connector_missing_output(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"something": "else"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = ResidentConnector(client=mock_httpx_client)
    result = await connector.execute("register_resident", {"name": "Test"})

    assert result.success is False
    assert result.error_code == ErrorCode.UNKNOWN_EXTERNAL_ERROR


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
async def test_payment_connector_success(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"payment_id": "PAY-1", "payment_status": "SUCCESS"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = PaymentConnector(client=mock_httpx_client)
    result = await connector.execute("pay_fee", {"booking_id": "BOOK-1"})

    assert result.success is True
    assert result.data == {"payment_id": "PAY-1", "payment_status": "SUCCESS"}


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
