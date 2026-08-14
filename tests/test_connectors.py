"""Tests cho Connector layer.

Owner: Mạnh Hiệp (Executor layer)
File: tests/test_connectors.py
"""

import uuid
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.common.enums import ErrorCode
from src.connectors.base import Connector
from src.connectors.consultation import ConsultationConnector
from src.connectors.payment import PaymentConnector
from src.connectors.resident import ResidentConnector
from src.connectors.shuttle import ShuttleConnector
from src.connectors.tour import TourConnector
from src.connectors.transport import TransportConnector
from src.services.mock.consultation import consultation_app
from src.services.mock.payment import payment_app
from src.services.mock.resident import resident_app
from src.services.mock.shuttle import shuttle_app
from src.services.mock.tour import tour_app
from src.services.mock.transport import transport_app


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
    mock_response.json.return_value = {
        "success": True,
        "data": {"resident_id": "RES-123", "extra_field": "ignore_me"},
        "error_code": None,
        "message": "Created",
        "retryable": False,
    }
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
    mock_response.json.return_value = {"success": True, "data": {"resident_id": "RES-999"}, "message": "Created"}
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
    mock_response.json.return_value = {"success": True, "data": {"something": "else"}, "message": "Created"}
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
    mock_response.json.return_value = {
        "success": True,
        "data": {"vehicle_id": "VEH-123", "color": "red"},
        "message": "Created",
    }
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
    mock_response.json.return_value = {"success": True, "data": {"vehicle_id": "VEH-888"}, "message": "Created"}
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
        "success": True,
        "data": {
            "booking_id": "BOOK-1",
            "parking_zone": "ZONE_A",
            "booking_date": "2026-08-10",
            "amount": 100,
            "currency": "VND",
            "ignore_me": "yes",
        },
        "error_code": None,
        "message": "Created",
        "retryable": False,
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = TransportConnector(client=mock_httpx_client)
    result = await connector.execute("book_parking", {"vehicle_id": "VEH-123"})

    assert result.success is True
    assert result.data == {
        "booking_id": "BOOK-1",
        "parking_zone": "ZONE_A",
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
        "success": True,
        "data": {
            "booking_id": "BOOK-777",
            "parking_zone": "ZONE_B",
            "booking_date": "2026-08-10",
            "amount": 200000,
            "currency": "VND",
        },
        "message": "Created",
    }
    mock_httpx_client.post_mock.return_value = mock_response

    payload = {"vehicle_id": "VEH-001", "booking_date": "2026-08-10", "parking_zone": "ZONE_B"}
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
    mock_response.json.return_value = {"success": True, "data": {"payment_id": "PAY-1", "payment_status": "PAID"}}
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
    mock_response.json.return_value = {"success": True, "data": {"payment_id": "PAY-2", "payment_status": "SUCCESS"}}
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
    mock_response.json.return_value = {
        "success": True,
        "data": {"payment_id": "PAY-3", "payment_status": "WEIRD_STATUS"},
    }
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
    mock_response.json.return_value = {"success": True, "data": {"payment_id": "PAY-999", "payment_status": "PAID"}}
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
    mock_response.json.return_value = {
        "success": False,
        "data": None,
        "error_code": "NO_AVAILABILITY",
        "message": "Full",
        "retryable": False,
    }
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
    mock_response.json.return_value = {
        "success": False,
        "data": None,
        "error_code": "WEIRD_ERROR",
        "message": "???",
        "retryable": False,
    }
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


