import asyncio
import json
import logging
import re
from datetime import date, time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from src.agents.graph import agent
from src.common.projects import find_project_id, resolve_project_id
from src.common.task_plan import TaskPlan
from src.config import get_settings
from src.models.schemas import (
    ChatRequest,
    ChatResponse,
    DemoDetailItem,
    DemoPlanTask,
    DemoTaskResult,
    DemoWorkflowContinueRequest,
    DemoWorkflowEvent,
    DemoWorkflowRequest,
    DemoWorkflowResponse,
)
from src.orchestration.demo_service import read_demo_workflow, run_demo_workflow

router = APIRouter()
logger = logging.getLogger(__name__)

_DEMO_JOBS: dict[str, dict[str, Any]] = {}
_DEMO_TASKS: set[asyncio.Task[None]] = set()

_DATE_FIELDS = frozenset({"viewing_date", "booking_date", "preferred_date", "move_date"})
_TIME_FIELDS = frozenset({"viewing_time", "preferred_time", "move_time"})
_BOOLEAN_FIELDS = frozenset({"consent", "needs_elevator", "needs_loading_support"})
_FOLLOW_UP_VALIDATION_MESSAGES = {
    "viewing_date": "Ngày xem phải tồn tại, theo định dạng YYYY-MM-DD hoặc DD/MM/YYYY và không ở quá khứ.",
    "booking_date": "Ngày đặt chỗ phải tồn tại, theo định dạng YYYY-MM-DD hoặc DD/MM/YYYY và không ở quá khứ.",
    "preferred_date": "Ngày bảo trì phải tồn tại, theo định dạng YYYY-MM-DD hoặc DD/MM/YYYY và không ở quá khứ.",
    "move_date": "Ngày chuyển nhà phải tồn tại, theo định dạng YYYY-MM-DD hoặc DD/MM/YYYY và không ở quá khứ.",
    "viewing_time": "Giờ xem phải theo định dạng HH:MM và trong khoảng 08:00–17:30.",
    "preferred_time": "Giờ bảo trì phải theo định dạng HH:MM và trong khoảng 08:00–18:00.",
    "move_time": "Giờ chuyển nhà phải theo định dạng HH:MM và trong khoảng 07:00–20:00.",
    "parking_zone": "Khu vực đỗ xe hiện chỉ hỗ trợ ZONE_A hoặc ZONE_B.",
    "plate_number": "Vui lòng nhập biển số xe, ví dụ 59A-12345.",
}


