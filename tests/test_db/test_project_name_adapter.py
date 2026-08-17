"""Contract công khai dùng `project_name`; TaskPlan vẫn dùng `project_id`.

Người dùng không biết `PRJ-xxx` và không có cách nào tra được, nên hỏi họ
`project_id` là hỏi một thứ chắc chắn không trả lời được: form hiện ô trống, họ
gõ tên dự án, và `/continue` trả 422. Adapter nằm ở đúng biên API — bên trong
biên đó Planner và provider không đổi gì.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_db.conftest import _register_and_login


async def _pending_viewing(routes, db_pool, username: str):
    """Dựng một workflow đang chờ bổ sung thông tin dự án."""
    owner = str(await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username))
    workflow_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    await routes._ensure_workflow_shell(
        workflow_id,
        goal="Tôi muốn đặt lịch xem nhà.",
        session_id=session_id,
        parent_workflow_id=None,
        owner_user_id=owner,
    )
    await routes._persist_clarification(
        workflow_id,
        session_id=session_id,
        parent_workflow_id=None,
        goal="Tôi muốn đặt lịch xem nhà.",
        # Ghim theo tên CÔNG KHAI — đúng thứ client đã nhìn thấy.
        missing_fields=["project_name", "viewing_date", "viewing_time"],
        question="Bạn muốn xem dự án nào?",
        existing_context={},
    )
    routes._DEMO_JOBS.clear()
    return workflow_id


def test_the_public_alias_maps_the_internal_field() -> None:
    from src.api.routes import _to_public_missing_fields

    assert _to_public_missing_fields(["project_id", "viewing_date"]) == ["project_name", "viewing_date"]


def test_a_supported_project_name_resolves_to_its_internal_id() -> None:
    from src.api.routes import _resolve_public_answers

    resolved, bad = _resolve_public_answers({"project_name": "Vinhomes Ocean Park", "viewing_time": "09:30"})

    assert bad is None
    assert resolved["project_id"].startswith("PRJ-")
    # Tên không đi tiếp: giữ cả hai là để hai nguồn cùng mô tả một dự án.
    assert "project_name" not in resolved


def test_an_unsupported_project_name_is_reported_not_guessed() -> None:
    from src.api.routes import _resolve_public_answers

    resolved, bad = _resolve_public_answers({"project_name": "Khu đô thị Không Có Thật"})

    assert bad == "project_name"
    assert "project_id" not in resolved


@pytest.mark.asyncio
async def test_continue_with_a_project_name_puts_the_id_into_the_child_context(client, db_pool, monkeypatch):
    """Tên dự án của người dùng phải thành `project_id` trước khi tới Planner."""
    import asyncio

    from src.api import routes

    token = await _register_and_login(client, "nn_prj_ok")
    workflow_id = await _pending_viewing(routes, db_pool, "nn_prj_ok")

    seen: dict = {}
    done = asyncio.Event()

    async def _capture(*_args, **kwargs):
        seen.update(kwargs.get("existing_context") or {})
        done.set()
        return {"planner_status": "READY", "plan": None, "task_results": {}}

    monkeypatch.setattr(routes, "run_demo_workflow", _capture)

    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        headers={"Authorization": f"Bearer {token}"},
        json={"fields": {"project_name": "Vinhomes Ocean Park", "viewing_date": "2030-07-15", "viewing_time": "09:30"}},
    )
    assert response.status_code in {200, 202}, response.text

    await asyncio.wait_for(done.wait(), timeout=10)

    assert seen.get("project_id", "").startswith("PRJ-")
    assert "project_name" not in seen


@pytest.mark.asyncio
async def test_combined_viewing_and_parking_form_accepts_every_displayed_value(client, db_pool, monkeypatch):
    """Khoá payload thực tế của form kết hợp từng trả 422 dù mọi ô đều hợp lệ."""
    import asyncio

    from src.api import routes

    token = await _register_and_login(client, "nn_prj_combo")
    owner = str(await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_prj_combo"))
    workflow_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    await routes._ensure_workflow_shell(
        workflow_id,
        goal="Đặt lịch tham quan và chỗ đỗ xe.",
        session_id=session_id,
        parent_workflow_id=None,
        owner_user_id=owner,
    )
    missing = ["project_name", "viewing_time", "plate_number", "vehicle_type", "parking_zone"]
    await routes._persist_clarification(
        workflow_id,
        session_id=session_id,
        parent_workflow_id=None,
        goal="Đặt lịch tham quan và chỗ đỗ xe.",
        missing_fields=missing,
        question="Bạn bổ sung thông tin giúp mình nhé?",
        existing_context={},
    )
    routes._DEMO_JOBS.clear()

    seen: dict = {}
    done = asyncio.Event()

    async def _capture(*_args, **kwargs):
        seen.update(kwargs.get("existing_context") or {})
        done.set()
        return {"planner_status": "READY", "plan": None, "task_results": {}}

    monkeypatch.setattr(routes, "run_demo_workflow", _capture)
    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "fields": {
                "project_name": "Vinhomes Global Gate Hạ Long",
                "viewing_time": "12:00",
                "plate_number": "21A-29292",
                "vehicle_type": "motorcycle",
                "parking_zone": "ZONE_A",
            }
        },
    )

    assert response.status_code == 202, response.text
    await asyncio.wait_for(done.wait(), timeout=10)
    assert set(seen) >= {"project_id", "viewing_time", "plate_number", "vehicle_type", "parking_zone"}


@pytest.mark.asyncio
async def test_an_unknown_project_is_422_and_leaves_the_clarification_open(client, db_pool):
    """Tên lạ không được đốt mất lượt hỏi — người dùng còn sửa lại được."""
    from src.api import routes

    token = await _register_and_login(client, "nn_prj_la")
    workflow_id = await _pending_viewing(routes, db_pool, "nn_prj_la")
    headers = {"Authorization": f"Bearer {token}"}

    bad = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        headers=headers,
        json={"fields": {"project_name": "Khu Không Tồn Tại", "viewing_date": "2030-07-15", "viewing_time": "09:30"}},
    )

    assert bad.status_code == 422, bad.text
    assert "PRJ-" not in bad.text, "không được lộ mã nội bộ trong lỗi"

    still_open = await db_pool.fetchval(
        "SELECT resolved_at IS NULL FROM workflow_clarifications WHERE workflow_id = $1::uuid", workflow_id
    )
    assert still_open is True, "clarification bị consume dù câu trả lời sai"


@pytest.mark.asyncio
async def test_the_public_response_never_shows_an_internal_project_code(client, db_pool):
    """UI không được thấy `PRJ-xxx` ở bất kỳ đâu."""
    from src.api import routes

    token = await _register_and_login(client, "nn_prj_an")
    workflow_id = await _pending_viewing(routes, db_pool, "nn_prj_an")

    seen = await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers={"Authorization": f"Bearer {token}"})

    assert "PRJ-" not in seen.text
    assert "project_id" not in seen.text
