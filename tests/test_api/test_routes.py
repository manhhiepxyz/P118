import asyncio

import pytest

from src.api import routes
from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.models.schemas import DemoWorkflowRequest
from src.orchestration.runtime_provider import set_repository_provider


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


async def _no_session(session_id, **_kwargs):
    """Giả lập DB không có session (trả None) — dùng cho route test không chạy
    PostgreSQL thật (tests/conftest.py client fixture không có test DB)."""
    return None


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
    assert not any(item["name"] == "Tìm gợi ý bất động sản" for item in body["capabilities"])
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
        ("project_name", "Vinhomes Sài Gòn Park"),
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


@pytest.mark.parametrize(
    "message",
    [
        "Có những dự án nào?",
        "danh sách dự án",
        "P-118 hỗ trợ dự án nào",
    ],
)
def test_project_catalog_question_is_not_treated_as_a_field_answer(message) -> None:
    assert routes._asks_for_project_catalog(message, ["project_name", "viewing_date"])
    assert not routes._asks_for_project_catalog(message, ["parking_zone"])


def test_project_catalog_answer_comes_from_the_canonical_catalogue() -> None:
    answer = routes._project_catalog_answer()

    for project in routes.PROJECTS:
        assert project["project_name"] in answer
    assert "PRJ-" not in answer


@pytest.mark.asyncio
async def test_project_catalog_question_keeps_the_same_clarification_open(client, monkeypatch) -> None:
    routes._DEMO_JOBS.clear()
    workflow_id = "workflow-project-catalog"
    response = routes.DemoWorkflowResponse(
        workflow_id=workflow_id,
        status="NEEDS_INFORMATION",
        question="Bạn muốn tham quan dự án nào?",
        # Mô phỏng đúng cache RAM dựng từ AgentState: ở đây vẫn là tên nội bộ.
        # Route public phải chuẩn hoá giống đường đọc lại từ PostgreSQL.
        missing_fields=["project_id", "viewing_date", "viewing_time"],
    )
    routes._DEMO_JOBS[workflow_id] = {
        "stage": "NEEDS_INFORMATION",
        "message": response.question,
        "plan": None,
        "events": [],
        "goal": "Đặt lịch tham quan dự án.",
        "existing_context": {},
        "response": response,
    }

    async def _must_not_consume(*args, **kwargs):
        raise AssertionError("câu hỏi tra cứu không được consume clarification")

    monkeypatch.setattr(routes, "_consume_and_create_child", _must_not_consume)
    result = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        json={"message": "Có những dự án nào?"},
    )

    assert result.status_code == 202
    body = result.json()
    assert body["workflow_id"] == workflow_id
    assert body["status"] == "NEEDS_INFORMATION"
    assert body["missing_fields"] == ["project_name", "viewing_date", "viewing_time"]
    assert body["response_state"] == "READY"
    assert all(project["project_name"] in body["answer"] for project in routes.PROJECTS)
    assert routes._DEMO_JOBS[workflow_id]["response"].status == "NEEDS_INFORMATION"


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
async def test_structured_form_rejects_partial_submission_without_creating_a_child(
    client,
    monkeypatch,
) -> None:
    """Form hiển thị nhiều ô phải gửi đủ trong một lần.

    Chấp nhận một phần sẽ tạo một child workflow và một câu Response Agent cho
    mỗi ô người dùng bỏ trống — đúng vòng lặp UI mà test này khoá lại.
    """
    routes._DEMO_JOBS.clear()
    original_id = "workflow-structured-needs-date-time"
    routes._DEMO_JOBS[original_id] = {
        "stage": "NEEDS_INFORMATION",
        "message": "Thiếu ngày và giờ.",
        "plan": None,
        "events": [],
        "goal": "Đặt lịch tham quan PRJ-001.",
        "existing_context": {"project_id": "PRJ-001"},
        "response": routes.DemoWorkflowResponse(
            status="NEEDS_INFORMATION",
            question="Thiếu ngày và giờ.",
            missing_fields=["viewing_date", "viewing_time"],
        ),
    }
    ran = False

    async def _fake_run(*_args, **_kwargs):
        nonlocal ran
        ran = True

    monkeypatch.setattr(routes, "_run_demo_job", _fake_run)
    before = set(routes._DEMO_JOBS)

    response = await client.post(
        f"/api/v1/workflows/demo/{original_id}/continue",
        json={"fields": {"viewing_time": "10:00"}},
    )

    assert response.status_code == 422
    assert set(routes._DEMO_JOBS) == before
    assert ran is False


