"""Response Agent nối vào workflow ở đâu, và nó KHÔNG được đụng vào cái gì.

Test đơn vị ở `tests/test_response_agent.py` kiểm bản thân lớp đó. Ở đây kiểm
điểm nối: nó chạy đúng lúc, câu trả lời đi ra tới API, và — quan trọng nhất —
một Response Agent hỏng hoặc nói linh tinh cũng không đổi được trạng thái
workflow, số tiền, hay kết quả từng bước.
"""

from __future__ import annotations

import asyncio

import pytest

from src.agents.response_agent import AgentReply
from src.api.routes import _run_demo_job as _original_run_demo_job
from tests.test_db.conftest import _register_and_login

GOAL = "Tôi muốn đăng ký xe và đặt chỗ đỗ xe."


class _FakeAgent:
    """Thay cho `ResponseAgent` thật — không gọi mạng."""

    def __init__(self, reply: AgentReply | None = None, error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error
        self.views: list = []

    def __call__(self, *_args, **_kwargs):
        return self

    async def reply(self, view):
        self.views.append(view)
        if self._error is not None:
            raise self._error
        return self._reply


async def _run_to_stop(client, monkeypatch, username: str, *, state: dict):
    """Chạy `_run_demo_job` thật với một workflow kết thúc ở `state`."""
    from src.api import routes

    scheduled: list[tuple] = []

    async def _defer(*args, **kwargs):
        scheduled.append((args, kwargs))

    monkeypatch.setattr(routes, "_run_demo_job", _defer)
    token = await _register_and_login(client, username)
    headers = {"Authorization": f"Bearer {token}"}
    started = await client.post("/api/v1/workflows/demo/start", headers=headers, json={"goal": GOAL})
    workflow_id = started.json()["workflow_id"]
    await asyncio.sleep(0)

    async def _finished(*_args, **_kwargs):
        return state

    monkeypatch.setattr(routes, "run_demo_workflow", _finished)
    args, kwargs = scheduled[0]
    await _original_run_demo_job(*args, **kwargs)
    # Câu trả lời được gắn ở một tác vụ nền, để không cộng thêm một lượt gọi
    # LLM vào thời gian người dùng phải chờ. Nhường vòng lặp cho nó chạy xong.
    await _drain_background()
    return workflow_id, headers


async def _drain_background() -> None:
    """Chờ mọi tác vụ nền của demo kết thúc."""
    from src.api import routes

    for _ in range(50):
        pending = [t for t in routes._DEMO_TASKS if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _chat_state():
    """State tối thiểu cho một workflow kết thúc — không cần Executor thật."""
    return {
        "planner_status": "NEEDS_INFORMATION",
        "question": "Bạn cho mình biết biển số xe nhé?",
        "missing_fields": ("plate_number",),
    }


@pytest.mark.asyncio
async def test_the_answer_reaches_the_api(client, db_pool, monkeypatch):
    from src.api import routes

    agent = _FakeAgent(AgentReply(answer="Mình cần thêm biển số xe của bạn nhé.", suggestions=["Báo bảo trì"]))
    monkeypatch.setattr(routes, "ResponseAgent", agent)

    workflow_id, headers = await _run_to_stop(client, monkeypatch, "nn_speak_ok", state=_chat_state())
    body = (await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers=headers)).json()

    assert body["answer"] == "Mình cần thêm biển số xe của bạn nhé."
    assert body["suggestions"] == ["Báo bảo trì"]


@pytest.mark.asyncio
async def test_a_broken_response_agent_does_not_break_the_workflow(client, db_pool, monkeypatch):
    """Câu trả lời là trang trí. Nó hỏng thì workflow vẫn phải nguyên vẹn."""
    from src.api import routes

    monkeypatch.setattr(routes, "ResponseAgent", _FakeAgent(error=RuntimeError("nổ")))

    workflow_id, headers = await _run_to_stop(client, monkeypatch, "nn_speak_broken", state=_chat_state())
    body = (await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers=headers)).json()

    assert body["status"] == "NEEDS_INFORMATION", "workflow bị lỗi lây từ lớp trả lời"
    assert body["missing_fields"] == ["plate_number"]
    assert body["question"]
    # Không có câu tự nhiên thì dùng câu deterministic, và nói rõ đó là dự
    # phòng — giao diện cần biết để không quảng cáo nó như câu của P-118.
    assert body.get("response_state") in (None, "FALLBACK")
    assert body.get("answer") != "", "câu dự phòng rỗng thì màn hình trống"


