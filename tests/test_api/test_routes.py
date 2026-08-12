import asyncio

import pytest

from src.api import routes
from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan


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


@pytest.mark.asyncio
async def test_demo_ui_is_served(client):
    response = await client.get("/demo")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert "P-118 · Workflow Demo" in response.text
    assert "/api/v1/workflows/demo" in response.text
    assert "Workflow Agent" in response.text
    assert "Kết quả nghiệp vụ" in response.text
    assert "Tài khoản demo" in response.text
    assert "Hoàn tất trong" in response.text
    assert "Xem chi tiết" in response.text
    assert "ĐÃ XONG" in response.text
    assert "Mock Payment API" not in response.text
    assert "Thông tin Agent chưa có" in response.text
    assert "TỰ ĐỘNG TẠO TỪ FORM" in response.text
    assert 'data-service="resident"' not in response.text
    assert 'data-service="property-search"' not in response.text
    assert 'data-service="property-viewing"' in response.text
    assert 'data-service="property-interest"' in response.text
    assert 'data-service="vehicle"' in response.text
    assert 'data-service="parking"' in response.text
    assert 'data-service="payment"' in response.text
    assert 'data-service="maintenance"' in response.text
    assert 'data-service="moving"' in response.text
    assert 'data-persona="prospect"' in response.text
    assert 'data-persona="resident"' in response.text
    assert "chưa có resident-property mapping" in response.text
    assert "Sắp ra mắt" in response.text
    assert "Đăng ký quan tâm / nhận tư vấn" in response.text
    for project_name in (
        "Vinhomes Sài Gòn Park",
        "Vinhomes Global Gate Hạ Long",
        "Vinhomes Hải Vân Bay",
        "Vinhomes Pearl Bay",
        "Vinhomes Green Paradise",
        "Vinhomes Golden City",
        "Vinhomes Ocean Park",
    ):
        assert project_name in response.text
    assert "dự án mã ${values.project_id}" not in response.text
    assert 'addEventListener("click", () => executeWorkflow())' in response.text
    assert "Khách & quyền ra vào" not in response.text
    assert "Giữ chỗ / đăng ký sớm" in response.text
    assert "Theo dõi hồ sơ / giao dịch" in response.text
    assert "Agent chỉ thực hiện mục đã chọn và luôn giữ thứ tự 01 → 04" in response.text
    assert "Resolve kết quả bước trước" in response.text
    assert "pendingMissingFields" in response.text
    assert "normalizeGoalText" in response.text
    assert "align-self: flex-end" in response.text
    assert "event.isComposing" in response.text
    assert 'autocomplete="off"' in response.text
    assert "Đã tự điền từ tài khoản · bạn có thể chỉnh" in response.text
    assert "contact_profile: propertyContactProfile()" in response.text
    assert "hasInvalidRequiredFields()" in response.text
    assert "P-118 đang xử lý" in response.text
    assert "thinking-dots" in response.text
    assert "showProcessing(" in response.text
    assert "hideProcessing()" in response.text


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


def test_follow_up_does_not_silently_map_unsupported_zone() -> None:
    answers, unresolved = routes._extract_follow_up_answers("Zone C", ["parking_zone"])

    assert answers == {}
    assert unresolved == ["parking_zone"]
    assert "ZONE_A hoặc ZONE_B" in routes._follow_up_validation_message(unresolved)


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
        ("viewing_date", "không ở quá khứ"),
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

    async def _fake_run(workflow_id, goal, approved, urls, account_state):
        job = routes._DEMO_JOBS[workflow_id]
        captured.update(
            workflow_id=workflow_id,
            goal=goal,
            approved=approved,
            account_state=account_state,
            context=job["existing_context"],
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

    async def _fake_run(workflow_id, goal, approved, urls, account_state):
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

    async def _fake_job(workflow_id, goal, approve_mock_payment, service_urls, account_state):
        assert goal == "Đăng ký dữ liệu test"
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
    assert events[5]["message"] == "Agent đang thực hiện bước “Đăng ký cư dân”."


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
        "Đã đặt chỗ đỗ xe (ZONE_A · 2026-08-20). "
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
    assert response.tasks[2].message == "Đã đặt chỗ đỗ xe (ZONE_A · 2026-08-20)."
    assert response.tasks[3].message == "Đã thanh toán phí đặt chỗ thành công."
    assert {item.label: item.value for item in response.tasks[2].details} == {
        "Mã đặt chỗ": "BOOK-001",
        "Khu vực": "ZONE_A",
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
    assert "không ở quá khứ" in validation.summary
    assert "ZONE_A hoặc ZONE_B" in validation.summary
    assert "safe internal category" not in planning.model_dump_json()


@pytest.mark.asyncio
async def test_demo_workflow_reports_payment_approval_required(client, monkeypatch):
    plan = _demo_plan(with_payment=True)

    async def _run_demo_workflow(*args, **kwargs):
        return {
            "planner_status": "READY",
            "plan": plan,
            "execution_error": "Thực thi thất bại (PaymentApprovalRequiredError).",
        }

    monkeypatch.setattr(routes, "run_demo_workflow", _run_demo_workflow)

    response = await client.post(
        "/api/v1/workflows/demo",
        json={"goal": "Thanh toán phí mock", "approve_mock_payment": False},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "PAYMENT_APPROVAL_REQUIRED"


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