@pytest.mark.asyncio
async def test_demo_start_returns_immediately_then_status_returns_background_result(client, monkeypatch):
    routes._DEMO_JOBS.clear()

    async def _fake_job(workflow_id, goal, approve_mock_payment, service_urls, account_state, **kwargs):
        assert goal == "Đăng ký dữ liệu test"
        # Persona do server suy ra từ token + user_resident_links; request
        # không còn khai được. Tài khoản test chưa có liên kết đã VERIFIED.
        assert account_state == "prospect"
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
        json={"goal": "Đăng ký dữ liệu test"},
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
async def _start_and_poll(client, goal: str) -> dict:
    """Chạy workflow qua đường async CHÍNH THỨC rồi đọc kết quả.

    Bốn test dưới đây trước chạy qua `POST /workflows/demo` — biến thể đồng bộ
    đã bị xoá vì nó không đòi xác thực nhưng vẫn gọi LLM và runtime thật. Ý
    định của chúng (view an toàn, không echo lỗi, không nguỵ trang lỗi thực thi
    thành lời mời thanh toán) không đổi; chỉ đường đi đổi.
    """
    started = await client.post("/api/v1/workflows/demo/start", json={"goal": goal})
    assert started.status_code == 202, started.text
    workflow_id = started.json()["workflow_id"]

    # Poll cho tới khi job nền kết thúc. `asyncio.sleep(0)` chỉ nhường một vòng
    # lặp, không đủ cho một job có nhiều điểm await — và một test đọc quá sớm
    # sẽ luôn thấy RUNNING, tức là xanh/đỏ theo tốc độ máy chứ không theo code.
    for _ in range(200):
        polled = await client.get(f"/api/v1/workflows/demo/{workflow_id}")
        assert polled.status_code == 200, polled.text
        body = polled.json()
        if body["status"] not in {"PENDING", "RUNNING"}:
            return body
        await asyncio.sleep(0.01)

    raise AssertionError(f"workflow không kết thúc, trạng thái cuối: {body['status']}")


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

    body = await _start_and_poll(client, "Đăng ký dữ liệu test")

    assert body["status"] == "SUCCESS"
    # Đường async dùng workflow_id do SERVER sinh, không phải id trong state
    # giả — và chính nó là id mà client poll. Giữ nguyên khẳng định cũ sẽ khoá
    # test vào một chi tiết của biến thể đồng bộ đã bị xoá.
    assert body["workflow_id"]
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
    # Token của provider và dữ liệu thô của tool KHÔNG được lọt ra view.
    import json as _json

    rendered = _json.dumps(body, ensure_ascii=False)
    assert "MUST-NOT-LEAK" not in rendered
    assert "provider_token" not in rendered
    assert "input" not in rendered


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


def test_a_payment_approval_signal_renders_as_a_waiting_view_with_a_quote() -> None:
    """`policy_error=PAYMENT_APPROVAL_REQUIRED` phải thành view chờ duyệt kèm báo giá.

    Test này trước chạy qua `POST /workflows/demo` — biến thể đồng bộ đã bị xoá.
    Thứ nó thực sự kiểm là hàm render `_demo_response`, nên giờ gọi thẳng hàm
    đó. Đi vòng qua nhánh async sẽ kéo theo persist báo giá xuống PostgreSQL,
    tức là kiểm một thứ khác — và nhánh đó đã có test riêng ngay bên dưới.
    """
    from src.common.results import StandardResult

    plan = _demo_plan(with_payment=True)
    state = {
        "planner_status": "READY",
        "plan": plan,
        # Chờ duyệt là tín hiệu TƯỜNG MINH từ policy guard, kèm kết quả prefix.
        "policy_error": "PAYMENT_APPROVAL_REQUIRED",
        "workflow_id": "wf-approval",
        "task_results": {"T2": StandardResult.ok({"booking_id": "BOOK-001", "amount": 150_000, "currency": "VND"})},
    }

    body = routes._demo_response(state, False)

    assert body.status == "WAITING_APPROVAL"
    assert body.payment_quote["amount"] == 150_000


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

    body = await _start_and_poll(client, "Thanh toán phí mock")

    assert body["status"] == "EXECUTION_ERROR"


