"""Response Agent nối vào workflow ở đâu, và nó KHÔNG được đụng vào cái gì.

Test đơn vị ở `tests/test_response_agent.py` kiểm bản thân lớp đó. Ở đây kiểm
điểm nối: nó chạy đúng lúc, câu trả lời đi ra tới API, và — quan trọng nhất —
một Response Agent hỏng hoặc nói linh tinh cũng không đổi được trạng thái
workflow, số tiền, hay kết quả từng bước.
"""

from __future__ import annotations

import asyncio
import uuid

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


def test_every_decision_path_asks_for_a_spoken_answer():
    """MỌI đường quyết định, không chỉ đường thanh toán.

    Các đường này KHÔNG đi qua `_run_demo_job`, nên không có gì tự sinh câu trả
    lời cho tình huống MỚI. Bản trước chỉ kiểm đường thanh toán — và đúng thứ
    nó không kiểm là thứ bị quên: duyệt dịch vụ và duyệt lịch tham quan đều im
    lặng, nên khách vừa được duyệt xong vẫn đọc câu của lúc còn đang chờ.

    Đo được: duyệt cả hai bước → `pay_fee` sang WAITING_APPROVAL với báo giá
    100.000 VND, mà `answer` vẫn là "đang chờ đơn vị cung cấp dịch vụ xác nhận".

    Guard CẤU TRÚC, yếu hơn một phép kiểm hành vi: nó chỉ khẳng định handler có
    gọi `request_fresh_answer`. Nó tồn tại vì dựng một lần duyệt đầy đủ trong
    test ASGI đòi cả một workflow đỗ xe thật, và cái giá đó không đáng cho một
    dòng bị quên. Bằng chứng hành vi nằm ở probe chạy trên stack thật.
    """
    import inspect

    from src.api.routes import cancel_demo_workflow, continue_demo_workflow, decide_demo_payment
    from src.api.service_approval_routes import decide_service_approval
    from src.api.viewing_approval_routes import decide_viewing_approval

    # `continue` là đường NGƯỜI DÙNG đi nhiều nhất — mỗi lần họ sửa một ô rồi
    # gửi lại. Bản trước của test này chỉ liệt kê các đường DUYỆT, nên nó bỏ
    # sót đúng đường ấy: người dùng đổi ngày, bước hỏng được mở lại đúng với
    # ngày mới, và màn hình không đổi một chữ nào. Họ báo "chẳng có thay đổi
    # gì" — và họ đúng.
    duong_quyet_dinh = {
        "duyệt thanh toán": decide_demo_payment,
        "duyệt dịch vụ": decide_service_approval,
        "duyệt lịch tham quan": decide_viewing_approval,
        "huỷ yêu cầu": cancel_demo_workflow,
        "sửa rồi chạy lại": continue_demo_workflow,
    }
    thieu = [
        ten for ten, handler in duong_quyet_dinh.items() if "request_fresh_answer" not in inspect.getsource(handler)
    ]
    assert not thieu, f"{thieu} không xin câu trả lời mới — khách sẽ đọc lại câu của tình huống trước"


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


# ---------------------------------------------------------------------------
# Nhánh khách vừa HỎI: câu nền phải nói thật, và dữ liệu phải được tra
# ---------------------------------------------------------------------------


def test_the_question_branch_does_not_claim_it_already_answered():
    """`status="CHAT"` mang HAI nghĩa, và câu nền của chúng phải khác nhau.

    Small-talk trả CHAT khi câu trả lời ĐÃ nằm sẵn phía trên. Nhánh câu hỏi
    cũng trả CHAT — để frontend dừng poll — nhưng câu trả lời chưa được viết.
    Dùng chung câu nền nghĩa là khi guard loại câu của model, khách đọc đúng
    một lời khẳng định sai: "Mình đã trả lời bạn ở trên." cho một câu chưa ai
    đáp. Đo được trên stack thật, `response_state = FALLBACK`.
    """
    from src.api.routes import _QUESTION_BASELINE, _reply_view
    from src.models.schemas import DemoWorkflowResponse

    asked = _reply_view(
        DemoWorkflowResponse(status="CHAT", stage="QUESTION"),
        goal="ngày nào còn trống chỗ đỗ xe",
        capabilities=[],
    )
    answered = _reply_view(
        DemoWorkflowResponse(status="CHAT", stage="CHAT"),
        goal="cảm ơn bạn nhé",
        capabilities=[],
    )

    assert asked.baseline_message == _QUESTION_BASELINE
    assert "đã trả lời bạn ở trên" not in asked.baseline_message
    # Small-talk KHÔNG được đổi: ở đó câu trả lời thật sự nằm phía trên.
    assert answered.baseline_message == "Mình đã trả lời bạn ở trên."


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        ("ngày nào còn trống chỗ đỗ xe", True),
        ("khu B còn chỗ không?", True),
        # Đúng câu đã bịa ra "ngày 25, 27 và 30 tháng 8" trên stack thật. Nó
        # không chứa chữ "xe" nào — chỉ tên khu.
        ("khu B còn trống ngày nào?", True),
        ("bãi xe hôm nào hết chỗ vậy", True),
        # Hỏi GIÁ, không hỏi chỗ trống. Nhét thêm bảng chỗ trống vào câu trả
        # lời về phí là trả lời một câu không ai hỏi.
        ("phí gửi xe ô tô một tháng khoảng bao nhiêu", False),
        # "còn trống" nhưng không nói gì tới xe.
        ("lịch tham quan ngày nào còn trống", False),
        ("đặt chỗ đỗ xe khu A ngày 2026-09-01", False),
        ("xin chào", False),
        ("", False),
    ],
)
def test_only_a_real_availability_question_opens_a_database_query(goal: str, expected: bool):
    """Nhận diện TẤT ĐỊNH, không hỏi model.

    Đây là thứ mở một truy vấn database; thêm một lượt gọi mô hình để phân loại
    vừa tốn tiền vừa thêm một chỗ có thể sai.
    """
    from src.api.routes import _asks_parking_availability

    assert _asks_parking_availability(goal) is expected


