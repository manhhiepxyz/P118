import asyncio

import pytest

from src.api import routes
from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.models.schemas import DemoWorkflowRequest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_chat_empty_message(client):
    response = await client.post("/api/v1/chat", json={"message": ""})
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/status")
    assert response.status_code == 200


def _demo_plan(with_payment: bool = False) -> TaskPlan:
    tasks = [
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "TEST_PERSON",
                "apartment_code": "TEST_APARTMENT",
                "residential_area": "TEST_AREA",
            },
        )
    ]
    if with_payment:
        tasks.append(
            Task(
                task_id="T2",
                tool="pay_fee",
                depends_on=["T1"],
                input={"booking_id": "BOOK-TEST", "amount": 1000, "currency": "VND"},
            )
        )
    return TaskPlan(goal="Dữ liệu test", tasks=tasks)


async def _no_session(session_id):
    """Giả lập DB không có session (trả None) — dùng cho route test không chạy
    PostgreSQL thật (tests/conftest.py client fixture không có test DB)."""
    return None


@pytest.mark.asyncio
async def test_demo_ui_is_served(client):
    """/demo phải phục vụ Agent Workspace, không phải giao diện chat cũ."""
    response = await client.get("/demo")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert "P-118 · Trợ lý dịch vụ cư dân" in response.text

    # Sidebar đủ 6 mục theo thiết kế đã duyệt.
    for item in ("Tổng quan", "Đang thực hiện", "Chờ bạn xử lý", "Đã hoàn thành", "Dịch vụ", "Hồ sơ cư dân"):
        assert item in response.text, item

    # Ô giao việc nhỏ gọn trên topbar, KHÔNG phải composer chiếm màn hình.
    assert "Giao việc cho P-118…" in response.text

    # Workspace, không phải chatbot: không còn khung hội thoại nào.
    markup = response.text.split("<script>")[0]
    for chat_pattern in ('class="stream', "streamInner", "msg agent", "bubble", "typing"):
        assert chat_pattern not in markup, chat_pattern

    # Ngôn ngữ nghiệp vụ, không lộ thuật ngữ kỹ thuật ra markup.
    for jargon in (
        "PostgreSQL",
        "InputRef",
        "pay_fee",
        "TaskPlan",
        "Validator",
        "Executor",
        "Connector",
        "Mock Payment API",
    ):
        assert jargon not in markup, jargon

    # Enum thô chỉ được nằm trong bảng dịch của <script>.
    for raw_enum in ("WAITING_APPROVAL", "NEEDS_INFORMATION", "VALIDATION_ERROR"):
        assert raw_enum not in markup, raw_enum

    # Enter không cướp phím giữa lúc gõ dấu tiếng Việt qua IME.
    assert "event.isComposing" in response.text
    assert 'autocomplete="off"' in response.text


@pytest.mark.asyncio
async def test_demo_ui_reads_the_workflow_list_from_the_backend(client):
    """Danh sách trên Tổng quan phải gọi API thật, không phải mảng cứng."""
    response = await client.get("/demo")

    assert "status=attention" in response.text
    assert "status=running" in response.text
    assert "status=completed" in response.text
    # Không có dữ liệu workflow nhúng sẵn trong trang.
    assert 'workflow_id: "wf-' not in response.text


@pytest.mark.asyncio
async def test_demo_ui_gives_each_workflow_its_own_url(client):
    """Refresh trang phải đọc lại workflow theo ID."""
    response = await client.get("/demo")

    assert "/demo?workflow_id=" in response.text
    assert "history.pushState" in response.text
    assert 'new URLSearchParams(location.search).get("workflow_id")' in response.text


@pytest.mark.asyncio
async def test_demo_ui_quick_actions_send_a_goal_directly(client):
    """Bấm mục tiêu là giao việc luôn, không bắt mô tả lại."""
    response = await client.get("/demo")

    for label in (
        "Đăng ký xe và chỗ đậu",
        "Đăng ký chuyển nhà",
        "Báo hỏng cần sửa",
        "Đặt lịch tham quan dự án",
        "Nhận tư vấn",
    ):
        assert label in response.text, label
    assert "startWorkflow(item.goal)" in response.text


@pytest.mark.asyncio
async def test_demo_ui_locks_resident_services_without_hiding_them(client):
    """Khoá bằng `hidden` thì người dùng không biết dịch vụ tồn tại."""
    response = await client.get("/demo")

    assert "Cần liên kết căn hộ" in response.text
    assert 'card.setAttribute("aria-disabled", "true")' in response.text
    assert "item.resident && !resident" in response.text


@pytest.mark.asyncio
async def test_demo_ui_progress_is_counted_from_real_tasks(client):
    """Tiến độ đếm từ tasks[], không phải animation giả lập."""
    response = await client.get("/demo")

    assert 'if (status === "SUCCESS") done += 1' in response.text
    assert "Math.round((done / total) * 100)" in response.text
    assert "setInterval" not in response.text


@pytest.mark.asyncio
async def test_demo_ui_decision_body_contains_only_the_decision(client):
    """Không gửi amount/currency/booking_id/khoá idempotency từ trình duyệt."""
    response = await client.get("/demo")

    assert "/payment-decision" in response.text
    assert "JSON.stringify({ decision })" in response.text
    decide = response.text.split("const decide = async (decision)")[1].split("};")[0]
    for forbidden in ("amount", "currency", "booking_id", "idempotency"):
        assert forbidden not in decide, forbidden
    # Cả hai nút bị vô hiệu khi đang gửi: bấm hai lần là hai lệnh duyệt.
    assert "approve.disabled = true" in response.text
    assert "reject.disabled = true" in response.text


@pytest.mark.asyncio
async def test_demo_ui_continue_never_sends_a_goal(client):
    """Trả lời form bổ sung đi /continue với đúng fields."""
    response = await client.get("/demo")

    assert "/continue" in response.text
    assert "JSON.stringify({ fields })" in response.text