# ---------------------------------------------------------------------------
# Envelope: HTTP 2xx nhưng success=false
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resident_envelope_success_false_returns_failure(mock_httpx_client):
    """HTTP 2xx + envelope success=false → StandardResult failure đã map error_code."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": False,
        "data": None,
        "error_code": "RESIDENT_ALREADY_EXISTS",
        "message": "Resident for apartment A101 already exists",
        "retryable": False,
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = ResidentConnector(client=mock_httpx_client)
    result = await connector.execute("register_resident", {"full_name": "Test"})

    assert result.success is False
    assert result.data is None
    assert result.error_code == ErrorCode.RESIDENT_ALREADY_EXISTS
    assert result.retryable is False
    assert result.message == "Resident for apartment A101 already exists"


@pytest.mark.asyncio
async def test_transport_envelope_success_false_maps_alias_error_code(mock_httpx_client):
    """error_code alias (VEHICLE_EXISTS) trong envelope phải đi qua _map_error_code()."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": False,
        "data": None,
        "error_code": "VEHICLE_EXISTS",
        "message": "Duplicated plate",
        "retryable": False,
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = TransportConnector(client=mock_httpx_client)
    result = await connector.execute("register_vehicle", {"plate_number": "51A-00000"})

    assert result.success is False
    assert result.error_code == ErrorCode.VEHICLE_ALREADY_EXISTS


@pytest.mark.asyncio
async def test_transport_book_parking_envelope_success_false_retryable(mock_httpx_client):
    """Envelope retryable=true phải được giữ nguyên trên StandardResult."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": False,
        "data": None,
        "error_code": "SERVICE_UNAVAILABLE",
        "message": "[MOCK] Injected: service unavailable",
        "retryable": True,
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = TransportConnector(client=mock_httpx_client)
    result = await connector.execute("book_parking", {"vehicle_id": "VEH-1"})

    assert result.success is False
    assert result.error_code == ErrorCode.SERVICE_UNAVAILABLE
    assert result.retryable is True


@pytest.mark.asyncio
async def test_payment_envelope_success_false_returns_failure(mock_httpx_client):
    """PaymentConnector: envelope success=false → PAYMENT_FAILED, không normalize status."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": False,
        "data": None,
        "error_code": "INSUFFICIENT_BALANCE",
        "message": "[MOCK] Injected: insufficient balance",
        "retryable": False,
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = PaymentConnector(client=mock_httpx_client)
    result = await connector.execute("pay_fee", {"booking_id": "BOOK-1"})

    assert result.success is False
    assert result.data is None
    assert result.error_code == ErrorCode.PAYMENT_FAILED