@pytest.mark.asyncio
async def test_the_agent_cannot_change_status_or_steps(client, db_pool, monkeypatch):
    """Kể cả khi nó "nói" workflow đã xong, trạng thái thật không đổi."""
    from src.api import routes

    liar = _FakeAgent(AgentReply(answer="Mọi thứ đã xong xuôi và mình không cần gì thêm cả."))
    monkeypatch.setattr(routes, "ResponseAgent", liar)

    workflow_id, headers = await _run_to_stop(client, monkeypatch, "nn_speak_liar", state=_chat_state())
    body = (await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers=headers)).json()

    assert body["status"] == "NEEDS_INFORMATION"
    assert body["missing_fields"] == ["plate_number"]

    db_status = await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    assert db_status != "SUCCESS", "lớp trả lời đổi được trạng thái trong database"


@pytest.mark.asyncio
async def test_the_view_given_to_the_agent_carries_no_internals(client, db_pool, monkeypatch):
    from src.api import routes

    agent = _FakeAgent(AgentReply(answer="Mình cần thêm chút thông tin từ bạn nhé."))
    monkeypatch.setattr(routes, "ResponseAgent", agent)

    await _run_to_stop(client, monkeypatch, "nn_speak_view", state=_chat_state())

    assert agent.views, "Response Agent không được gọi ở điểm dừng"
    view = agent.views[0]
    dumped = view.model_dump_json()
    for leaked in ("postgresql://", "input_data", "task_plan", "owner_user_id", "session_id", "Bearer"):
        assert leaked not in dumped, f"view mang theo {leaked!r}"
    # Field còn thiếu đi vào view ở dạng NHÃN tiếng Việt, không phải tên kỹ thuật.
    assert "plate_number" not in dumped


@pytest.mark.asyncio
async def test_it_is_not_called_while_the_workflow_is_still_running(client, db_pool, monkeypatch):
    """Gọi mỗi lượt poll sẽ đốt một request LLM mỗi 1.5 giây."""
    from src.api import routes

    agent = _FakeAgent(AgentReply(answer="Mình cần thêm chút thông tin từ bạn nhé."))
    monkeypatch.setattr(routes, "ResponseAgent", agent)

    workflow_id, headers = await _run_to_stop(client, monkeypatch, "nn_speak_poll", state=_chat_state())
    before = len(agent.views)
    assert before == 1, "Response Agent phải được gọi đúng một lần ở điểm dừng"
    for _ in range(3):
        await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers=headers)
        await _drain_background()

    assert len(agent.views) == before, "mỗi lượt poll lại gọi mô hình một lần"


@pytest.mark.asyncio
async def test_the_fallback_sentence_never_claims_completion_too_early(client, db_pool, monkeypatch):
    """Câu dự phòng cũng phải đúng — nó không đi qua bộ kiểm nào cả.

    Bản trước rơi về `_STAGE_MESSAGES["FINISHED"]` cho MỌI trạng thái, nên một
    workflow đang chờ người dùng bổ sung thông tin nhận được câu "Yêu cầu đã
    hoàn tất.". Bộ kiểm của Response Agent chặn đúng câu đó khi MÔ HÌNH nói nó
    — nhưng ở đây nó là mặc định của chính chúng ta.
    """
    from src.api import routes

    # Dùng ResponseAgent THẬT với một LLM hỏng: chỉ đường đó mới chạy qua
    # `_fallback(view)`. Patch cả ResponseAgent thì `_speak` bắt lỗi ở vòng
    # ngoài, `answer` giữ nguyên None, và test không kiểm được gì.
    class _BrokenLLM:
        def with_structured_output(self, *a, **k):  # noqa: ANN002, ANN003, ANN001, ARG002
            return self

        async def ainvoke(self, messages):  # noqa: ANN001, ARG002
            raise RuntimeError("nhà cung cấp không phản hồi")

    monkeypatch.setattr(routes, "get_llm", lambda *a, **k: _BrokenLLM())
    monkeypatch.setattr(routes, "structured_output_method", lambda *a, **k: None)
    workflow_id, headers = await _run_to_stop(client, monkeypatch, "nn_speak_baseline", state=_chat_state())
    body = (await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers=headers)).json()

    assert body.get("answer"), "không đi qua đường dự phòng — test không kiểm được gì"
    spoken = f"{body['answer']} {body.get('message') or ''}".lower()
    for claim in ("đã hoàn tất", "đã hoàn thành", "đã xong", "đã thanh toán"):
        assert claim not in spoken, f"câu dự phòng khẳng định {claim!r} khi workflow còn chờ thông tin"


