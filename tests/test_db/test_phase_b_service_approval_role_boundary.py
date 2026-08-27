"""Ranh giới quyền của `/service-approvals` — chưa có test HTTP+Postgres nào phủ.

`viewing-approvals` (cổng song song, cùng `require_roles("provider", "admin")`)
đã có `test_customer_cannot_list_or_decide` trong
`tests/test_integration/test_viewing_approval_routes.py`. `/service-approvals`
— cổng phục vụ SÁU dịch vụ còn lại (đăng ký xe, chỗ đỗ, bảo trì, chuyển nhà, xe
đưa đón, đăng ký tư vấn) — không có test tương đương chạy qua HTTP thật.

Ranh giới ở đây có HAI tầng, và cả hai phải cùng đúng:

    ROLE        khách và người ẩn danh không vào được → 403 / 401
    SỞ HỮU      provider chỉ quyết định phần của đơn vị mình → 404

Tầng thứ hai mới thêm. Trước đó file này khẳng định ngược lại — tên bài kiểm cũ
là `test_a_provider_can_decide_a_service_approval_that_is_not_theirs`, và
docstring nói thẳng "đây KHÔNG phải IDOR cổ điển". Nó đúng với thiết kế lúc ấy:
một `provider` duy nhất đại diện cho mọi đơn vị, nên "phần của mình" chưa phải
một khái niệm. Khi mở dịch vụ cho đối tác nhỏ lẻ bên ngoài thì nó thành khái
niệm — đơn vị chuyển nhà A không được từ chối việc của đơn vị B — nên bài kiểm
ấy bị ĐẢO, không phải bị xoá: cùng một tình huống, kết luận ngược lại.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_db.conftest import _register_and_login, dang_nhap_don_vi


async def _seed_awaiting_service_approval(
    db_pool, *, owner_user_id, don_vi: str = "FIX-01"
) -> tuple[str, str]:
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
            workflow_id, task_id, tool, service_label, details, status, applicant_user_id,
            -- Chủ sở hữu là BẮT BUỘC với dòng mới. Cổng duyệt fail-closed, nên
            -- một dòng NULL không phải "ai cũng duyệt được" mà là "không ai
            -- duyệt được" — gieo NULL ở đây sẽ làm mọi kiểm dương đỏ vì một lý
            -- do không liên quan đến điều nó tuyên bố.
            service_provider_id
        )
        VALUES ($1::uuid, $2, 'create_maintenance_request', 'Báo bảo trì / sửa chữa', '{}'::jsonb, 'AWAITING',
                $3, $4)
        """,
        workflow_id,
        task_id,
        owner_user_id,
        don_vi,
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
async def test_a_provider_can_decide_a_service_approval_of_its_own_unit(client, db_pool):
    """Kiểm dương: provider PHẢI duyệt được phần của mình.

    Không có vế này thì mọi 401/403/404 phía trên có thể đúng chỉ vì route hỏng.
    Người tạo yêu cầu là một tài khoản KHÁC: quyền duyệt đến từ đơn vị, không
    từ việc quen biết người yêu cầu.
    """
    token, _ = await dang_nhap_don_vi(client, db_pool, "svcappr_nha_cung_cap", don_vi=("FIX-01",))
    await _register_and_login(client, "svcappr_chu_khac")
    other_owner_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'svcappr_chu_khac'")
    workflow_id, task_id = await _seed_awaiting_service_approval(
        db_pool, owner_user_id=other_owner_id, don_vi="FIX-01"
    )

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


@pytest.mark.asyncio
async def test_a_provider_cannot_decide_a_service_approval_that_is_not_theirs(client, db_pool):
    """Đúng vai, sai đơn vị → 404, và dòng KHÔNG đổi.

    Đây là bài kiểm bị đảo. Nó dựng đúng tình huống mà bản cũ khẳng định là hợp
    lệ: một provider hợp lệ gọi thẳng endpoint với một `workflow_id` thuộc đơn
    vị khác. Không đi qua danh sách — kẻ tấn công cũng vậy.

    404 chứ không 403: 403 xác nhận dòng ấy CÓ TỒN TẠI, và đó là một mẩu thông
    tin miễn phí cho người đang dò mã.

    Khẳng định trạng thái sau lời từ chối là phần bắt buộc: một route trả 404
    SAU khi đã ghi quyết định vẫn là một lỗ hổng, chỉ khó thấy hơn.
    """
    token, _ = await dang_nhap_don_vi(client, db_pool, "svcappr_don_vi_khac", don_vi=("MOV-01",))
    await _register_and_login(client, "svcappr_chu_cua_fix")
    owner_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'svcappr_chu_cua_fix'")
    workflow_id, task_id = await _seed_awaiting_service_approval(
        db_pool, owner_user_id=owner_id, don_vi="FIX-01"
    )

    response = await client.post(
        f"/api/v1/service-approvals/{workflow_id}/{task_id}/decide",
        json={
            "decision": "reject",
            "reject_code": "SERVICE_UNAVAILABLE",
            "reject_reason": "Đơn vị đang bảo trì hệ thống",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404, response.text
    row = await db_pool.fetchrow(
        "SELECT status, decided_by, reject_reason FROM service_approvals "
        "WHERE workflow_id = $1::uuid AND task_id = $2",
        workflow_id,
        task_id,
    )
    assert (row["status"], row["decided_by"], row["reject_reason"]) == ("AWAITING", None, None)
