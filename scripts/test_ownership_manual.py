"""
Test script thủ công cho Mock Ownership Provider.

Chạy script này để kiểm tra hành vi THỰC TẾ của Week 1 qua HTTP API:
  - verify-ownership trả trạng thái tối thiểu (không có owner_name — PII)
  - verify-ownership báo 403 / 404 đúng
  - register_resident chạy ĐỘC LẬP, KHÔNG verify ownership (chưa có
    VerificationGuard — hạng mục Week 2)
"""

import asyncio

from httpx import ASGITransport, AsyncClient

from src.mock.main import app


async def test_ownership_verification():
    transport = ASGITransport(app=app)

    print("=" * 80)
    print("TEST 1: Verify ownership thành công — response KHÔNG chứa owner_name")
    print("=" * 80)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/apartment-owners/verify-ownership",
            json={
                "full_name": "Lâm Thành Bảo",
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park",
            },
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        assert response.status_code == 200
        assert response.json()["data"]["verified"] is True
        assert "owner_name" not in response.text
        print("✓ Verified, không lộ PII")

    print("\n" + "=" * 80)
    print("TEST 2: Verify ownership sai tên → 403 OWNERSHIP_MISMATCH")
    print("=" * 80)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/apartment-owners/verify-ownership",
            json={
                "full_name": "Nguyễn Văn A",  # Không phải chủ A1201
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park",
            },
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        assert response.status_code == 403
        assert response.json()["error_code"] == "OWNERSHIP_MISMATCH"
        print("✓ Đã từ chối khi tên không khớp chủ sở hữu")

    print("\n" + "=" * 80)
    print("TEST 3: Verify ownership căn hộ không tồn tại → 404 OWNERSHIP_NOT_FOUND")
    print("=" * 80)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/apartment-owners/verify-ownership",
            json={
                "full_name": "Lê Thị D",
                "apartment_code": "Z9999",  # Không tồn tại
                "residential_area": "Vinhomes Ocean Park",
            },
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        assert response.status_code == 404
        assert response.json()["error_code"] == "OWNERSHIP_NOT_FOUND"
        print("✓ Đã từ chối khi căn hộ không có trong ownership records")

    print("\n" + "=" * 80)
    print("TEST 4: register_resident chạy ĐỘC LẬP — KHÔNG verify ownership")
    print("=" * 80)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/residents",
            json={
                "full_name": "Nguyễn Văn A",  # KHÔNG phải chủ A1201
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park",
            },
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        # Week 1: chưa có VerificationGuard → register chỉ check UNIQUE.
        assert response.status_code == 201
        print("✓ Đúng hành vi Week 1: register_resident không gọi ownership verification")

    print("\n" + "=" * 80)
    print("TEST 5: register_resident trùng căn hộ → 409 RESIDENT_ALREADY_EXISTS")
    print("=" * 80)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/residents",
            json={
                "full_name": "Lê Thị D",
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park",
            },
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        assert response.status_code == 409
        assert response.json()["error_code"] == "RESIDENT_ALREADY_EXISTS"
        print("✓ UNIQUE constraint hoạt động")

    print("\n" + "=" * 80)
    print("TẤT CẢ TESTS ĐỀU PASS ✓")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_ownership_verification())
