"""Test ownership verification endpoint (hub thuần - provider độc lập)"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.mock.main import app
from src.services.mock.apartment_ownership import apartment_ownership_app


@pytest.mark.asyncio
async def test_verify_ownership_success():
    """Test verify ownership thành công khi owner đúng"""
    transport = ASGITransport(app=apartment_ownership_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/apartment-owners/verify-ownership",
            json={"full_name": "Lâm Thành Bảo", "apartment_code": "A1201", "residential_area": "Vinhomes Ocean Park"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["verified"] is True
        assert data["data"]["apartment_code"] == "A1201"
        assert data["data"]["residential_area"] == "Vinhomes Ocean Park"


@pytest.mark.asyncio
async def test_verify_ownership_apartment_not_found():
    """Test verify thất bại khi apartment không tồn tại trong ownership records"""
    transport = ASGITransport(app=apartment_ownership_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/apartment-owners/verify-ownership",
            json={
                "full_name": "Nguyễn Văn A",
                "apartment_code": "Z9999",  # Không tồn tại
                "residential_area": "Vinhomes Ocean Park",
            },
        )
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "OWNERSHIP_NOT_FOUND"
        assert "not found in ownership records" in data["message"]


@pytest.mark.asyncio
async def test_verify_ownership_wrong_area():
    """Test verify thất bại khi residential_area sai"""
    transport = ASGITransport(app=apartment_ownership_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/apartment-owners/verify-ownership",
            json={
                "full_name": "Lâm Thành Bảo",
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Smart City",  # Sai area
            },
        )
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "OWNERSHIP_NOT_FOUND"


@pytest.mark.asyncio
async def test_verify_ownership_wrong_owner_name():
    """Test verify thất bại khi owner_name không khớp"""
    transport = ASGITransport(app=apartment_ownership_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/apartment-owners/verify-ownership",
            json={
                "full_name": "Nguyễn Văn A",  # Sai tên, không phải chủ sở hữu
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park",
            },
        )
        assert response.status_code == 403
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "OWNERSHIP_MISMATCH"
        assert "not the owner" in data["message"]


@pytest.mark.asyncio
async def test_register_resident_independent():
    """Test register_resident độc lập, không verify ownership (hub thuần)"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register resident - chỉ check UNIQUE, không verify ownership
        response = await client.post(
            "/api/residents",
            json={
                "full_name": "Nguyễn Văn A",  # Không phải chủ sở hữu
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park",
            },
        )
        # Theo hub thuần: register_resident chỉ check UNIQUE, không verify ownership
        # Nếu apartment chưa có ai đăng ký → 201 Created
        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        assert "resident_id" in data["data"]


@pytest.mark.asyncio
async def test_register_resident_duplicate():
    """Test register_resident fail khi apartment đã có người đăng ký (UNIQUE constraint)"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register lần 1
        response1 = await client.post(
            "/api/residents",
            json={"full_name": "Trần Thị Bích", "apartment_code": "B2305", "residential_area": "Vinhomes Ocean Park"},
        )
        assert response1.status_code == 201

        # Register lần 2 (cùng apartment)
        response2 = await client.post(
            "/api/residents",
            json={
                "full_name": "Lê Thị D",  # Người khác
                "apartment_code": "B2305",
                "residential_area": "Vinhomes Ocean Park",
            },
        )
        # Theo hub thuần: chỉ check UNIQUE constraint
        assert response2.status_code == 409
        data = response2.json()
        assert data["success"] is False
        assert data["error_code"] == "RESIDENT_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_verify_ownership_response_has_no_owner_name():
    """Regression: response verify-ownership KHÔNG được chứa owner_name (PII)."""
    for target_app in (apartment_ownership_app, app):
        transport = ASGITransport(app=target_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/apartment-owners/verify-ownership",
                json={
                    "full_name": "Lâm Thành Bảo",
                    "apartment_code": "A1201",
                    "residential_area": "Vinhomes Ocean Park",
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["data"]["verified"] is True
            assert "owner_name" not in data["data"]
            assert "owner_name" not in response.text
            # PII không được lộ ở bất kỳ đâu trong body
            assert "Lâm Thành Bảo" not in response.text


@pytest.mark.asyncio
async def test_get_apartment_owner_endpoint_removed():
    """Endpoint tra cứu chủ sở hữu đã bị xoá — trả owner_name (PII) mà không có nhu cầu nghiệp vụ."""
    for target_app in (apartment_ownership_app, app):
        transport = ASGITransport(app=target_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/apartment-owners/A1201/Vinhomes%20Ocean%20Park")
            assert response.status_code in (404, 405)
            assert "Lâm Thành Bảo" not in response.text