@pytest.mark.asyncio
async def test_envelope_success_true_but_data_null(mock_httpx_client):
    """success=true nhưng data=null → thiếu canonical field → UNKNOWN_EXTERNAL_ERROR."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"success": True, "data": None, "message": "Created"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = ResidentConnector(client=mock_httpx_client)
    result = await connector.execute("register_resident", {"full_name": "Test"})

    assert result.success is False
    assert result.error_code == ErrorCode.UNKNOWN_EXTERNAL_ERROR


def test_extract_payload_handles_flat_and_invalid_body():
    """Helper: flat dict được dung thứ; body không phải dict → EnvelopeError."""
    payload, err = Connector._extract_payload({"resident_id": "RES-1"})
    assert err is None
    assert payload == {"resident_id": "RES-1"}

    payload, err = Connector._extract_payload(["not", "a", "dict"])
    assert payload is None
    assert err is not None
    assert err.error_code == "UNKNOWN_EXTERNAL_ERROR"


# ---------------------------------------------------------------------------
# Integration: Connector ↔ Mock Provider thật (in-process qua ASGITransport)
#
# KHÔNG dùng MagicMock — các test này chứng minh Connector nhận đúng ID thật
# do Mock Provider sinh ra, và bóc đúng envelope {success, data, ...}.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def resident_client():
    async with AsyncClient(transport=ASGITransport(app=resident_app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def transport_client():
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def payment_client():
    async with AsyncClient(transport=ASGITransport(app=payment_app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def tour_client():
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def shuttle_client():
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def consultation_client():
    async with AsyncClient(transport=ASGITransport(app=consultation_app), base_url="http://test") as ac:
        yield ac


def _unique(prefix: str) -> str:
    """Sinh giá trị duy nhất — store của mock provider dùng chung giữa các test."""
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


@pytest.mark.asyncio
async def test_integration_register_resident(resident_client):
    connector = ResidentConnector(base_url="http://test", client=resident_client)

    result = await connector.execute(
        "register_resident",
        {
            "full_name": "Nguyễn Văn A",
            "apartment_code": _unique("A"),
            "residential_area": "VH-SGV",
        },
    )

    assert result.success is True, result.message
    assert set(result.data) == {"resident_id"}, "Chỉ canonical field được trả về"
    assert result.data["resident_id"].startswith("RES")


@pytest.mark.asyncio
async def test_integration_register_vehicle(resident_client, transport_client):
    resident_connector = ResidentConnector(base_url="http://test", client=resident_client)
    transport_connector = TransportConnector(base_url="http://test", client=transport_client)

    resident = await resident_connector.execute(
        "register_resident",
        {"full_name": "Trần Thị B", "apartment_code": _unique("A"), "residential_area": "VH-SGV"},
    )
    assert resident.success is True, resident.message

    result = await transport_connector.execute(
        "register_vehicle",
        {
            "resident_id": resident.data["resident_id"],
            "plate_number": _unique("51A-"),
            "vehicle_type": "car",
        },
    )

    assert result.success is True, result.message
    assert set(result.data) == {"vehicle_id"}
    assert result.data["vehicle_id"].startswith("VEH")


@pytest.mark.asyncio
async def test_integration_book_parking(resident_client, transport_client):
    resident_connector = ResidentConnector(base_url="http://test", client=resident_client)
    transport_connector = TransportConnector(base_url="http://test", client=transport_client)

    resident = await resident_connector.execute(
        "register_resident",
        {"full_name": "Lê Văn C", "apartment_code": _unique("A"), "residential_area": "VH-SGV"},
    )
    vehicle = await transport_connector.execute(
        "register_vehicle",
        {
            "resident_id": resident.data["resident_id"],
            "plate_number": _unique("51B-"),
            "vehicle_type": "car",
        },
    )
    assert vehicle.success is True, vehicle.message

    result = await transport_connector.execute(
        "book_parking",
        {
            "vehicle_id": vehicle.data["vehicle_id"],
            "booking_date": "2026-08-10",
            "parking_zone": "ZONE_B",
        },
    )

    assert result.success is True, result.message
    assert set(result.data) == {"booking_id", "parking_zone", "booking_date", "amount", "currency"}
    assert result.data["booking_id"].startswith("BOOK")
    assert result.data["parking_zone"] == "ZONE_B"
    assert result.data["booking_date"] == "2026-08-10"
    assert result.data["currency"] == "VND"
    assert isinstance(result.data["amount"], int)


@pytest.mark.asyncio
async def test_integration_pay_fee_full_chain(resident_client, transport_client, payment_client):
    """Chuỗi 4 bước đầy đủ: resident → vehicle → booking → payment."""
    resident_connector = ResidentConnector(base_url="http://test", client=resident_client)
    transport_connector = TransportConnector(base_url="http://test", client=transport_client)
    payment_connector = PaymentConnector(base_url="http://test", client=payment_client)

    resident = await resident_connector.execute(
        "register_resident",
        {"full_name": "Phạm Thị D", "apartment_code": _unique("A"), "residential_area": "VH-SGV"},
    )
    vehicle = await transport_connector.execute(
        "register_vehicle",
        {
            "resident_id": resident.data["resident_id"],
            "plate_number": _unique("51C-"),
            "vehicle_type": "motorcycle",
        },
    )
    booking = await transport_connector.execute(
        "book_parking",
        {
            "vehicle_id": vehicle.data["vehicle_id"],
            "booking_date": "2026-08-11",
            "parking_zone": "ZONE_B",
        },
    )
    assert booking.success is True, booking.message

    result = await payment_connector.execute(
        "pay_fee",
        {
            "booking_id": booking.data["booking_id"],
            "amount": booking.data["amount"],
            "currency": booking.data["currency"],
        },
    )

    assert result.success is True, result.message
    assert set(result.data) == {"payment_id", "payment_status"}
    assert result.data["payment_id"].startswith("PAY")
    assert result.data["payment_status"] == "PAID"


@pytest.mark.asyncio
async def test_integration_error_envelope_from_real_provider(transport_client):
    """Lỗi nghiệp vụ từ provider thật → Connector map sang ErrorCode nội bộ."""
    connector = TransportConnector(base_url="http://test", client=transport_client)

    result = await connector.execute(
        "book_parking",
        {"vehicle_id": "VEH-KHONG-TON-TAI", "booking_date": "2026-08-12", "parking_zone": "ZONE_A"},
    )

    assert result.success is False
    assert result.error_code == ErrorCode.VEHICLE_NOT_FOUND
    assert result.data is None


@pytest.mark.asyncio
async def test_integration_validation_error_maps_to_invalid_input(resident_client, transport_client):
    """422 từ provider thật (schema validation) → ErrorCode.INVALID_INPUT.

    FastAPI mặc định trả ``{"detail": [...]}`` cho 422 — không khớp envelope nên
    connector sẽ fallback UNKNOWN_EXTERNAL_ERROR. Handler chung trong
    ``src/mock/errors.py`` đảm bảo 422 cũng theo envelope contract.
    """
    resident_connector = ResidentConnector(base_url="http://test", client=resident_client)
    transport_connector = TransportConnector(base_url="http://test", client=transport_client)

    resident = await resident_connector.execute(
        "register_resident",
        {"full_name": "Hoàng Văn E", "apartment_code": _unique("A"), "residential_area": "VH-SGV"},
    )
    vehicle = await transport_connector.execute(
        "register_vehicle",
        {
            "resident_id": resident.data["resident_id"],
            "plate_number": _unique("51D-"),
            "vehicle_type": "car",
        },
    )
    assert vehicle.success is True, vehicle.message

    result = await transport_connector.execute(
        "book_parking",
        {
            "vehicle_id": vehicle.data["vehicle_id"],
            "booking_date": "2026-08-13",
            "parking_zone": "NOT_A_ZONE",
        },
    )

    assert result.success is False
    assert result.error_code == ErrorCode.INVALID_INPUT
    assert result.data is None
    assert result.retryable is False


# ---------------------------------------------------------------------------
# TourConnector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tour_connector_success(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": True,
        "data": {
            "tour_id": "TOUR-123",
            "residential_area": "Vinhomes Ocean Park",
            "tour_date": "2026-08-14",
            "tour_slot": "MORNING",
            "ignore_me": "yes",
        },
        "error_code": None,
        "message": "Created",
        "retryable": False,
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = TourConnector(client=mock_httpx_client)
    result = await connector.execute("book_tour", {"residential_area": "Vinhomes Ocean Park"})

    assert result.success is True
    assert result.data == {
        "tour_id": "TOUR-123",
        "residential_area": "Vinhomes Ocean Park",
        "tour_date": "2026-08-14",
        "tour_slot": "MORNING",
    }


@pytest.mark.asyncio
async def test_tour_connector_url_and_payload(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": True,
        "data": {
            "tour_id": "TOUR-888",
            "residential_area": "Vinhomes",
            "tour_date": "2026-08-14",
            "tour_slot": "MORNING",
        },
        "message": "Created",
    }
    mock_httpx_client.post_mock.return_value = mock_response

    payload = {"residential_area": "Vinhomes Ocean Park", "tour_date": "2026-08-14", "tour_slot": "MORNING"}
    connector = TourConnector(base_url="http://localhost:8005", client=mock_httpx_client)
    await connector.execute("book_tour", payload)

    mock_httpx_client.post_mock.assert_called_once_with(
        "http://localhost:8005/api/tours/bookings",
        json=payload,
        timeout=30.0,
    )


@pytest.mark.asyncio
async def test_tour_connector_missing_output(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"success": True, "data": {"something": "else"}, "message": "Created"}
    mock_httpx_client.post_mock.return_value = mock_response

    connector = TourConnector(client=mock_httpx_client)
    result = await connector.execute("book_tour", {})

    assert result.success is False
    assert result.error_code == ErrorCode.UNKNOWN_EXTERNAL_ERROR


@pytest.mark.asyncio
async def test_tour_connector_no_availability(mock_httpx_client):
    """Envelope lỗi NO_AVAILABILITY → map sang ErrorCode.NO_AVAILABILITY."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": False,
        "data": None,
        "error_code": "NO_AVAILABILITY",
        "message": "Tour slot is full",
        "retryable": False,
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = TourConnector(client=mock_httpx_client)
    result = await connector.execute("book_tour", {})

    assert result.success is False
    assert result.error_code == ErrorCode.NO_AVAILABILITY
    assert result.retryable is False