@pytest.mark.asyncio
async def test_demo_workflow_does_not_echo_unexpected_exception(client, monkeypatch):
    secret = "postgresql://user:secret@example.invalid/database"

    async def _run_demo_workflow(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(routes, "run_demo_workflow", _run_demo_workflow)

    body = await _start_and_poll(client, "Đăng ký dữ liệu test")

    # Lỗi bất ngờ KHÔNG được mang DSN ra ngoài. Nhánh async báo lỗi qua trạng
    # thái workflow chứ không qua HTTP 503, nhưng ràng buộc thì y hệt: không
    # mẩu nào của exception gốc được xuất hiện trong thứ người dùng đọc được.
    import json as _json

    rendered = _json.dumps(body, ensure_ascii=False)
    assert secret not in rendered
    assert "postgresql://" not in rendered
    assert "user:secret" not in rendered
    assert body["status"] != "SUCCESS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"goal": "   "},
        {"goal": "Đăng ký test", "existing_context": {"amount": 1}},
    ],
)
async def test_demo_workflow_rejects_untrusted_request_shape(client, payload):
    response = await client.post("/api/v1/workflows/demo/start", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        # Quyết định thanh toán chỉ được mang ĐÚNG quyết định. Số tiền và mã đặt
        # chỗ là dữ liệu có thẩm quyền của backend; nhận chúng từ browser là để
        # người dùng tự định giá dịch vụ của mình.
        ("payment-decision", {"decision": "approve", "amount": 1}),
        ("payment-decision", {"decision": "approve", "booking_id": "BOOK-001"}),
        # Trả lời câu hỏi bổ sung KHÔNG được kèm goal mới: đổi goal giữa chừng
        # là thay việc cần làm sau khi quyền đã được xét cho việc cũ.
        ("continue", {"fields": {"parking_zone": "ZONE_A"}, "goal": "Đăng ký cư dân"}),
        ("continue", {"fields": {"parking_zone": "ZONE_A"}, "account_state": "resident"}),
    ],
)
async def test_the_api_refuses_bodies_that_carry_backend_owned_data(client, path, payload):
    """Coverage này trước được kiểm bằng cách đọc source `static/demo.html`.

    Trang đó đã bị xoá, và đọc source của một client vốn cũng chỉ chứng minh
    MỘT client cư xử đúng. Kiểm ở biên API mạnh hơn: bất kỳ client nào gửi thừa
    field cũng bị từ chối, kể cả curl.
    """
    workflow_id = "00000000-0000-4000-8000-000000000abc"
    response = await client.post(f"/api/v1/workflows/demo/{workflow_id}/{path}", json=payload)

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
    """Guard đọc context do server dựng, không đọc gì từ request body.

    `_DEMO_ACCOUNT_CONTEXTS["resident"]` giờ là KHUNG, không phải dữ liệu:
    `resident_id`/`apartment_code` được điền từ `user_resident_links` cộng bảng
    `residents`. Trước đây RES-001/A1201 nằm cứng ở đây nên mọi tài khoản
    resident đều thao tác trên cùng một căn hộ.
    """
    prospect = routes._DEMO_ACCOUNT_CONTEXTS["prospect"]
    resident = routes._DEMO_ACCOUNT_CONTEXTS["resident"]

    assert prospect.get("resident_verification_status") != "VERIFIED"
    assert "resident_id" not in prospect
    assert resident["resident_verification_status"] == "VERIFIED"
    assert "resident_id" not in resident, "danh tính phải đến từ DB, không nằm cứng trong hằng số"


