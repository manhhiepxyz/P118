"""Báo giá chuyển nhà chỉ tồn tại khi yêu cầu đủ dữ kiện nghiệp vụ.

Ngày/giờ/loại xe không đủ để định giá. Điểm đi, điểm đến và quy mô đồ phải
thuộc danh mục nội khu; ngoài danh mục thì provider từ chối rõ ràng, không dựng
một con số có vẻ hợp lệ.
"""

from __future__ import annotations

import httpx
import pytest

from src.common.move_locations import (
    MOVE_LOCATIONS,
    distance_band,
    find_move_location_id,
    resolve_move_location_id,
)
from src.common.tool_contract import TOOL_CONTRACTS
from src.services.mock.resident_services import resident_services_app

BASE = {
    "move_date": "2030-12-10",
    "move_time": "09:00",
    "needs_elevator": False,
    "needs_loading_support": False,
    "move_vehicle": "van",
    "move_origin_id": "MOVE-Q7-A1",
    "move_destination_id": "MOVE-Q7-B1",
    "move_size": "small",
}


def test_the_move_contract_names_the_three_facts_that_make_a_quote_real() -> None:
    contract = TOOL_CONTRACTS["schedule_move"]
    assert {"move_origin_id", "move_destination_id", "move_size"} <= set(contract.inputs)


def test_the_location_catalog_is_canonical_and_resolves_vietnamese_names() -> None:
    assert len(MOVE_LOCATIONS) >= 4
    assert resolve_move_location_id("Tòa A1 Riverside") == "MOVE-Q7-A1"
    assert resolve_move_location_id("toa a1 riverside") == "MOVE-Q7-A1"
    assert resolve_move_location_id("Hà Nội") is None
    assert distance_band("MOVE-Q7-A1", "MOVE-Q7-B1") == "SAME_DISTRICT"


def test_a_sentence_with_two_locations_is_ambiguous_instead_of_picking_one() -> None:
    assert find_move_location_id("Chuyển từ Tòa A1 Riverside sang Tòa B1 Green View") is None


@pytest.mark.asyncio
async def test_quote_refuses_to_invent_a_price_when_route_or_size_is_missing() -> None:
    transport = httpx.ASGITransport(app=resident_services_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://resident-services") as client:
        for field in ("move_origin_id", "move_destination_id", "move_size"):
            response = await client.post(
                "/api/resident-services/moves/quotes/MOV-01",
                json={key: value for key, value in BASE.items() if key != field},
            )
            assert response.status_code == 422, field


@pytest.mark.asyncio
async def test_quote_says_the_route_is_not_supported_instead_of_returning_a_fake_price() -> None:
    transport = httpx.ASGITransport(app=resident_services_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://resident-services") as client:
        response = await client.post(
            "/api/resident-services/moves/quotes/MOV-01",
            json={**BASE, "move_destination_id": "HA-NOI"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["error_code"] == "OUT_OF_SERVICE_AREA"
    assert response.json().get("data") is None


@pytest.mark.asyncio
async def test_distance_and_move_size_change_the_real_quote() -> None:
    transport = httpx.ASGITransport(app=resident_services_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://resident-services") as client:
        small = await client.post("/api/resident-services/moves/quotes/MOV-01", json=BASE)
        large = await client.post(
            "/api/resident-services/moves/quotes/MOV-01",
            json={**BASE, "move_size": "large"},
        )
        same_building = await client.post(
            "/api/resident-services/moves/quotes/MOV-01",
            json={**BASE, "move_destination_id": "MOVE-Q7-A1"},
        )

    assert small.json()["success"] is True
    assert large.json()["data"]["amount"] > small.json()["data"]["amount"]
    assert same_building.json()["data"]["amount"] < small.json()["data"]["amount"]
