"""Mỗi Connector phải gọi trúng provider của nó — kiểm bằng HTTP thật.

Đây là lớp test mà suite cũ không có, và chính vì thế hai lỗi routing sống sót:

  - `PropertyConnector` trỏ 8005, nhưng 8005 là mock-tour và service đó không
    có `/api/properties/search` → 404 lúc chạy thật.
  - `ResidentServicesConnector` trỏ 8006, nhưng Docker đang chạy mock-shuttle ở
    8006 → 404 lúc chạy thật.

Cả hai đều xanh trong unit test vì unit test mock `httpx` đi. Ở đây Connector
nói chuyện với ASGI app THẬT của provider qua `ASGITransport`, nên URL sai hay
thiếu endpoint đều lộ ra ngay.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.connectors.consultation import ConsultationConnector
from src.connectors.property import PropertyConnector
from src.connectors.resident_services import ResidentServicesConnector
from src.connectors.tour import TourConnector
from src.services.mock.consultation import consultation_app
from src.services.mock.property import property_app
from src.services.mock.resident_services import resident_services_app
from src.services.mock.tour import tour_app

# Ngày trong tương lai: provider từ chối ngày quá khứ, và một hằng số ngày cứng
# sẽ biến test thành quả bom hẹn giờ.
FUTURE = (date.today() + timedelta(days=30)).isoformat()


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://provider")


@pytest_asyncio.fixture
async def property_client():
    async with _client(property_app) as c:
        yield c


@pytest_asyncio.fixture
async def tour_client():
    async with _client(tour_app) as c:
        yield c


@pytest_asyncio.fixture
async def consultation_client():
    async with _client(consultation_app) as c:
        yield c


@pytest_asyncio.fixture
async def resident_services_client():
    async with _client(resident_services_app) as c:
        yield c


@pytest.mark.asyncio
async def test_search_properties_reaches_the_property_provider(property_client):
    result = await PropertyConnector(client=property_client).execute(
        "search_properties",
        {
            "transaction_type": "buy",
            "property_type": "apartment",
            "residential_area": "Vinhomes Ocean Park",
            "max_price": 5_000_000_000,
        },
    )

    assert result.success is True, result.message
    assert "properties" in result.data


@pytest.mark.asyncio
async def test_schedule_property_viewing_reaches_the_tour_provider(tour_client):
    result = await TourConnector(client=tour_client).execute(
        "schedule_property_viewing",
        {"project_id": "PRJ-001", "viewing_date": FUTURE, "viewing_time": "09:30"},
    )

    assert result.success is True, result.message
    assert set(result.data) == {
        "viewing_id",
        "project_id",
        "project_name",
        "viewing_date",
        "viewing_time",
        "viewing_status",
        "contact_name",
        "contact_phone",
    }


@pytest.mark.asyncio
async def test_register_property_interest_reaches_the_consultation_provider(consultation_client):
    result = await ConsultationConnector(client=consultation_client).execute(
        "register_property_interest",
        {
            "project_id": "PRJ-001",
            "interest_type": "buy",
            "preferred_contact_time": "morning",
            "consent": True,
        },
    )

    assert result.success is True, result.message
    assert set(result.data) == {
        "interest_id",
        "project_id",
        "project_name",
        "interest_status",
        "contact_channel",
    }


@pytest.mark.asyncio
async def test_create_maintenance_request_reaches_resident_services(resident_services_client):
    result = await ResidentServicesConnector(client=resident_services_client).execute(
        "create_maintenance_request",
        {
            "issue_type": "plumbing",
            "description": "Vòi nước bồn rửa bị rò rỉ",
            "location": "Bếp căn A-1201",
            "preferred_date": FUTURE,
            "preferred_time": "09:00",
        },
    )

    assert result.success is True, result.message


@pytest.mark.asyncio
async def test_schedule_move_reaches_resident_services(resident_services_client):
    result = await ResidentServicesConnector(client=resident_services_client).execute(
        "schedule_move",
        {
            "move_date": FUTURE,
            "move_time": "09:00",
            "needs_elevator": True,
            "needs_loading_support": False,
            "move_vehicle": "truck",
        },
    )

    assert result.success is True, result.message


@pytest.mark.asyncio
async def test_property_connector_pointed_at_the_tour_provider_fails(tour_client):
    """Mutation guard: trỏ nhầm service phải hỏng ngay, không im lặng.

    Nếu test này pass khi `PropertyConnector` nói chuyện với mock-tour, nghĩa là
    suite không phân biệt được hai service — đúng trạng thái đã để lọt lỗi 404.
    """
    result = await PropertyConnector(client=tour_client).execute(
        "search_properties",
        {
            "transaction_type": "buy",
            "property_type": "apartment",
            "residential_area": "Vinhomes Ocean Park",
            "max_price": 5_000_000_000,
        },
    )

    assert result.success is False


@pytest.mark.asyncio
async def test_resident_services_connector_pointed_at_the_shuttle_provider_fails():
    """Mutation guard: 8006 từng là shuttle, và không ai phát hiện."""
    from src.services.mock.shuttle import shuttle_app

    async with _client(shuttle_app) as shuttle_client:
        result = await ResidentServicesConnector(client=shuttle_client).execute(
            "create_maintenance_request",
            {
                "issue_type": "plumbing",
                "description": "Vòi nước bồn rửa bị rò rỉ",
                "location": "Bếp căn A-1201",
                "preferred_date": FUTURE,
                "preferred_time": "09:00",
            },
        )

    assert result.success is False
