"""Chỉ ĐƠN VỊ CUNG CẤP được ra quyết định nghiệp vụ. Admin thì không.

Ba vai, ba việc khác nhau — và trước file này backend gộp hai trong ba:

    customer   tạo yêu cầu, bổ sung thông tin, xác nhận khoản tiền CỦA MÌNH
    provider   duyệt/từ chối dịch vụ. Đây là bên DUY NHẤT quyết định nghiệp vụ.
    admin      giám sát Agent và workflow. Nhận được thông báo, KHÔNG quyết định.

Vì sao tách admin ra khỏi quyền duyệt
-------------------------------------
Quyền duyệt là quyền NHÂN DANH một đơn vị cung cấp dịch vụ nhận việc. Admin của
hệ thống không phải đơn vị ấy: họ không có mặt bằng, không có đội bảo trì,
không có xe. Cho họ bấm Duyệt nghĩa là một quyết định thương mại được ký bởi
người không chịu trách nhiệm thực hiện nó — và trong log thì nó trông y hệt một
quyết định thật.

Nó cũng phá chính công cụ giám sát: nếu người giám sát tự tay giải quyết được
hàng đợi, con số "đang chờ đơn vị" không còn đo cái gì cả.

Ba nhóm route đều là cùng một quyết định ấy, nên cả ba phải chặn giống nhau:

    /verification-records   xác minh căn hộ / xe
    /viewing-approvals      lịch tham quan
    /service-approvals      sáu dịch vụ còn lại

Kiểm ROLE, không phải quyền sở hữu: người duyệt được quyết định TOÀN BỘ hàng
đợi chứ không riêng phần "của mình", nên đây không phải IDOR.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_db.conftest import _register_and_login

VERIFICATION = "/api/v1/verification-records"
VIEWING = "/api/v1/viewing-approvals"
SERVICE = "/api/v1/service-approvals"


async def _user(client, db_pool, username: str, role: str | None = None) -> tuple[str, str]:
    """Tài khoản có ROLE thật, và token cấp SAU khi role đã đổi.

    Thứ tự quan trọng: `require_roles` đọc role từ bản ghi user qua JWT, nên
    một token phát trước lúc promote sẽ kiểm ra vai cũ — và test sẽ đo nhầm
    một thứ không phải điều nó tuyên bố.
    """
    await _register_and_login(client, username)
    if role is not None:
        await db_pool.execute("UPDATE users SET role = $2 WHERE username = $1", username, role)
    token = await _register_and_login(client, username)
    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    return token, str(user_id)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- dàn dựng hồ sơ chờ duyệt THẬT ------------------------------------------


async def _awaiting_service(db_pool, owner_user_id: str) -> tuple[str, str]:
    workflow_id, task_id = str(uuid.uuid4()), "T1"
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid,'Báo bảo trì điều hoà','WAITING_APPROVAL',$2::uuid)",
        workflow_id,
        owner_user_id,
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
        "VALUES ($1::uuid,$2,'create_maintenance_request','WAITING_APPROVAL','[]'::jsonb)",
        workflow_id,
        task_id,
    )
    await db_pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, "
        "applicant_user_id) VALUES ($1::uuid,$2,'create_maintenance_request','Báo bảo trì / sửa chữa',"
        "'{}'::jsonb,'AWAITING',$3::uuid)",
        workflow_id,
        task_id,
        owner_user_id,
    )
    return workflow_id, task_id


async def _service_status(db_pool, workflow_id, task_id) -> tuple[str, str | None]:
    row = await db_pool.fetchrow(
        "SELECT status, decided_by FROM service_approvals WHERE workflow_id=$1::uuid AND task_id=$2",
        workflow_id,
        task_id,
    )
    return row["status"], row["decided_by"]


async def _awaiting_viewing(db_pool, owner_user_id: str) -> str:
    """Lịch tham quan chờ duyệt. `viewing_approvals` là KHUNG NHÌN trên
    `service_approvals` sau khi hai hàng đợi gộp — nên ghi vào bảng gốc."""
    workflow_id, task_id = str(uuid.uuid4()), "T1"
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid,'Đặt lịch tham quan','WAITING_APPROVAL',$2::uuid)",
        workflow_id,
        owner_user_id,
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data, depends_on) "
        "VALUES ($1::uuid,$2,'schedule_property_viewing','WAITING_APPROVAL',"
        '\'{"project_id":"PRJ-001","viewing_date":"2030-07-15","viewing_time":"09:30"}\'::jsonb,'
        "'[]'::jsonb)",
        workflow_id,
        task_id,
    )
    await db_pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, "
        "applicant_user_id) VALUES ($1::uuid,$2,'schedule_property_viewing','Lịch tham quan',"
        '\'{"project_id":"PRJ-001","viewing_date":"2030-07-15","viewing_time":"09:30"}\'::jsonb,'
        "'AWAITING',$3::uuid)",
        workflow_id,
        task_id,
        owner_user_id,
    )
    return workflow_id


# ---------------------------------------------------------------------------
# Không đăng nhập → 401 (cả ba nhóm)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [VERIFICATION, VIEWING, SERVICE],
    ids=["verification", "viewing", "service"],
)
@pytest.mark.asyncio
async def test_an_anonymous_caller_cannot_even_see_the_queue(client, db_pool, path):
    assert (await client.get(path)).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        f"{VERIFICATION}/{uuid.uuid4()}/decide",
        f"{VIEWING}/{uuid.uuid4()}/decide",
        f"{SERVICE}/{uuid.uuid4()}/T1/decide",
    ],
    ids=["verification", "viewing", "service"],
)
@pytest.mark.asyncio
async def test_an_anonymous_caller_cannot_decide(client, db_pool, path):
    assert (await client.post(path, json={"decision": "approve"})).status_code == 401


# ---------------------------------------------------------------------------
# Khách hàng → 403 (cả ba nhóm)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [VERIFICATION, VIEWING, SERVICE], ids=["verification", "viewing", "service"])
@pytest.mark.asyncio
async def test_a_customer_cannot_see_the_internal_queue(client, db_pool, path):
    token, _ = await _user(client, db_pool, f"vai_khach_{path.rsplit('/', 1)[-1][:8]}")
    assert (await client.get(path, headers=_auth(token))).status_code == 403


# ---------------------------------------------------------------------------
# Admin → 403. Đây là phần contract mà backend đang cấp thừa.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [VERIFICATION, VIEWING, SERVICE], ids=["verification", "viewing", "service"])
@pytest.mark.asyncio
async def test_an_admin_cannot_see_the_provider_queue(client, db_pool, path):
    """Admin giám sát bằng số liệu ở `/admin`, không bằng hàng đợi của đơn vị."""
    token, _ = await _user(client, db_pool, f"vai_admin_ds_{path.rsplit('/', 1)[-1][:8]}", role="admin")
    assert (await client.get(path, headers=_auth(token))).status_code == 403


@pytest.mark.asyncio
async def test_an_admin_decision_is_refused_and_changes_nothing_service(client, db_pool):
    admin_token, _ = await _user(client, db_pool, "vai_admin_qd_dv", role="admin")
    _, owner_id = await _user(client, db_pool, "vai_chu_dv")
    workflow_id, task_id = await _awaiting_service(db_pool, owner_id)

    response = await client.post(
        f"{SERVICE}/{workflow_id}/{task_id}/decide",
        json={"decision": "approve"},
        headers=_auth(admin_token),
    )

    assert response.status_code == 403, response.text
    status, decided_by = await _service_status(db_pool, workflow_id, task_id)
    assert (status, decided_by) == ("AWAITING", None), "quyết định bị từ chối nhưng dòng vẫn đổi"


@pytest.mark.asyncio
async def test_an_admin_decision_is_refused_and_changes_nothing_viewing(client, db_pool):
    admin_token, _ = await _user(client, db_pool, "vai_admin_qd_tq", role="admin")
    _, owner_id = await _user(client, db_pool, "vai_chu_tq")
    workflow_id = await _awaiting_viewing(db_pool, owner_id)

    response = await client.post(
        f"{VIEWING}/{workflow_id}/decide",
        json={"decision": "approve"},
        headers=_auth(admin_token),
    )

    assert response.status_code == 403, response.text
    status, decided_by = await _service_status(db_pool, workflow_id, "T1")
    assert (status, decided_by) == ("AWAITING", None)


@pytest.mark.asyncio
async def test_a_customer_decision_is_refused_and_changes_nothing(client, db_pool):
    token, owner_id = await _user(client, db_pool, "vai_khach_qd")
    workflow_id, task_id = await _awaiting_service(db_pool, owner_id)

    response = await client.post(
        f"{SERVICE}/{workflow_id}/{task_id}/decide",
        json={"decision": "approve"},
        headers=_auth(token),
    )

    assert response.status_code == 403, response.text
    assert await _service_status(db_pool, workflow_id, task_id) == ("AWAITING", None), (
        "người tạo yêu cầu tự duyệt được dịch vụ của chính mình"
    )


@pytest.mark.asyncio
async def test_a_refused_decision_is_403_not_a_404_about_a_missing_record(client, db_pool):
    """403 phải đến TRƯỚC khi route đi tra hồ sơ.

    404 ở đây là một câu trả lời sai về một câu hỏi sai: nó nói "không có hồ
    sơ" cho một người lẽ ra không được hỏi, và nó khiến test 403 xanh vì lý do
    khác hẳn — chỉ cần fixture quên seed là mọi khẳng định quyền mất hiệu lực.
    """
    admin_token, _ = await _user(client, db_pool, "vai_admin_404", role="admin")
    khong_ton_tai = uuid.uuid4()

    for path in (
        f"{VERIFICATION}/{khong_ton_tai}/decide",
        f"{VIEWING}/{khong_ton_tai}/decide",
        f"{SERVICE}/{khong_ton_tai}/T1/decide",
    ):
        response = await client.post(path, json={"decision": "approve"}, headers=_auth(admin_token))
        assert response.status_code == 403, f"{path} → {response.status_code}"


# ---------------------------------------------------------------------------
# Kiểm DƯƠNG: provider phải làm được. Thiếu phần này thì mọi 403 ở trên có thể
# đúng chỉ vì route hỏng.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_provider_can_open_all_three_queues(client, db_pool):
    token, _ = await _user(client, db_pool, "vai_don_vi_ds", role="provider")

    assert (await client.get(SERVICE, headers=_auth(token))).status_code == 200
    assert (await client.get(VIEWING, headers=_auth(token))).status_code == 200
    # `/verification-records` đi qua Ownership provider (dịch vụ NGOÀI). Cổng
    # quyền mở ra là điều kiểm được ở tầng này; provider có đang chạy hay không
    # là chuyện của tầng system E2E, nên chỉ khẳng định KHÔNG bị chặn quyền.
    assert (await client.get(VERIFICATION, headers=_auth(token))).status_code not in (401, 403)


@pytest.mark.asyncio
async def test_the_provider_decision_changes_the_right_row(client, db_pool):
    token, _ = await _user(client, db_pool, "vai_don_vi_qd", role="provider")
    _, owner_id = await _user(client, db_pool, "vai_chu_cua_don_vi")
    workflow_id, task_id = await _awaiting_service(db_pool, owner_id)
    khac_wid, khac_tid = await _awaiting_service(db_pool, owner_id)

    response = await client.post(
        f"{SERVICE}/{workflow_id}/{task_id}/decide",
        # Nguyên nhân canonical là bắt buộc: main app đọc MÃ để biết lời từ
        # chối này khách có sửa được hay không.
        json={
            "decision": "reject",
            "reject_code": "SERVICE_UNAVAILABLE",
            "reject_reason": "Đơn vị đang bảo trì hệ thống",
        },
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    status, decided_by = await _service_status(db_pool, workflow_id, task_id)
    assert status == "REJECTED", status
    assert decided_by, "quyết định không ghi lại ai đã ký"
    # ĐÚNG một dòng đổi: một lệnh duyệt không được quét cả hàng đợi.
    assert await _service_status(db_pool, khac_wid, khac_tid) == ("AWAITING", None)
    reason = await db_pool.fetchval(
        "SELECT reject_reason FROM service_approvals WHERE workflow_id=$1::uuid AND task_id=$2",
        workflow_id,
        task_id,
    )
    assert reason == "Đơn vị đang bảo trì hệ thống"