@pytest.mark.asyncio
async def test_the_lookup_matches_what_booking_will_actually_allow(db_pool):
    """Cách đếm chỗ trống PHẢI trùng khít với đường cưỡng chế.

    Lệch một chút thôi là hệ thống nói còn chỗ rồi từ chối ngay sau đó — tệ hơn
    hẳn việc không trả lời được.
    """
    from datetime import date, timedelta

    from src.db.capacity_repository import CapacityRepository, NoAvailabilityError

    repo = CapacityRepository(db_pool)
    when = date.today() + timedelta(days=3)

    # `zone_capacity_config` là bảng CẤU HÌNH, không nằm trong `clean_tables`.
    # Sửa xong mà không trả lại thì test này bẻ gãy
    # `test_zone_b_has_room_for_a_demo` ở file khác — đo được đúng như vậy.
    original = await db_pool.fetchval("SELECT capacity FROM zone_capacity_config WHERE parking_zone = 'ZONE_B'")
    await db_pool.execute("UPDATE zone_capacity_config SET capacity = 1 WHERE parking_zone = 'ZONE_B'")
    await db_pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
        "VALUES ('RES-AV', 'Khách tra chỗ trống', 'AV-01', 'Khu A') ON CONFLICT DO NOTHING"
    )
    for n in (1, 2):
        await db_pool.execute(
            "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type) "
            "VALUES ($1, 'RES-AV', $2, 'car') ON CONFLICT DO NOTHING",
            f"VEH-AV-{n}",
            f"51A-0000{n}",
        )

    try:
        before = next(r for r in await repo.availability(when, 1) if r["parking_zone"] == "ZONE_B")
        assert before["remaining"] == 1

        await repo.check_and_reserve_capacity("ZONE_B", when.isoformat(), "BK-AV-1", "VEH-AV-1", 100000)

        after = next(r for r in await repo.availability(when, 1) if r["parking_zone"] == "ZONE_B")
        assert after["remaining"] == 0

        # Và lời hứa "hết chỗ" phải đúng: lượt đặt tiếp theo bị từ chối thật.
        with pytest.raises(NoAvailabilityError):
            await repo.check_and_reserve_capacity("ZONE_B", when.isoformat(), "BK-AV-2", "VEH-AV-2", 100000)
    finally:
        await db_pool.execute("UPDATE zone_capacity_config SET capacity = $1 WHERE parking_zone = 'ZONE_B'", original)


# ---------------------------------------------------------------------------
# Một câu trả lời mô tả một TÌNH HUỐNG, không phải một trạng thái
# ---------------------------------------------------------------------------


def test_the_two_kinds_of_waiting_are_not_the_same_situation():
    """`WAITING_APPROVAL` mang hai tình huống, và chúng NỐI TIẾP nhau.

    Đơn vị duyệt xong thì tới lượt khách xác nhận tiền — cùng workflow, cùng
    `status`, hai câu hoàn toàn khác nhau. Khoá chỉ theo `status` thì
    `claim_assistant_response` thấy khoá không đổi nên không giành quyền sinh,
    và câu cũ ở lại. Đo được: duyệt xong, `pay_fee` đã sang WAITING_APPROVAL với
    báo giá 100.000 VND, mà khách vẫn đọc "đang chờ đơn vị cung cấp dịch vụ
    xác nhận".
    """
    from src.api.routes import answer_key

    assert answer_key("WAITING_APPROVAL", "PROVIDER") != answer_key("WAITING_APPROVAL", "USER")
    # Trạng thái không mơ hồ thì khoá vẫn đúng bằng trạng thái — không đổi
    # nghĩa dữ liệu đã ghi của mọi workflow cũ.
    assert answer_key("SUCCESS") == "SUCCESS"
    assert answer_key("CHAT") == "CHAT"
    assert answer_key("WAITING_APPROVAL") == "WAITING_APPROVAL"