# ---------------------------------------------------------------------------
# ShuttleConnector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shuttle_connector_success(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": True,
        "data": {
            "shuttle_id": "SHUTTLE-123",
            "tour_id": "TOUR-1",
            "tour_date": "2026-08-14",
            "passenger_count": 4,
            "ignore_me": "yes",
        },
        "error_code": None,
        "message": "Created",
        "retryable": False,
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = ShuttleConnector(client=mock_httpx_client)
    result = await connector.execute("book_shuttle", {"tour_id": "TOUR-1"})

    assert result.success is True
    assert result.data == {
        "shuttle_id": "SHUTTLE-123",
        "tour_id": "TOUR-1",
        "tour_date": "2026-08-14",
        "passenger_count": 4,
    }


@pytest.mark.asyncio
async def test_shuttle_connector_url_and_payload(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": True,
        "data": {"shuttle_id": "SHUTTLE-888", "tour_id": "TOUR-2", "tour_date": "2026-08-14", "passenger_count": 2},
        "message": "Created",
    }
    mock_httpx_client.post_mock.return_value = mock_response

    payload = {"tour_id": "TOUR-2", "tour_date": "2026-08-14", "passenger_count": 2}
    connector = ShuttleConnector(base_url="http://localhost:8006", client=mock_httpx_client)
    await connector.execute("book_shuttle", payload)

    mock_httpx_client.post_mock.assert_called_once_with(
        "http://localhost:8006/api/shuttles/bookings",
        json=payload,
        timeout=30.0,
    )