@pytest.mark.asyncio
async def test_demo_ui_never_invents_business_data(client):
    """Ngày/giờ để trống; select bắt đầu bằng lựa chọn rỗng."""
    response = await client.get("/demo")

    definitions = response.text.split("const FIELDS = {")[1].split("\n      };")[0]
    for line in definitions.splitlines():
        if '"date"' in line or '"time"' in line:
            assert "value:" not in line, line.strip()

    assert 'textContent: "Vui lòng chọn"' in response.text
    assert "c.checkValidity()" in response.text

    # Chỉ field của tài khoản mới được điền sẵn, và phải nói rõ.
    prefilled = [ln.split(":")[0].strip() for ln in definitions.splitlines() if "value: ACCOUNT." in ln]
    assert sorted(prefilled) == ["email", "full_name", "phone"]
    assert "Đã tự điền từ tài khoản · bạn có thể chỉnh" in response.text


def test_demo_needs_information_exposes_structured_missing_fields() -> None:
    response = routes._demo_response(
        {
            "planner_status": "NEEDS_INFORMATION",
            "question": "Mình cần thêm giờ xem nhà.",
            "missing_fields": ("viewing_time",),
        },
        payment_approved=False,
    )

    assert response.status == "NEEDS_INFORMATION"
    assert response.question == "Mình cần thêm giờ xem nhà."
    assert response.missing_fields == ["viewing_time"]


def test_demo_needs_information_exposes_draft_plan_for_preview_only() -> None:
    draft = TaskPlan(
        goal="Đặt chỗ đậu xe.",
        tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={})],
    )

    response = routes._demo_response(
        {
            "planner_status": "NEEDS_INFORMATION",
            "question": "Mình cần thêm thông tin.",
            "missing_fields": ("booking_date",),
            "plan": None,
            "draft_plan": draft,
            "plan_validated": False,
        },
        payment_approved=False,
    )

    assert [task.tool for task in response.plan] == ["book_parking"]
    assert response.status == "NEEDS_INFORMATION"


def test_account_context_never_selects_a_default_property_for_free_chat() -> None:
    for context in routes._DEMO_ACCOUNT_CONTEXTS.values():
        assert "property_id" not in context
        assert "project_id" not in context
        assert "project_name" not in context


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("22/8/2026", "2026-08-22"),
        ("2026-08-22", "2026-08-22"),
        ("31/2/2026", None),
    ],
)
def test_follow_up_date_is_parsed_deterministically(value, expected) -> None:
    assert routes._extract_date(value) == expected


@pytest.mark.parametrize("value", ["13:40", "13;40", "13h40"])
def test_follow_up_time_accepts_common_vietnamese_separators(value) -> None:
    assert routes._extract_time(value) == "13:40"


@pytest.mark.parametrize("value", ["12h", "12 giờ", "lúc 12h", "12H"])
def test_follow_up_time_accepts_an_hour_without_minutes_as_on_the_hour(value) -> None:
    assert routes._extract_time(value) == "12:00"


@pytest.mark.parametrize("value", ["12h99", "12h 99", "25h", "24 giờ"])
def test_follow_up_time_does_not_truncate_an_invalid_hour_or_minute(value) -> None:
    assert routes._extract_time(value) is None


def test_follow_up_extracts_the_exact_viewing_answer_used_in_terminal() -> None:
    answers, unresolved = routes._extract_follow_up_answers(
        "22/8/2026,12h",
        ["viewing_date", "viewing_time"],
    )

    assert answers == {"viewing_date": "2026-08-22", "viewing_time": "12:00"}
    assert unresolved == []


def test_follow_up_extracts_parking_zone_and_plate_from_one_answer() -> None:
    answers, unresolved = routes._extract_follow_up_answers(
        "Chọn khu A, biển số 59a 12345, ngày 20/12/2026.",
        ["booking_date", "parking_zone", "plate_number"],
    )

    assert answers == {
        "booking_date": "2026-12-20",
        "parking_zone": "ZONE_A",
        "plate_number": "59A-12345",
    }
    assert unresolved == []


def test_follow_up_extracts_the_exact_vehicle_answer_used_in_terminal() -> None:
    answers, unresolved = routes._extract_follow_up_answers(
        "51A-202929, ôto, 29/8/2026, khu A",
        ["plate_number", "vehicle_type", "booking_date", "parking_zone"],
    )

    assert answers == {
        "plate_number": "51A-202929",
        "vehicle_type": "car",
        "booking_date": "2026-08-29",
        "parking_zone": "ZONE_A",
    }
    assert unresolved == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ô tô", "car"),
        ("ôto", "car"),
        ("oto", "car"),
        ("xe hơi", "car"),
        ("xe máy", "motorcycle"),
        ("mô tô", "motorcycle"),
        ("moto", "motorcycle"),
    ],
)
def test_follow_up_normalizes_common_vietnamese_vehicle_names(value, expected) -> None:
    assert routes._extract_vehicle_type(value) == expected


def test_follow_up_extracts_project_date_and_time_from_one_natural_answer() -> None:
    answers, unresolved = routes._extract_follow_up_answers(
        "Vinhomes Ocean Park, ngày 29/8/2026 lúc 10:00",
        ["project_id", "viewing_date", "viewing_time"],
    )

    assert answers == {
        "project_id": "PRJ-007",
        "viewing_date": "2026-08-29",
        "viewing_time": "10:00",
    }
    assert unresolved == []


@pytest.mark.asyncio
async def test_projects_endpoint_returns_names_without_internal_ids(client) -> None:
    response = await client.get("/api/v1/projects")

    assert response.status_code == 200
    payload = response.json()
    assert "Vinhomes Ocean Park" in payload["projects"]
    assert "PRJ-001" not in response.text


