"""Bản đồ tài khoản ↔ cư dân: ai được ghi, và ai chỉ được đọc.

File này TỪNG kiểm `POST /admin/resident-links/{user_id}` — đường admin tự gán
liên kết cư dân. Route đó đã bị xoá: nó bật được công tắc `VERIFIED` mà không
cần hồ sơ nào và không ai bên ngoài xác nhận (bằng chứng đo được nằm trong ghi
chú ở `src/api/admin_routes.py`).

Các bất biến file này bảo vệ KHÔNG mất đi — chúng chuyển chỗ:

    khách không tự cấp quyền cho mình      → giữ ở đây, nay qua route đã đóng
    admin không tự cấp quyền               → giữ ở đây
    chỉ VERIFIED mới mở dịch vụ cư dân     → giữ nguyên, đọc thẳng database
    admin không có link vẫn là khách       → giữ nguyên
    đường CẤP quyền chạy được              → `tests/e2e/system_docker.py`,
                                             vì nó cần Ownership provider thật

Mapping vẫn đọc từ `user_resident_links`; chỉ đường HTTP ghi vào đó bị đóng.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from tests.test_db.conftest import _register_and_login

BYPASS = "/api/v1/admin/resident-links/{}"


async def _user(client, db_pool, username, role=None):
    await _register_and_login(client, username)
    if role:
        await db_pool.execute("UPDATE users SET role=$2 WHERE username=$1", username, role)
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", username)
    return token, str(uid)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


async def _seed_link(db_pool, user_id, status):
    """Mapping dựng THẲNG ở database — không đi qua endpoint bypass đã xoá.

    Fixture phải mô phỏng kết quả cuối của đường canonical, không mô phỏng một
    đường tắt: nếu test cần một cửa hậu để dàn dựng thì cửa hậu ấy vẫn còn.
    """
    rid = f"RES-{uuid.uuid4().hex[:6].upper()}"
    await db_pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
        "VALUES ($1,'Nguyen Van Map',$2,'Toà S1')",
        rid,
        f"MP{uuid.uuid4().hex[:4]}",
    )
    await db_pool.execute(
        "INSERT INTO user_resident_links (user_id, resident_id, verification_status, verified_at) "
        "VALUES ($1::uuid,$2,$3,$4)",
        user_id,
        rid,
        status,
        datetime.now(UTC) if status == "VERIFIED" else None,
    )
    return rid


@pytest.mark.asyncio
async def test_a_customer_cannot_grant_itself_a_verified_link(client, db_pool):
    token, uid = await _user(client, db_pool, "map_khach_tu_cap")

    response = await client.post(
        BYPASS.format(uid),
        json={"resident_id": "RES-001", "verification_status": "VERIFIED"},
        headers=_auth(token),
    )

    assert response.status_code in (403, 404, 405), response.status_code
    assert await db_pool.fetchval("SELECT count(*) FROM user_resident_links WHERE user_id=$1::uuid", uid) == 0


@pytest.mark.asyncio
async def test_an_admin_cannot_grant_a_link_either(client, db_pool):
    """Đây là thay đổi contract, không phải một test bị hỏng.

    Bản cũ tên là `test_an_admin_can_grant_and_then_revoke_a_link` và nó khẳng
    định điều ngược lại. Quyền xác minh quyền sở hữu căn hộ giờ thuộc về ĐƠN VỊ
    CUNG CẤP, qua `/verification-records` — nơi có hồ sơ và ảnh chứng minh.
    """
    admin, _ = await _user(client, db_pool, "map_admin_cap", role="admin")
    _, target = await _user(client, db_pool, "map_khach_bi_cap")

    response = await client.post(
        BYPASS.format(target),
        json={"resident_id": "RES-001", "verification_status": "VERIFIED"},
        headers=_auth(admin),
    )

    assert response.status_code in (404, 405), response.status_code
    assert await db_pool.fetchval("SELECT count(*) FROM user_resident_links WHERE user_id=$1::uuid", target) == 0


@pytest.mark.asyncio
async def test_the_removed_endpoint_is_not_an_anonymous_hole_either(client, db_pool):
    response = await client.post(
        BYPASS.format(uuid.uuid4()), json={"resident_id": "RES-001", "verification_status": "VERIFIED"}
    )
    assert response.status_code in (401, 404, 405), response.status_code


@pytest.mark.parametrize(
    "status,expected",
    [("VERIFIED", "VERIFIED"), ("PENDING", "PENDING"), ("REJECTED", "REJECTED")],
)
@pytest.mark.asyncio
async def test_only_a_verified_link_grants_resident_access(client, db_pool, status, expected):
    """Bất biến trung tâm, không đổi: chỉ VERIFIED mới mở dịch vụ cư dân."""
    token, uid = await _user(client, db_pool, f"map_trang_thai_{status.lower()}")
    await _seed_link(db_pool, uid, status)

    me = (await client.get("/api/v1/auth/me", headers=_auth(token))).json()

    assert me["resident_verification_status"] == expected
    if expected != "VERIFIED":
        # Không mở quyền, và cũng không phơi căn hộ của ai.
        assert me.get("apartment_code") in (None, "")


@pytest.mark.asyncio
async def test_an_admin_without_a_link_is_still_a_prospect(client, db_pool):
    """Vai trong hệ thống không thay cho quyền cư dân đã xác minh."""
    admin, _ = await _user(client, db_pool, "map_admin_khong_link", role="admin")

    me = (await client.get("/api/v1/auth/me", headers=_auth(admin))).json()

    assert me["role"] == "admin"
    assert me["resident_verification_status"] == "NOT_LINKED"


@pytest.mark.asyncio
async def test_the_resident_id_never_reaches_the_client(client, db_pool):
    """Định danh nội bộ không rời backend — nó là chìa khoá tra cứu hồ sơ cư dân."""
    token, uid = await _user(client, db_pool, "map_khong_ro_id")
    rid = await _seed_link(db_pool, uid, "VERIFIED")

    body = (await client.get("/api/v1/auth/me", headers=_auth(token))).text

    assert rid not in body
    assert "resident_id" not in body