def test_every_public_status_has_a_baseline_sentence():
    """Thiếu một trạng thái thì `_DEFAULT_BASELINE[...]` ném KeyError giữa job."""
    from src.api.routes import _DEFAULT_BASELINE
    from src.models.schemas import DemoWorkflowResponse

    declared = set(DemoWorkflowResponse.model_fields["status"].annotation.__args__)
    assert declared <= set(_DEFAULT_BASELINE), declared - set(_DEFAULT_BASELINE)


def test_the_payment_decision_path_also_asks_for_a_spoken_answer():
    """Duyệt thanh toán là điểm dừng người dùng chú ý nhất — đừng im lặng ở đó.

    Đường này KHÔNG đi qua `_run_demo_job`, nên nó từng không sinh câu trả lời
    nào: `answer` vắng mặt và giao diện lặng lẽ rơi về câu ghép cứng. Bug chỉ lộ
    ra khi so `answer` với `summary` trên một workflow đã thanh toán xong.

    Đây là guard CẤU TRÚC, yếu hơn một phép kiểm hành vi: nó chỉ khẳng định
    handler có gọi `_attach_answer`. Bằng chứng hành vi nằm ở browser E2E (mục
    6h) và ở `scripts`/probe chạy trên stack thật. Guard này tồn tại vì dựng
    một lần duyệt thanh toán đầy đủ trong test ASGI đòi cả một workflow đỗ xe
    thật, và cái giá đó không đáng cho một dòng bị quên.
    """
    import inspect

    from src.api.routes import decide_demo_payment

    body = inspect.getsource(decide_demo_payment)
    assert "_attach_answer" in body, "đường duyệt thanh toán không sinh câu trả lời"


@pytest.mark.asyncio
async def test_the_route_actually_writes_the_answer_to_postgresql(client, db_pool, monkeypatch):
    """Test gọi thẳng repository KHÔNG chứng minh route có ghi.

    Mutation bỏ lời gọi `_save_answer_safely` trong route vẫn để toàn bộ test
    persistence xanh, vì chúng thao tác trực tiếp trên repository. Chỗ duy nhất
    bắt được là đọc DATABASE sau khi chạy đúng đường production.
    """
    from src.api import routes

    agent = _FakeAgent(AgentReply(answer="Mình cần thêm biển số xe của bạn nhé.", suggestions=[]))
    monkeypatch.setattr(routes, "ResponseAgent", agent)

    workflow_id, _ = await _run_to_stop(client, monkeypatch, "nn_speak_persist", state=_chat_state())

    row = await db_pool.fetchrow(
        "SELECT assistant_answer, assistant_response_state, assistant_for_status "
        "FROM workflows WHERE workflow_id = $1::uuid",
        workflow_id,
    )
    assert row["assistant_answer"] == "Mình cần thêm biển số xe của bạn nhé."
    assert row["assistant_response_state"] == "READY"
    assert row["assistant_for_status"] == "NEEDS_INFORMATION"


def test_the_response_layer_is_tracked_as_its_own_usage_stage():
    """Không tách stage thì chi phí lớp trả lời dồn hết vào "plan".

    Lớp này chạy MỘT lần cho mỗi điểm dừng của mọi workflow, nên nó là một
    khoản chi thật. Gộp vào `plan` thì không ai biết nó tốn bao nhiêu, và câu
    hỏi "có nên tắt lớp trả lời không" không có số để trả lời.
    """
    import inspect

    from src.api.routes import _speak

    body = inspect.getsource(_speak)
    assert 'stage="respond"' in body, "lớp trả lời không có stage usage riêng"
