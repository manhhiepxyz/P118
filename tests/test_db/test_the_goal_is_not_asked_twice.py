"""Điều người dùng nói rõ trong goal phải sống qua clarification và restart.

Đây là nửa PERSISTENCE của cùng một contract; nửa kia — Planner hiểu câu, code
ràng buộc hậu quả — nằm ở `tests/test_planner_explicit_facts.py`.

Chuỗi đo được ban đầu: goal nói rõ "tôi đồng ý được liên hệ" và "cần người bốc
xếp", lượt hỏi đầu hỏi `description` + `needs_elevator`, và sau khi người dùng
trả lời hai ô ấy thì `existing_context` chỉ còn hai ô vừa trả lời cộng phần tin
cậy. Hai boolean kia biến mất, nên lượt Planner sau hỏi lại đúng điều người
dùng đã nói ở câu đầu tiên.

Bản trước đọc chúng bằng regex (`src/common/goal_facts.py`). Regex hỏng theo
kiểu không vá được — xem docstring của file test kia. Giờ Planner trả chúng
trong CÙNG lượt gọi nó vốn đã dùng để đọc goal, và các test dưới đây đo phần
còn lại: fact có tới được PostgreSQL, có quay về sau restart, và có nhường chỗ
cho câu trả lời mới của người dùng hay không.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.test_db.conftest import _register_and_login

GOAL_NHIEU_DICH_VU = (
    "Đặt lịch tham quan Vinhomes Green Paradise ngày 2026-09-04 lúc 10:30 xe đưa đón cho 2 khách "
    "tại ABCD liên hệ 09999822. Đăng ký quan tâm / nhận tư vấn Vinhomes Golden City nhu cầu "
    "Tìm hiểu thêm gọi lúc 09:30 tôi đồng ý được liên hệ. Đăng ký phương tiện và chỗ đỗ xe bắt "
    "đầu từ ngày 2026-08-22 Xe máy biển số 12M-88923 chỗ đỗ Khu A. Báo bảo trì / sửa chữa ngày "
    "2026-08-27 hạng mục Nước lúc 10:00 ở ad hư. Đặt lịch chuyển nhà ngày 2026-09-02 lúc 10:30 "
    "phương tiện Xe van cần người bốc xếp"
)

# Ngữ cảnh đã ghim ở lượt trước: hai boolean Planner đã hiểu, cộng phần tin cậy
# tra từ database. Đây chính là dict mà `/continue` đọc lại sau restart.
DA_GHIM = {
    "consent": True,
    "needs_loading_support": True,
    "resident_id": "RES-GOAL",
    "resident_verification_status": "VERIFIED",
}


async def _seed_pending_clarification(routes, *, owner_id, goal, missing_fields, context):
    workflow_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    await routes._ensure_workflow_shell(
        workflow_id, goal=goal, session_id=session_id, parent_workflow_id=None, owner_user_id=owner_id
    )
    await routes._persist_clarification(
        workflow_id,
        session_id=session_id,
        parent_workflow_id=None,
        goal=goal,
        missing_fields=list(missing_fields),
        question="Bạn mô tả giúp mình sự cố, và cho biết có cần thang máy không nhé.",
        existing_context=dict(context),
    )
    return workflow_id


def _capture(routes, monkeypatch):
    """Bắt `existing_context` THẬT truyền vào Planner của lượt sau."""
    thay: dict = {}
    xong = asyncio.Event()

    async def _bat(*_args, **kwargs):
        thay.update(kwargs.get("existing_context") or {})
        xong.set()
        return {"planner_status": "READY", "plan": None, "task_results": {}}

    monkeypatch.setattr(routes, "run_demo_workflow", _bat)
    return thay, xong


# ---------------------------------------------------------------------------
# Fact đi từ Planner xuống PostgreSQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_what_the_planner_understood_is_pinned_with_the_question(client, db_pool, monkeypatch):
    """Ghim fact CÙNG lượt hỏi. Không ghim thì lượt sau không có gì để đọc."""
    from src.api import routes

    token = await _register_and_login(client, "fact_ghim_cung_cau_hoi")
    ghi: dict = {}
    xong = asyncio.Event()

    async def _planner(*_args, **_kwargs):
        return {
            "planner_status": "NEEDS_INFORMATION",
            "missing_fields": ("description", "needs_elevator"),
            "question": "Bạn mô tả giúp mình sự cố nhé.",
            # Đây là thứ `plan_node` đặt vào state sau khi code đã kiểm trích dẫn.
            "explicit_facts": {"consent": True, "needs_loading_support": True},
            "task_results": {},
        }

    async def _bat_ghim(workflow_id, **kwargs):
        ghi.update(kwargs.get("existing_context") or {})
        xong.set()
        return True

    monkeypatch.setattr(routes, "run_demo_workflow", _planner)
    monkeypatch.setattr(routes, "_persist_clarification", _bat_ghim)

    response = await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": GOAL_NHIEU_DICH_VU},
    )
    assert response.status_code == 202, response.text
    await asyncio.wait_for(xong.wait(), timeout=10)

    assert ghi.get("consent") is True, "fact không được ghim cùng lượt hỏi"
    assert ghi.get("needs_loading_support") is True
    assert "needs_elevator" not in ghi, "ghim một ô còn đang hỏi"


# ---------------------------------------------------------------------------
# Fact quay về từ PostgreSQL — đây là đường /continue thật
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answering_two_fields_does_not_erase_what_the_goal_already_said(client, db_pool, monkeypatch):
    """Lỗi được báo, đo tại ranh giới thật: context truyền vào Planner lượt sau."""
    from src.api import routes

    token = await _register_and_login(client, "muc_tieu_khong_hoi_lai")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'muc_tieu_khong_hoi_lai'"))
    workflow_id = await _seed_pending_clarification(
        routes,
        owner_id=owner_id,
        goal=GOAL_NHIEU_DICH_VU,
        missing_fields=["description", "needs_elevator"],
        context=DA_GHIM,
    )
    routes._DEMO_JOBS.clear()
    thay, xong = _capture(routes, monkeypatch)

    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        headers={"Authorization": f"Bearer {token}"},
        json={"fields": {"description": "vòi nước hỏng", "needs_elevator": "có"}},
    )
    assert response.status_code in {200, 202}, response.text
    await asyncio.wait_for(xong.wait(), timeout=10)

    assert thay.get("consent") is True, "lời đồng ý đã ghim không tới được Planner lượt sau"
    assert thay.get("needs_loading_support") is True
    assert thay.get("description") == "vòi nước hỏng"
    assert thay.get("needs_elevator") is True
    assert thay.get("resident_id") == "RES-GOAL", "ngữ cảnh tin cậy bị mất"


@pytest.mark.asyncio
async def test_the_two_booleans_survive_a_restart(client, db_pool, monkeypatch):
    """Bộ nhớ tiến trình trống thì ngữ cảnh phải đến từ PostgreSQL."""
    from src.api import routes

    token = await _register_and_login(client, "muc_tieu_restart")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'muc_tieu_restart'"))
    workflow_id = await _seed_pending_clarification(
        routes,
        owner_id=owner_id,
        goal=GOAL_NHIEU_DICH_VU,
        missing_fields=["description", "needs_elevator"],
        context=DA_GHIM,
    )
    routes._DEMO_JOBS.clear()
    thay, xong = _capture(routes, monkeypatch)

    await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        headers={"Authorization": f"Bearer {token}"},
        json={"fields": {"description": "vòi nước hỏng", "needs_elevator": "có"}},
    )
    await asyncio.wait_for(xong.wait(), timeout=10)

    assert thay.get("consent") is True, "sau restart, lời đồng ý biến mất"
    assert thay.get("needs_loading_support") is True


@pytest.mark.asyncio
async def test_a_new_answer_overrides_the_pinned_fact(client, db_pool, monkeypatch):
    """Người dùng đổi ý được: câu vừa gõ thắng fact đã ghim."""
    from src.api import routes

    token = await _register_and_login(client, "muc_tieu_doi_y")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'muc_tieu_doi_y'"))
    workflow_id = await _seed_pending_clarification(
        routes,
        owner_id=owner_id,
        goal=GOAL_NHIEU_DICH_VU,
        missing_fields=["needs_loading_support"],
        context=DA_GHIM,
    )
    routes._DEMO_JOBS.clear()
    thay, xong = _capture(routes, monkeypatch)

    await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        headers={"Authorization": f"Bearer {token}"},
        json={"fields": {"needs_loading_support": "không"}},
    )
    await asyncio.wait_for(xong.wait(), timeout=10)

    assert thay.get("needs_loading_support") is False, "fact cũ đè lên câu người dùng vừa trả lời"
    assert thay.get("consent") is True, "ô không được hỏi thì không được đụng tới"


@pytest.mark.asyncio
async def test_the_goal_is_never_re_read_as_a_second_source_of_truth(client, db_pool, monkeypatch):
    """Ngữ cảnh đến TỪ POSTGRESQL, không từ việc đọc lại câu chữ.

    Dòng đã ghim nói `consent=False`; goal thì viết "tôi đồng ý được liên hệ".
    Nếu còn một bộ đọc goal nào chạy song song, nó sẽ thắng và test này đỏ.
    """
    from src.api import routes

    token = await _register_and_login(client, "muc_tieu_mot_nguon")
    owner_id = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'muc_tieu_mot_nguon'"))
    da_tu_choi = {**DA_GHIM, "consent": False, "needs_loading_support": False}
    workflow_id = await _seed_pending_clarification(
        routes,
        owner_id=owner_id,
        goal=GOAL_NHIEU_DICH_VU,
        missing_fields=["description"],
        context=da_tu_choi,
    )
    routes._DEMO_JOBS.clear()
    thay, xong = _capture(routes, monkeypatch)

    await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        headers={"Authorization": f"Bearer {token}"},
        json={"fields": {"description": "vòi nước hỏng"}},
    )
    await asyncio.wait_for(xong.wait(), timeout=10)

    assert thay.get("consent") is False, "có nguồn sự thật thứ hai đang đọc lại goal"
    assert thay.get("needs_loading_support") is False


@pytest.mark.asyncio
async def test_a_goal_reader_no_longer_exists_in_production(client, db_pool):
    """Hai nguồn cho cùng ba ô là một lệch chờ sẵn — không được có bộ đọc regex."""
    import src.api.routes as routes_module

    assert not hasattr(routes_module, "extract_goal_facts"), "bộ đọc goal bằng regex còn trong routes"
    with pytest.raises(ModuleNotFoundError):
        __import__("src.common.goal_facts")
