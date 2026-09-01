"""Quyền cư dân chỉ mở được bằng MỘT đường: provider duyệt hồ sơ xác minh.

`user_resident_links.verification_status = 'VERIFIED'` là công tắc mở toàn bộ
dịch vụ cư dân — đăng ký xe, chỗ đỗ, bảo trì, chuyển nhà. Nếu công tắc ấy có
hai đường bật thì đường yếu hơn là đường thật, vì kẻ tấn công và người vội đều
chọn đường dễ.

Trước file này có BA đường:

    provider  POST /verification-records/{id}/decide     ← đường canonical
    admin     POST /admin/resident-links/{user_id}       ← đặt thẳng VERIFIED
    admin     POST /admin/resident-link-requests/{id}/decision

Hai đường sau bỏ qua provider hoàn toàn: không hồ sơ, không ảnh chứng minh,
không ai bên ngoài xác nhận. Chúng biến "xác minh quyền sở hữu căn hộ" thành
một biểu mẫu nội bộ — và mô hình quyền cư dân chỉ còn là một lời hứa.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_db.conftest import _register_and_login

VERIFICATION = "/api/v1/verification-records"
BYPASS = "/api/v1/admin/resident-links/{}"
LEGACY_QUEUE = "/api/v1/admin/resident-link-requests"
LEGACY_CREATE = "/api/v1/auth/resident-link-requests"


async def _user(client, db_pool, username, role=None):
    await _register_and_login(client, username)
    if role:
        await db_pool.execute("UPDATE users SET role=$2 WHERE username=$1", username, role)
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", username)
    return token, str(uid)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


async def _links(db_pool, user_id):
    return [
        dict(r)
        for r in await db_pool.fetch(
            "SELECT resident_id, verification_status FROM user_resident_links WHERE user_id=$1::uuid",
            user_id,
        )
    ]


# --- các đường vòng phải biến mất -------------------------------------------


@pytest.mark.asyncio
async def test_an_admin_cannot_hand_out_resident_rights_directly(client, db_pool):
    """Đường ngắn nhất tới VERIFIED: một request, không hồ sơ, không provider."""
    admin, _ = await _user(client, db_pool, "cq_admin_bypass", role="admin")
    _, target = await _user(client, db_pool, "cq_khach_bypass")
    await db_pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
        "VALUES ('RES-CQ1','Nguyen Van Bypass','CQ01','Toà S1')"
    )
    truoc = await _links(db_pool, target)
    assert truoc == [], "fixture đã có link sẵn — test sẽ không chứng minh được gì"

    response = await client.post(
        BYPASS.format(target),
        json={"resident_id": "RES-CQ1", "verification_status": "VERIFIED"},
        headers=_auth(admin),
    )

    assert response.status_code in (404, 405), f"admin vẫn tự cấp được quyền ({response.status_code})"
    assert await _links(db_pool, target) == [], "quyền cư dân được mở dù request bị từ chối"
    # Và không có hồ sơ nào được provider duyệt để biện minh cho nó.
    assert await db_pool.fetchval("SELECT count(*) FROM verification_records") == 0


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", LEGACY_QUEUE),
        ("post", f"{LEGACY_QUEUE}/{uuid.uuid4()}/decision"),
        ("post", LEGACY_CREATE),
        ("get", f"{LEGACY_CREATE}/me"),
    ],
    ids=["admin-queue", "admin-decision", "customer-create", "customer-mine"],
)
@pytest.mark.asyncio
async def test_the_legacy_resident_link_routes_are_gone(client, db_pool, method, path):
    """Cả hai đầu của đường cũ — chỗ nộp và chỗ duyệt — đều phải đóng.

    Đóng một đầu thôi thì hồ sơ vẫn nộp được vào một hàng đợi không ai duyệt,
    hoặc tệ hơn, vẫn duyệt được những hồ sơ cũ còn sót.
    """
    admin, _ = await _user(client, db_pool, f"cq_legacy_{abs(hash(path)) % 10000}", role="admin")
    response = await client.request(method.upper(), path, headers=_auth(admin))
    assert response.status_code in (404, 405), f"{method.upper()} {path} → {response.status_code}"


@pytest.mark.asyncio
async def test_a_customer_cannot_declare_themselves_verified(client, db_pool):
    """Người dùng tự khẳng định mình sở hữu căn hộ là hết chuyện."""
    token, uid = await _user(client, db_pool, "cq_khach_tu_phong")

    for path, body in (
        (BYPASS.format(uid), {"resident_id": "RES-CQ9", "verification_status": "VERIFIED"}),
        (LEGACY_CREATE, {"apartment_code": "CQ02", "residential_area": "Toà S1", "full_name": "X"}),
    ):
        response = await client.post(path, json=body, headers=_auth(token))
        assert response.status_code in (403, 404, 405), f"{path} → {response.status_code}"
    assert await _links(db_pool, uid) == []


# --- đường canonical phải THẬT SỰ chạy --------------------------------------


@pytest.mark.asyncio
async def test_the_admin_cannot_touch_the_provider_queue(client, db_pool):
    """Đóng đường vòng mà mở cửa sau thì không đóng được gì."""
    admin, _ = await _user(client, db_pool, "cq_admin_hang_doi", role="admin")

    assert (await client.get(VERIFICATION, headers=_auth(admin))).status_code == 403
    assert (
        await client.post(f"{VERIFICATION}/{uuid.uuid4()}/decide", json={"decision": "approve"}, headers=_auth(admin))
    ).status_code == 403


@pytest.mark.asyncio
async def test_a_customer_cannot_see_or_decide_the_provider_queue(client, db_pool):
    token, _ = await _user(client, db_pool, "cq_khach_hang_doi")
    assert (await client.get(VERIFICATION, headers=_auth(token))).status_code == 403
    assert (
        await client.post(f"{VERIFICATION}/{uuid.uuid4()}/decide", json={"decision": "approve"}, headers=_auth(token))
    ).status_code == 403


@pytest.mark.asyncio
async def test_the_provider_queue_is_the_only_door_that_opens(client, db_pool):
    """Kiểm dương: provider PHẢI mở được. Không có nó, mọi 403 ở trên vô nghĩa."""
    provider, _ = await _user(client, db_pool, "cq_don_vi", role="provider")
    assert (await client.get(VERIFICATION, headers=_auth(provider))).status_code not in (401, 403)
