"""HTTP + PostgreSQL acceptance tests for the schedule_conflict feature (P-118).

Tests round-trip through the real HTTP handler (via `client`) and a real
PostgreSQL pool (via `db_pool`).  The `clean_tables` autouse fixture in
conftest.py truncates all tables after each test, so tests are isolated.

Naming convention:
  # helper test — narrow verifications of supporting DB state
  # route test   — full HTTP round-trips through the route handlers

Running:
    pytest tests/test_db/test_schedule_conflict_route.py -v
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from src.api import routes
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.models.schemas import (
    ConflictTaskInfo,
    DemoWorkflowResponse,
    ScheduleConflictAction,
)
from src.orchestration.schedule_conflict import (
    compute_fingerprint,
    is_acknowledged,
    load_conflict_check,
    save_conflict_check,
)
from tests.test_db.conftest import _register_and_login

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_DATE = "2026-10-20"
_TIME = "09:00"
_SVC_MOVE = "schedule_move"
_SVC_MAINT = "create_maintenance_request"


# ---------------------------------------------------------------------------
# Shared DB helpers
# ---------------------------------------------------------------------------


async def _seed_conflict_workflow(
    db_pool,
    *,
    owner_id: str,
    session_id: str,
    goal: str = "Đặt chuyển nhà và bảo trì cùng lúc",
) -> tuple[str, str]:
    """Create a workflow in WAITING_APPROVAL with two tasks at the same datetime
    and a pending conflict-check row.

    Returns (workflow_id, fingerprint).

    # helper test
    """
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid = str(uuid.uuid4())

    await repository.create_shell_and_session(
        workflow_id=wid,
        owner_user_id=owner_id,
        session_id=session_id,
        goal=goal,
        account_state="resident",
        resident_id=None,
    )

    # Escalate to WAITING_APPROVAL (the conflict boundary would have done this).
    await db_pool.execute(
        "UPDATE workflows SET status='WAITING_APPROVAL' WHERE workflow_id=$1::uuid",
        wid,
    )

    # Task A: schedule_move — blocked by conflict.
    await db_pool.execute(
        "INSERT INTO workflow_tasks "
        "(workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', $2, 'WAITING_APPROVAL', '[]'::jsonb, $3::jsonb)",
        wid,
        _SVC_MOVE,
        json.dumps({"move_date": _DATE, "move_time": _TIME}),
    )

    # Task B: create_maintenance_request — same slot.
    await db_pool.execute(
        "INSERT INTO workflow_tasks "
        "(workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T2', $2, 'PENDING', '[]'::jsonb, $3::jsonb)",
        wid,
        _SVC_MAINT,
        json.dumps({"preferred_date": _DATE, "preferred_time": _TIME}),
    )

    fp = compute_fingerprint(
        owner_id,
        wid,
        "T1",
        _SVC_MOVE,
        (_DATE, _TIME),
        wid,
        "T2",
        _SVC_MAINT,
        (_DATE, _TIME),
    )

    await save_conflict_check(
        db_pool,
        fingerprint=fp,
        owner=owner_id,
        workflow_id=wid,
        task_id="T1",
        service_a=_SVC_MOVE,
        date_a=_DATE,
        time_a=_TIME,
        workflow_id_b=wid,
        task_id_b="T2",
        service_b=_SVC_MAINT,
        date_b=_DATE,
        time_b=_TIME,
    )

    return wid, fp


def _inject_conflict_job(
    workflow_id: str,
    *,
    owner_id: str,
    session_id: str,
) -> None:
    """Populate _DEMO_JOBS to simulate the cached response after the conflict
    boundary fired.  Does NOT touch the database.

    # helper test
    """
    task_a = ConflictTaskInfo(
        workflow_id=workflow_id,
        task_id="T1",
        service=_SVC_MOVE,
        service_label="Đăng ký chuyển nhà",
        datetime_display=f"{_DATE} {_TIME}",
    )
    task_b = ConflictTaskInfo(
        workflow_id=workflow_id,
        task_id="T2",
        service=_SVC_MAINT,
        service_label="Yêu cầu bảo trì",
        datetime_display=f"{_DATE} {_TIME}",
    )
    conflict_response = DemoWorkflowResponse(
        workflow_id=workflow_id,
        status="WAITING_APPROVAL",
        stage="WAITING_SCHEDULE_CONFLICT_CHECK",
        summary="Mình thấy bạn đang có 2 lịch cùng lúc.",
        customer_action=ScheduleConflictAction(task_a=task_a, task_b=task_b, can_act=True),
    )
    routes._DEMO_JOBS[workflow_id] = {
        "response": conflict_response,
        "stage": "WAITING_SCHEDULE_CONFLICT_CHECK",
        "message": "Mình thấy lịch có khả năng bị trùng.",
        "events": [],
        "goal": "Đặt chuyển nhà và bảo trì cùng lúc",
        "session_id": session_id,
        "owner_user_id": owner_id,
    }


# ---------------------------------------------------------------------------
# Test 1 — Two same-time tasks → SCHEDULE_CONFLICT response
# ---------------------------------------------------------------------------


async def test_same_time_tasks_yield_schedule_conflict_response(client, db_pool, monkeypatch) -> None:
    """GET on a workflow whose intra-plan conflict is still pending must return
    a SCHEDULE_CONFLICT customer action, a DB-persisted conflict row, and zero
    provider approvals.

    # route test
    """
    token = await _register_and_login(client, "sc_route_t1_owner")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'sc_route_t1_owner'"))
    session_id = str(uuid.uuid4())

    wid, fp = await _seed_conflict_workflow(db_pool, owner_id=owner_id, session_id=session_id)
    _inject_conflict_job(wid, owner_id=owner_id, session_id=session_id)

    res = await client.get(
        f"/api/v1/workflows/demo/{wid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # Top-level status
    assert body["status"] == "WAITING_APPROVAL", body
    assert body.get("stage") == "WAITING_SCHEDULE_CONFLICT_CHECK", body

    # customer_action carries the conflict card
    ca = body.get("customer_action")
    assert ca is not None, "customer_action must be present for conflict"
    assert ca["kind"] == "SCHEDULE_CONFLICT", ca
    assert ca["task_a"]["service"] == _SVC_MOVE
    assert ca["task_b"]["service"] == _SVC_MAINT

    # DB has the conflict row and it is unacknowledged
    conflict_row = await load_conflict_check(db_pool, wid)
    assert conflict_row is not None, "conflict row must be persisted"
    assert conflict_row["fingerprint"] == fp
    assert conflict_row["acknowledged"] is False

    # No provider approvals sent yet
    approvals_count = await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id = $1::uuid", wid)
    assert approvals_count == 0, "no provider approval must have been opened"

    # Cleanup RAM cache
    routes._DEMO_JOBS.pop(wid, None)


# ---------------------------------------------------------------------------
# Test 2 — POST keep_both continues exactly once, second call is idempotent
# ---------------------------------------------------------------------------


async def test_keep_both_continues_workflow_and_is_idempotent(client, db_pool, monkeypatch) -> None:
    """keep_both acknowledges the conflict and resumes the workflow once.
    A second identical POST must NOT trigger a second resume (idempotent).

    # route test
    """
    token = await _register_and_login(client, "sc_route_t2_owner")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'sc_route_t2_owner'"))
    session_id = str(uuid.uuid4())

    wid, fp = await _seed_conflict_workflow(db_pool, owner_id=owner_id, session_id=session_id)
    _inject_conflict_job(wid, owner_id=owner_id, session_id=session_id)

    resume_calls: list[dict[str, Any]] = []

    async def _fake_resume(workflow_id: str, *, owner_user_id: str, conflict_task_id: str, **_kw: str) -> dict:
        resume_calls.append({"workflow_id": workflow_id, "task_id": conflict_task_id})
        return {"status": "WAITING_APPROVAL"}

    monkeypatch.setattr(routes, "resume_after_conflict_ack", _fake_resume)

    # --- First call ---
    res1 = await client.post(
        f"/api/v1/workflows/demo/{wid}/conflict-respond",
        json={"choice": "keep_both"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res1.status_code == 200, res1.text
    body1 = res1.json()
    # Must not have failed
    assert body1.get("status") != "FAILED", body1
    # Resume was invoked exactly once
    assert len(resume_calls) == 1

    # DB: conflict is acknowledged
    assert await is_acknowledged(db_pool, fp) is True

    # --- Second call (idempotent) ---
    res2 = await client.post(
        f"/api/v1/workflows/demo/{wid}/conflict-respond",
        json={"choice": "keep_both"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 200, res2.text
    body2 = res2.json()
    # Still not FAILED — idempotent, not an error
    assert body2.get("status") != "FAILED", body2

    # Resume was NOT called a second time — no duplicate execution
    assert len(resume_calls) == 1, "resume must fire at most once"

    # DB: still only one acknowledged row (no extra rows)
    ack_count = await db_pool.fetchval(
        "SELECT count(*) FROM schedule_conflict_checks WHERE fingerprint = $1 AND acknowledged = TRUE",
        fp,
    )
    assert ack_count == 1

    routes._DEMO_JOBS.pop(wid, None)


# ---------------------------------------------------------------------------
# Test 3 — Cold read after _DEMO_JOBS is cleared
# ---------------------------------------------------------------------------


async def test_cold_read_after_demo_jobs_cleared_is_not_needs_information(client, db_pool) -> None:
    """After _DEMO_JOBS is cleared (simulating a restart) a GET on the workflow
    must reconstruct the full conflict card from DB — not degrade to NEEDS_INFORMATION.

    Checked: status, stage, customer_action.kind, task A/B identities, can_act.
    After acknowledge: cold GET must NOT rebuild the card.

    # route test
    """
    token = await _register_and_login(client, "sc_route_t3_owner")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'sc_route_t3_owner'"))
    session_id = str(uuid.uuid4())

    wid, fp = await _seed_conflict_workflow(db_pool, owner_id=owner_id, session_id=session_id)

    # Intentionally do NOT put anything in _DEMO_JOBS — cold read path.
    routes._DEMO_JOBS.pop(wid, None)

    res = await client.get(
        f"/api/v1/workflows/demo/{wid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # Must not re-ask for information — the conflict is still pending.
    assert body["status"] != "NEEDS_INFORMATION", (
        "cold read must not show a clarification card when a conflict is pending"
    )
    assert body["status"] == "WAITING_APPROVAL", body
    assert body.get("stage") == "WAITING_SCHEDULE_CONFLICT_CHECK", body

    # customer_action must carry the conflict card with full A/B info.
    ca = body.get("customer_action")
    assert ca is not None, "customer_action must be present on cold read"
    assert ca["kind"] == "SCHEDULE_CONFLICT", ca
    assert ca["can_act"] is True, "can_act must be true when conflict is pending"
    assert ca["task_a"]["service"] == _SVC_MOVE, ca
    assert ca["task_b"]["service"] == _SVC_MAINT, ca

    # After acknowledge: cold GET must not rebuild the conflict card.
    from src.orchestration.schedule_conflict import acknowledge_conflict

    await acknowledge_conflict(db_pool, fp)

    routes._DEMO_JOBS.pop(wid, None)
    res2 = await client.get(
        f"/api/v1/workflows/demo/{wid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    body2 = res2.json()
    ca2 = body2.get("customer_action")
    assert ca2 is None or ca2.get("kind") != "SCHEDULE_CONFLICT", (
        f"After acknowledge, the conflict card must not be shown again: customer_action={ca2}"
    )


# ---------------------------------------------------------------------------
# Test 4 — Chat "đúng rồi, giữ nguyên" (KEEP_BOTH) does NOT call Planner
# ---------------------------------------------------------------------------


async def test_keep_both_via_chat_does_not_call_planner(client, db_pool, monkeypatch) -> None:
    """When a pending conflict exists and the user says "giữ nguyên", the POST
    /start handler must route through the conflict lane and NEVER invoke
    _run_demo_job (the Planner entry-point).

    # route test
    """
    token = await _register_and_login(client, "sc_route_t4_owner")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'sc_route_t4_owner'"))
    session_id = str(uuid.uuid4())

    wid, _fp = await _seed_conflict_workflow(db_pool, owner_id=owner_id, session_id=session_id)

    # Ensure the session row exists so _conflict_dang_cho_trong_phien can find
    # the workflow (create_shell_and_session already creates it).

    planner_called: list[bool] = []

    async def _fake_planner(*_a: Any, **_kw: Any) -> None:
        planner_called.append(True)

    monkeypatch.setattr(routes, "_run_demo_job", _fake_planner)

    # Classifier returns KEEP_BOTH.
    from src.agents.conflict_intent import KetQuaXungDot, YDinhXungDot

    async def _fake_doc(_self: Any, *_a: Any, **_kw: Any) -> KetQuaXungDot:
        return KetQuaXungDot(y_dinh=YDinhXungDot.KEEP_BOTH)

    monkeypatch.setattr(
        "src.agents.conflict_intent.BoPhanLoaiXungDot.doc",
        _fake_doc,
    )

    # Mock resume so no real workflow execution happens.
    async def _fake_resume(*_a: Any, **_kw: Any) -> dict:
        return {"status": "WAITING_APPROVAL"}

    monkeypatch.setattr(routes, "resume_after_conflict_ack", _fake_resume)

    res = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "đúng rồi, giữ nguyên", "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Allow any 2xx or the conflict-lane's own response.
    assert res.status_code in (200, 202), res.text

    # Planner must NOT have been invoked.
    assert not planner_called, "_run_demo_job must not be called when conflict is pending"

    routes._DEMO_JOBS.pop(wid, None)


# ---------------------------------------------------------------------------
# Test 5 — Chat "đổi lịch bảo trì" (CHANGE_B) targets the right task
# ---------------------------------------------------------------------------


async def test_change_b_intent_via_chat_targets_maintenance_task(client, db_pool, monkeypatch) -> None:
    """When the user says "đổi lịch bảo trì" (CHANGE_B), the conflict-lane
    handler must clear the pending conflict check and initiate a repair for
    task_b (the maintenance task), NOT create a new workflow.

    # route test
    """
    token = await _register_and_login(client, "sc_route_t5_owner")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'sc_route_t5_owner'"))
    session_id = str(uuid.uuid4())

    wid, fp = await _seed_conflict_workflow(db_pool, owner_id=owner_id, session_id=session_id)

    workflow_count_before = await db_pool.fetchval("SELECT count(*) FROM workflows")

    planner_called: list[bool] = []

    async def _fake_planner(*_a: Any, **_kw: Any) -> None:
        planner_called.append(True)

    monkeypatch.setattr(routes, "_run_demo_job", _fake_planner)

    # Classifier returns CHANGE_B — user wants to reschedule task_b (maintenance).
    from src.agents.conflict_intent import KetQuaXungDot, YDinhXungDot

    async def _fake_doc(_self: Any, *_a: Any, **_kw: Any) -> KetQuaXungDot:
        return KetQuaXungDot(y_dinh=YDinhXungDot.CHANGE_B)

    monkeypatch.setattr(
        "src.agents.conflict_intent.BoPhanLoaiXungDot.doc",
        _fake_doc,
    )

    repaired: list[tuple[str, str]] = []

    async def _fake_repair(target_wf: str, target_task: str) -> dict:
        repaired.append((target_wf, target_task))
        return {"status": "NEEDS_INFORMATION"}

    monkeypatch.setattr(routes, "repair_conflict_task", _fake_repair)

    res = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "đổi lịch bảo trì", "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code in (200, 202), res.text
    body = res.json()

    # Response must reference the EXISTING workflow, not a brand-new one.
    assert body.get("workflow_id") == wid, (
        f"expected wid={wid}, got {body.get('workflow_id')} — a new workflow "
        "must not be created when the conflict lane handles the message"
    )

    # No new workflow was created.
    workflow_count_after = await db_pool.fetchval("SELECT count(*) FROM workflows")
    assert workflow_count_after == workflow_count_before, "CHANGE_B must not create an extra workflow row"

    # The conflict row was cleared (not merely acknowledged).
    remaining = await load_conflict_check(db_pool, wid)
    assert remaining is None, "conflict check must be cleared after CHANGE_B"

    # repair_conflict_task was called with the maintenance task (T2 = task_b).
    assert repaired, "repair_conflict_task must be called"
    repaired_wf, repaired_task = repaired[0]
    assert repaired_task == "T2", f"CHANGE_B must target task_b (T2), got {repaired_task}"

    # Planner must NOT have been invoked.
    assert not planner_called, "_run_demo_job must not be called"

    routes._DEMO_JOBS.pop(wid, None)


# ---------------------------------------------------------------------------
# Test 6 — UNKNOWN intent does NOT call Planner; returns conflict card
# ---------------------------------------------------------------------------


async def test_unknown_intent_does_not_call_planner(client, db_pool, monkeypatch) -> None:
    """When the classifier returns UNKNOWN (e.g. "hello how are you"), the
    conflict lane must ask the user to clarify — without calling _run_demo_job.

    # route test
    """
    token = await _register_and_login(client, "sc_route_t6_owner")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'sc_route_t6_owner'"))
    session_id = str(uuid.uuid4())

    wid, _fp = await _seed_conflict_workflow(db_pool, owner_id=owner_id, session_id=session_id)

    planner_called: list[bool] = []

    async def _fake_planner(*_a: Any, **_kw: Any) -> None:
        planner_called.append(True)

    monkeypatch.setattr(routes, "_run_demo_job", _fake_planner)

    # Classifier returns UNKNOWN.
    from src.agents.conflict_intent import KetQuaXungDot, YDinhXungDot

    async def _fake_doc(_self: Any, *_a: Any, **_kw: Any) -> KetQuaXungDot:
        return KetQuaXungDot(y_dinh=YDinhXungDot.UNKNOWN)

    monkeypatch.setattr(
        "src.agents.conflict_intent.BoPhanLoaiXungDot.doc",
        _fake_doc,
    )

    res = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "hello how are you", "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code in (200, 202), res.text
    body = res.json()

    # Must return the conflict card asking the user to clarify — not a new workflow.
    assert body.get("status") == "WAITING_APPROVAL", body
    assert body.get("stage") == "WAITING_SCHEDULE_CONFLICT_CHECK", body

    # Planner must NOT have been invoked.
    assert not planner_called, "_run_demo_job must not be called when conflict is pending"

    routes._DEMO_JOBS.pop(wid, None)


# ---------------------------------------------------------------------------
# Test 7 — Double-click / concurrent keep_both is idempotent
# ---------------------------------------------------------------------------


async def test_double_click_keep_both_side_effects_at_most_once(client, db_pool, monkeypatch) -> None:
    """Simulates two concurrent POST keep_both requests.  The conflict must be
    acknowledged at most once; resume_after_conflict_ack must fire at most once.

    # route test
    """
    token = await _register_and_login(client, "sc_route_t7_owner")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'sc_route_t7_owner'"))
    session_id = str(uuid.uuid4())

    wid, fp = await _seed_conflict_workflow(db_pool, owner_id=owner_id, session_id=session_id)
    _inject_conflict_job(wid, owner_id=owner_id, session_id=session_id)

    resume_calls: list[str] = []

    async def _fake_resume(workflow_id: str, *, owner_user_id: str, conflict_task_id: str, **_kw: str) -> dict:
        resume_calls.append(workflow_id)
        return {"status": "WAITING_APPROVAL"}

    monkeypatch.setattr(routes, "resume_after_conflict_ack", _fake_resume)

    # Fire both requests concurrently via asyncio.gather.
    res1, res2 = await asyncio.gather(
        client.post(
            f"/api/v1/workflows/demo/{wid}/conflict-respond",
            json={"choice": "keep_both"},
            headers={"Authorization": f"Bearer {token}"},
        ),
        client.post(
            f"/api/v1/workflows/demo/{wid}/conflict-respond",
            json={"choice": "keep_both"},
            headers={"Authorization": f"Bearer {token}"},
        ),
    )

    # Both calls must succeed (no 500, no unhandled error).
    assert res1.status_code == 200, res1.text
    assert res2.status_code == 200, res2.text

    # DB: acknowledged flag must be TRUE and there must be at most one acknowledgement.
    assert await is_acknowledged(db_pool, fp) is True

    ack_count = await db_pool.fetchval(
        "SELECT count(*) FROM schedule_conflict_checks WHERE fingerprint = $1 AND acknowledged = TRUE",
        fp,
    )
    assert ack_count == 1, f"expected exactly one acknowledged row, got {ack_count}"

    # resume must have fired at most once.
    assert len(resume_calls) <= 1, (
        f"resume_after_conflict_ack fired {len(resume_calls)} times — "
        "concurrent keep_both must not duplicate workflow execution"
    )

    routes._DEMO_JOBS.pop(wid, None)


# ---------------------------------------------------------------------------
# Test 8 — ScheduleConflictBoundary (no seed helper) creates conflict row
# ---------------------------------------------------------------------------


async def test_boundary_creates_conflict_row_and_keep_both_acknowledges(client, db_pool, monkeypatch) -> None:
    """ScheduleConflictBoundary.execute() called directly must persist the
    conflict row.  Subsequent HTTP keep_both must acknowledge it exactly once.

    This test does NOT use _seed_conflict_workflow; it calls the real boundary.
    # route test
    """
    from src.common.task_plan import Task, TaskPlan
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
    from src.orchestration.schedule_conflict import (
        ScheduleConflictBoundary,
        ScheduleConflictRequiredError,
        load_conflict_check,
    )

    token = await _register_and_login(client, "sc_route_t8_owner")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'sc_route_t8_owner'"))
    session_id = str(uuid.uuid4())
    wid = str(uuid.uuid4())

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    await repository.create_shell_and_session(
        workflow_id=wid,
        owner_user_id=owner_id,
        session_id=session_id,
        goal="Chuyển nhà và bảo trì cùng lúc",
        account_state="resident",
        resident_id=None,
    )

    plan = TaskPlan(
        goal="Chuyển nhà và bảo trì cùng lúc",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                depends_on=[],
                input={
                    "move_date": _DATE,
                    "move_time": _TIME,
                    "origin": "A",
                    "destination": "B",
                    "contact_phone": "0901234567",
                },
            ),
            Task(
                task_id="T2",
                tool="create_maintenance_request",
                depends_on=[],
                input={
                    "preferred_date": _DATE,
                    "preferred_time": _TIME,
                    "issue_description": "vòi nước hỏng",
                    "unit_id": "U1",
                },
            ),
        ],
    )

    class _StubBoundary:
        async def execute(self, *_a: Any, **_kw: Any) -> tuple:
            return ("WAITING_APPROVAL", {})

    boundary = ScheduleConflictBoundary(_StubBoundary(), repository=repository, owner_user_id=owner_id)

    raised = False
    try:
        await boundary.execute(plan, wid)
    except ScheduleConflictRequiredError:
        raised = True

    assert raised, "boundary must raise ScheduleConflictRequiredError for intra-plan conflict"

    conflict = await load_conflict_check(db_pool, wid)
    assert conflict is not None, "boundary must have persisted a conflict row"
    assert conflict["acknowledged"] is False
    assert conflict["service_a"] == "schedule_move"
    assert conflict["service_b"] == "create_maintenance_request"

    # Now POST keep_both and verify it acknowledges the row.
    _inject_conflict_job(wid, owner_id=owner_id, session_id=session_id)

    resume_calls: list[str] = []

    async def _fake_resume(workflow_id: str, *, owner_user_id: str, conflict_task_id: str, **_kw: str) -> dict:
        resume_calls.append(workflow_id)
        return {"status": "WAITING_APPROVAL"}

    monkeypatch.setattr(routes, "resume_after_conflict_ack", _fake_resume)

    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/conflict-respond",
        json={"choice": "keep_both"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text

    from src.orchestration.schedule_conflict import is_acknowledged

    assert await is_acknowledged(db_pool, conflict["fingerprint"]) is True
    assert len(resume_calls) == 1, "resume must fire exactly once after keep_both"

    routes._DEMO_JOBS.pop(wid, None)


# ---------------------------------------------------------------------------
# Test 9 — repair_conflict_task (real) writes SCHEDULE_CONFLICT_CHANGE_REQUESTED
# ---------------------------------------------------------------------------


async def test_repair_conflict_task_writes_correct_error_code(client, db_pool) -> None:
    """repair_conflict_task called for real must write SCHEDULE_CONFLICT_CHANGE_REQUESTED
    (not NO_AVAILABILITY) to workflow_repair_hints, with the correct task_id.

    # route test
    """
    from src.orchestration.demo_service import repair_conflict_task

    await _register_and_login(client, "sc_route_t9_owner")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'sc_route_t9_owner'"))
    session_id = str(uuid.uuid4())

    wid, _fp = await _seed_conflict_workflow(db_pool, owner_id=owner_id, session_id=session_id)

    # Call the real repair function — no mocks.
    result = await repair_conflict_task(wid, "T1")

    # Function must not crash and must signal repair is pending.
    assert result.get("repair_pending") is True, result

    # DB must have a repair hint with the conflict error code, not NO_AVAILABILITY.
    rows = await db_pool.fetch(
        "SELECT task_id, error_code FROM workflow_repair_hints WHERE workflow_id = $1::uuid",
        wid,
    )
    assert rows, "workflow_repair_hints must have at least one row"

    t1_hints = [r for r in rows if r["task_id"] == "T1"]
    assert t1_hints, f"no hint for T1; rows={list(rows)}"
    assert t1_hints[0]["error_code"] == "SCHEDULE_CONFLICT_CHANGE_REQUESTED", (
        f"expected SCHEDULE_CONFLICT_CHANGE_REQUESTED, got {t1_hints[0]['error_code']}"
    )

    routes._DEMO_JOBS.pop(wid, None)
