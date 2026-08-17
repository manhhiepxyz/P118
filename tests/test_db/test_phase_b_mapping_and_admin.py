"""Liên kết cư dân: chỉ admin ghi được, và chỉ VERIFIED mở quyền."""

from __future__ import annotations

import pytest

from tests.test_db.conftest import _register_and_login


async def _make_admin(db_pool, username: str) -> None:
    await db_pool.execute("UPDATE users SET role = 'admin' WHERE username = $1", username)


async def _seed_resident(db_pool, resident_id: str = "RES-PHASEB") -> str:
    await db_pool.execute(
        """
        INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)
        VALUES ($1, 'Nguyễn Văn Cư Dân', 'B-0808', 'Vinhomes Ocean Park')
        ON CONFLICT (resident_id) DO NOTHING
        """,
        resident_id,
    )
    return resident_id


async def _user_id(db_pool, username: str) -> str:
    # Tầng app chuẩn hoá username về lowercase khi đăng ký. Query nguyên chữ
    # hoa sẽ không thấy row, và test thất bại ở một chỗ chẳng liên quan gì.
    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username.lower())
    assert user_id is not None, f"chưa đăng ký được {username}"
    return str(user_id)


@pytest.mark.asyncio
async def test_a_customer_cannot_grant_itself_a_verified_link(client, db_pool):
    """Tự cấp quyền cho mình phải là 403, không phải một biểu mẫu."""
    token = await _register_and_login(client, "kh_tu_cap")
    resident_id = await _seed_resident(db_pool)
    user_id = await _user_id(db_pool, "kh_tu_cap")

    response = await client.post(
        f"/api/v1/admin/resident-links/{user_id}",
        json={"resident_id": resident_id, "verification_status": "VERIFIED"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403, response.text
    assert await db_pool.fetchval("SELECT 1 FROM user_resident_links WHERE user_id = $1::uuid", user_id) is None


@pytest.mark.asyncio
async def test_the_admin_endpoint_rejects_an_anonymous_caller(client, db_pool):
    await _register_and_login(client, "kh_an_danh")
    user_id = await _user_id(db_pool, "kh_an_danh")

    response = await client.post(
        f"/api/v1/admin/resident-links/{user_id}",
        json={"resident_id": "RES-PHASEB", "verification_status": "VERIFIED"},
    )

    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_an_admin_can_grant_and_then_revoke_a_link(client, db_pool):
    """`verified_at` chỉ tồn tại khi VERIFIED.

    Một mốc "đã xác minh" còn sót lại trên liên kết đã bị từ chối là bằng chứng
    sai trong audit trail — người đọc sau này sẽ tin vào nó.
    """
    await _register_and_login(client, "kh_duoc_cap")
    admin_token = await _register_and_login(client, "quan_tri_vien")
    await _make_admin(db_pool, "quan_tri_vien")
    resident_id = await _seed_resident(db_pool)
    user_id = await _user_id(db_pool, "kh_duoc_cap")
    headers = {"Authorization": f"Bearer {admin_token}"}

    granted = await client.post(
        f"/api/v1/admin/resident-links/{user_id}",
        json={"resident_id": resident_id, "verification_status": "VERIFIED"},
        headers=headers,
    )
    assert granted.status_code == 200, granted.text
    row = await db_pool.fetchrow(
        "SELECT verification_status, verified_at FROM user_resident_links WHERE user_id = $1::uuid", user_id
    )
    assert row["verification_status"] == "VERIFIED"
    assert row["verified_at"] is not None

    revoked = await client.post(
        f"/api/v1/admin/resident-links/{user_id}",
        json={"resident_id": resident_id, "verification_status": "REJECTED"},
        headers=headers,
    )
    assert revoked.status_code == 200, revoked.text
    row = await db_pool.fetchrow(
        "SELECT verification_status, verified_at FROM user_resident_links WHERE user_id = $1::uuid", user_id
    )
    assert row["verification_status"] == "REJECTED"
    assert row["verified_at"] is None, "verified_at phải bị bỏ khi thu hồi"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["apartment_code", "residential_area", "verified_at", "user_id"])
async def test_the_admin_endpoint_refuses_extra_identity_fields(client, db_pool, field):
    """Dữ liệu căn hộ đọc từ `residents`, không nhận từ body.

    Nhận từ body là tạo nguồn sự thật thứ hai về việc ai ở căn nào; hai nguồn
    thì sớm muộn cũng lệch, và lúc đó không biết tin cái nào.
    """
    admin_token = await _register_and_login(client, f"qtv_{field}")
    await _make_admin(db_pool, f"qtv_{field}")
    await _register_and_login(client, f"kh_{field}")
    user_id = await _user_id(db_pool, f"kh_{field}")
    resident_id = await _seed_resident(db_pool)

    response = await client.post(
        f"/api/v1/admin/resident-links/{user_id}",
        json={"resident_id": resident_id, "verification_status": "VERIFIED", field: "gia-tri-bat-ky"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_an_unknown_resident_is_refused_without_revealing_which_id_is_missing(client, db_pool):
    admin_token = await _register_and_login(client, "qtv_khong_ro")
    await _make_admin(db_pool, "qtv_khong_ro")
    await _register_and_login(client, "kh_khong_ro")
    user_id = await _user_id(db_pool, "kh_khong_ro")

    response = await client.post(
        f"/api/v1/admin/resident-links/{user_id}",
        json={"resident_id": "RES-KHONG-TON-TAI", "verification_status": "VERIFIED"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404, response.text
    body = response.text
    for leaked in ("RES-KHONG-TON-TAI", user_id, "residents", "users"):
        assert leaked not in body, f"lỗi rò {leaked!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [(None, "prospect"), ("PENDING", "prospect"), ("REJECTED", "prospect"), ("VERIFIED", "resident")],
)
async def test_only_a_verified_link_grants_resident_access(client, db_pool, status, expected):
    """PENDING và REJECTED đều fail-closed, giống hệt chưa có liên kết.

    Ba trạng thái đó khác nhau về vận hành nhưng giống nhau về quyền; gộp lại ở
    một chỗ khiến không nhánh nào vô tình mở quyền cho hai cái đầu.
    """
    from src.db.resident_link_repository import VerificationStatus, get_verified_identity, upsert_link

    username = f"kh_status_{status}"
    await _register_and_login(client, username)
    user_id = await _user_id(db_pool, username)
    resident_id = await _seed_resident(db_pool)

    if status is not None:
        await upsert_link(
            db_pool, user_id=user_id, resident_id=resident_id, verification_status=VerificationStatus(status)
        )

    identity = await get_verified_identity(db_pool, user_id)

    assert (identity is not None) == (expected == "resident")
    if identity is not None:
        assert identity.resident_id == resident_id
        assert identity.apartment_code == "B-0808"


@pytest.mark.asyncio
async def test_an_admin_without_a_link_is_still_a_prospect(client, db_pool):
    """Role và quyền cư dân là hai trục độc lập.

    Quản trị viên là người vận hành hệ thống, không phải chủ căn hộ. Gộp lại
    nghĩa là một tài khoản vận hành đặt được chỗ đỗ xe dưới danh nghĩa cư dân.
    """
    from src.db.resident_link_repository import get_verified_identity

    await _register_and_login(client, "qtv_khong_lien_ket")
    await _make_admin(db_pool, "qtv_khong_lien_ket")
    user_id = await _user_id(db_pool, "qtv_khong_lien_ket")

    assert await get_verified_identity(db_pool, user_id) is None
