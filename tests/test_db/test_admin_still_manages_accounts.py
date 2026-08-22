"""Admin read-only với NGHIỆP VỤ, vẫn toàn quyền với TÀI KHOẢN.

Ranh giới dễ trượt theo cả hai hướng. Siết quá tay thì admin không còn phân vai
được cho ai — kể cả không phong nổi một provider, tức là hệ thống tự khoá chính
mình. Nới ra thì "quản trị tài khoản" lại thành một đường vòng tới quyền cư dân.

Contract:

    admin    role/status của tài khoản         ĐƯỢC
    admin    quyết định nghiệp vụ, resident link  KHÔNG
    provider quyết định verification/dịch vụ    ĐƯỢC
    customer xác nhận khoản tiền của mình       ĐƯỢC
"""

from __future__ import annotations

import pytest

from tests.test_db.conftest import _register_and_login


async def _user(client, db_pool, username, role=None):
    await _register_and_login(client, username)
    if role:
        await db_pool.execute("UPDATE users SET role=$2 WHERE username=$1", username, role)
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", username)
    return token, str(uid)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _role_path(uid):
    return f"/api/v1/admin/users/{uid}/role"


@pytest.mark.asyncio
async def test_an_anonymous_caller_cannot_change_a_role(client, db_pool):
    _, uid = await _user(client, db_pool, "qt_muc_tieu_an_danh")
    assert (await client.patch(_role_path(uid), json={"role": "admin"})).status_code == 401


@pytest.mark.parametrize("vai", ["customer", "provider"], ids=["khách", "đơn-vị"])
@pytest.mark.asyncio
async def test_only_an_admin_changes_roles(client, db_pool, vai):
    """Provider quyết định dịch vụ, không quyết định ai là ai trong hệ thống."""
    token, _ = await _user(client, db_pool, f"qt_{vai}", role=None if vai == "customer" else vai)
    _, uid = await _user(client, db_pool, f"qt_muc_tieu_{vai}")

    response = await client.patch(_role_path(uid), json={"role": "admin"}, headers=_auth(token))

    assert response.status_code == 403, response.status_code
    assert await db_pool.fetchval("SELECT role FROM users WHERE id=$1::uuid", uid) == "customer"


@pytest.mark.asyncio
async def test_an_admin_promotes_exactly_one_account_and_nothing_else(client, db_pool):
    admin, _ = await _user(client, db_pool, "qt_admin", role="admin")
    _, muc_tieu = await _user(client, db_pool, "qt_duoc_phong")
    _, nguoi_khac = await _user(client, db_pool, "qt_nguoi_khac")

    response = await client.patch(_role_path(muc_tieu), json={"role": "provider"}, headers=_auth(admin))

    assert response.status_code == 200, response.text
    assert await db_pool.fetchval("SELECT role FROM users WHERE id=$1::uuid", muc_tieu) == "provider"
    # Đúng MỘT tài khoản đổi.
    assert await db_pool.fetchval("SELECT role FROM users WHERE id=$1::uuid", nguoi_khac) == "customer"
    # Và không có tác dụng phụ nào sang phía nghiệp vụ.
    assert await db_pool.fetchval("SELECT count(*) FROM user_resident_links") == 0
    assert await db_pool.fetchval("SELECT count(*) FROM workflows") == 0
    assert await db_pool.fetchval("SELECT count(*) FROM service_approvals") == 0


@pytest.mark.asyncio
async def test_promoting_someone_does_not_hand_them_resident_rights(client, db_pool):
    """Phong provider KHÔNG phải cách vòng qua `/verification-records`."""
    admin, _ = await _user(client, db_pool, "qt_admin_2", role="admin")
    _, muc_tieu = await _user(client, db_pool, "qt_duoc_phong_2")

    await client.patch(_role_path(muc_tieu), json={"role": "provider"}, headers=_auth(admin))

    assert await db_pool.fetchval("SELECT count(*) FROM user_resident_links WHERE user_id=$1::uuid", muc_tieu) == 0


@pytest.mark.asyncio
async def test_the_role_body_carries_only_the_role(client, db_pool):
    """Thêm trường lạ vào body là cách kinh điển để nhét một quyền không ai duyệt."""
    admin, _ = await _user(client, db_pool, "qt_admin_3", role="admin")
    _, muc_tieu = await _user(client, db_pool, "qt_muc_tieu_3")

    response = await client.patch(
        _role_path(muc_tieu),
        json={"role": "provider", "resident_id": "RES-001", "verification_status": "VERIFIED"},
        headers=_auth(admin),
    )

    assert response.status_code == 422, response.status_code
    assert await db_pool.fetchval("SELECT role FROM users WHERE id=$1::uuid", muc_tieu) == "customer"


@pytest.mark.asyncio
async def test_an_unsupported_role_is_refused(client, db_pool):
    admin, _ = await _user(client, db_pool, "qt_admin_4", role="admin")
    _, muc_tieu = await _user(client, db_pool, "qt_muc_tieu_4")

    response = await client.patch(_role_path(muc_tieu), json={"role": "superadmin"}, headers=_auth(admin))

    assert response.status_code == 422
    assert await db_pool.fetchval("SELECT role FROM users WHERE id=$1::uuid", muc_tieu) == "customer"


@pytest.mark.asyncio
async def test_an_admin_can_archive_and_restore_an_account(client, db_pool):
    admin, _ = await _user(client, db_pool, "qt_admin_5", role="admin")
    _, muc_tieu = await _user(client, db_pool, "qt_muc_tieu_5")
    path = f"/api/v1/admin/users/{muc_tieu}/status"

    khoa = await client.patch(path, json={"is_archived": True}, headers=_auth(admin))
    assert khoa.status_code == 200, khoa.text
    assert await db_pool.fetchval("SELECT archived_at IS NOT NULL FROM users WHERE id=$1::uuid", muc_tieu)

    mo = await client.patch(path, json={"is_archived": False}, headers=_auth(admin))
    assert mo.status_code == 200, mo.text
    assert not await db_pool.fetchval("SELECT archived_at IS NOT NULL FROM users WHERE id=$1::uuid", muc_tieu)


@pytest.mark.asyncio
async def test_nothing_here_can_decide_a_verification(client, db_pool):
    """Kiểm chéo: giữ quyền quản trị tài khoản KHÔNG mở lại cổng quyết định."""
    import uuid as _uuid

    admin, _ = await _user(client, db_pool, "qt_admin_6", role="admin")
    _, muc_tieu = await _user(client, db_pool, "qt_muc_tieu_6")
    await client.patch(_role_path(muc_tieu), json={"role": "provider"}, headers=_auth(admin))

    quyet_dinh = await client.post(
        f"/api/v1/verification-records/{_uuid.uuid4()}/decide",
        json={"decision": "approve"},
        headers=_auth(admin),
    )
    assert quyet_dinh.status_code == 403