@pytest.mark.asyncio
async def test_capability_catalog_is_user_facing_and_marks_resident_services(client) -> None:
    response = await client.get("/api/v1/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert any(item["name"] == "Đặt lịch tham quan dự án" for item in body["capabilities"])
    assert any(item["requires_resident"] for item in body["capabilities"])
    assert "register_vehicle" not in response.text
    assert "pay_fee" not in response.text


def test_follow_up_does_not_silently_map_unsupported_zone() -> None:
    answers, unresolved = routes._extract_follow_up_answers("Zone C", ["parking_zone"])

    assert answers == {}
    assert unresolved == ["parking_zone"]
    assert "Khu A hoặc Khu B" in routes._follow_up_validation_message(unresolved)


def test_follow_up_rejects_past_date_and_outside_business_hours() -> None:
    past_answers, past_unresolved = routes._extract_follow_up_answers("01/01/2020", ["viewing_date"])
    early_answers, early_unresolved = routes._extract_follow_up_answers("07:59", ["viewing_time"])
    late_answers, late_unresolved = routes._extract_follow_up_answers("18:01", ["preferred_time"])

    assert past_answers == {} and past_unresolved == ["viewing_date"]
    assert early_answers == {} and early_unresolved == ["viewing_time"]
    assert late_answers == {} and late_unresolved == ["preferred_time"]


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("viewing_date", "từ hôm nay trở đi"),
        ("viewing_time", "08:00–17:30"),
        ("preferred_time", "08:00–18:00"),
        ("move_time", "07:00–20:00"),
    ],
)
def test_follow_up_validation_message_is_specific_and_safe(field, expected) -> None:
    message = routes._follow_up_validation_message([field])

    assert expected in message
    assert "12h99" not in message


@pytest.mark.asyncio
async def test_continue_workflow_maps_answer_into_context_without_rewriting_goal(client, monkeypatch) -> None:
    routes._DEMO_JOBS.clear()
    original_id = "workflow-needs-time"
    original_goal = "Đặt tham quan căn hộ Vinhomes ngày 2026-08-22."
    routes._DEMO_JOBS[original_id] = {
        "stage": "NEEDS_INFORMATION",
        "message": "Thiếu giờ xem.",
        "plan": None,
        "events": [],
        "goal": original_goal,
        "account_state": "resident",
        "approve_mock_payment": False,
        "existing_context": {"project_id": "PRJ-001"},
        "response": routes.DemoWorkflowResponse(
            status="NEEDS_INFORMATION",
            question="Thiếu giờ xem.",
            missing_fields=["viewing_time"],
        ),
    }
    captured = {}

    async def _fake_run(workflow_id, goal, approved, urls, account_state, **kwargs):
        job = routes._DEMO_JOBS[workflow_id]
        captured.update(
            workflow_id=workflow_id,
            goal=goal,
            approved=approved,
            account_state=account_state,
            context=job["existing_context"],
            session_id=kwargs.get("session_id"),
            parent_workflow_id=kwargs.get("parent_workflow_id"),
        )

    monkeypatch.setattr(routes, "_run_demo_job", _fake_run)
    response = await client.post(
        f"/api/v1/workflows/demo/{original_id}/continue",
        json={"message": "13;40"},
    )
    await asyncio.sleep(0)

    assert response.status_code == 202
    assert captured["goal"] == original_goal
    assert captured["context"] == {"project_id": "PRJ-001", "viewing_time": "13:40"}
    assert "Thông tin người dùng bổ sung" not in captured["goal"]


@pytest.mark.asyncio
async def test_continue_workflow_accepts_partial_answer_then_planner_can_ask_remaining_field(
    client,
    monkeypatch,
) -> None:
    routes._DEMO_JOBS.clear()
    original_id = "workflow-needs-date-time"
    routes._DEMO_JOBS[original_id] = {
        "stage": "NEEDS_INFORMATION",
        "message": "Thiếu ngày và giờ.",
        "plan": None,
        "events": [],
        "goal": "Đặt lịch tham quan PRJ-001.",
        "account_state": "resident",
        "approve_mock_payment": False,
        "existing_context": {"project_id": "PRJ-001"},
        "response": routes.DemoWorkflowResponse(
            status="NEEDS_INFORMATION",
            question="Thiếu ngày và giờ.",
            missing_fields=["viewing_date", "viewing_time"],
        ),
    }
    captured = {}

    async def _fake_run(workflow_id, goal, approved, urls, account_state, **kwargs):
        captured.update(routes._DEMO_JOBS[workflow_id]["existing_context"])

    monkeypatch.setattr(routes, "_run_demo_job", _fake_run)
    response = await client.post(
        f"/api/v1/workflows/demo/{original_id}/continue",
        json={"message": "22/8/2026"},
    )
    await asyncio.sleep(0)

    assert response.status_code == 202
    assert captured == {"project_id": "PRJ-001", "viewing_date": "2026-08-22"}


@pytest.mark.asyncio
async def test_demo_start_returns_immediately_then_status_returns_background_result(client, monkeypatch):
    routes._DEMO_JOBS.clear()

    async def _fake_job(workflow_id, goal, approve_mock_payment, service_urls, account_state, **kwargs):
        assert goal == "Đăng ký dữ liệu test"
        # Request khai rõ persona; test này không dựa vào default nữa.
        assert account_state == "resident"
        routes._DEMO_JOBS[workflow_id]["stage"] = "FINISHED"
        routes._DEMO_JOBS[workflow_id]["message"] = "Đã hoàn tất."
        routes._DEMO_JOBS[workflow_id]["response"] = routes.DemoWorkflowResponse(
            workflow_id=workflow_id,
            status="SUCCESS",
            summary="Đã hoàn tất.",
        )

    async def _no_record(workflow_id):
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)
    monkeypatch.setattr(routes, "read_demo_workflow", _no_record)

    started = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đăng ký dữ liệu test", "account_state": "resident"},
    )

    assert started.status_code == 202
    workflow_id = started.json()["workflow_id"]
    assert started.json()["status"] == "PENDING"
    assert started.json()["stage"] == "PLANNING"

    await asyncio.sleep(0)
    status = await client.get(f"/api/v1/workflows/demo/{workflow_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "SUCCESS"
    assert status.json()["stage"] == "FINISHED"


@pytest.mark.asyncio
async def test_demo_start_keeps_contact_profile_outside_trusted_planner_context(client, monkeypatch) -> None:
    routes._DEMO_JOBS.clear()

    async def _fake_job(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)
    profile = {
        "full_name": "Nguyễn Văn A",
        "phone": "0948500414",
        "email": "nguyenvana@example.com",
        "note": "Muốn được tư vấn buổi chiều",
    }
    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={
            "goal": "Đặt lịch tham quan PRJ-001 ngày 2026-12-10 lúc 10:00.",
            "account_state": "prospect",
            "contact_profile": profile,
        },
    )
    await asyncio.sleep(0)

    assert response.status_code == 202
    job = routes._DEMO_JOBS[response.json()["workflow_id"]]
    assert job["contact_profile"] == profile
    assert set(profile).isdisjoint(job["existing_context"])
    assert "0948500414" not in job["goal"]


