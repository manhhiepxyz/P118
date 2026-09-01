"""Đường CŨ để xin liên kết căn hộ đã đóng. Bất biến của nó thì không mất.

File này từng kiểm bốn route:

    POST /auth/resident-link-requests            khách nộp đơn
    GET  /auth/resident-link-requests/me         khách xem trạng thái
    GET  /admin/resident-link-requests           admin xem hàng đợi
    POST /admin/resident-link-requests/{id}/decision   admin quyết định

Cả bốn đã bị xoá. Vấn đề không nằm ở chỗ chúng thiếu kiểm tra — chúng có khá
nhiều — mà ở chỗ NGƯỜI QUYẾT ĐỊNH sai: admin của hệ thống không phải bên xác
minh quyền sở hữu căn hộ. Đường canonical là `/verification-records`, nơi hồ sơ
có ảnh chứng minh và ĐƠN VỊ CUNG CẤP là bên ký.

Phân loại các bất biến cũ và chỗ chúng sống tiếp:

    khách không tự quyết định đơn của mình        → ở đây (route đã đóng) +
                                                    `test_only_a_provider_opens_resident_rights`
    khách không đọc được hàng đợi nội bộ          → như trên
    khách không nhét được trường quyền vào body   → không còn body nào để nhét
    duyệt hai lần không tạo dữ liệu thứ hai       → `system_docker.py`, vì nó
                                                    cần Ownership provider thật
    duyệt mở quyền trong MỘT transaction          → như trên
    từ chối không mở quyền                        → như trên
    hàng đợi che tên và giấu ID nội bộ            → `/admin/requests` + queue
                                                    provider của `/review`

Ba bất biến cuối KHÔNG kiểm được ở tầng ASGI này: `/verification-records` gọi
Ownership provider qua HTTP, và trong pytest nó trỏ tới một service đọc database
KHÁC. Ghi ra đây thay vì giả vờ đã phủ.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_db.conftest import _register_and_login

CREATE = "/api/v1/auth/resident-link-requests"
MINE = "/api/v1/auth/resident-link-requests/me"
QUEUE = "/api/v1/admin/resident-link-requests"


async def _user(client, db_pool, username, role=None):
    await _register_and_login(client, username)
    if role:
        await db_pool.execute("UPDATE users SET role=$2 WHERE username=$1", username, role)
    return await _register_and_login(client, username)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


async def _link_count(db_pool) -> int:
    return await db_pool.fetchval("SELECT count(*) FROM user_resident_links")


@pytest.mark.parametrize("vai", ["customer", "admin", "provider"], ids=["khách", "admin", "đơn-vị"])
@pytest.mark.asyncio
async def test_nobody_can_open_the_legacy_link_request_flow(client, db_pool, vai):
    """Không vai nào còn nộp hay đọc được đơn theo đường cũ.

    Kể cả provider: họ quyết định ở `/verification-records`, không ở đây. Một
    đường thứ hai còn mở cho đúng một vai vẫn là một đường thứ hai.
    """
    token = await _user(client, db_pool, f"dl_{vai}", role=None if vai == "customer" else vai)
    truoc = await _link_count(db_pool)

    tao = await client.post(
        CREATE,
        json={"apartment_code": "DL01", "residential_area": "Toà S1", "full_name": "Nguyen Van Cu"},
        headers=_auth(token),
    )
    cua_toi = await client.get(MINE, headers=_auth(token))
    hang_doi = await client.get(QUEUE, headers=_auth(token))
    quyet_dinh = await client.post(
        f"{QUEUE}/{uuid.uuid4()}/decision", json={"decision": "approve"}, headers=_auth(token)
    )

    for ten, response in (
        ("tạo đơn", tao),
        ("xem đơn", cua_toi),
        ("hàng đợi", hang_doi),
        ("quyết định", quyet_dinh),
    ):
        assert response.status_code in (403, 404, 405), f"{vai} vẫn {ten} được ({response.status_code})"
    assert await _link_count(db_pool) == truoc, "quyền cư dân đổi qua một đường đã đóng"


@pytest.mark.asyncio
async def test_an_anonymous_caller_gets_nothing_from_the_legacy_flow(client, db_pool):
    for path in (CREATE, MINE, QUEUE):
        assert (await client.get(path)).status_code in (401, 404, 405), path


@pytest.mark.asyncio
async def test_the_legacy_table_is_unreachable_but_its_data_is_not_destroyed(client, db_pool):
    """Đóng đường HTTP, GIỮ dữ liệu.

    Xoá bảng cùng lúc với route là trộn hai việc: một việc có thể hoàn tác bằng
    một dòng route, một việc thì không. Đơn cũ vẫn phải đọc được để đối soát.
    """
    ton_tai = await db_pool.fetchval("SELECT to_regclass('resident_link_requests') IS NOT NULL")
    assert ton_tai, "bảng đơn cũ bị xoá mất — dữ liệu đối soát không còn"