def _extract_date(text: str) -> str | None:
    match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if match:
        year, month, day = map(int, match.groups())
    else:
        match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
        if not match:
            return None
        day, month, year = map(int, match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _extract_time(text: str) -> str | None:
    match = re.search(r"\b([01]?\d|2[0-3])[:;hH]([0-5]\d)\b", text)
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _extract_parking_zone(text: str) -> str | None:
    match = re.search(r"\b(?:zone|khu)[ _-]*([ab])\b", text, re.IGNORECASE)
    return f"ZONE_{match.group(1).upper()}" if match else None


def _extract_plate_number(text: str) -> str | None:
    match = re.search(r"\b(\d{2}[a-z]{1,2})[ .-]?(\d{3,5})\b", text, re.IGNORECASE)
    return f"{match.group(1).upper()}-{match.group(2)}" if match else None


def _is_allowed_schedule_date(value: str) -> bool:
    return date.fromisoformat(value) >= date.today()


def _is_allowed_schedule_time(field: str, value: str) -> bool:
    parsed = time.fromisoformat(value)
    windows = {
        "viewing_time": (time(8, 0), time(17, 30)),
        "preferred_time": (time(8, 0), time(18, 0)),
        "move_time": (time(7, 0), time(20, 0)),
    }
    window = windows.get(field)
    return window is None or window[0] <= parsed <= window[1]


def _extract_follow_up_answers(message: str, missing_fields: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Map câu trả lời vào field backend đang chờ, không nhờ LLM suy đoán."""
    text = message.strip()
    answers: dict[str, Any] = {}
    unresolved: list[str] = []
    for field in missing_fields:
        if field in _DATE_FIELDS:
            value: Any = _extract_date(text)
            if value is not None and not _is_allowed_schedule_date(value):
                value = None
        elif field in _TIME_FIELDS:
            value = _extract_time(text)
            if value is not None and not _is_allowed_schedule_time(field, value):
                value = None
        elif field == "project_id":
            value = resolve_project_id(text)
        elif field == "parking_zone":
            value = _extract_parking_zone(text)
        elif field == "plate_number":
            value = _extract_plate_number(text)
        elif field in _BOOLEAN_FIELDS:
            lowered = text.casefold()
            value = True if any(word in lowered for word in ("có", "đồng ý", "yes")) else None
            if any(word in lowered for word in ("không", "no")):
                value = False
        elif len(missing_fields) == 1 and field not in {"supported_goal", "payment_quote"}:
            value = text
        else:
            value = None
        if value is None:
            unresolved.append(field)
        else:
            answers[field] = value
    return answers, unresolved


def _extract_structured_follow_up_answers(
    fields: dict[str, str | bool | int | float],
    missing_fields: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """Validate từng field form bằng cùng luật với câu trả lời chat."""
    allowed = set(missing_fields)
    if any(name not in allowed for name in fields):
        return {}, list(missing_fields)

    answers: dict[str, Any] = {}
    unresolved: list[str] = []
    for name in missing_fields:
        if name not in fields:
            unresolved.append(name)
            continue
        raw = fields[name]
        if name in _BOOLEAN_FIELDS and isinstance(raw, str) and raw.casefold() in {"true", "false"}:
            answers[name] = raw.casefold() == "true"
        elif isinstance(raw, str):
            parsed, missing = _extract_follow_up_answers(raw, [name])
            if missing:
                unresolved.append(name)
            else:
                answers.update(parsed)
        elif name in _BOOLEAN_FIELDS and isinstance(raw, bool):
            answers[name] = raw
        else:
            unresolved.append(name)
    return answers, unresolved


def _follow_up_validation_message(unresolved: list[str]) -> str:
    """Trả hướng dẫn deterministic, không echo câu trả lời không hợp lệ."""
    if unresolved:
        message = _FOLLOW_UP_VALIDATION_MESSAGES.get(unresolved[0])
        if message is not None:
            return message
    return "Thông tin bổ sung chưa đúng định dạng được yêu cầu."


# Demo-only server-side identity context. Browser chỉ chọn persona, không được
# tự gửi resident_id/apartment_id. Production phải thay bằng auth/session và
# resident directory thật.
_DEMO_ACCOUNT_CONTEXTS: dict[str, dict[str, Any]] = {
    "prospect": {
        "account_id": "DEMO-PROSPECT",
        "resident_verification_status": "NOT_LINKED",
        "account_contact_status": "VERIFIED",
    },
    "resident": {
        "account_id": "DEMO-RESIDENT",
        "resident_id": "RES-001",
        "apartment_id": "A1201",
        "apartment_code": "A1201",
        "residential_area": "Vinhomes Ocean Park",
        "resident_verification_status": "VERIFIED",
        "account_contact_status": "VERIFIED",
    },
}

_STAGE_MESSAGES = {
    "PLANNING": "LLM đang phân tích mục tiêu và chọn các dịch vụ cần thiết.",
    "PLANNED": "Agent đã tạo TaskPlan có cấu trúc.",
    "VALIDATING": "Validator đang kiểm tra dependency, allowlist và dữ liệu an toàn.",
    "VALIDATED": "Kế hoạch đã hợp lệ và được phép chuyển sang thực thi.",
    "EXECUTING": "Executor đang gọi các dịch vụ theo đúng thứ tự phụ thuộc.",
    "TASK_RUNNING": "Executor đang thực hiện một tác vụ nghiệp vụ.",
    "TASK_SUCCESS": "Một tác vụ nghiệp vụ đã hoàn thành.",
    "TASK_FAILED": "Một tác vụ nghiệp vụ đã thất bại.",
    "NEEDS_INFORMATION": "Agent cần thêm thông tin trước khi lập kế hoạch.",
    "VALIDATION_FAILED": "Kế hoạch bị Validator từ chối.",
    "EXECUTION_FAILED": "Workflow dừng trong quá trình thực thi.",
    "FINISHED": "Workflow đã kết thúc và trạng thái đã được lưu.",
}


def _event_message(stage: str, payload: dict[str, Any], plan: TaskPlan | None) -> str:
    task_id = payload.get("task_id")
    task = next((item for item in plan.tasks if item.task_id == task_id), None) if plan else None
    title = _TOOL_PRESENTATION.get(task.tool, ("tác vụ", ""))[0] if task else "tác vụ"
    if stage == "TASK_RUNNING":
        return f"Agent đang thực hiện bước “{title}”."
    if stage == "TASK_SUCCESS":
        return f"Agent đã hoàn thành bước “{title}”."
    if stage == "TASK_FAILED":
        return f"Bước “{title}” không thể hoàn thành. Workflow sẽ dừng an toàn."
    return _STAGE_MESSAGES.get(stage, "Agent đang xử lý workflow.")


def _append_job_event(job: dict[str, Any], stage: str, payload: dict[str, Any] | None = None) -> None:
    payload = payload or {}
    events = job.setdefault("events", [])
    plan = job.get("plan") if isinstance(job.get("plan"), TaskPlan) else None
    message = _event_message(stage, payload, plan)
    previous = events[-1] if events else None
    signature = (stage, payload.get("task_id"), payload.get("task_status"))
    if previous is not None and previous["signature"] == signature:
        return
    events.append(
        {
            "sequence": len(events) + 1,
            "stage": stage,
            "message": message,
            "task_id": payload.get("task_id"),
            "task_status": payload.get("task_status"),
            "signature": signature,
        }
    )
    job["stage"] = stage
    job["message"] = message


def _public_events(job: dict[str, Any] | None) -> list[DemoWorkflowEvent]:
    if job is None:
        return []
    return [
        DemoWorkflowEvent.model_validate({k: v for k, v in event.items() if k != "signature"})
        for event in job.get("events", [])
    ]


_TOOL_PRESENTATION = {
    "search_properties": (
        "Tìm bất động sản",
        "Lọc danh sách phù hợp; không tự thuê, mua hoặc đặt cọc.",
    ),
    "schedule_property_viewing": (
        "Đặt lịch tham quan",
        "Đặt lịch tham quan dự án mà người dùng đã chọn.",
    ),
    "register_property_interest": (
        "Đăng ký nhận tư vấn",
        "Gửi nhu cầu cho bộ phận kinh doanh qua liên hệ đã xác minh.",
    ),
    "create_maintenance_request": (
        "Yêu cầu bảo trì",
        "Gửi yêu cầu sửa chữa và đặt lịch kỹ thuật viên.",
    ),
    "schedule_move": (
        "Đặt lịch chuyển nhà",
        "Đăng ký khung giờ chuyển đồ và nhu cầu hỗ trợ.",
    ),
    "register_resident": (
        "Đăng ký cư dân",
        "Tạo hồ sơ cư dân cho căn hộ đã cung cấp.",
    ),
    "register_vehicle": (
        "Đăng ký phương tiện",
        "Liên kết phương tiện với hồ sơ cư dân.",
    ),
    "book_parking": (
        "Đặt chỗ đỗ xe",
        "Đặt chỗ theo ngày và khu vực đã yêu cầu.",
    ),
    "pay_fee": (
        "Thanh toán phí",
        "Thanh toán đúng khoản phí do dịch vụ đặt chỗ trả về.",
    ),
}


def _text(value: Any) -> str | None:
    """Chỉ nhận scalar để presentation layer không phát tán raw object."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _detail(label: str, value: Any) -> DemoDetailItem | None:
    safe_value = _text(value)
    return DemoDetailItem(label=label, value=safe_value) if safe_value is not None else None


def _money(amount: Any, currency: Any) -> str | None:
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return None
    currency_text = _text(currency)
    formatted = f"{amount:,.0f}".replace(",", ".")
    return f"{formatted} {currency_text}" if currency_text else formatted


def _task_presentation(task: Any, result: Any) -> tuple[str, str, list[DemoDetailItem]]:
    """Tạo nội dung tiếng Việt từ allowlist field của các tool MVP."""
    title, _ = _TOOL_PRESENTATION[task.tool]
    inputs = task.input
    data = result.data if result is not None and isinstance(result.data, dict) else {}
    candidates: list[DemoDetailItem | None]

    if task.tool == "search_properties":
        properties = data.get("properties") if isinstance(data.get("properties"), list) else []
        count = data.get("result_count") if isinstance(data.get("result_count"), int) else len(properties)
        message = f"Đã tìm thấy {count} bất động sản phù hợp."
        candidates = [_detail("Số kết quả", count)]
        for index, item in enumerate(properties[:3], start=1):
            if not isinstance(item, dict):
                continue
            property_id = _text(item.get("property_id"))
            title_text = _text(item.get("title"))
            price = _money(item.get("price"), item.get("currency"))
            summary = " · ".join(value for value in (property_id, title_text, price) if value)
            candidates.append(_detail(f"Gợi ý {index}", summary))
    elif task.tool == "schedule_property_viewing":
        project_name = _text(data.get("project_name"))
        viewing_date = _text(data.get("viewing_date")) or _text(inputs.get("viewing_date"))
        viewing_time = _text(data.get("viewing_time")) or _text(inputs.get("viewing_time"))
        project = project_name
        message = f"Đã đặt lịch tham quan{f' dự án {project}' if project else ' dự án đã chọn'}."
        candidates = [
            _detail("Mã lịch xem", data.get("viewing_id")),
            _detail("Dự án", project),
            _detail("Thời gian", " ".join(value for value in (viewing_date, viewing_time) if value)),
            _detail("Liên hệ", data.get("contact_name")),
            _detail("Điện thoại", data.get("contact_phone")),
        ]
    elif task.tool == "register_property_interest":
        project_name = _text(data.get("project_name"))
        project = project_name
        message = f"Đã đăng ký nhận tư vấn{f' cho dự án {project}' if project else ' cho dự án đã chọn'}."
        candidates = [
            _detail("Mã yêu cầu", data.get("interest_id")),
            _detail("Dự án", project),
            _detail("Trạng thái", data.get("interest_status")),
        ]
    elif task.tool == "register_resident":
        apartment = _text(inputs.get("apartment_code"))
        area = _text(inputs.get("residential_area"))
        location = " tại ".join(value for value in (apartment, area) if value)
        message = f"Đã đăng ký hồ sơ cư dân{f' cho {location}' if location else ''}."
        candidates = [
            _detail("Căn hộ", apartment),
            _detail("Khu dân cư", area),
            _detail("Mã cư dân", data.get("resident_id")),
        ]
    elif task.tool == "register_vehicle":
        plate = _text(inputs.get("plate_number"))
        message = f"Đã đăng ký phương tiện{f' biển số {plate}' if plate else ''}."
        candidates = [
            _detail("Biển số", plate),
            _detail("Loại xe", inputs.get("vehicle_type")),
            _detail("Mã phương tiện", data.get("vehicle_id")),
        ]
    elif task.tool == "book_parking":
        zone = _text(data.get("parking_zone")) or _text(inputs.get("parking_zone"))
        booking_date = _text(data.get("booking_date")) or _text(inputs.get("booking_date"))
        place = " · ".join(value for value in (zone, booking_date) if value)
        message = f"Đã đặt chỗ đỗ xe{f' ({place})' if place else ''}."
        candidates = [
            _detail("Mã đặt chỗ", data.get("booking_id")),
            _detail("Khu vực", zone),
            _detail("Ngày đặt", booking_date),
            _detail("Phí đặt chỗ", _money(data.get("amount"), data.get("currency"))),
        ]
    elif task.tool == "pay_fee":
        payment_status = _text(data.get("payment_status"))
        message = "Đã thanh toán phí đặt chỗ thành công."
        candidates = [
            _detail("Mã thanh toán", data.get("payment_id")),
            _detail("Trạng thái", payment_status),
        ]
    elif task.tool == "create_maintenance_request":
        appointment_date = _text(data.get("appointment_date")) or _text(inputs.get("preferred_date"))
        appointment_time = _text(data.get("appointment_time")) or _text(inputs.get("preferred_time"))
        message = "Đã tiếp nhận yêu cầu bảo trì và xếp lịch kỹ thuật viên."
        candidates = [
            _detail("Mã yêu cầu", data.get("maintenance_id")),
            _detail("Hạng mục", inputs.get("issue_type")),
            _detail("Vị trí", inputs.get("location")),
            _detail("Lịch hẹn", " ".join(value for value in (appointment_date, appointment_time) if value)),
            _detail("Trạng thái", data.get("maintenance_status")),
        ]
    elif task.tool == "schedule_move":
        move_date = _text(data.get("move_date")) or _text(inputs.get("move_date"))
        move_time = _text(data.get("move_time")) or _text(inputs.get("move_time"))
        message = "Đã đăng ký lịch chuyển nhà."
        candidates = [
            _detail("Mã yêu cầu", data.get("move_request_id")),
            _detail("Thời gian", " ".join(value for value in (move_date, move_time) if value)),
            _detail("Khung thang máy", data.get("elevator_slot")),
            _detail("Phương tiện", inputs.get("move_vehicle")),
            _detail("Trạng thái", data.get("move_status")),
        ]
    else:  # pragma: no cover - Task.tool đã bị schema allowlist chặn
        message = "Tác vụ đã hoàn thành."
        candidates = []

    return title, message, [item for item in candidates if item is not None]


def _workflow_summary(task_views: list[DemoTaskResult], succeeded: bool) -> str:
    if succeeded:
        return " ".join(task.message for task in task_views)
    failed_tasks = [task for task in task_views if task.status == "FAILED"]
    failed = next(
        (task for task in failed_tasks if task.error_code != "DEPENDENCY_ERROR"),
        failed_tasks[0] if failed_tasks else None,
    )
    if failed is not None:
        return failed.message
    return "Workflow chưa hoàn tất toàn bộ các bước."


def _validation_guidance(error: str) -> str:
    """Map lỗi Validator sang hướng dẫn cố định; không đưa raw plan ra UI."""
    lowered = error.casefold()
    guidance = []
    rules = (
        ("booking_date", "chọn ngày đặt chỗ hợp lệ và không ở quá khứ"),
        ("parking_zone", "chọn khu vực ZONE_A hoặc ZONE_B"),
        ("plate_number", "bổ sung biển số xe"),
        ("vehicle_type", "chọn loại xe ô tô hoặc xe máy"),
        ("viewing_date", "chọn ngày tham quan hợp lệ và không ở quá khứ"),
        ("viewing_time", "chọn giờ tham quan trong khoảng 08:00–17:30"),
    )
    for marker, instruction in rules:
        if marker in lowered and instruction not in guidance:
            guidance.append(instruction)
    if not guidance:
        return "Kế hoạch chưa đạt quy tắc an toàn. Bạn hãy kiểm tra lại thông tin bắt buộc và thử lại."
    return "Kế hoạch chưa được thực hiện. Bạn cần " + "; ".join(guidance) + "."


def _plan_view(plan: TaskPlan | None) -> list[DemoPlanTask]:
    if plan is None:
        return []
    return [
        DemoPlanTask(
            task_id=task.task_id,
            tool=task.tool,
            depends_on=task.depends_on,
            title=_TOOL_PRESENTATION[task.tool][0],
            description=_TOOL_PRESENTATION[task.tool][1],
        )
        for task in plan.tasks
    ]


def _plan_from_job_or_record(job: dict[str, Any] | None, record: dict[str, Any] | None) -> TaskPlan | None:
    if job is not None and isinstance(job.get("plan"), TaskPlan):
        return job["plan"]
    if record is None:
        return None
    raw = record["workflow"].get("task_plan")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return None
    try:
        return TaskPlan.model_validate(raw)
    except ValueError:
        return None


def _polling_task_views(plan: TaskPlan | None, record: dict[str, Any] | None) -> list[DemoTaskResult]:
    if plan is None:
        return []
    rows = {row["task_id"]: row for row in (record or {}).get("tasks", [])}
    views = []
    for task in plan.tasks:
        row = rows.get(task.task_id)
        title, description = _TOOL_PRESENTATION[task.tool]
        if row is None:
            views.append(
                DemoTaskResult(
                    task_id=task.task_id,
                    tool=task.tool,
                    status="PENDING",
                    title=title,
                    message="Đang chờ bước trước hoàn thành.",
                )
            )
            continue
        status = row["status"]
        if status == "SUCCESS":
            _, message, details = _task_presentation(
                task,
                SimpleNamespace(data=row.get("result_data") or {}),
            )
            views.append(
                DemoTaskResult(
                    task_id=task.task_id,
                    tool=task.tool,
                    status="SUCCESS",
                    title=title,
                    message=message,
                    details=details,
                )
            )
        elif status == "FAILED":
            code = row.get("error_code") or "UNKNOWN_EXTERNAL_ERROR"
            views.append(
                DemoTaskResult(
                    task_id=task.task_id,
                    tool=task.tool,
                    status="FAILED",
                    error_code=code,
                    retryable=bool(row.get("retryable")),
                    title=title,
                    message=_task_failure_message(task, title, code),
                )
            )
        else:
            safe_status = "RUNNING" if status == "RUNNING" else "PENDING"
            message = (
                f"Đang thực hiện: {description}" if safe_status == "RUNNING" else "Đang chờ bước trước hoàn thành."
            )
            views.append(
                DemoTaskResult(
                    task_id=task.task_id,
                    tool=task.tool,
                    status=safe_status,
                    title=title,
                    message=message,
                )
            )
    return views


async def _run_demo_job(
    workflow_id: str,
    goal: str,
    approve_mock_payment: bool,
    service_urls: dict[str, str],
    account_state: str,
) -> None:
    job = _DEMO_JOBS[workflow_id]

    async def on_stage(stage: str, payload: dict[str, Any]) -> None:
        plan = payload.get("plan")
        if isinstance(plan, TaskPlan):
            job["plan"] = plan
        _append_job_event(job, stage, payload)

    try:
        state = await run_demo_workflow(
            goal,
            workflow_id=workflow_id,
            on_stage=on_stage,
            existing_context=job["existing_context"],
            approve_mock_payment=approve_mock_payment,
            resident_url=service_urls["resident"],
            transport_url=service_urls["transport"],
            payment_url=service_urls["payment"],
            property_url=service_urls["property"],
            resident_services_url=service_urls["resident_services"],
            contact_profile=job.get("contact_profile"),
        )
        response = _demo_response(state, approve_mock_payment)
        terminal_stage = "FINISHED"
        if response.status == "NEEDS_INFORMATION":
            terminal_stage = "NEEDS_INFORMATION"
        elif response.status == "VALIDATION_ERROR":
            terminal_stage = "VALIDATION_FAILED"
        elif response.status in {"EXECUTION_ERROR", "PLANNING_ERROR"}:
            terminal_stage = "EXECUTION_FAILED"
        _append_job_event(job, terminal_stage)
        job["message"] = response.summary or response.question or job["message"]
        job["response"] = response
    except Exception as exc:  # noqa: BLE001 - không để raw exception vào job public
        logger.warning("demo background workflow failed (%s)", type(exc).__name__)
        _append_job_event(job, "EXECUTION_FAILED")
        job["message"] = "Workflow không thể hoàn thành do lỗi dịch vụ hoặc cấu hình."
        job["response"] = DemoWorkflowResponse(
            workflow_id=workflow_id,
            status="EXECUTION_ERROR",
            stage="EXECUTION_FAILED",
            message=f"Workflow unavailable ({type(exc).__name__}).",
        )


def _keep_demo_task(task: asyncio.Task[None]) -> None:
    _DEMO_TASKS.add(task)
    task.add_done_callback(_DEMO_TASKS.discard)


def _task_failure_message(task: Any, title: str, code: str) -> str:
    """Đổi mã lỗi provider thành thông báo nghiệp vụ, không lộ raw exception."""
    inputs = task.input
    if code == "RESIDENT_ALREADY_EXISTS":
        apartment = _text(inputs.get("apartment_code"))
        subject = f"Căn hộ {apartment}" if apartment else "Căn hộ này"
        return f"{subject} đã có hồ sơ cư dân. Hãy sử dụng tài khoản cư dân đã liên kết."
    if code == "VEHICLE_ALREADY_EXISTS":
        plate = _text(inputs.get("plate_number"))
        subject = f"Biển số {plate}" if plate else "Biển số này"
        return f"{subject} đã được đăng ký. Hãy sử dụng phương tiện đã liên kết hoặc kiểm tra lại biển số."
    if code == "NO_AVAILABILITY":
        if task.tool == "schedule_property_viewing":
            viewing_date = _text(inputs.get("viewing_date"))
            viewing_time = _text(inputs.get("viewing_time"))
            slot = " ".join(value for value in (viewing_date, viewing_time) if value)
            suffix = f" {slot}" if slot else " này"
            return f"Khung giờ tham quan{suffix} không còn trống. Hãy chọn thời gian khác."
        booking_date = _text(inputs.get("booking_date"))
        suffix = f" cho ngày {booking_date}" if booking_date else ""
        return f"Khu vực đỗ xe đã hết chỗ{suffix}. Hãy chọn ngày hoặc khu vực khác."
    if code == "BOOKING_ALREADY_EXISTS":
        return "Phương tiện này đã có chỗ đỗ trong ngày được chọn."
    if code == "DEPENDENCY_ERROR":
        return f"Bước “{title}” chưa được thực hiện vì bước trước đó không thành công."
    if code == "INVALID_INPUT":
        return f"Thông tin của bước “{title}” chưa hợp lệ. Hãy kiểm tra lại dữ liệu đã nhập."
    return f"Không thể hoàn thành bước “{title}”. Vui lòng thử lại."


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Chat với AI agent."""
    try:
        result = await agent.ainvoke({"query": request.message})
        return ChatResponse(
            response=result.get("response", ""),
            analysis=result.get("analysis", ""),
        )
    except Exception as exc:
        # Không echo raw exception: SDK/driver có thể chứa credential hoặc PII.
        raise HTTPException(
            status_code=500,
            detail=f"Agent unavailable ({type(exc).__name__}).",
        ) from None


def _demo_response(state: dict[str, Any], payment_approved: bool) -> DemoWorkflowResponse:
    """Chuyển AgentState thành view model chỉ chứa field nghiệp vụ allowlist."""
    plan = state.get("plan")
    plan_view = _plan_view(plan)

    if state.get("planning_error"):
        return DemoWorkflowResponse(
            status="PLANNING_ERROR",
            summary="Mình chưa thể tạo kế hoạch từ yêu cầu này. Bạn hãy chọn một dịch vụ được hỗ trợ hoặc mô tả lại rõ dịch vụ, ngày giờ và thông tin bắt buộc.",
            plan=plan_view,
        )
    if state.get("planner_status") == "NEEDS_INFORMATION":
        return DemoWorkflowResponse(
            status="NEEDS_INFORMATION",
            question=state.get("question"),
            missing_fields=list(state.get("missing_fields") or []),
            plan=plan_view,
        )
    if state.get("validation_error"):
        return DemoWorkflowResponse(
            status="VALIDATION_ERROR",
            summary=_validation_guidance(str(state["validation_error"])),
            plan=plan_view,
        )
    if state.get("execution_error"):
        contains_payment = plan is not None and any(task.tool == "pay_fee" for task in plan.tasks)
        status = "PAYMENT_APPROVAL_REQUIRED" if contains_payment and not payment_approved else "EXECUTION_ERROR"
        return DemoWorkflowResponse(status=status, plan=plan_view)

    task_results = state.get("task_results", {})
    task_views = []
    for task in plan.tasks if plan is not None else []:
        result = task_results.get(task.task_id)
        if result is None:
            title, _ = _TOOL_PRESENTATION[task.tool]
            task_views.append(
                DemoTaskResult(
                    task_id=task.task_id,
                    tool=task.tool,
                    status="NOT_RUN",
                    title=title,
                    message="Bước này chưa được thực hiện.",
                )
            )
        elif result.success:
            title, message, details = _task_presentation(task, result)
            task_views.append(
                DemoTaskResult(
                    task_id=task.task_id,
                    tool=task.tool,
                    status="SUCCESS",
                    title=title,
                    message=message,
                    details=details,
                )
            )
        else:
            code = result.error_code.value if result.error_code else "UNKNOWN_EXTERNAL_ERROR"
            title, _ = _TOOL_PRESENTATION[task.tool]
            task_views.append(
                DemoTaskResult(
                    task_id=task.task_id,
                    tool=task.tool,
                    status="FAILED",
                    error_code=code,
                    retryable=result.is_retryable,
                    title=title,
                    message=_task_failure_message(task, title, code),
                )
            )

    succeeded = bool(task_views) and all(task.status == "SUCCESS" for task in task_views)
    return DemoWorkflowResponse(
        status="SUCCESS" if succeeded else "FAILED",
        summary=_workflow_summary(task_views, succeeded),
        workflow_id=state.get("workflow_id"),
        plan=plan_view,
        tasks=task_views,
    )


@router.post(
    "/workflows/demo/start",
    response_model=DemoWorkflowResponse,
    status_code=202,
)
async def start_demo_workflow(request: DemoWorkflowRequest) -> DemoWorkflowResponse:
    """Trả workflow_id ngay; workflow tiếp tục chạy trong background task."""
    workflow_id = str(uuid4())
    settings = get_settings()
    context = dict(_DEMO_ACCOUNT_CONTEXTS[request.account_state])
    if request.project_name is not None:
        selected_project_id = resolve_project_id(request.project_name)
        if selected_project_id is None:
            raise HTTPException(status_code=422, detail="Dự án chưa nằm trong danh sách được hỗ trợ.")
    else:
        selected_project_id = find_project_id(request.goal)
    if selected_project_id is not None:
        context["project_id"] = selected_project_id

    _DEMO_JOBS[workflow_id] = {
        "stage": "PLANNING",
        "message": _STAGE_MESSAGES["PLANNING"],
        "plan": None,
        "response": None,
        "events": [],
        "goal": request.goal,
        "account_state": request.account_state,
        "approve_mock_payment": request.approve_mock_payment,
        "existing_context": context,
        "contact_profile": request.contact_profile.model_dump(exclude_none=True)
        if request.contact_profile is not None
        else {},
    }
    _append_job_event(_DEMO_JOBS[workflow_id], "PLANNING")
    task = asyncio.create_task(
        _run_demo_job(
            workflow_id,
            request.goal,
            request.approve_mock_payment,
            {
                "resident": settings.resident_service_url,
                "transport": settings.transport_service_url,
                "payment": settings.payment_service_url,
                "property": settings.property_service_url,
                "resident_services": settings.resident_services_service_url,
            },
            request.account_state,
        )
    )
    _keep_demo_task(task)
    return DemoWorkflowResponse(
        workflow_id=workflow_id,
        status="PENDING",
        stage="PLANNING",
        message=_STAGE_MESSAGES["PLANNING"],
    )


@router.post(
    "/workflows/demo/{workflow_id}/continue",
    response_model=DemoWorkflowResponse,
    status_code=202,
)
async def continue_demo_workflow(
    workflow_id: str,
    request: DemoWorkflowContinueRequest,
) -> DemoWorkflowResponse:
    """Tiếp tục goal gốc bằng field đã map deterministic ở backend."""
    previous = _DEMO_JOBS.get(workflow_id)
    response = previous.get("response") if previous is not None else None
    if previous is None or not isinstance(response, DemoWorkflowResponse):
        raise HTTPException(status_code=409, detail="Workflow chưa sẵn sàng để tiếp tục.")
    if response.status != "NEEDS_INFORMATION" or not response.missing_fields:
        raise HTTPException(status_code=409, detail="Workflow không chờ thêm thông tin.")

    goal = previous["goal"]
    context = dict(previous["existing_context"])
    missing_fields = response.missing_fields
    if missing_fields == ["supported_goal"]:
        if request.message is None:
            raise HTTPException(status_code=422, detail=_follow_up_validation_message(missing_fields))
        goal = request.message.strip()
    elif "payment_quote" in missing_fields:
        raise HTTPException(status_code=409, detail="Hệ thống chưa lấy được báo phí; người dùng không thể tự nhập.")
    else:
        if request.fields:
            answers, unresolved = _extract_structured_follow_up_answers(request.fields, missing_fields)
        elif request.message is not None:
            answers, unresolved = _extract_follow_up_answers(request.message, missing_fields)
        else:
            answers, unresolved = {}, missing_fields
        if not answers:
            raise HTTPException(status_code=422, detail=_follow_up_validation_message(unresolved))
        context.update(answers)

    new_workflow_id = str(uuid4())
    settings = get_settings()
    service_urls = {
        "resident": settings.resident_service_url,
        "transport": settings.transport_service_url,
        "payment": settings.payment_service_url,
        "property": settings.property_service_url,
        "resident_services": settings.resident_services_service_url,
    }
    _DEMO_JOBS[new_workflow_id] = {
        "stage": "PLANNING",
        "message": _STAGE_MESSAGES["PLANNING"],
        "plan": None,
        "response": None,
        "events": [],
        "goal": goal,
        "account_state": previous["account_state"],
        "approve_mock_payment": previous["approve_mock_payment"],
        "existing_context": context,
        "contact_profile": dict(previous.get("contact_profile", {})),
    }
    _append_job_event(_DEMO_JOBS[new_workflow_id], "PLANNING")
    task = asyncio.create_task(
        _run_demo_job(
            new_workflow_id,
            goal,
            previous["approve_mock_payment"],
            service_urls,
            previous["account_state"],
        )
    )
    _keep_demo_task(task)
    return DemoWorkflowResponse(
        workflow_id=new_workflow_id,
        status="PENDING",
        stage="PLANNING",
        message=_STAGE_MESSAGES["PLANNING"],
    )


@router.get(
    "/workflows/demo/{workflow_id}",
    response_model=DemoWorkflowResponse,
)
async def get_demo_workflow_status(workflow_id: str) -> DemoWorkflowResponse:
    """Kết hợp stage của Agent với task status thật đọc từ PostgreSQL."""
    job = _DEMO_JOBS.get(workflow_id)
    try:
        record = await read_demo_workflow(workflow_id)
    except Exception:  # noqa: BLE001 - DB tạm lỗi không được lộ connection detail
        record = None
    if job is None and record is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    if job is not None and isinstance(job.get("response"), DemoWorkflowResponse):
        response = job["response"]
        return response.model_copy(
            update={
                "stage": job["stage"],
                "message": job["message"],
                "persisted": record is not None,
                "events": _public_events(job),
            }
        )

    plan = _plan_from_job_or_record(job, record)
    task_views = _polling_task_views(plan, record)
    stage = job["stage"] if job is not None else "FINISHED"
    message = job["message"] if job is not None else _STAGE_MESSAGES["FINISHED"]
    database_status = record["workflow"]["status"] if record is not None else None
    status = database_status if database_status in {"SUCCESS", "FAILED"} else "RUNNING"
    return DemoWorkflowResponse(
        workflow_id=workflow_id,
        status=status,
        stage=stage,
        message=message,
        persisted=record is not None,
        plan=_plan_view(plan),
        tasks=task_views,
        events=_public_events(job),
    )


@router.post("/workflows/demo", response_model=DemoWorkflowResponse)
async def demo_workflow(request: DemoWorkflowRequest) -> DemoWorkflowResponse:
    """Chạy Gate 2 E2E demo; browser không được tự gửi trusted context."""
    settings = get_settings()
    try:
        state = await run_demo_workflow(
            request.goal,
            existing_context=_DEMO_ACCOUNT_CONTEXTS[request.account_state],
            approve_mock_payment=request.approve_mock_payment,
            resident_url=settings.resident_service_url,
            transport_url=settings.transport_service_url,
            payment_url=settings.payment_service_url,
            property_url=settings.property_service_url,
            resident_services_url=settings.resident_services_service_url,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Workflow demo unavailable ({type(exc).__name__}).",
        ) from None
    return _demo_response(state, request.approve_mock_payment)


@router.get("/status")
async def agent_status():
    """Kiểm tra trạng thái agent."""
    return {"status": "ready", "agent": "LangGraph Agent v1.0"}
    (DemoDetailItem,)