@pytest.mark.asyncio
async def test_demo_start_maps_public_project_name_to_trusted_internal_id(client, monkeypatch) -> None:
    routes._DEMO_JOBS.clear()

    async def _fake_job(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)
    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={
            "goal": "Đặt lịch tham quan Vinhomes Ocean Park ngày 2026-12-10 lúc 10:00.",
            "project_name": "Vinhomes Ocean Park",
        },
    )
    await asyncio.sleep(0)

    assert response.status_code == 202
    job = routes._DEMO_JOBS[response.json()["workflow_id"]]
    assert job["existing_context"]["project_id"] == "PRJ-007"
    assert "project_name" not in job["existing_context"]


@pytest.mark.asyncio
async def test_demo_start_recognizes_supported_project_name_in_free_chat(client, monkeypatch) -> None:
    routes._DEMO_JOBS.clear()

    async def _fake_job(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)
    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đặt lịch tham quan vinhome ocean park ngày 2026-12-10 lúc 10:00."},
    )
    await asyncio.sleep(0)

    assert response.status_code == 202
    job = routes._DEMO_JOBS[response.json()["workflow_id"]]
    assert job["existing_context"]["project_id"] == "PRJ-007"


@pytest.mark.asyncio
async def test_demo_start_rejects_unsupported_selected_project_without_echo(client) -> None:
    unknown = "Dự án bí mật không hỗ trợ"
    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đặt lịch tham quan.", "project_name": unknown},
    )

    assert response.status_code == 422
    assert unknown not in response.text


@pytest.mark.asyncio
async def test_demo_start_rejects_invalid_contact_before_planning(client) -> None:
    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={
            "goal": "Đặt lịch tham quan.",
            "contact_profile": {
                "full_name": "Nguyễn Văn A",
                "phone": "not-a-phone",
                "email": "invalid-email",
            },
        },
    )

    assert response.status_code == 422
    assert "not-a-phone" not in response.text
    assert "invalid-email" not in response.text

    whitespace = await client.post(
        "/api/v1/workflows/demo/start",
        json={
            "goal": "Đặt lịch tham quan.",
            "contact_profile": {"full_name": "   ", "phone": "0948500414"},
        },
    )
    assert whitespace.status_code == 422


