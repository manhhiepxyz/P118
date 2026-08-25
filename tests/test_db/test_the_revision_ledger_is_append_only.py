"""Sổ sửa đổi chỉ được GHI THÊM, và khoá đọc-lại phải thật sự khoá.

`workflow_tasks` là hình chiếu vận hành: `input_data` bị update mỗi lần một
bước đổi. Nó không phải nhật ký, nên nó không trả lời được "ai đã đổi gì, lúc
nào, từ phiên bản kế hoạch nào". Bảng `workflow_plan_revisions` trả lời câu đó,
và nó chỉ có giá trị nếu KHÔNG SỬA ĐƯỢC — một dòng audit viết đè lên được thì
nó là ghi chú, không phải bằng chứng.

Chặn ở DATABASE, không ở tầng ứng dụng: một script vận hành, một lần
`psql`, hay một tầng mới viết sau này đều đi vòng qua tầng ứng dụng.

Phần thứ hai của file: primitive khoá. Phase 2B sẽ ghi dựa trên nó, nên ở đây
nó phải chứng minh được bốn điều — khoá thật, so phiên bản, xung đột thì không
ghi gì, và lỗi ở bước cuối thì cuộn lại toàn bộ.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg
import pytest

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository


async def _seed(pool, *, task_status: str = "PENDING") -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, password_hash) VALUES ($1,$2,'x') ON CONFLICT DO NOTHING",
            OWNER,
            f"nguoi-{OWNER.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) VALUES ($1,'x','CANCELLED',$2)",
            wid,
            OWNER,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1','schedule_property_viewing',$2,$3::jsonb)",
            wid,
            task_status,
            json.dumps({"project_id": "PRJ-001", "viewing_date": "2030-05-04"}),
        )
    return str(wid)


OWNER = uuid.uuid4()


async def _append(repository, workflow_id: str, *, number_hint: str = "2030-05-05") -> dict:
    return await repository.append_plan_revision(
        workflow_id=workflow_id,
        requester_user_id=str(OWNER),
        plan_version_before="aaaaaaaaaaaaaaaa",
        plan_version_after="bbbbbbbbbbbbbbbb",
        accepted_patch={"viewing_date": number_hint},
        targets={"viewing_date": "T1"},
        consequence="PATCH_ACCEPTED",
    )


# --- Append-only, chứng minh bằng chính PostgreSQL --------------------------


@pytest.mark.asyncio
async def test_a_revision_can_be_written(client, db_pool):
    workflow_id = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    written = await _append(repository, workflow_id)
    assert written["revision_number"] == 1

    row = await db_pool.fetchrow(
        "SELECT * FROM workflow_plan_revisions WHERE workflow_id=$1::uuid", uuid.UUID(workflow_id)
    )
    assert json.loads(row["accepted_patch"]) == {"viewing_date": "2030-05-05"}
    assert json.loads(row["targets"]) == {"viewing_date": "T1"}
    assert row["consequence"] == "PATCH_ACCEPTED"
    assert str(row["requester_user_id"]) == str(OWNER)


@pytest.mark.asyncio
async def test_a_revision_can_never_be_updated(client, db_pool):
    workflow_id = await _seed(db_pool)
    await _append(PostgreSQLWorkflowStateRepository(db_pool), workflow_id)
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db_pool.execute(
            "UPDATE workflow_plan_revisions SET consequence='PATCH_REJECTED' WHERE workflow_id=$1::uuid",
            uuid.UUID(workflow_id),
        )
    assert (
        await db_pool.fetchval(
            "SELECT consequence FROM workflow_plan_revisions WHERE workflow_id=$1::uuid", uuid.UUID(workflow_id)
        )
        == "PATCH_ACCEPTED"
    )


@pytest.mark.asyncio
async def test_a_revision_can_never_be_deleted(client, db_pool):
    workflow_id = await _seed(db_pool)
    await _append(PostgreSQLWorkflowStateRepository(db_pool), workflow_id)
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db_pool.execute("DELETE FROM workflow_plan_revisions WHERE workflow_id=$1::uuid", uuid.UUID(workflow_id))
    assert (
        await db_pool.fetchval(
            "SELECT count(*) FROM workflow_plan_revisions WHERE workflow_id=$1::uuid", uuid.UUID(workflow_id)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_two_revisions_never_share_a_number(client, db_pool):
    """Số thứ tự là thứ dựng lại được LỊCH SỬ. Hai dòng cùng số thì không dựng lại được."""
    workflow_id = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    written = await asyncio.gather(
        *(_append(repository, workflow_id, number_hint=f"2030-05-0{i}") for i in range(1, 5))
    )
    numbers = sorted(item["revision_number"] for item in written)
    assert numbers == [1, 2, 3, 4]

    rows = await db_pool.fetch(
        "SELECT revision_number FROM workflow_plan_revisions WHERE workflow_id=$1::uuid", uuid.UUID(workflow_id)
    )
    assert sorted(r["revision_number"] for r in rows) == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_the_unique_order_is_enforced_by_the_database(client, db_pool):
    workflow_id = await _seed(db_pool)
    await _append(PostgreSQLWorkflowStateRepository(db_pool), workflow_id)
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await db_pool.execute(
            "INSERT INTO workflow_plan_revisions "
            "(workflow_id, revision_number, requester_user_id, plan_version_before, plan_version_after, "
            " accepted_patch, targets, consequence) "
            "VALUES ($1::uuid, 1, $2, 'a', 'b', '{}'::jsonb, '{}'::jsonb, 'PATCH_ACCEPTED')",
            uuid.UUID(workflow_id),
            OWNER,
        )


@pytest.mark.asyncio
async def test_the_ledger_holds_no_free_text_from_the_user_or_the_model(client, db_pool):
    """Sổ này chỉ giữ BẢN VÁ, không giữ câu người dùng gõ hay output model.

    Cả hai là văn bản tự do đi thẳng vào một bảng lưu vĩnh viễn — chúng có thể
    mang dữ liệu cá nhân, và không giúp gì cho việc dựng lại lịch sử sửa đổi.
    """
    columns = {
        r["column_name"]
        for r in await db_pool.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='workflow_plan_revisions'"
        )
    }
    for banned in ("utterance", "goal", "raw_output", "reasoning", "prompt", "error_message", "question"):
        assert banned not in columns, banned


# --- Primitive khoá ---------------------------------------------------------


@pytest.mark.asyncio
async def test_the_locked_snapshot_matches_the_expected_version(client, db_pool):
    from src.common.plan_fingerprint import plan_version_of

    workflow_id = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    rows = await db_pool.fetch(
        "SELECT task_id, tool, depends_on, status, input_data, provider_submission_status, "
        "external_request_id, provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1::uuid",
        uuid.UUID(workflow_id),
    )
    expected = plan_version_of([dict(r) for r in rows], [])

    snapshot = await repository.lock_workflow_for_amendment(workflow_id, expected_plan_version=expected)
    assert snapshot.conflict is None
    assert snapshot.plan_version == expected
    assert snapshot.owner_user_id == str(OWNER)
    assert snapshot.task_status == {"T1": "PENDING"}
    assert snapshot.submission_evidence["T1"]["provider_submission_status"] == "NOT_SUBMITTED"


@pytest.mark.asyncio
async def test_a_stale_version_is_refused_and_writes_nothing(client, db_pool):
    workflow_id = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    snapshot = await repository.lock_workflow_for_amendment(workflow_id, expected_plan_version="khong-dung")
    assert snapshot.conflict == "PLAN_VERSION_CHANGED"
    assert snapshot.task_status == {}
    assert (
        await db_pool.fetchval(
            "SELECT count(*) FROM workflow_plan_revisions WHERE workflow_id=$1::uuid", uuid.UUID(workflow_id)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_an_awaiting_approval_shows_up_but_is_not_a_submission(client, db_pool):
    from src.common.plan_fingerprint import plan_version_of
    from src.orchestration.service_approval import save_pending_service_approvals

    workflow_id = await _seed(db_pool, task_status="WAITING_APPROVAL")
    await save_pending_service_approvals(
        db_pool,
        workflow_id=workflow_id,
        rows=[{"task_id": "T1", "tool": "book_parking", "service_label": "Giữ chỗ đỗ xe", "details": {}}],
    )
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    rows = await db_pool.fetch(
        "SELECT task_id, tool, depends_on, status, input_data, provider_submission_status, "
        "external_request_id, provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1::uuid",
        uuid.UUID(workflow_id),
    )
    approvals = [("service", "T1", "AWAITING")]
    snapshot = await repository.lock_workflow_for_amendment(
        workflow_id, expected_plan_version=plan_version_of([dict(r) for r in rows], approvals)
    )
    assert snapshot.conflict is None
    assert snapshot.open_approvals == (("service", "T1", "AWAITING"),)
    # Có người phải quyết định — nhưng KHÔNG có bằng chứng nào rời hệ thống.
    assert snapshot.submission_evidence["T1"]["provider_submission_status"] == "NOT_SUBMITTED"


@pytest.mark.asyncio
async def test_two_locked_amendments_are_serialised_not_interleaved(client, db_pool):
    """Hai lượt sửa cùng lúc: chúng xếp hàng, không chạy chồng lên nhau.

    Không khoá thì cả hai đọc cùng `MAX(revision_number)` và cùng xin số 1 —
    một cái vỡ vì ràng buộc UNIQUE, và người dùng nhận một lỗi database cho một
    thao tác hoàn toàn hợp lệ.

    Callback tuỳ ý đã bị bỏ khỏi API (nó là một cửa gọi mạng khi transaction
    đang mở), nên thao tác chạy bên trong là một BẢN GHI có cấu trúc.
    """
    from src.common.plan_fingerprint import plan_version_of

    workflow_id = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    rows = await db_pool.fetch(
        "SELECT task_id, tool, depends_on, status, input_data, provider_submission_status, "
        "external_request_id, provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1::uuid",
        uuid.UUID(workflow_id),
    )
    expected = plan_version_of([dict(r) for r in rows], [])

    async def attempt(tag: str):
        return await repository.lock_workflow_for_amendment(
            workflow_id,
            expected_plan_version=expected,
            record_revision={
                "requester_user_id": str(OWNER),
                "plan_version_after": tag,
                "accepted_patch": {"viewing_date": tag},
                "targets": {"viewing_date": "T1"},
                "consequence": "PATCH_ACCEPTED",
            },
        )

    results = await asyncio.gather(attempt("2030-06-01"), attempt("2030-06-02"))
    assert all(r.conflict is None for r in results)
    numbers = sorted(
        r["revision_number"]
        for r in await db_pool.fetch(
            "SELECT revision_number FROM workflow_plan_revisions WHERE workflow_id=$1::uuid",
            uuid.UUID(workflow_id),
        )
    )
    assert numbers == [1, 2], numbers


@pytest.mark.asyncio
async def test_a_failure_inside_the_transaction_writes_nothing(client, db_pool):
    """Lỗi ở bước cuối cuộn lại toàn bộ — không có nửa bản ghi nào sống sót."""
    from src.common.plan_fingerprint import plan_version_of

    workflow_id = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    rows = await db_pool.fetch(
        "SELECT task_id, tool, depends_on, status, input_data, provider_submission_status, "
        "external_request_id, provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1::uuid",
        uuid.UUID(workflow_id),
    )
    expected = plan_version_of([dict(r) for r in rows], [])

    with pytest.raises(asyncpg.PostgresError):
        await repository.lock_workflow_for_amendment(
            workflow_id,
            expected_plan_version=expected,
            record_revision={
                "requester_user_id": str(OWNER),
                "plan_version_after": "x",
                "accepted_patch": {},
                "targets": {},
                # Dài hơn VARCHAR(40) — database từ chối ở đúng bước cuối.
                "consequence": "K" * 200,
            },
        )
    assert (
        await db_pool.fetchval(
            "SELECT count(*) FROM workflow_plan_revisions WHERE workflow_id=$1::uuid", uuid.UUID(workflow_id)
        )
        == 0
    )


def test_the_locking_primitive_takes_no_code_from_its_caller():
    """Đọc thân hàm là bảo đảm GIẢ khi hàm nhận một callback: mã chạy bên trong
    do caller viết, và thân hàm không biết gì về nó. Kiểm CHỮ KÝ."""
    import inspect

    signature = inspect.signature(PostgreSQLWorkflowStateRepository.lock_workflow_for_amendment)
    assert "inside_transaction" not in signature.parameters
    assert "record_revision" in signature.parameters
