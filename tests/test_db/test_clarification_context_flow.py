"""Câu trả lời clarification phải tới được Planner của workflow con.

Test này KHÔNG giả định kết luận. Nó bắt đúng lời gọi Planner của workflow con
và soi `existing_context` thật sự được truyền vào. Nếu context đủ bốn giá trị
đã chuẩn hoá thì dữ liệu không bị mất, và lỗi "hỏi lại field đã trả lời" nằm ở
phía Planner chứ không ở đường API.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.test_db.conftest import _register_and_login


@pytest.mark.asyncio
async def test_structured_answers_reach_the_child_planner_context(client, db_pool, monkeypatch):
    """Bốn field form phải xuất hiện trong context của Planner con, đã chuẩn hoá."""
    from src.api import routes

    token = await _register_and_login(client, "nn_ctx_flow")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_ctx_flow'"))
    headers = {"Authorization": f"Bearer {token}"}

    workflow_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    await routes._ensure_workflow_shell(
        workflow_id,
        goal="Tôi muốn đặt chỗ đỗ xe.",
        session_id=session_id,
        parent_workflow_id=None,
        owner_user_id=owner_id,
    )
    trusted = {"resident_id": "RES-CTX", "resident_verification_status": "VERIFIED"}
    await routes._persist_clarification(
        workflow_id,
        session_id=session_id,
        parent_workflow_id=None,
        goal="Tôi muốn đặt chỗ đỗ xe.",
        missing_fields=["plate_number", "vehicle_type", "booking_date", "parking_zone"],
        question="Cho mình xin thêm thông tin.",
        existing_context=trusted,
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
        headers=headers,
        json={
            "fields": {
                "plate_number": "30A-77777",
                "vehicle_type": "ô tô",
                "booking_date": "2030-07-15",
                "parking_zone": "Khu A",
            }
        },
    )
    assert response.status_code in {200, 202}, response.text

    try:
        await asyncio.wait_for(done.wait(), timeout=10)
    except TimeoutError:
        pytest.fail("Planner của workflow con không được gọi")

    # Giá trị phải đã CHUẨN HOÁ, không phải chuỗi thô người dùng gõ.
    assert seen.get("plate_number") == "30A-77777"
    assert seen.get("vehicle_type") in {"car", "motorcycle"}
    assert seen.get("booking_date") == "2030-07-15"
    assert seen.get("parking_zone") in {"ZONE_A", "ZONE_B"}

    # Ngữ cảnh cư dân đã xác minh phải còn nguyên — mất nó là mất quyền.
    assert seen.get("resident_verification_status") == "VERIFIED"
    assert seen.get("resident_id") == "RES-CTX"

    # KHÔNG được nhét body thô hay câu nguyên văn vào context: Planner phải đọc
    # field đã chuẩn hoá, không phải đọc lại tiếng Việt tự do.
    blob = " ".join(str(v) for v in seen.values())
    assert "Khu A" not in blob and "ô tô" not in blob