@pytest.mark.asyncio
async def test_demo_status_reads_live_task_state_from_repository(client, monkeypatch):
    workflow_id = "bc8a091d-f0c6-477c-9e8e-f749da22a87f"
    plan = _demo_plan()
    routes._DEMO_JOBS[workflow_id] = {
        "stage": "EXECUTING",
        "message": "Executor đang gọi dịch vụ.",
        "plan": plan,
        "response": None,
    }

    async def _record(received_id):
        assert received_id == workflow_id
        return {
            "workflow": {"status": "RUNNING", "task_plan": plan.model_dump(mode="json")},
            "tasks": [
                {
                    "task_id": "T1",
                    "status": "RUNNING",
                    "result_data": None,
                    "error_code": None,
                    "retryable": False,
                }
            ],
        }

    monkeypatch.setattr(routes, "read_demo_workflow", _record)

    response = await client.get(f"/api/v1/workflows/demo/{workflow_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "RUNNING"
    assert body["stage"] == "EXECUTING"
    assert body["persisted"] is True
    assert body["tasks"][0]["status"] == "RUNNING"
    assert body["tasks"][0]["message"].startswith("Đang thực hiện:")


@pytest.mark.asyncio
async def test_demo_status_preserves_fast_intermediate_events(client, monkeypatch):
    workflow_id = "30fd69c6-4af7-4373-9ec0-b4d21fd7e31e"
    plan = _demo_plan()
    job = {
        "stage": "PLANNING",
        "message": routes._STAGE_MESSAGES["PLANNING"],
        "plan": plan,
        "response": None,
        "events": [],
    }
    routes._DEMO_JOBS[workflow_id] = job
    routes._append_job_event(job, "PLANNING")
    routes._append_job_event(job, "PLANNED", {"plan": plan})
    routes._append_job_event(job, "VALIDATING")
    routes._append_job_event(job, "VALIDATED")
    routes._append_job_event(job, "EXECUTING")
    routes._append_job_event(job, "TASK_RUNNING", {"task_id": "T1", "task_status": "RUNNING"})
    routes._append_job_event(job, "TASK_SUCCESS", {"task_id": "T1", "task_status": "SUCCESS"})
    routes._append_job_event(job, "FINISHED")

    async def _no_record(_workflow_id):
        return None

    monkeypatch.setattr(routes, "read_demo_workflow", _no_record)

    response = await client.get(f"/api/v1/workflows/demo/{workflow_id}")

    events = response.json()["events"]
    assert [event["sequence"] for event in events] == list(range(1, 9))
    assert [event["stage"] for event in events] == [
        "PLANNING",
        "PLANNED",
        "VALIDATING",
        "VALIDATED",
        "EXECUTING",
        "TASK_RUNNING",
        "TASK_SUCCESS",
        "FINISHED",
    ]
    assert events[5]["message"] == "Đang đăng ký cư dân."

    # Message công khai không được chứa thuật ngữ kỹ thuật ở BẤT KỲ stage nào.
    joined = " ".join(event["message"] for event in events)
    for jargon in ("LLM", "TaskPlan", "Validator", "Executor", "Connector", "PostgreSQL", "InputRef"):
        assert jargon not in joined, jargon


@pytest.mark.asyncio
async def test_demo_workflow_returns_safe_success_view(client, monkeypatch):
    plan = _demo_plan()

    async def _run_demo_workflow(*args, **kwargs):
        return {
            "planner_status": "READY",
            "plan": plan,
            "workflow_id": "workflow-test",
            "task_results": {
                "T1": StandardResult.ok(
                    {
                        "resident_id": "RES-001",
                        "provider_token": "MUST-NOT-LEAK",
                    }
                )
            },
        }

    monkeypatch.setattr(routes, "run_demo_workflow", _run_demo_workflow)

    response = await client.post(
        "/api/v1/workflows/demo",
        json={"goal": "Đăng ký dữ liệu test", "approve_mock_payment": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["workflow_id"] == "workflow-test"
    assert body["summary"] == "Đã đăng ký hồ sơ cư dân cho TEST_APARTMENT tại TEST_AREA."
    assert body["plan"] == [
        {
            "task_id": "T1",
            "tool": "register_resident",
            "depends_on": [],
            "title": "Đăng ký cư dân",
            "description": "Tạo hồ sơ cư dân cho căn hộ đã cung cấp.",
        }
    ]
    assert body["tasks"] == [
        {
            "task_id": "T1",
            "tool": "register_resident",
            "status": "SUCCESS",
            "error_code": None,
            "retryable": False,
            "title": "Đăng ký cư dân",
            "message": "Đã đăng ký hồ sơ cư dân cho TEST_APARTMENT tại TEST_AREA.",
            "details": [
                {"label": "Căn hộ", "value": "TEST_APARTMENT"},
                {"label": "Khu dân cư", "value": "TEST_AREA"},
                {"label": "Mã cư dân", "value": "RES-001"},
            ],
        }
    ]
    assert "MUST-NOT-LEAK" not in response.text
    assert "provider_token" not in response.text
    assert "input" not in response.text


def test_demo_response_presents_four_business_steps_in_vietnamese() -> None:
    plan = TaskPlan(
        goal="Dữ liệu test",
        tasks=[
            Task(
                task_id="T1",
                tool="register_resident",
                depends_on=[],
                input={
                    "full_name": "TEST_PERSON",
                    "apartment_code": "A1201",
                    "residential_area": "Ocean Park",
                },
            ),
            Task(
                task_id="T2",
                tool="register_vehicle",
                depends_on=["T1"],
                input={"resident_id": "RES-001", "plate_number": "TEST-123", "vehicle_type": "car"},
            ),
            Task(
                task_id="T3",
                tool="book_parking",
                depends_on=["T2"],
                input={"vehicle_id": "VEH-001", "booking_date": "2026-08-20", "parking_zone": "ZONE_A"},
            ),
            Task(
                task_id="T4",
                tool="pay_fee",
                depends_on=["T3"],
                input={"booking_id": "BOOK-001", "amount": 150000, "currency": "VND"},
            ),
        ],
    )
    state = {
        "plan": plan,
        "workflow_id": "workflow-test",
        "task_results": {
            "T1": StandardResult.ok({"resident_id": "RES-001"}),
            "T2": StandardResult.ok({"vehicle_id": "VEH-001"}),
            "T3": StandardResult.ok(
                {
                    "booking_id": "BOOK-001",
                    "parking_zone": "ZONE_A",
                    "booking_date": "2026-08-20",
                    "amount": 150000,
                    "currency": "VND",
                }
            ),
            "T4": StandardResult.ok({"payment_id": "PAY-001", "payment_status": "PAID"}),
        },
    }

    response = routes._demo_response(state, payment_approved=True)

    assert response.status == "SUCCESS"
    assert response.summary == (
        "Đã đăng ký hồ sơ cư dân cho A1201 tại Ocean Park. "
        "Đã đăng ký phương tiện biển số TEST-123. "
        "Đã đặt chỗ đỗ xe (Khu A · 2026-08-20). Phí đặt chỗ: 150.000 VND. "
        "Đã thanh toán phí đặt chỗ thành công."
    )
    assert [task.title for task in response.tasks] == [
        "Đăng ký cư dân",
        "Đăng ký phương tiện",
        "Đặt chỗ đỗ xe",
        "Thanh toán phí",
    ]
    assert response.tasks[0].message == "Đã đăng ký hồ sơ cư dân cho A1201 tại Ocean Park."
    assert response.tasks[1].message == "Đã đăng ký phương tiện biển số TEST-123."
    assert response.tasks[2].message == ("Đã đặt chỗ đỗ xe (Khu A · 2026-08-20). Phí đặt chỗ: 150.000 VND.")
    assert response.tasks[3].message == "Đã thanh toán phí đặt chỗ thành công."
    assert {item.label: item.value for item in response.tasks[2].details} == {
        "Mã đặt chỗ": "BOOK-001",
        "Khu vực": "Khu A",
        "Ngày đặt": "2026-08-20",
        "Phí đặt chỗ": "150.000 VND",
    }


def test_demo_response_explains_root_business_failure() -> None:
    plan = TaskPlan(
        goal="Đăng ký phương tiện",
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={
                    "resident_id": "RES-001",
                    "plate_number": "59A-12345",
                    "vehicle_type": "car",
                },
            ),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    "vehicle_id": "VEH-001",
                    "booking_date": "2026-08-20",
                    "parking_zone": "ZONE_A",
                },
            ),
        ],
    )
    state = {
        "plan": plan,
        "workflow_id": "workflow-test",
        "task_results": {
            "T1": StandardResult.fail(
                ErrorCode.VEHICLE_ALREADY_EXISTS,
                "raw provider message",
            ),
            "T2": StandardResult.fail(
                ErrorCode.DEPENDENCY_ERROR,
                "raw dependency message",
            ),
        },
    }

    response = routes._demo_response(state, payment_approved=False)

    assert response.status == "FAILED"
    assert response.summary == (
        "Biển số 59A-12345 đã được đăng ký. Hãy sử dụng phương tiện đã liên kết hoặc kiểm tra lại biển số."
    )
    assert response.tasks[1].message == ("Bước “Đặt chỗ đỗ xe” chưa được thực hiện vì bước trước đó không thành công.")
    assert "raw provider message" not in response.model_dump_json()


def test_viewing_no_availability_uses_property_specific_message() -> None:
    task = Task(
        task_id="T1",
        tool="schedule_property_viewing",
        depends_on=[],
        input={"project_id": "PRJ-001", "viewing_date": "2026-08-22", "viewing_time": "13:40"},
    )

    message = routes._task_failure_message(task, "Đặt lịch tham quan", "NO_AVAILABILITY")

    assert message == "Khung giờ tham quan 2026-08-22 13:40 không còn trống. Hãy chọn thời gian khác."
    assert "đỗ xe" not in message


