"""`/auth/me` và `/capabilities` phải phản ánh quyền cư dân THẬT."""

from __future__ import annotations

import json

import pytest

from tests.test_db.conftest import _register_and_login


async def _link(db_pool, username: str, status: str, resident_id: str = "RES-CTX") -> None:
    from src.db.resident_link_repository import VerificationStatus, upsert_link

    await db_pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
        "VALUES ($1, 'Cư dân Ngữ Cảnh', 'C-1502', 'Vinhomes Ocean Park') ON CONFLICT DO NOTHING",
        resident_id,
    )
    # Tầng app chuẩn hoá username về lowercase khi đăng ký.
    user_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username.lower()))
    assert user_id != "None", f"chưa đăng ký được {username}"
    await upsert_link(db_pool, user_id=user_id, resident_id=resident_id, verification_status=VerificationStatus(status))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [(None, "NOT_LINKED"), ("PENDING", "PENDING"), ("REJECTED", "REJECTED"), ("VERIFIED", "VERIFIED")],
)
async def test_me_reports_the_real_link_status(client, db_pool, status, expected):
    username = f"ctx_me_{status}"
    token = await _register_and_login(client, username)
    if status is not None:
        await _link(db_pool, username, status)

    body = (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})).json()

    assert body["resident_verification_status"] == expected
    assert body["role"] == "customer"


@pytest.mark.asyncio
async def test_me_never_exposes_the_internal_resident_id(client, db_pool):
    """UI không cần mã nội bộ, và mỗi định danh gửi ra là một định danh gửi lại được."""
    token = await _register_and_login(client, "ctx_khong_lo_id")
    await _link(db_pool, "ctx_khong_lo_id", "VERIFIED")

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert "resident_id" not in response.json()
    assert "RES-CTX" not in response.text


@pytest.mark.asyncio
async def test_apartment_appears_only_after_verification(client, db_pool):
    token = await _register_and_login(client, "ctx_can_ho")
    await _link(db_pool, "ctx_can_ho", "PENDING")
    headers = {"Authorization": f"Bearer {token}"}

    pending = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert pending["apartment_code"] is None
    assert pending["residential_area"] is None

    await _link(db_pool, "ctx_can_ho", "VERIFIED")
    verified = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert verified["apartment_code"] == "C-1502"
    assert verified["residential_area"] == "Vinhomes Ocean Park"


@pytest.mark.asyncio
async def test_capabilities_requires_a_token(client):
    """`available` phụ thuộc quyền, nên trả cho người ẩn danh là vô nghĩa."""
    assert (await client.get("/api/v1/capabilities")).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,resident_open", [(None, False), ("PENDING", False), ("REJECTED", False), ("VERIFIED", True)]
)
async def test_capabilities_follow_the_real_link_status(client, db_pool, status, resident_open):
    username = f"ctx_cap_{status}"
    token = await _register_and_login(client, username)
    if status is not None:
        await _link(db_pool, username, status)

    items = (await client.get("/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"})).json()[
        "capabilities"
    ]

    public = [i for i in items if not i["requires_resident"]]
    resident = [i for i in items if i["requires_resident"]]

    assert public and resident, "cả hai nhóm phải được liệt kê"
    assert all(i["available"] for i in public), "dịch vụ public luôn mở"
    assert all(i["available"] is resident_open for i in resident)
    if not resident_open:
        # Khoá phải kèm lý do đọc được, không phải chỉ tắt đi.
        assert all(i["blocked_reason"] for i in resident)


@pytest.mark.asyncio
async def test_maintenance_and_moving_are_both_reachable_from_the_service_list(client, db_pool):
    """Bảo trì và chuyển nhà đều phải tới được từ danh sách dịch vụ.

    Coverage này trước nằm ở `static/demo.html` — trang demo một file đã bị xoá.
    Nó chuyển về đây vì `/capabilities` mới là nguồn danh sách: React dựng màn
    hình từ endpoint này, nên đọc thiếu ở đây thì giao diện cũng thiếu.

    Kiểm bằng NHÃN nghiệp vụ, không bằng tên tool: tên tool không được xuất hiện
    trước mặt người dùng.
    """
    username = "ctx_cap_services"
    token = await _register_and_login(client, username)
    await _link(db_pool, username, "VERIFIED")

    items = (await client.get("/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"})).json()[
        "capabilities"
    ]
    names = {i["name"] for i in items}

    assert "Báo bảo trì / sửa chữa" in names
    assert "Đặt lịch chuyển nhà" in names

    raw = json.dumps(items, ensure_ascii=False)
    for internal in ("create_maintenance_request", "schedule_move", "register_resident"):
        assert internal not in raw, f"danh sách dịch vụ lộ tên nội bộ {internal!r}"

    # Liên kết hồ sơ cư dân KHÔNG phải việc của Agent, nên nó không được nằm
    # trong danh sách dịch vụ người dùng bấm được.
    assert "Đăng ký cư dân" not in names


@pytest.mark.asyncio
async def test_a_blocked_capability_is_listed_not_hidden(client, db_pool):
    """Ẩn hẳn khiến người dùng không biết dịch vụ tồn tại, cũng không biết cách mở."""
    token = await _register_and_login(client, "ctx_khong_an")

    items = (await client.get("/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"})).json()[
        "capabilities"
    ]

    assert any(i["requires_resident"] and not i["available"] for i in items)


@pytest.mark.asyncio
async def test_an_admin_without_a_link_gets_no_resident_capability(client, db_pool):
    """Role và liên kết căn hộ là hai trục độc lập."""
    token = await _register_and_login(client, "ctx_admin")
    await db_pool.execute("UPDATE users SET role = 'admin' WHERE username = 'ctx_admin'")

    items = (await client.get("/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"})).json()[
        "capabilities"
    ]

    assert not any(i["requires_resident"] and i["available"] for i in items)
