"""IDOR trên `POST /workflows/demo/{id}/continue` — chưa có test HTTP+Postgres nào phủ.

`continue_demo_workflow` gọi `_require_workflow_owner(workflow_id, user)` NGAY
DÒNG ĐẦU, trước khi chạm `_DEMO_JOBS` (cùng hàm mà GET, payment-decision,
cancel, delete đã dùng và đã có test riêng). Nhưng bản thân endpoint
`continue` chưa có test nào xác nhận qua HTTP thật rằng người không sở hữu bị
chặn — mọi test `continue` khác đều chạy bằng chính chủ workflow. File này lấp
đúng khoảng trống đó.

Cố ý KHÔNG gọi `POST /workflows/demo/start`, Planner, hay bất kỳ LLM nào.
`/start` chạy Planner (goal "Tìm căn hộ..." có thể không còn reachable dưới
tool policy hiện tại) và spawn một background asyncio task mà test không await
— task đó có thể còn chạm Postgres sau khi fixture đã TRUNCATE bảng, đúng loại
race mà Tab B đang xử lý riêng ở tầng DB test stability. Vì `_require_workflow_
owner` là dòng ĐẦU TIÊN của `continue_demo_workflow`, seed thẳng một workflow
row (+ một clarification row) qua repository là đủ để chứng minh guard chủ sở
hữu hoạt động — không cần Planner chạy ra bất cứ thứ gì.
"""

from __future__ import annotations

import uuid

import pytest

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from tests.test_db.conftest import _register_and_login


async def _seed_owned_workflow_shell(db_pool, *, owner_user_id, goal: str) -> tuple[str, str]:
    """Ghim một workflow shell + clarification đang mở, thuộc `owner_user_id`.

    Dùng `PostgreSQLWorkflowStateRepository.create_shell_and_session` /
    `.save_clarification` — cùng đường production dùng để ghim shell trước khi
    Planner chạy (`src/db/workflow_repository.py`) — thay vì tự viết SQL INSERT
    tay, để row luôn khớp schema thật mà owner-scoped query (`get_workflow_owner`,
    list) sẽ tìm thấy.
    """
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    await repository.create_shell_and_session(
        workflow_id=workflow_id,
        owner_user_id=owner_user_id,
        session_id=session_id,
        goal=goal,
        account_state="prospect",
        resident_id=None,
    )

    # Một clarification đang mở, tied tới workflow này — chứng minh khối chặn
    # ở `continue` là guard CHỦ SỞ HỮU, không phải "không có gì để continue".
    # Nếu guard bị gỡ, request của B sẽ đi tiếp và CHẠM đúng row này.
    await repository.save_clarification(
        workflow_id,
        session_id=session_id,
        parent_workflow_id=None,
        goal=goal,
        missing_fields=["viewing_date"],
        question="Anh/chị muốn xem nhà ngày nào?",
        existing_context={},
    )

    return workflow_id, session_id


@pytest.mark.asyncio
async def test_a_user_cannot_continue_another_users_workflow(client, db_pool):
    await _register_and_login(client, "nn_continue_idor_a")
    token_b = await _register_and_login(client, "nn_continue_idor_b")
    user_a_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_continue_idor_a'")

    workflow_id, _session_id = await _seed_owned_workflow_shell(
        db_pool, owner_user_id=user_a_id, goal="Tìm căn hộ cho thuê tại Vinhomes Ocean Park"
    )

    before_workflow = await db_pool.fetchrow("SELECT * FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    before_clarification = await db_pool.fetchrow(
        "SELECT * FROM workflow_clarifications WHERE workflow_id = $1::uuid", workflow_id
    )
    before_workflow_count = await db_pool.fetchval("SELECT count(*) FROM workflows")

    hijacked = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        json={"message": "Vinhomes Ocean Park, ngân sách 5 tỷ"},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert hijacked.status_code == 404, hijacked.text
    # Message của lỗi không được để lộ workflow_id thật hay dữ liệu của A.
    assert workflow_id not in hijacked.text

    after_workflow = await db_pool.fetchrow("SELECT * FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    after_clarification = await db_pool.fetchrow(
        "SELECT * FROM workflow_clarifications WHERE workflow_id = $1::uuid", workflow_id
    )
    after_workflow_count = await db_pool.fetchval("SELECT count(*) FROM workflows")

    # Row của A không đổi — B chưa hề chạm được vào nó.
    assert dict(after_workflow) == dict(before_workflow)
    assert dict(after_clarification) == dict(before_clarification)
    # Không có workflow con/mới nào được tạo như tác dụng phụ của lần gọi bị chặn.
    assert after_workflow_count == before_workflow_count


@pytest.mark.asyncio
async def test_continuing_a_nonexistent_workflow_looks_the_same_as_someone_elses(client, db_pool):
    """Workflow không tồn tại và workflow của người khác phải trả CÙNG mã lỗi + body.

    Khác nhau ở đây là một kênh dò: gửi ID bất kỳ, đọc mã lỗi hoặc nội dung
    body, biết ID đó có tồn tại hay không.
    """
    await _register_and_login(client, "nn_continue_idor_c")
    token_b = await _register_and_login(client, "nn_continue_idor_d")
    user_a_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_continue_idor_c'")

    real_workflow_id, _session_id = await _seed_owned_workflow_shell(
        db_pool, owner_user_id=user_a_id, goal="Tìm căn hộ cho thuê tại Vinhomes Ocean Park"
    )

    stolen = await client.post(
        f"/api/v1/workflows/demo/{real_workflow_id}/continue",
        json={"message": "bất kỳ"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    fabricated = await client.post(
        "/api/v1/workflows/demo/00000000-0000-0000-0000-0000000000ff/continue",
        json={"message": "bất kỳ"},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert stolen.status_code == fabricated.status_code == 404
    assert stolen.json() == fabricated.json()
    # Body không được rò rỉ workflow_id thật hay bất kỳ ID nào của yêu cầu này.
    assert real_workflow_id not in stolen.text