def test_a_list_row_still_shows_the_answer_it_has():
    """Dòng trong danh sách chỉ có `status`, không có `approval_actor`.

    So nguyên khoá ở đây sẽ giấu MỌI câu của nhánh chờ duyệt — đúng lớp lỗi mà
    bộ lọc này từng gây ra với `CHAT`.
    """
    from src.api.routes import _assistant_fields

    row = {
        "status": "WAITING_APPROVAL",
        "assistant_for_status": "WAITING_APPROVAL:PROVIDER",
        "assistant_answer": "Đơn vị đang xác nhận giúp bạn nhé.",
        "assistant_suggestions": [],
        "assistant_response_state": "READY",
    }
    assert _assistant_fields(row)["answer"] == "Đơn vị đang xác nhận giúp bạn nhé."

    # Nhưng câu của một trạng thái KHÁC HẲN thì vẫn phải bị chặn.
    stale = {**row, "status": "SUCCESS"}
    assert _assistant_fields(stale)["answer"] is None


@pytest.mark.parametrize(
    ("database_status", "expected"),
    [
        ("SUCCESS", "SUCCESS"),
        ("FAILED", "FAILED"),
        ("CANCELLED", "CANCELLED"),
        # Từng bị gộp vào RUNNING ở CẢ HAI bản sao của luật này.
        ("WAITING_APPROVAL", "WAITING_APPROVAL"),
        ("PENDING", "RUNNING"),
        ("RUNNING", "RUNNING"),
        (None, "RUNNING"),
    ],
)
def test_the_database_status_is_translated_by_exactly_one_rule(database_status, expected):
    from src.api.routes import public_status_from_db

    assert public_status_from_db(database_status) == expected


def test_the_status_rule_is_not_written_a_second_time():
    """Hai bản sao của một luật không hỏng cùng lúc — chúng hỏng LỆCH nhau.

    Luật này từng được viết hai lần bằng cùng một chuỗi if/elif, và cả hai bản
    đều đánh rơi `WAITING_APPROVAL`. Guard bắt bản sao thứ hai xuất hiện lại.
    """
    import re
    from pathlib import Path

    source = Path("src/api/routes.py").read_text(encoding="utf-8")
    # Chuỗi so sánh trực tiếp cột trạng thái database — dấu hiệu của một bản
    # dịch viết tay thay vì gọi `public_status_from_db`.
    inline = re.findall(r'database_status\s*==\s*"(?:SUCCESS|FAILED|CANCELLED)"', source)
    assert not inline, (
        f"{len(inline)} chỗ tự dịch `database_status` — dùng `public_status_from_db()` thay vì viết lại luật"
    )


@pytest.mark.asyncio
async def test_the_answer_is_rewritten_when_the_waiting_changes_hands(client, db_pool, monkeypatch):
    """Kiểm HÀNH VI, không chỉ cấu trúc.

    Test cấu trúc ở trên không bắt được đột biến quan trọng nhất: gỡ
    `approval_actor` khỏi khoá lúc GHI thì cả bộ vẫn xanh, trong khi đó chính
    là con đường sinh ra lỗi. Chỗ duy nhất bắt được là chạy đúng đường ghi rồi
    đọc lại `workflows.assistant_for_status`.

    Nhận `client` để lifespan đăng ký repository provider — `_attach_answer`
    ghi qua provider đó, không qua `db_pool` trực tiếp.
    """
    from src.api import routes
    from src.models.schemas import DemoWorkflowResponse

    workflow_id = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid, 'kiểm khoá', 'WAITING_APPROVAL')",
        workflow_id,
    )

    async def _run(actor: str, answer: str) -> None:
        monkeypatch.setattr(routes, "ResponseAgent", _FakeAgent(AgentReply(answer=answer, suggestions=[])))
        job = {
            "response": DemoWorkflowResponse(workflow_id=workflow_id, status="WAITING_APPROVAL", approval_actor=actor),
            "goal": "kiểm khoá",
        }
        await routes._attach_answer(job, workflow_id, goal="kiểm khoá")

    await _run("PROVIDER", "Đơn vị đang xác nhận giúp bạn nhé.")
    first = await db_pool.fetchrow(
        "SELECT assistant_for_status, assistant_answer FROM workflows WHERE workflow_id = $1::uuid",
        workflow_id,
    )
    assert first["assistant_for_status"] == "WAITING_APPROVAL:PROVIDER"

    # Đơn vị duyệt xong → tới lượt khách xác nhận tiền. CÙNG `status`, khác
    # tình huống. Không đổi khoá thì `claim_assistant_response` không giành
    # được quyền sinh và câu cũ ở lại nguyên vẹn.
    await _run("USER", "Bạn xác nhận khoản 100.000 VND giúp mình nhé.")
    second = await db_pool.fetchrow(
        "SELECT assistant_for_status, assistant_answer FROM workflows WHERE workflow_id = $1::uuid",
        workflow_id,
    )
    assert second["assistant_for_status"] == "WAITING_APPROVAL:USER"
    assert second["assistant_answer"] != first["assistant_answer"], (
        "câu của lượt chờ trước còn nguyên sau khi việc chờ đã đổi tay"
    )
