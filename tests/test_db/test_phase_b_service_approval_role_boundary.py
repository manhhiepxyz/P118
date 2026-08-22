"""Ranh giới quyền của `/service-approvals` — chưa có test HTTP+Postgres nào phủ.

`viewing-approvals` (cổng song song, cùng `require_roles("provider", "admin")`)
đã có `test_customer_cannot_list_or_decide` trong
`tests/test_integration/test_viewing_approval_routes.py`. `/service-approvals`
— cổng phục vụ SÁU dịch vụ còn lại (đăng ký xe, chỗ đỗ, bảo trì, chuyển nhà, xe
đưa đón, đăng ký tư vấn) — không có test tương đương chạy qua HTTP thật. Đây
KHÔNG phải IDOR cổ điển (người duyệt được quyền quyết định TOÀN BỘ hàng đợi,
không phải chỉ phần "của mình"); ranh giới thật ở đây là ROLE, và đó là thứ bị
thiếu test.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_db.conftest import _register_and_login


async def _seed_awaiting_service_approval(db_pool, *, owner_user_id) -> tuple[str, str]:
    workflow_id = str(uuid.uuid4())
    task_id = "T1"
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'Báo bảo trì điều hoà', 'WAITING_APPROVAL', $2)",
        workflow_id,
        owner_user_id,
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
        "VALUES ($1::uuid, $2, 'create_maintenance_request', 'WAITING_APPROVAL', '[]'::jsonb)",
        workflow_id,
        task_id,
    )
    await db_pool.execute(
        """
        INSERT INTO service_approvals (
            workflow_id, task_id, tool, service_label, details, status, applicant_user_id
        )
        VALUES ($1::uuid, $2, 'create_maintenance_request', 'Báo bảo trì / sửa chữa', '{}'::jsonb, 'AWAITING', $3)
        """,
        workflow_id,
        task_id,
        owner_user_id,
    )
    return workflow_id, task_id


@pytest.mark.asyncio
async def test_a_customer_cannot_list_the_service_approval_queue(client, db_pool):
    token = await _register_and_login(client, "svcappr_khach_liet_ke")

    response = await client.get(
        "/api/v1/service-approvals",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_a_customer_cannot_decide_their_own_service_approval(client, db_pool):
    """Người vừa đăng ký dịch vụ không được tự duyệt phần của chính mình.

    Đây chính là lý do cổng duyệt tồn tại: dùng thẳng token của người tạo ra
    yêu cầu (chủ sở hữu thật) để chứng minh "chủ sở hữu" không phải là quyền
    hợp lệ ở đây — chỉ role mới là.
    """
    token = await _register_and_login(client, "svcappr_khach_tu_duyet")
    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'svcappr_khach_tu_duyet'")
    workflow_id, task_id = await _seed_awaiting_service_approval(db_pool, owner_user_id=user_id)

    response = await client.post(
        f"/api/v1/service-approvals/{workflow_id}/{task_id}/decide",
        json={"decision": "approve"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403, response.text
    status = await db_pool.fetchval(
        "SELECT status FROM service_approvals WHERE workflow_id = $1::uuid AND task_id = $2", workflow_id, task_id
    )
    assert status == "AWAITING", "quyết định của khách hàng đã đổi được trạng thái"


@pytest.mark.asyncio
async def test_an_anonymous_caller_cannot_decide_a_service_approval(client, db_pool):
    await _register_and_login(client, "svcappr_chu_an_danh")
    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'svcappr_chu_an_danh'")
    workflow_id, task_id = await _seed_awaiting_service_approval(db_pool, owner_user_id=user_id)

    response = await client.post(
        f"/api/v1/service-approvals/{workflow_id}/{task_id}/decide",
        json={"decision": "approve"},
    )

    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_a_provider_can_decide_a_service_approval_that_is_not_theirs(client, db_pool):
    """Kiểm dương: provider PHẢI duyệt được — nếu không, test 403 phía trên vô nghĩa."""
    token = await _register_and_login(client, "svcappr_nha_cung_cap")
    await db_pool.execute("UPDATE users SET role = 'provider' WHERE username = 'svcappr_nha_cung_cap'")
    await _register_and_login(client, "svcappr_chu_khac")
    other_owner_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'svcappr_chu_khac'")
    workflow_id, task_id = await _seed_awaiting_service_approval(db_pool, owner_user_id=other_owner_id)

    response = await client.post(
        f"/api/v1/service-approvals/{workflow_id}/{task_id}/decide",
        # Từ chối phải nêu NGUYÊN NHÂN canonical, không chỉ câu chữ: main app
        # đọc mã để biết khách có sửa được hay không.
        json={
            "decision": "reject",
            "reject_code": "SERVICE_UNAVAILABLE",
            "reject_reason": "Đơn vị đang bảo trì hệ thống",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
