"""Câu trả lời của P-118 phải sống trên WORKFLOW, không sống trong RAM.

Quyết định kiến trúc: P-118 là Agent thực hiện tác vụ, không phải chatbot có
trí nhớ hội thoại. Nên KHÔNG có bảng `conversation_messages`, không có lịch sử
small talk. Nguồn sự thật duy nhất là workflow — và câu trả lời là một thuộc
tính trình bày CỦA workflow đó, không phải một tin nhắn rời.

Hệ quả phải kiểm:

  - reload trang / restart backend vẫn đọc lại đúng câu trả lời đó;
  - poll mười lần không gọi lại mô hình cho cùng một trạng thái;
  - workflow đổi trạng thái (chờ duyệt → xong) thì sinh câu MỚI, không dùng lại
    câu mô tả trạng thái cũ — câu cũ giờ đã sai;
  - workflow cũ chưa có cột nào trong đây vẫn đọc được bình thường.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.test_db.conftest import _register_and_login


async def _workflow(db_pool, username: str, *, status: str = "SUCCESS") -> str:
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    workflow_id = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, session_id, owner_user_id) "
        "VALUES ($1::uuid, 'Đăng ký xe và đặt chỗ đỗ xe', $2, $3, $4)",
        workflow_id,
        status,
        str(uuid.uuid4()),
        owner,
    )
    return workflow_id


# ---------------------------------------------------------------------------
# Ghi và đọc lại
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_answer_survives_a_restart(client, db_pool):
    """`_DEMO_JOBS` mất theo tiến trình; câu trả lời thì không được mất."""
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "ar_persist")
    workflow_id = await _workflow(db_pool, "ar_persist")
    repository = WorkflowRepository(db_pool)

    await repository.save_assistant_response(
        workflow_id,
        answer="Mình đã đăng ký xe và giữ chỗ đỗ xe cho bạn xong rồi nhé.",
        suggestions=["Đặt lịch tham quan dự án"],
        state="READY",
        for_status="SUCCESS",
    )

    stored = await repository.get_assistant_response(workflow_id)
    assert stored["answer"].startswith("Mình đã đăng ký xe")
    assert stored["suggestions"] == ["Đặt lịch tham quan dự án"]
    assert stored["state"] == "READY"
    assert stored["for_status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_a_workflow_without_any_response_reads_cleanly(client, db_pool):
    """Dữ liệu cũ tạo trước migration vẫn phải đọc được, không nổ."""
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "ar_legacy")
    workflow_id = await _workflow(db_pool, "ar_legacy")

    stored = await WorkflowRepository(db_pool).get_assistant_response(workflow_id)
    assert stored["answer"] is None
    assert stored["suggestions"] == []
    assert stored["state"] is None


@pytest.mark.asyncio
async def test_suggestions_default_to_an_empty_list_not_null(client, db_pool):
    """`null` buộc mọi nơi đọc phải tự phòng thân; `[]` thì không."""
    await _register_and_login(client, "ar_default")
    workflow_id = await _workflow(db_pool, "ar_default")

    raw = await db_pool.fetchval(
        "SELECT assistant_suggestions FROM workflows WHERE workflow_id = $1::uuid", workflow_id
    )
    assert json.loads(raw) == []


# ---------------------------------------------------------------------------
# Sinh đúng MỘT lần cho mỗi trạng thái
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_one_caller_may_generate_for_a_given_status(client, db_pool):
    """Poll mười lần không được thành mười lượt gọi mô hình.

    Việc chốt quyền sinh nằm ở PostgreSQL, không ở một cờ trong RAM: cờ RAM chỉ
    chặn trong CÙNG tiến trình, và mất sau restart.
    """
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "ar_once")
    workflow_id = await _workflow(db_pool, "ar_once")
    repository = WorkflowRepository(db_pool)

    claims = [await repository.claim_assistant_response(workflow_id, for_status="SUCCESS") for _ in range(10)]

    assert claims.count(True) == 1, f"có {claims.count(True)} lượt được phép gọi mô hình"


@pytest.mark.asyncio
async def test_a_new_status_earns_a_new_answer(client, db_pool):
    """Chờ duyệt → xong: câu mô tả trạng thái cũ giờ đã sai, phải sinh câu mới."""
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "ar_newstatus")
    workflow_id = await _workflow(db_pool, "ar_newstatus", status="WAITING_APPROVAL")
    repository = WorkflowRepository(db_pool)

    assert await repository.claim_assistant_response(workflow_id, for_status="WAITING_APPROVAL") is True
    await repository.save_assistant_response(
        workflow_id,
        answer="Bạn xác nhận giúp mình khoản phí này nhé.",
        suggestions=[],
        state="READY",
        for_status="WAITING_APPROVAL",
    )
    assert await repository.claim_assistant_response(workflow_id, for_status="WAITING_APPROVAL") is False

    # Người dùng bấm duyệt → trạng thái đổi.
    assert await repository.claim_assistant_response(workflow_id, for_status="SUCCESS") is True


@pytest.mark.asyncio
async def test_a_failed_generation_is_recorded_as_fallback(client, db_pool):
    """FALLBACK phải được GHI, không để trống.

    Để trống thì lượt poll kế tiếp lại claim được và lại gọi mô hình — một vòng
    lặp gọi LLM cho một workflow đã kết thúc từ lâu.
    """
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "ar_fallback")
    workflow_id = await _workflow(db_pool, "ar_fallback")
    repository = WorkflowRepository(db_pool)

    await repository.claim_assistant_response(workflow_id, for_status="SUCCESS")
    await repository.save_assistant_response(
        workflow_id,
        answer="Yêu cầu của bạn đã hoàn tất.",
        suggestions=[],
        state="FALLBACK",
        for_status="SUCCESS",
    )

    assert (await repository.get_assistant_response(workflow_id))["state"] == "FALLBACK"
    assert await repository.claim_assistant_response(workflow_id, for_status="SUCCESS") is False


@pytest.mark.asyncio
async def test_a_pending_claim_left_by_a_dead_process_is_reclaimable(client, db_pool):
    """Backend chết giữa lúc đang sinh câu trả lời.

    Chính sách đã chọn: cho phép claim LẠI sau một khoảng chờ, đúng một lần
    nữa. Để PENDING vĩnh viễn thì workflow đó vĩnh viễn không có câu trả lời;
    còn cho claim lại ngay lập tức thì hai tiến trình cùng gọi mô hình.
    """
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "ar_stale")
    workflow_id = await _workflow(db_pool, "ar_stale")
    repository = WorkflowRepository(db_pool)

    assert await repository.claim_assistant_response(workflow_id, for_status="SUCCESS") is True
    # Ngay sau đó thì KHÔNG được claim lại.
    assert await repository.claim_assistant_response(workflow_id, for_status="SUCCESS") is False

    # Giả lập tiến trình đã chết: đẩy mốc thời gian lùi lại.
    await db_pool.execute(
        "UPDATE workflows SET assistant_updated_at = NOW() - INTERVAL '10 minutes' WHERE workflow_id = $1::uuid",
        workflow_id,
    )
    assert await repository.claim_assistant_response(workflow_id, for_status="SUCCESS") is True


# ---------------------------------------------------------------------------
# Đường đọc công khai
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_api_serves_the_persisted_answer_after_a_restart(client, db_pool):
    """Không có `_DEMO_JOBS`, API vẫn phải trả câu trả lời đã ghi."""
    from src.api import routes
    from src.db.workflow_repository import WorkflowRepository

    token = await _register_and_login(client, "ar_api")
    workflow_id = await _workflow(db_pool, "ar_api")
    await WorkflowRepository(db_pool).save_assistant_response(
        workflow_id,
        answer="Mình đã giữ chỗ đỗ xe cho bạn xong rồi nhé.",
        suggestions=["Báo bảo trì / sửa chữa"],
        state="READY",
        for_status="SUCCESS",
    )
    routes._DEMO_JOBS.pop(workflow_id, None)

    body = (
        await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers={"Authorization": f"Bearer {token}"})
    ).json()

    assert body["answer"] == "Mình đã giữ chỗ đỗ xe cho bạn xong rồi nhé."
    assert body["suggestions"] == ["Báo bảo trì / sửa chữa"]
    assert body["response_state"] == "READY"


@pytest.mark.asyncio
async def test_claiming_a_new_status_clears_the_previous_answer(client, db_pool):
    """Trong lúc đang sinh câu mới, KHÔNG được phục vụ câu của trạng thái cũ.

    `claim` đặt `assistant_for_status` sang trạng thái mới ngay lập tức. Nếu
    `assistant_answer` vẫn giữ câu cũ thì mọi đường đọc coi câu cũ là câu mô tả
    trạng thái mới — người dùng vừa bấm duyệt xong lại đọc "bạn vui lòng xác
    nhận thanh toán".
    """
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "ar_clear")
    workflow_id = await _workflow(db_pool, "ar_clear", status="WAITING_APPROVAL")
    repository = WorkflowRepository(db_pool)

    await repository.claim_assistant_response(workflow_id, for_status="WAITING_APPROVAL")
    await repository.save_assistant_response(
        workflow_id,
        answer="Bạn vui lòng xác nhận khoản thanh toán nhé.",
        suggestions=["Đặt lịch tham quan dự án"],
        state="READY",
        for_status="WAITING_APPROVAL",
    )

    await repository.claim_assistant_response(workflow_id, for_status="SUCCESS")

    stored = await repository.get_assistant_response(workflow_id)
    assert stored["state"] == "PENDING"
    assert stored["answer"] is None, "câu của trạng thái cũ vẫn còn trong lúc chờ câu mới"
    assert stored["suggestions"] == []
