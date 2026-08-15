"""Khách hàng xin liên kết căn hộ; admin quyết định. Không ai tự nâng quyền.

Trước lượt này, customer không có đường nào bắt đầu việc liên kết — admin phải
tự gõ UUID tài khoản và mã cư dân, hai thứ chỉ tồn tại ngoài hệ thống. Thêm một
đường cho khách hàng là cần thiết, nhưng nó chạm thẳng vào ranh giới tin cậy
quan trọng nhất của sản phẩm, nên phần lớn test ở đây là về những gì khách hàng
KHÔNG được làm.
"""

from __future__ import annotations

import pytest

from tests.test_db.conftest import _register_and_login

APARTMENT = {"apartment_code": "L-1201", "residential_area": "Vinhomes Ocean Park", "full_name": "Nguyen Van Canary"}


async def _admin_token(client, db_pool, username: str) -> str:
    await _register_and_login(client, username)
    await db_pool.execute("UPDATE users SET role = 'admin' WHERE username = $1", username)
    # Role nằm trong token, nên phải lấy token MỚI sau khi nâng quyền.
    return await _register_and_login(client, username)


async def _pending_request(client, token: str) -> dict:
    response = await client.post(
        "/api/v1/auth/resident-link-requests",
        headers={"Authorization": f"Bearer {token}"},
        json=APARTMENT,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Đường đi đúng
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_customer_can_ask_and_then_watch_the_status(client, db_pool):
    token = await _register_and_login(client, "lr_flow_user")
    created = await _pending_request(client, token)

    assert created["status"] == "PENDING"
    assert "resident_id" not in created, "mã cư dân là dữ liệu nội bộ"

    mine = (
        await client.get("/api/v1/auth/resident-link-requests/me", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert mine["request_id"] == created["request_id"]
    assert mine["status"] == "PENDING"


@pytest.mark.asyncio
async def test_approval_opens_the_services_in_one_transaction(client, db_pool):
    token = await _register_and_login(client, "lr_ok_user")
    created = await _pending_request(client, token)
    admin = await _admin_token(client, db_pool, "lr_ok_admin")

    decided = await client.post(
        f"/api/v1/admin/resident-link-requests/{created['request_id']}/decision",
        headers={"Authorization": f"Bearer {admin}"},
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text

    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'lr_ok_user'")
    link = await db_pool.fetchrow(
        "SELECT resident_id, verification_status FROM user_resident_links WHERE user_id = $1", user_id
    )
    assert link is not None, "duyệt xong mà không có liên kết"
    assert link["verification_status"] == "VERIFIED"

    resident = await db_pool.fetchrow(
        "SELECT apartment_code FROM residents WHERE resident_id = $1", link["resident_id"]
    )
    assert resident["apartment_code"] == APARTMENT["apartment_code"]

    # Quyền phải mở ngay ở đường đọc thật, không chỉ ở bảng.
    fresh = await _register_and_login(client, "lr_ok_user")
    me = (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh}"})).json()
    assert me["resident_verification_status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_rejection_leaves_the_account_without_access(client, db_pool):
    token = await _register_and_login(client, "lr_no_user")
    created = await _pending_request(client, token)
    admin = await _admin_token(client, db_pool, "lr_no_admin")

    await client.post(
        f"/api/v1/admin/resident-link-requests/{created['request_id']}/decision",
        headers={"Authorization": f"Bearer {admin}"},
        json={"decision": "reject"},
    )

    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'lr_no_user'")
    link = await db_pool.fetchval("SELECT 1 FROM user_resident_links WHERE user_id = $1", user_id)
    assert link is None, "từ chối mà vẫn tạo liên kết"

    fresh = await _register_and_login(client, "lr_no_user")
    me = (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh}"})).json()
    assert me["resident_verification_status"] != "VERIFIED"


# ---------------------------------------------------------------------------
# Ranh giới tin cậy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"resident_id": "RES-001"},
        {"verification_status": "VERIFIED"},
        {"user_id": "00000000-0000-4000-8000-000000000001"},
        {"status": "APPROVED"},
    ],
)
async def test_a_customer_cannot_smuggle_authority_fields(client, db_pool, extra):
    """Gửi kèm `resident_id` hay `VERIFIED` phải là 422, không phải bị bỏ qua.

    Bỏ qua im lặng cũng an toàn hôm nay, nhưng nó không nói cho ai biết rằng có
    người đang thử — và ngày mai một refactor vô tình đọc tới field đó.
    """
    token = await _register_and_login(client, f"lr_smuggle_{list(extra)[0]}")
    response = await client.post(
        "/api/v1/auth/resident-link-requests",
        headers={"Authorization": f"Bearer {token}"},
        json={**APARTMENT, **extra},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_a_customer_cannot_decide_their_own_request(client, db_pool):
    token = await _register_and_login(client, "lr_self_user")
    created = await _pending_request(client, token)

    response = await client.post(
        f"/api/v1/admin/resident-link-requests/{created['request_id']}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "approve"},
    )
    assert response.status_code in {401, 403}, response.text

    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'lr_self_user'")
    assert await db_pool.fetchval("SELECT 1 FROM user_resident_links WHERE user_id = $1", user_id) is None


@pytest.mark.asyncio
async def test_a_customer_cannot_read_the_pending_queue(client, db_pool):
    token = await _register_and_login(client, "lr_queue_user")
    response = await client.get("/api/v1/admin/resident-link-requests", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code in {401, 403}


@pytest.mark.asyncio
async def test_one_pending_request_per_account(client, db_pool):
    """Bấm gửi mười lần không được tạo mười dòng chờ duyệt giống hệt nhau."""
    token = await _register_and_login(client, "lr_dup_user")
    await _pending_request(client, token)

    again = await client.post(
        "/api/v1/auth/resident-link-requests",
        headers={"Authorization": f"Bearer {token}"},
        json=APARTMENT,
    )
    assert again.status_code == 409, again.text

    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'lr_dup_user'")
    count = await db_pool.fetchval(
        "SELECT count(*) FROM resident_link_requests WHERE user_id = $1 AND status = 'PENDING'", user_id
    )
    assert count == 1


@pytest.mark.asyncio
async def test_a_second_approval_is_refused_not_repeated(client, db_pool):
    """Hai admin bấm cùng lúc: người đến sau nhận 409, không duyệt lại."""
    token = await _register_and_login(client, "lr_twice_user")
    created = await _pending_request(client, token)
    admin = await _admin_token(client, db_pool, "lr_twice_admin")
    headers = {"Authorization": f"Bearer {admin}"}

    first = await client.post(
        f"/api/v1/admin/resident-link-requests/{created['request_id']}/decision",
        headers=headers,
        json={"decision": "approve"},
    )
    second = await client.post(
        f"/api/v1/admin/resident-link-requests/{created['request_id']}/decision",
        headers=headers,
        json={"decision": "approve"},
    )
    assert first.status_code == 200
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
async def test_the_queue_masks_names_and_hides_internal_ids(client, db_pool):
    token = await _register_and_login(client, "lr_mask_user")
    await _pending_request(client, token)
    admin = await _admin_token(client, db_pool, "lr_mask_admin")

    raw = (await client.get("/api/v1/admin/resident-link-requests", headers={"Authorization": f"Bearer {admin}"})).text

    assert "Nguyen Van Canary" not in raw, "danh sách hiện tên đầy đủ"
    assert "N***** V** C*****" in raw, "tên phải được mask theo từng từ"
    assert "resident_id" not in raw


@pytest.mark.asyncio
async def test_the_decision_body_carries_only_the_decision(client, db_pool):
    """Nhận `user_id` từ body cho phép duyệt yêu cầu này, mở quyền cho người khác."""
    token = await _register_and_login(client, "lr_body_user")
    created = await _pending_request(client, token)
    admin = await _admin_token(client, db_pool, "lr_body_admin")

    response = await client.post(
        f"/api/v1/admin/resident-link-requests/{created['request_id']}/decision",
        headers={"Authorization": f"Bearer {admin}"},
        json={"decision": "approve", "user_id": "00000000-0000-4000-8000-000000000009"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_an_already_linked_account_cannot_ask_for_another_apartment(client, db_pool):
    token = await _register_and_login(client, "lr_linked_user")
    created = await _pending_request(client, token)
    admin = await _admin_token(client, db_pool, "lr_linked_admin")
    await client.post(
        f"/api/v1/admin/resident-link-requests/{created['request_id']}/decision",
        headers={"Authorization": f"Bearer {admin}"},
        json={"decision": "approve"},
    )

    again = await client.post(
        "/api/v1/auth/resident-link-requests",
        headers={"Authorization": f"Bearer {token}"},
        json={**APARTMENT, "apartment_code": "L-9999"},
    )
    assert again.status_code == 409, again.text


@pytest.mark.asyncio
async def test_an_unknown_request_id_does_not_confirm_or_deny(client, db_pool):
    """404 không được dùng để dò xem một mã yêu cầu có thật hay không."""
    admin = await _admin_token(client, db_pool, "lr_probe_admin")
    response = await client.post(
        "/api/v1/admin/resident-link-requests/00000000-0000-4000-8000-0000000000ff/decision",
        headers={"Authorization": f"Bearer {admin}"},
        json={"decision": "approve"},
    )
    assert response.status_code == 409
    assert "không tồn tại" not in response.text.lower()