@pytest.mark.asyncio
async def test_shuttle_connector_no_availability(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": False,
        "data": None,
        "error_code": "NO_AVAILABILITY",
        "message": "Shuttle capacity exceeded",
        "retryable": False,
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = ShuttleConnector(client=mock_httpx_client)
    result = await connector.execute("book_shuttle", {})

    assert result.success is False
    assert result.error_code == ErrorCode.NO_AVAILABILITY


# ---------------------------------------------------------------------------
# ConsultationConnector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consultation_connector_success(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": True,
        "data": {
            "consultation_id": "CONS-123",
            "consultation_type": "BUY",
            "buy_sub_type": "RESIDE",
            "ignore_me": "yes",
        },
        "error_code": None,
        "message": "Created",
        "retryable": False,
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = ConsultationConnector(client=mock_httpx_client)
    result = await connector.execute("register_consultation", {"consultation_type": "BUY"})

    assert result.success is True
    assert result.data == {
        "consultation_id": "CONS-123",
        "consultation_type": "BUY",
        "buy_sub_type": "RESIDE",
    }


@pytest.mark.asyncio
async def test_consultation_connector_rent_null_subtype(mock_httpx_client):
    """Tư vấn thuê (RENT) không có buy_sub_type → vẫn thành công với buy_sub_type=None."""
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": True,
        "data": {"consultation_id": "CONS-456", "consultation_type": "RENT", "buy_sub_type": None},
        "message": "Created",
    }
    mock_httpx_client.post_mock.return_value = mock_response

    connector = ConsultationConnector(client=mock_httpx_client)
    result = await connector.execute("register_consultation", {"consultation_type": "RENT"})

    assert result.success is True
    assert result.data == {"consultation_id": "CONS-456", "consultation_type": "RENT", "buy_sub_type": None}