def test_demo_response_explains_planning_and_validation_failures() -> None:
    planning = routes._demo_response(
        {"planning_error": "safe internal category"},
        payment_approved=False,
    )
    validation = routes._demo_response(
        {"validation_error": "Tool 'book_parking' has booking_date in the past and invalid parking_zone"},
        payment_approved=False,
    )

    assert planning.status == "PLANNING_ERROR"
    assert "mô tả lại" in planning.summary
    assert validation.status == "VALIDATION_ERROR"
    assert "từ hôm nay trở đi" in validation.summary
    assert "Khu A hoặc Khu B" in validation.summary
    assert "safe internal category" not in planning.model_dump_json()


@pytest.mark.asyncio
async def test_demo_workflow_reports_payment_approval_required(client, monkeypatch):
    plan = _demo_plan(with_payment=True)

    from src.common.results import StandardResult

    async def _run_demo_workflow(*args, **kwargs):
        # Chờ duyệt là tín hiệu TƯỜNG MINH từ policy guard, kèm kết quả prefix.
        return {
            "planner_status": "READY",
            "plan": plan,
            "policy_error": "PAYMENT_APPROVAL_REQUIRED",
            "workflow_id": "wf-approval",
            "task_results": {"T2": StandardResult.ok({"booking_id": "BOOK-001", "amount": 150_000, "currency": "VND"})},
        }

    monkeypatch.setattr(routes, "run_demo_workflow", _run_demo_workflow)

    response = await client.post(
        "/api/v1/workflows/demo",
        json={"goal": "Thanh toán phí mock", "approve_mock_payment": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "WAITING_APPROVAL"
    assert body["payment_quote"]["amount"] == 150_000


@pytest.mark.asyncio
async def test_background_payment_approval_is_not_reported_as_failure_or_finished(monkeypatch):
    routes._DEMO_JOBS.clear()
    workflow_id = "workflow-waiting-approval"
    plan = _demo_plan(with_payment=True)
    routes._DEMO_JOBS[workflow_id] = {
        "stage": "PLANNING",
        "message": routes._STAGE_MESSAGES["PLANNING"],
        "plan": None,
        "response": None,
        "events": [],
        "existing_context": {},
        "contact_profile": {},
    }

    async def _run_demo_workflow(*args, **kwargs):
        return {
            "planner_status": "READY",
            "plan": plan,
            "policy_error": "PAYMENT_APPROVAL_REQUIRED",
            "workflow_id": workflow_id,
            "task_results": {"T2": StandardResult.ok({"booking_id": "BOOK-001", "amount": 150_000, "currency": "VND"})},
        }

    async def _persist(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "run_demo_workflow", _run_demo_workflow)
    monkeypatch.setattr(routes, "persist_pending_approval", _persist)

    await routes._run_demo_job(
        workflow_id,
        "Đặt chỗ và thanh toán",
        False,
        {"resident": "", "transport": "", "payment": "", "property": "", "resident_services": ""},
        "resident",
    )

    events = routes._DEMO_JOBS[workflow_id]["events"]
    assert events[-1]["stage"] == "WAITING_APPROVAL"
    assert all(event["stage"] not in {"EXECUTION_FAILED", "FINISHED"} for event in events)


@pytest.mark.asyncio
async def test_execution_failure_is_not_disguised_as_a_payment_prompt(client, monkeypatch):
    """Lỗi thực thi phải hiện đúng là lỗi, không thành lời mời thanh toán.

    Bản cũ suy ra "cần duyệt thanh toán" chỉ vì plan có chứa `pay_fee`, nên MỌI
    lỗi thực thi đều bị che. Một `TypeError` thật trong runtime từng hiện ra
    thành "Chờ bạn xác nhận" và giấu hoàn toàn nguyên nhân.
    """
    plan_with_payment = _demo_plan(with_payment=True)

    async def _run_demo_workflow(*args, **kwargs):
        return {
            "planner_status": "READY",
            "plan": plan_with_payment,
            "execution_error": "Thực thi thất bại (TypeError).",
        }

    monkeypatch.setattr(routes, "run_demo_workflow", _run_demo_workflow)

    response = await client.post(
        "/api/v1/workflows/demo",
        json={"goal": "Thanh toán phí mock", "approve_mock_payment": False},
    )

    assert response.json()["status"] == "EXECUTION_ERROR"


@pytest.mark.asyncio
async def test_demo_workflow_does_not_echo_unexpected_exception(client, monkeypatch):
    secret = "postgresql://user:secret@example.invalid/database"

    async def _run_demo_workflow(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(routes, "run_demo_workflow", _run_demo_workflow)

    response = await client.post(
        "/api/v1/workflows/demo",
        json={"goal": "Đăng ký dữ liệu test"},
    )

    assert response.status_code == 503
    assert secret not in response.text
    assert response.json()["detail"] == "Workflow demo unavailable (RuntimeError)."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"goal": "   "},
        {"goal": "Đăng ký test", "existing_context": {"amount": 1}},
    ],
)
async def test_demo_workflow_rejects_untrusted_request_shape(client, payload):
    response = await client.post("/api/v1/workflows/demo", json=payload)

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# A. workflow_id không được rơi mất trên đường poll
#
# Regression thật: người dùng chọn Khách → Tham quan → backend hỏi thêm → điền
# form → bấm Tiếp tục → UI báo "Dữ liệu không hợp lệ: body.goal".
#
# Nguyên nhân: `_demo_response()` dựng view model từ AgentState nên không có
# workflow_id (default None). Job cache giữ nguyên None, GET trả None, UI mất
# pendingWorkflowId rồi gửi nhầm sang /start với goal=null.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_keeps_path_workflow_id_for_needs_information(client):
    workflow_id = "wf-needs-info"
    cached = routes._demo_response(
        {
            "planner_status": "NEEDS_INFORMATION",
            "question": "Mình cần thêm ngày xem nhà.",
            "missing_fields": ("viewing_date",),
        },
        payment_approved=False,
    )
    # Chính là hình dạng response mà `_demo_response()` sinh ra: chưa có id.
    assert cached.workflow_id is None

    routes._DEMO_JOBS[workflow_id] = {
        "stage": "NEEDS_INFORMATION",
        "message": "Mình cần thêm ngày xem nhà.",
        "plan": None,
        "response": cached,
        "events": [],
    }
    try:
        response = await client.get(f"/api/v1/workflows/demo/{workflow_id}")
    finally:
        routes._DEMO_JOBS.pop(workflow_id, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "NEEDS_INFORMATION"
    # Không có dòng này thì UI mất id và lần submit sau rơi sang /start.
    assert payload["workflow_id"] == workflow_id


@pytest.mark.asyncio
async def test_poll_keeps_path_workflow_id_for_cached_error_response(client):
    workflow_id = "wf-cached-error"
    routes._DEMO_JOBS[workflow_id] = {
        "stage": "VALIDATION_FAILED",
        "message": "Không thể tiếp tục.",
        "plan": None,
        "response": routes._demo_response({"validation_error": "missing required input"}, payment_approved=False),
        "events": [],
    }
    try:
        response = await client.get(f"/api/v1/workflows/demo/{workflow_id}")
    finally:
        routes._DEMO_JOBS.pop(workflow_id, None)

    assert response.json()["workflow_id"] == workflow_id


@pytest.mark.asyncio
async def test_request_validation_error_never_leaks_field_location(client):
    """422 không được trả `body.goal` — chuỗi đó từng hiện thẳng trong khung chat."""
    response = await client.post("/api/v1/workflows/demo/start", json={})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "body.goal" not in detail
    assert "goal" not in detail
    assert "body" not in detail
    assert detail == "Yêu cầu chưa hợp lệ. Bạn kiểm tra lại thông tin vừa nhập giúp mình nhé."


# ---------------------------------------------------------------------------
# B. Policy guard: quyền theo resident-property mapping
# ---------------------------------------------------------------------------


def test_resident_only_denial_speaks_business_language_only() -> None:
    response = routes._demo_response({"policy_error": "RESIDENT_ACCESS_REQUIRED"}, payment_approved=False)

    assert response.status == "EXECUTION_ERROR"
    assert response.summary == routes.RESIDENT_ACCESS_REQUIRED_MESSAGE
    # Không lộ tên tool, mã policy, thuật ngữ schema hay tên exception.
    for leak in (
        "book_parking",
        "register_vehicle",
        "pay_fee",
        "create_maintenance_request",
        "schedule_move",
        "RESIDENT_ACCESS_REQUIRED",
        "resident-property mapping",
        "VERIFIED",
        "NOT_LINKED",
    ):
        assert leak not in response.summary


def test_prospect_context_cannot_prove_resident_mapping() -> None:
    """Guard đọc context do server dựng, không đọc gì từ request body."""
    prospect = routes._DEMO_ACCOUNT_CONTEXTS["prospect"]
    resident = routes._DEMO_ACCOUNT_CONTEXTS["resident"]

    assert prospect.get("resident_verification_status") != "VERIFIED"
    assert "resident_id" not in prospect
    assert resident["resident_verification_status"] == "VERIFIED"
    assert resident["resident_id"]


def test_account_state_defaults_to_the_least_privileged_persona() -> None:
    """Fail-closed: quên khai account_state là MẤT quyền, không phải được thêm.

    Default cũ là "resident", nên một request chỉ có `goal` được cấp thẳng
    context cư dân đã xác thực (RES-001 / căn A1201) và chạm được tới pay_fee.
    """
    request = DemoWorkflowRequest(goal="Đăng ký chỗ đậu xe cho xe của tôi")

    assert request.account_state == "prospect"
    context = routes._DEMO_ACCOUNT_CONTEXTS[request.account_state]
    assert context.get("resident_verification_status") != "VERIFIED"
    assert "resident_id" not in context


# ---------------------------------------------------------------------------
# Payment decision API: browser chỉ gửi decision
# ---------------------------------------------------------------------------


def test_payment_decision_body_accepts_only_a_decision() -> None:
    """Không nhận amount/currency/booking_id/idempotency key từ trình duyệt.

    Nhận số tiền từ client là để người dùng tự định giá dịch vụ.
    """
    from pydantic import ValidationError

    from src.models.schemas import DemoPaymentDecisionRequest

    assert DemoPaymentDecisionRequest(decision="approve").decision == "approve"
    assert DemoPaymentDecisionRequest(decision="reject").decision == "reject"

    for forbidden in ("amount", "currency", "booking_id", "idempotency_key", "workflow_id"):
        with pytest.raises(ValidationError):
            DemoPaymentDecisionRequest(decision="approve", **{forbidden: "x"})

    with pytest.raises(ValidationError):
        DemoPaymentDecisionRequest(decision="maybe")


@pytest.mark.asyncio
async def test_successful_payment_decision_replaces_stale_waiting_stage(monkeypatch) -> None:
    workflow_id = "workflow-decision-cache"
    routes._DEMO_JOBS[workflow_id] = {
        "stage": "WAITING_APPROVAL",
        "message": "Đang chờ bạn xác nhận thanh toán.",
        "events": [],
        "response": routes.DemoWorkflowResponse(status="WAITING_APPROVAL"),
    }

    async def _reject(_workflow_id: str) -> None:
        assert _workflow_id == workflow_id

    monkeypatch.setattr(routes, "reject_payment", _reject)
    try:
        response = await routes.decide_demo_payment(
            workflow_id,
            routes.DemoPaymentDecisionRequest(decision="reject"),
        )

        job = routes._DEMO_JOBS[workflow_id]
        assert response.status == "FAILED"
        assert job["response"] is None
        assert job["stage"] == "FINISHED"
        assert "chờ" not in job["message"].casefold()
    finally:
        routes._DEMO_JOBS.pop(workflow_id, None)


def test_awaiting_approval_response_carries_the_quote() -> None:
    from src.common.results import StandardResult

    response = routes._demo_response(
        {
            "policy_error": "PAYMENT_APPROVAL_REQUIRED",
            "workflow_id": "wf-1",
            "task_results": {"T2": StandardResult.ok({"booking_id": "BOOK-001", "amount": 150_000, "currency": "VND"})},
        },
        payment_approved=False,
    )

    assert response.status == "WAITING_APPROVAL"
    assert response.workflow_id == "wf-1"
    assert response.payment_quote == {
        "booking_id": "BOOK-001",
        "amount": 150_000,
        "currency": "VND",
        "description": "Phí đặt chỗ đỗ xe",
    }
    # Số tiền hiển thị đúng định dạng Việt Nam, không lộ thuật ngữ kỹ thuật.
    assert "150.000 VND" in response.summary
    for leak in ("pay_fee", "InputRef", "PostgreSQL", "AWAITING", "booking_id"):
        assert leak not in response.summary


def test_approval_decision_status_is_a_separate_axis_from_workflow_status() -> None:
    """AWAITING/APPROVED/REJECTED mô tả QUYẾT ĐỊNH, không phải workflow.

    Hai tập giá trị này cố tình khác nhau; test khoá lại để không ai gộp nhầm.
    """
    from src.common.enums import WorkflowStatus
    from src.orchestration.payment_approval import APPROVED, AWAITING, REJECTED

    decision_values = {AWAITING, APPROVED, REJECTED}
    workflow_values = {status.value for status in WorkflowStatus}

    assert decision_values.isdisjoint(workflow_values)


def test_public_stage_messages_are_free_of_internal_vocabulary() -> None:
    """Toàn bộ bảng message công khai, không chỉ những stage test khác chạm tới.

    Bản trước đưa nguyên văn "LLM đang phân tích", "Agent đã tạo TaskPlan",
    "Validator đang kiểm tra dependency, allowlist", "Executor đang gọi các
    dịch vụ" vào `events[].message` — người dùng cuối không có cách nào hiểu,
    và đó cũng là chi tiết nội bộ không nên lộ.
    """
    jargon = (
        "LLM",
        "TaskPlan",
        "Validator",
        "Executor",
        "Connector",
        "PostgreSQL",
        "InputRef",
        "dependency",
        "allowlist",
        "workflow",
    )
    for stage, message in routes._STAGE_MESSAGES.items():
        for word in jargon:
            assert word.lower() not in message.lower(), f"{stage}: {word}"


# --- Phase A: session server-side (identity) -------------------------------
#
# Browser không được quyết định quyền. Persona ghim ở lần /start đầu; persona
# switch = session mới; /continue đọc quyền từ session, không từ body.


@pytest.mark.asyncio
async def test_second_start_with_new_persona_returns_new_session_id(client, monkeypatch) -> None:
    """Persona switch phải tạo session mới — thread mới, KHÔNG nối tiếp session cũ.

    Server tự sinh session_id ở mỗi /start; account_state body chỉ là persona
    mong muốn CHO LẦN TẠO session đó. Chuyển persona không được tái sử dụng
    session_id của cuộc hội thoại trước (nếu không lần /continue sau đọc session
    cũ — ghim persona CŨ — là leo thang hoặc khóa nhầm quyền).
    """
    routes._DEMO_JOBS.clear()
    captured = []

    async def _fake_job(workflow_id, goal, approve_mock_payment, service_urls, account_state, **kwargs):
        captured.append(account_state)

    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)

    first = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đặt lịch tham quan PRJ-001 ngày 2026-12-10 lúc 10:00.", "account_state": "resident"},
    )
    second = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đặt lịch tham quan PRJ-001 ngày 2026-12-10 lúc 10:00.", "account_state": "prospect"},
    )
    await asyncio.sleep(0)

    first_sid = first.json()["session_id"]
    second_sid = second.json()["session_id"]
    assert first_sid and second_sid
    assert first_sid != second_sid
    assert captured == ["resident", "prospect"]


