"""Test vòng đời `verification_records` trên Mock Ownership Provider.

Provider giờ Postgres-backed: record nằm trong test DB qua `wire_provider_pool`
(autouse). Chạy qua ASGITransport nên không có lifespan — `get_pool()` trả pool
đã được `override_pool()` từ conftest.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.mock.apartment_ownership import apartment_ownership_app

# Dữ liệu seed trong `apartment_owners` (seed.sql) — chủ sở hữu thật.
OWNER_CLAIM = {
    "apartment_code": "A1201",
    "residential_area": "Vinhomes Ocean Park",
    "full_name": "Lâm Thành Bảo",
}


async def _client() -> AsyncClient:
    transport = ASGITransport(app=apartment_ownership_app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_create_apartment_record_pending():
    async with await _client() as client:
        response = await client.post(
            "/api/verification-records",
            json={
                "record_type": "apartment",
                "claimed_data": OWNER_CLAIM,
                "proof_image_urls": ["/uploads/r1/abc.jpg"],
            },
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["record_type"] == "apartment"
        assert data["status"] == "PENDING"
        assert data["ownership_match"] is True
        assert data["proof_image_urls"] == ["/uploads/r1/abc.jpg"]


@pytest.mark.asyncio
async def test_create_vehicle_record_pending():
    async with await _client() as client:
        response = await client.post(
            "/api/verification-records",
            json={
                "record_type": "vehicle",
                "claimed_data": {"plate_number": "51F-88999", "vehicle_type": "car"},
                "proof_image_urls": [],
            },
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["record_type"] == "vehicle"
        assert data["status"] == "PENDING"
        # Không phải apartment → không có ownership_match.
        assert "ownership_match" not in data


@pytest.mark.asyncio
async def test_duplicate_pending_same_apartment_rejected():
    async with await _client() as client:
        first = await client.post(
            "/api/verification-records",
            json={"record_type": "apartment", "claimed_data": OWNER_CLAIM},
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/verification-records",
            json={"record_type": "apartment", "claimed_data": OWNER_CLAIM},
        )
        assert second.status_code == 409
        assert second.json()["error_code"] == "VERIFICATION_ALREADY_PENDING"


@pytest.mark.asyncio
async def test_list_filters_by_type():
    async with await _client() as client:
        await client.post(
            "/api/verification-records",
            json={"record_type": "apartment", "claimed_data": OWNER_CLAIM},
        )
        await client.post(
            "/api/verification-records",
            json={"record_type": "vehicle", "claimed_data": {"plate_number": "51F-88999", "vehicle_type": "car"}},
        )

        response = await client.get("/api/verification-records", params={"record_type": "vehicle"})
        assert response.status_code == 200
        items = response.json()["data"]
        assert len(items) == 1
        assert items[0]["record_type"] == "vehicle"


@pytest.mark.asyncio
async def test_decide_approve():
    async with await _client() as client:
        created = await client.post(
            "/api/verification-records",
            json={"record_type": "vehicle", "claimed_data": {"plate_number": "51F-88999", "vehicle_type": "car"}},
        )
        record_id = created.json()["data"]["record_id"]

        response = await client.post(
            f"/api/verification-records/{record_id}/decide",
            json={"decision": "approve", "decided_by": "provider1"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "APPROVED"
        assert data["decided_by"] == "provider1"
        assert data["decided_at"] is not None


@pytest.mark.asyncio
async def test_decide_reject_requires_reason():
    async with await _client() as client:
        created = await client.post(
            "/api/verification-records",
            json={"record_type": "vehicle", "claimed_data": {"plate_number": "51F-88999", "vehicle_type": "car"}},
        )
        record_id = created.json()["data"]["record_id"]

        response = await client.post(
            f"/api/verification-records/{record_id}/decide",
            json={"decision": "reject", "decided_by": "provider1"},
        )
        assert response.status_code == 422
        assert response.json()["error_code"] == "REJECT_REASON_REQUIRED"


@pytest.mark.asyncio
async def test_decide_reject_with_reason():
    async with await _client() as client:
        created = await client.post(
            "/api/verification-records",
            json={"record_type": "vehicle", "claimed_data": {"plate_number": "51F-88999", "vehicle_type": "car"}},
        )
        record_id = created.json()["data"]["record_id"]

        response = await client.post(
            f"/api/verification-records/{record_id}/decide",
            json={"decision": "reject", "reject_reason": "Ảnh giấy tờ không rõ mặt biển số", "decided_by": "provider1"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "REJECTED"
        assert data["reject_reason"] == "Ảnh giấy tờ không rõ mặt biển số"


@pytest.mark.asyncio
async def test_double_decide_blocked():
    async with await _client() as client:
        created = await client.post(
            "/api/verification-records",
            json={"record_type": "vehicle", "claimed_data": {"plate_number": "51F-88999", "vehicle_type": "car"}},
        )
        record_id = created.json()["data"]["record_id"]

        first = await client.post(
            f"/api/verification-records/{record_id}/decide",
            json={"decision": "approve", "decided_by": "provider1"},
        )
        assert first.status_code == 200

        second = await client.post(
            f"/api/verification-records/{record_id}/decide",
            json={"decision": "approve", "decided_by": "provider2"},
        )
        assert second.status_code == 409
        assert second.json()["error_code"] == "VERIFICATION_ALREADY_DECIDED"


def _has_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


@pytest.mark.asyncio
async def test_responses_never_contain_registered_owner_name():
    """owner_name (tên chủ sở hữu trong registry) KHÔNG bao giờ ra response.

    claimed_data.full_name là tên NGƯỜI YÊU CẦU tự khai — người duyệt cần để so
    sánh, nên nó xuất hiện. Điều cấm là tên trong `apartment_owners` (owner_name)
    bị phơi ra ngoài.
    """
    async with await _client() as client:
        created = await client.post(
            "/api/verification-records",
            json={"record_type": "apartment", "claimed_data": OWNER_CLAIM},
        )
        assert created.status_code == 201
        assert not _has_key(created.json(), "owner_name")

        record_id = created.json()["data"]["record_id"]
        decided = await client.post(
            f"/api/verification-records/{record_id}/decide",
            json={"decision": "approve", "decided_by": "provider1"},
        )
        assert decided.status_code == 200
        assert not _has_key(decided.json(), "owner_name")

        listed = await client.get("/api/verification-records")
        assert listed.status_code == 200
        assert not _has_key(listed.json(), "owner_name")
