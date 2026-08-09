"""
Test script thủ công cho tính năng ownership verification
Chạy script này để verify tính năng hoạt động đúng qua HTTP API
"""
import asyncio
from httpx import AsyncClient, ASGITransport
from src.mock.main import app


async def test_ownership_verification():
    """Test thủ công ownership verification qua API"""
    transport = ASGITransport(app=app)

    print("=" * 80)
    print("TEST 1: Verify ownership thành công")
    print("=" * 80)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/apartment-owners/verify-ownership",
            json={
                "full_name": "Lâm Thành Bảo",
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park"
            }
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        assert response.status_code == 200
        assert response.json()["data"]["verified"] is True

    print("\n" + "=" * 80)
    print("TEST 2: Register resident với owner đúng")
    print("=" * 80)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/residents",
            json={
                "full_name": "Trần Thị Bích",
                "apartment_code": "B2305",
                "residential_area": "Vinhomes Ocean Park"
            }
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        assert response.status_code == 201
        print("✓ Resident đã đăng ký thành công")

    print("\n" + "=" * 80)
    print("TEST 3: Register resident với owner SAI (403)")
    print("=" * 80)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/residents",
            json={
                "full_name": "Nguyễn Văn A",  # Sai tên, không phải chủ A1201
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park"
            }
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        assert response.status_code == 403
        assert response.json()["error_code"] == "OWNERSHIP_MISMATCH"
        print("✓ Đã chặn đăng ký với owner sai")

    print("\n" + "=" * 80)
    print("TEST 4: Register resident với apartment không tồn tại (404)")
    print("=" * 80)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/residents",
            json={
                "full_name": "Lê Thị D",
                "apartment_code": "Z9999",  # Không tồn tại
                "residential_area": "Vinhomes Ocean Park"
            }
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        assert response.status_code == 404
        assert response.json()["error_code"] == "OWNERSHIP_NOT_FOUND"
        print("✓ Đã chặn đăng ký với apartment không có trong ownership records")

    print("\n" + "=" * 80)
    print("TẤT CẢ TESTS ĐỀU PASS ✓")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_ownership_verification())