@pytest.mark.asyncio
async def test_continue_never_uses_body_account_state(client, monkeypatch) -> None:
    """/continue KHÔNG tin `account_state` từ body — quyền lấy từ session server-side.

    Đây là test chống leo thang: job được tạo ở persona resident (browser gửi
    account_state="resident" lúc /start), nhưng session server-side KHÔNG tồn tại
    trong DB test → `_load_session` trả None → fail-closed về prospect. Nếu code
    đọc quyền từ job cache (`_DEMO_JOBS[].account_state`) thì nó sẽ lấy "resident"
    — test này khoá rằng quyền PHẢI đến từ session, không từ nơi browser có thể
    chạm vào.

    (Body /continue không cho phép account_state — `extra="forbid"` — nên đây là
    phòng thủ thứ hai: ngay cả khi schema nới lỏng, backend vẫn không đọc nó.)
    """
    routes._DEMO_JOBS.clear()
    workflow_id = "workflow-session-ghost"
    routes._DEMO_JOBS[workflow_id] = {
        "stage": "NEEDS_INFORMATION",
        "message": "Thiếu giờ xem.",
        "plan": None,
        "events": [],
        "goal": "Đặt lịch tham quan PRJ-001.",
        # Browser đã gửi resident ở /start — nhưng session server-side không tồn
        # tại, nên đây phải bị bỏ qua.
        "account_state": "resident",
        "approve_mock_payment": False,
        "existing_context": {"project_id": "PRJ-001"},
        "response": routes.DemoWorkflowResponse(
            status="NEEDS_INFORMATION",
            question="Thiếu giờ xem.",
            missing_fields=["viewing_time"],
        ),
        "session_id": "sess-that-was-never-persisted",
    }
    captured = {}

    async def _fake_run(workflow_id, goal, approved, urls, account_state, **kwargs):
        captured["account_state"] = account_state

    monkeypatch.setattr(routes, "_load_session", _no_session)
    monkeypatch.setattr(routes, "_run_demo_job", _fake_run)
    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        json={"message": "13:40"},
    )
    await asyncio.sleep(0)

    assert response.status_code == 202
    assert captured["account_state"] == "prospect"


@pytest.mark.asyncio
async def test_session_persist_failure_is_nonfatal(client, monkeypatch) -> None:
    """DB lỗi lúc ghim session KHÔNG được làm hỏng workflow.

    Session chỉ ảnh hưởng quyền của các lần đọc sau; nếu không ghim được thì
    fail-closed về prospect. Workflow vẫn phải chạy bình thường.
    """
    routes._DEMO_JOBS.clear()
    calls = []

    async def _boom(*args, **kwargs):
        raise RuntimeError("DB down")

    async def _fake_job(workflow_id, goal, approve_mock_payment, service_urls, account_state, **kwargs):
        calls.append(("job", account_state))

    monkeypatch.setattr(routes, "_persist_session", _boom)
    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)
    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đặt lịch tham quan PRJ-001 ngày 2026-12-10 lúc 10:00.", "account_state": "resident"},
    )
    await asyncio.sleep(0)

    assert response.status_code == 202
    assert calls == [("job", "resident")]