def test_account_state_defaults_to_the_least_privileged_persona() -> None:
    """`account_state` không còn là field của request — gửi nó là 422.

    Fail-closed bằng default là chưa đủ: caller vẫn gửi được và vẫn tin nó có
    tác dụng. Giờ nó bị TỪ CHỐI, nên hiểu nhầm trở thành một lỗi nhìn thấy được
    thay vì một giả định sai âm thầm.
    """
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError):
        DemoWorkflowRequest(goal="Đăng ký chỗ đậu xe cho xe của tôi", account_state="resident")

    request = DemoWorkflowRequest(goal="Đăng ký chỗ đậu xe cho xe của tôi")
    assert not hasattr(request, "account_state")


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

    # Guard ownership tra chủ sở hữu từ PostgreSQL. Test này gọi thẳng handler
    # nên phải cấp một repository trả đúng chủ — KHÔNG tắt guard đi, vì chính
    # thứ tự "kiểm quyền trước khi đọc trạng thái" là điều cần giữ.
    user = {"id": "00000000-0000-0000-0000-0000000000aa"}

    class _Pool:
        async def close(self) -> None:
            return None

    class _Repo:
        _pool = _Pool()

        async def get_workflow_owner(self, _wf_id: str) -> str:
            return user["id"]

    async def _build_repo(**_kwargs):
        return _Repo()

    set_repository_provider(_build_repo)
    try:
        response = await routes.decide_demo_payment(
            workflow_id,
            routes.DemoPaymentDecisionRequest(decision="reject"),
            user=user,
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
    """Mỗi /start là một thread mới, và persona LUÔN do server quyết định.

    Bản trước khẳng định persona đi theo `account_state` trong body. Field đó
    đã rời contract: quyền giờ suy ra từ token cộng `user_resident_links`, nên
    hai lần /start của cùng một tài khoản phải cho cùng một persona bất kể body
    gửi gì. Đây là khẳng định MẠNH HƠN bản cũ, không phải nới lỏng nó.

    Session_id vẫn phải khác nhau: nối tiếp session cũ sẽ khiến lần /continue
    sau đọc trúng ngữ cảnh của cuộc hội thoại trước.
    """
    routes._DEMO_JOBS.clear()
    captured = []

    async def _fake_job(workflow_id, goal, approve_mock_payment, service_urls, account_state, **kwargs):
        captured.append(account_state)

    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)

    first = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đặt lịch tham quan PRJ-001 ngày 2026-12-10 lúc 10:00."},
    )
    second = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đặt lịch tham quan PRJ-001 ngày 2026-12-10 lúc 10:00."},
    )
    await asyncio.sleep(0)

    first_sid = first.json()["session_id"]
    second_sid = second.json()["session_id"]
    assert first_sid and second_sid
    assert first_sid != second_sid
    # Tài khoản test chưa có liên kết cư dân đã VERIFIED → prospect ở cả hai lần.
    assert captured == ["prospect", "prospect"]


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
async def test_session_persist_failure_before_202_is_fatal(client, monkeypatch) -> None:
    """Không ghim được phiên TRƯỚC khi trả 202 → 503, và Planner không chạy.

    Contract cũ coi đây là non-fatal: `/start` vẫn trả 202 rồi fail-closed về
    prospect. Nhưng khi đó người dùng nhận một `workflow_id` mà mọi lần đọc sau
    đều mất quyền, và không có gì nói cho họ biết vì sao. Thà báo lỗi ngay.
    """
    routes._DEMO_JOBS.clear()
    calls = []

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("DB down")

    async def _fake_job(*args, **_kwargs):
        calls.append(args[0] if args else None)

    monkeypatch.setattr(routes, "_create_shell_and_session", _boom)
    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)

    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đặt lịch xem nhà tại Vinhomes Ocean Park ngày 2030-12-10 lúc 10:00."},
    )
    await asyncio.sleep(0)

    assert response.status_code == 503, response.text
    assert calls == [], "Planner chạy dù chưa ghim được phiên"
    for leaked in ("DB down", "postgresql://", "RuntimeError"):
        assert leaked not in response.text


@pytest.mark.asyncio
async def test_a_redundant_session_write_inside_the_job_never_breaks_a_running_workflow(client, monkeypatch) -> None:
    """Ghim lại phiên trong background job là phòng thủ, không phải điều kiện.

    Phiên durable đã có từ `/start`. Lần ghi lại bên trong `_run_demo_job` chỉ
    là idempotent; nó lỗi thì workflow đang chạy vẫn phải chạy tiếp, và quyền
    đã persist không được mất.
    """
    routes._DEMO_JOBS.clear()
    ran = []

    async def _redundant_write_fails(*_args, **_kwargs):
        return False

    async def _fake_job(workflow_id, goal, approve, urls, account_state, **kwargs):
        # Job ghi lại phiên (idempotent) rồi vẫn phải chạy tiếp.
        await routes._persist_session(kwargs.get("session_id"), account_state)
        ran.append(account_state)

    monkeypatch.setattr(routes, "_persist_session", _redundant_write_fails)
    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)

    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đặt lịch xem nhà tại Vinhomes Ocean Park ngày 2030-12-10 lúc 10:00."},
    )
    for _ in range(20):
        await asyncio.sleep(0)

    assert response.status_code == 202, response.text
    assert ran == ["prospect"], "workflow bị chặn vì một lần ghi lại phiên thất bại"


@pytest.mark.asyncio
async def test_demo_start_refuses_a_contact_profile_from_the_browser(client, monkeypatch) -> None:
    """`contact_profile` đã rời contract — gửi nó là 422.

    Hai test trước kiểm rằng hồ sơ liên hệ do browser gửi không lọt vào trusted
    context. Ràng buộc đó giờ mạnh hơn: browser không gửi được nữa. Thông tin
    liên hệ lấy từ tài khoản/provider, nên số điện thoại và email không còn đi
    qua request body ở bất kỳ đường nào.
    """
    routes._DEMO_JOBS.clear()

    async def _fake_job(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)

    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={
            "goal": "Đặt lịch tham quan PRJ-001 ngày 2026-12-10 lúc 10:00.",
            "contact_profile": {"full_name": "Nguyễn Văn A", "phone": "0948500414"},
        },
    )

    assert response.status_code == 422, response.text