@pytest.mark.asyncio
async def test_consultation_connector_url_and_payload(mock_httpx_client):
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": True,
        "data": {"consultation_id": "CONS-999", "consultation_type": "BUY", "buy_sub_type": "INVEST"},
        "message": "Created",
    }
    mock_httpx_client.post_mock.return_value = mock_response

    payload = {"consultation_type": "BUY", "buy_sub_type": "INVEST"}
    connector = ConsultationConnector(base_url="http://localhost:8007", client=mock_httpx_client)
    await connector.execute("register_consultation", payload)

    mock_httpx_client.post_mock.assert_called_once_with(
        "http://localhost:8007/api/consultations",
        json=payload,
        timeout=30.0,
    )


# ---------------------------------------------------------------------------
# Integration: 3 Connector mới ↔ Mock Provider thật (in-process qua ASGITransport)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_book_tour(tour_client):
    connector = TourConnector(base_url="http://test", client=tour_client)

    result = await connector.execute(
        "book_tour",
        {
            "residential_area": "Vinhomes Ocean Park",
            "tour_date": "2026-08-20",
            "tour_slot": "MORNING",
        },
    )

    assert result.success is True, result.message
    assert set(result.data) == {"tour_id", "residential_area", "tour_date", "tour_slot"}
    assert result.data["tour_id"].startswith("TOUR")
    assert result.data["residential_area"] == "Vinhomes Ocean Park"
    assert result.data["tour_slot"] == "MORNING"


@pytest.mark.asyncio
async def test_integration_book_tour_slot_not_found(tour_client):
    """Slot không được seed → 404 TOUR_SLOT_NOT_FOUND → UNKNOWN_EXTERNAL_ERROR."""
    connector = TourConnector(base_url="http://test", client=tour_client)

    result = await connector.execute(
        "book_tour",
        {"residential_area": "Khong Ton Tai", "tour_date": "2026-08-20", "tour_slot": "MORNING"},
    )

    assert result.success is False
    assert result.data is None


@pytest.mark.asyncio
async def test_integration_book_shuttle(tour_client, shuttle_client):
    tour_connector = TourConnector(base_url="http://test", client=tour_client)
    shuttle_connector = ShuttleConnector(base_url="http://test", client=shuttle_client)

    tour = await tour_connector.execute(
        "book_tour",
        {
            "residential_area": "Vinhomes Ocean Park",
            "tour_date": "2026-08-21",
            "tour_slot": "AFTERNOON",
        },
    )
    assert tour.success is True, tour.message

    result = await shuttle_connector.execute(
        "book_shuttle",
        {
            "tour_id": tour.data["tour_id"],
            "tour_date": "2026-08-21",
            "passenger_count": 3,
        },
    )

    assert result.success is True, result.message
    assert set(result.data) == {"shuttle_id", "tour_id", "tour_date", "passenger_count"}
    assert result.data["shuttle_id"].startswith("SHUTTLE")
    assert result.data["passenger_count"] == 3


@pytest.mark.asyncio
async def test_integration_register_consultation(consultation_client):
    connector = ConsultationConnector(base_url="http://test", client=consultation_client)

    result = await connector.execute(
        "register_consultation",
        {"consultation_type": "BUY", "buy_sub_type": "RESIDE"},
    )

    assert result.success is True, result.message
    assert set(result.data) == {"consultation_id", "consultation_type", "buy_sub_type"}
    assert result.data["consultation_id"].startswith("CONS")
    assert result.data["consultation_type"] == "BUY"
    assert result.data["buy_sub_type"] == "RESIDE"


@pytest.mark.asyncio
async def test_integration_register_consultation_missing_buy_sub_type(consultation_client):
    """BUY thiếu buy_sub_type → 422 → INVALID_INPUT."""
    connector = ConsultationConnector(base_url="http://test", client=consultation_client)

    result = await connector.execute(
        "register_consultation",
        {"consultation_type": "BUY"},
    )

    assert result.success is False
    assert result.error_code == ErrorCode.INVALID_INPUT
    assert result.data is None
