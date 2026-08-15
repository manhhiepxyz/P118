import asyncio
import json
import logging
import re
import unicodedata
import uuid
from datetime import date, time, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.agents.planner import MISSING_FIELD_LABELS
from src.agents.response_agent import ReplyView, ResponseAgent
from src.agents.validator import TaskPlanValidator
from src.api.deps import get_current_user
from src.api.small_talk import SmallTalk, SpeechType, answer_capability_question, classify
from src.common.enums import ErrorCode, WorkflowStatus
from src.common.failure_messages import repair_question, task_failure_message
from src.common.failures import classify_failure, failure_for_code
from src.common.projects import PROJECTS, find_project_id, project_name, resolve_project_id
from src.common.task_plan import InputRef, TaskPlan
from src.config import get_settings
from src.db.resident_link_repository import get_link_status, get_verified_identity
from src.db.session_repository import create_session, get_session
from src.models.schemas import (
    ChatRequest,
    ChatResponse,
    DemoCapabilityItem,
    DemoCapabilityListResponse,
    DemoDetailItem,
    DemoPaymentDecisionRequest,
    DemoPlanTask,
    DemoProjectListResponse,
    DemoSessionListResponse,
    DemoTaskResult,
    DemoWorkflowContinueRequest,
    DemoWorkflowEvent,
    DemoWorkflowListItem,
    DemoWorkflowListResponse,
    DemoWorkflowRequest,
    DemoWorkflowResponse,
)
from src.monitoring.usage_tracker import LlmUsageLogger, reset_usage_context, usage_context
from src.orchestration.compensation import release_on_failure
from src.orchestration.demo_service import (
    ResumeError,
    persist_pending_approval,
    read_demo_workflow,
    reject_payment,
    resume_payment_after_approval,
    run_demo_workflow,
)
from src.orchestration.payment_approval import quote_from_results
from src.orchestration.repair import RepairHint, RepairManager, repair_missing_fields
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.sweeper import sweep_zombie_workflows
from src.services.llm import get_llm, structured_output_method

router = APIRouter()
logger = logging.getLogger(__name__)

_DEMO_JOBS: dict[str, dict[str, Any]] = {}
_DEMO_TASKS: set[asyncio.Task[None]] = set()
_DEMO_WORKFLOW_TASKS: dict[str, asyncio.Task[None]] = {}

_DATE_FIELDS = frozenset({"viewing_date", "booking_date", "preferred_date", "move_date"})
_TIME_FIELDS = frozenset({"viewing_time", "preferred_time", "move_time"})
_BOOLEAN_FIELDS = frozenset({"consent", "needs_elevator", "needs_loading_support"})
_SUPPORTED_PROJECTS_MESSAGE = (
    "Dự án bạn chọn chưa nằm trong danh sách được hỗ trợ. Các dự án đang có: "
    + ", ".join(project["project_name"] for project in PROJECTS)
    + "."
)

_FOLLOW_UP_VALIDATION_MESSAGES = {
    # Kèm luôn danh sách: đây là hội thoại, không có bảng nào để người dùng
    # nhìn. Bảo "chọn trong danh sách được hỗ trợ" mà không đưa danh sách thì
    # họ chỉ còn cách đoán — và ảnh chụp lúc sự cố cho thấy họ đoán bốn lần.
    "project_id": _SUPPORTED_PROJECTS_MESSAGE,
    "project_name": _SUPPORTED_PROJECTS_MESSAGE,
    "viewing_date": "Ngày tham quan chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.",
    "booking_date": "Ngày đặt chỗ chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.",
    "preferred_date": "Ngày bảo trì chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.",
    "move_date": "Ngày chuyển nhà chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.",
    "viewing_time": "Giờ xem phải theo định dạng HH:MM và trong khoảng 08:00–17:30.",
    "preferred_time": "Giờ bảo trì phải theo định dạng HH:MM và trong khoảng 08:00–18:00.",
    "move_time": "Giờ chuyển nhà phải theo định dạng HH:MM và trong khoảng 07:00–20:00.",
    "preferred_contact_time": "Giờ liên hệ phải theo định dạng HH:MM và trong khoảng 08:00–18:00.",
    "parking_zone": "Hãy chọn Khu A hoặc Khu B.",
    "plate_number": "Vui lòng nhập biển số xe, ví dụ 59A-12345.",
    "vehicle_type": "Hãy cho biết phương tiện là ô tô hoặc xe máy.",
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
    with_minutes = re.search(
        r"(?<!\d)([01]?\d|2[0-3])\s*[:;hH]\s*([0-5]\d)(?!\d)",
        text,
    )
    if with_minutes:
        return f"{int(with_minutes.group(1)):02d}:{with_minutes.group(2)}"

    # Trong tiếng Việt, "12h" và "12 giờ" có nghĩa chính xác là 12:00.
    # Negative lookahead ngăn 12h99 bị cắt thành 12h rồi chấp nhận nhầm.
    hour_only = re.search(
        r"(?<!\d)([01]?\d|2[0-3])\s*(?:h|giờ)(?!\s*\d)",
        text,
        re.IGNORECASE,
    )
    if hour_only:
        return f"{int(hour_only.group(1)):02d}:00"
    return None


def _extract_parking_zone(text: str) -> str | None:
    match = re.search(r"\b(?:zone|khu)[ _-]*([ab])\b", text, re.IGNORECASE)
    return f"ZONE_{match.group(1).upper()}" if match else None


def _extract_plate_number(text: str) -> str | None:
    # Contract chỉ yêu cầu chuỗi biển số; demo chấp nhận 3–6 chữ số phía sau
    # để không tự áp một chuẩn đăng kiểm cụ thể lên dữ liệu mock.
    match = re.search(r"\b(\d{2}[a-z]{1,2})[ .-]?(\d{3,6})\b", text, re.IGNORECASE)
    return f"{match.group(1).upper()}-{match.group(2)}" if match else None


def _extract_vehicle_type(text: str) -> str | None:
    """Chuẩn hoá cách gọi phương tiện phổ biến, không suy diễn từ câu mơ hồ."""
    lowered = text.casefold()
    motorcycle = r"\b(?:xe\s*máy|xemay|mô\s*tô|moto|motorcycle)\b"
    car = r"\b(?:xe\s*hơi|xe\s*ô\s*tô|ô\s*tô|ôto|oto|car)\b"
    if re.search(motorcycle, lowered):
        return "motorcycle"
    if re.search(car, lowered):
        return "car"
    return None


# Xa nhất người dùng được đặt trước. Không có trần thì "2199-12-31" là một ngày
# hợp lệ: nó không nằm trong quá khứ, nên mọi lớp kiểm đều cho qua. Chỗ đỗ xe
# năm 2199 vẫn được giữ thật trong database và chiếm capacity thật.
#
# Hai năm là con số của demo, cố ý rộng rãi. Điều quan trọng không phải trị số
# mà là CÓ một trần: một ngày vô lý phải bị từ chối lúc nhập, không phải lúc ai
# đó đọc báo cáo.
MAX_SCHEDULE_HORIZON_DAYS = 1825


def _is_allowed_schedule_date(value: str) -> bool:
    parsed = date.fromisoformat(value)
    today = date.today()
    return today <= parsed <= today + timedelta(days=MAX_SCHEDULE_HORIZON_DAYS)


# Khung giờ dựng TỪ `TaskPlanValidator.TIME_INPUTS`, không chép tay.
#
# Bản chép tay trước đây thiếu `preferred_contact_time`: người dùng trả lời
# "19:00" thì bộ lọc này cho qua, Validator mới chặn — và người dùng nhận
# VALIDATION_ERROR chung chung thay vì một câu bảo chọn lại giờ. Hai bảng nói
# về cùng một luật thì sớm muộn cũng lệch nhau; dựng từ một nguồn thì không.
_TIME_WINDOWS: dict[str, tuple[time, time]] = {
    field: (opens, closes) for field, opens, closes in TaskPlanValidator.TIME_INPUTS.values()
}


def _is_allowed_schedule_time(field: str, value: str) -> bool:
    parsed = time.fromisoformat(value)
    window = _TIME_WINDOWS.get(field)
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
        elif field in {"project_id", "project_name"}:
            # Câu trả lời thường gộp tên dự án + ngày + giờ. resolve chỉ nhận
            # đúng toàn bộ tên; find tìm tên đóng nằm bên trong câu tự nhiên.
            #
            # Với `project_name` (tên CÔNG KHAI), vẫn phải khớp danh mục — nhưng
            # giữ lại TÊN, không phải mã: việc đổi sang `project_id` do adapter
            # ở biên làm, và làm ở đúng một chỗ.
            project_id = find_project_id(text) or resolve_project_id(text)
            if field == "project_id":
                value = project_id
            else:
                value = project_name(project_id) if project_id else None
        elif field == "parking_zone":
            value = _extract_parking_zone(text)
        elif field == "plate_number":
            value = _extract_plate_number(text)
        elif field == "vehicle_type":
            value = _extract_vehicle_type(text)
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


# ---------------------------------------------------------------------------
# Adapter `project_name` (public) ↔ `project_id` (nội bộ)
# ---------------------------------------------------------------------------
#
# Planner và TaskPlan làm việc với `project_id` (PRJ-xxx) — mã ổn định, không
# đổi khi dự án đổi tên. Nhưng người dùng không biết mã đó và không có cách nào
# tra được, nên hỏi họ "project_id" là hỏi một thứ họ chắc chắn không trả lời
# được: form sẽ hiện ô trống, họ gõ tên dự án, và `/continue` trả 422.
#
# Vì vậy contract CÔNG KHAI dùng `project_name`, còn phần dịch nằm ở đúng biên
# API. Bên trong biên đó không có gì thay đổi.
_PUBLIC_FIELD_ALIASES: dict[str, str] = {"project_id": "project_name"}

# Message KHÔNG liệt kê danh mục dự án và KHÔNG nhắc mã nội bộ. UI đã có
# `/projects` để dựng danh sách chọn; lặp lại ở đây là hai nguồn cho một thứ.
_UNSUPPORTED_PROJECT_MESSAGE = _SUPPORTED_PROJECTS_MESSAGE


def _to_public_missing_fields(fields: list[str]) -> list[str]:
    """Đổi tên field nội bộ sang tên người dùng hiểu được, giữ nguyên thứ tự."""
    return [_PUBLIC_FIELD_ALIASES.get(name, name) for name in fields]


def _resolve_public_answers(answers: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Dịch câu trả lời công khai sang khoá nội bộ.

    Trả `(context_update, field_lỗi)`. `field_lỗi` khác None nghĩa là giá trị
    không nằm trong danh mục dự án — caller phải trả 422 và KHÔNG được consume
    clarification, để người dùng còn sửa lại.
    """
    resolved = dict(answers)
    name = resolved.pop("project_name", None)
    if name is None:
        return resolved, None

    project_id = resolve_project_id(str(name))
    if project_id is None:
        return resolved, "project_name"

    # Chỉ giữ mã nội bộ. `project_name` không cần đi tiếp: Planner đọc
    # `project_id`, và giữ cả hai là để hai nguồn cùng mô tả một dự án.
    resolved["project_id"] = project_id
    return resolved, None


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


def _asks_for_project_catalog(message: str, missing_fields: list[str]) -> bool:
    """Câu hỏi tra cứu dự án trong lúc workflow đang chờ chọn dự án.

    Đây KHÔNG phải câu trả lời cho ``project_name`` nên không được đưa qua
    parser field rồi trả 422. Nó cũng không được consume clarification: người
    dùng cần xem danh sách trước, sau đó mới chọn và tiếp tục chính workflow
    đang dở.
    """
    if "project_name" not in missing_fields:
        return False
    normalized = unicodedata.normalize("NFD", message.casefold())
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    normalized = " ".join(normalized.replace("đ", "d").strip(" .,!?").split())
    markers = (
        "co nhung du an nao",
        "co du an nao",
        "danh sach du an",
        "ho tro du an nao",
        "nhung du an nao",
        "du an nao duoc ho tro",
    )
    return any(marker in normalized for marker in markers)


def _project_catalog_answer() -> str:
    """Câu trả lời từ catalogue canonical, không phải danh sách chép tay."""
    names = [str(project["project_name"]) for project in PROJECTS]
    return "Hiện mình hỗ trợ các dự án: " + "; ".join(names) + ". Bạn chọn một dự án để mình tiếp tục nhé."


# Demo-only server-side identity context. Browser chỉ chọn persona, không được
# tự gửi resident_id/apartment_id. Production phải thay bằng auth/session và
# resident directory thật.
# Câu duy nhất người dùng thấy khi policy guard chặn vì chưa liên kết căn hộ.
# Không nêu tên tool, mã policy, raw status hay thuật ngữ schema.
RESIDENT_ACCESS_REQUIRED_MESSAGE = (
    "Dịch vụ này chỉ dành cho tài khoản đã liên kết căn hộ. "
    "Bạn cần liên kết hoặc xác minh hồ sơ cư dân trước khi tiếp tục."
)

RESIDENT_DIRECTORY_UNAVAILABLE_MESSAGE = "Hiện chưa thể kiểm tra hồ sơ cư dân. Bạn vui lòng thử lại sau ít phút."

# Đăng ký/liên kết hồ sơ cư dân nằm NGOÀI workflow của Agent. Câu này hướng
# người dùng đi đúng đường, không gợi ý rằng trợ lý làm hộ được.
RESIDENT_LINKING_OUTSIDE_AGENT_MESSAGE = (
    "Việc đăng ký và xác minh hồ sơ cư dân được thực hiện ngoài trợ lý. "
    "Bạn vui lòng hoàn tất liên kết căn hộ trong phần tài khoản, "
    "sau đó quay lại để dùng các dịch vụ dành cho cư dân."
)

_DEMO_ACCOUNT_CONTEXTS: dict[str, dict[str, Any]] = {
    "prospect": {
        "account_id": "DEMO-PROSPECT",
        "resident_verification_status": "NOT_LINKED",
        "account_contact_status": "VERIFIED",
    },
    # KHUNG, không phải dữ liệu. Danh tính thật (`resident_id`,
    # `apartment_code`, `residential_area`) được điền từ `user_resident_links`
    # cộng bảng `residents`. Trước đây RES-001/A1201 nằm cứng ở đây, nên mọi
    # tài khoản được coi là resident đều thao tác trên CÙNG một căn hộ — dữ
    # liệu của người này hiện ra dưới phiên của người kia.
    "resident": {
        "account_id": "DEMO-RESIDENT",
        "resident_verification_status": "VERIFIED",
        "account_contact_status": "VERIFIED",
    },
}


def _context_for_session(session: dict[str, Any] | None) -> dict[str, Any]:
    """Derive trusted context từ row session đã ghim, KHÔNG từ body request.

    `account_state` + `resident_id` đến từ bảng `sessions` (ghi ở lần `/start`
    đầu). Không có session hoặc không phải resident → fail-closed về prospect
    (ít đặc quyền nhất). Đây là lớp "LLM đề xuất, code quyết định" cho quyền:
    browser chỉ CHỌN persona lúc tạo session, không thay đổi được sau đó.
    """
    if session is None or session.get("account_state") != "resident":
        return dict(_DEMO_ACCOUNT_CONTEXTS["prospect"])
    context = dict(_DEMO_ACCOUNT_CONTEXTS["resident"])
    resident_id = session.get("resident_id")
    if resident_id:
        context["resident_id"] = resident_id
    return context


async def _load_session(session_id: str | None, *, user_id: str | None = None) -> dict[str, Any] | None:
    """Đọc row session CỦA `user_id`; trả None nếu không có, không thuộc, hoặc DB lỗi.

    Mở pool qua composition root (pattern `_read_repair_hints`), đóng trong
    finally. DB lỗi KHÔNG được raise vào route — fail-closed về prospect.

    Phạm vi theo user được ép NGAY TRONG SQL. Kiểm chủ sở hữu workflow cha rồi
    coi session là hệ quả sẽ dựa vào giả định "dữ liệu luôn nhất quán" — và
    một guard quyền không được đứng trên giả định đó. Session của người khác
    trả None, tức là fail-closed về prospect, giống hệt session không tồn tại.
    """
    if not session_id:
        return None

    # `acquire_repository()` phải nằm TRONG try. Chạm database có thể lỗi:
    # DB sập hoặc DSN sai sẽ raise ngay tại đây, và nếu nó nằm ngoài try thì
    # exception thoát thẳng ra route — trái đúng cam kết "DB lỗi trả None và
    # fail-closed về prospect" ghi trong docstring.
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        return await get_session(pool, session_id, user_id=user_id)
    except Exception:  # noqa: BLE001 - DB tạm lỗi không được lộ ra route
        # Log generic: session_id là định danh phiên, không cần đưa vào log để
        # chẩn đoán một sự cố hạ tầng.
        logger.warning("load session failed; falling back to prospect")
        return None
    finally:
        # Chỉ đóng pool nếu nó đã được tạo thành công.
        if pool is not None:
            await pool.close()


async def _create_shell_and_session(
    workflow_id: str,
    *,
    owner_user_id: str,
    session_id: str,
    goal: str,
    account_state: str,
    resident_id: str | None,
) -> bool:
    """Ghim shell + session TRONG MỘT transaction. Trả True nếu đã lưu được.

    Hai lần ghi rời nhau để lại khe hở: shell vào được, session lỗi, route trả
    503 — và một workflow PENDING không ai đọc được nằm lại trong database.

    Mọi exception đều quy về False. Route không được phân biệt lý do hỏng: chi
    tiết của driver có thể chứa DSN, và người dùng chỉ cần biết "chưa nhận được
    yêu cầu, thử lại".
    """
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        await repository.create_shell_and_session(
            workflow_id=workflow_id,
            owner_user_id=owner_user_id,
            session_id=session_id,
            goal=goal,
            account_state=account_state,
            resident_id=resident_id,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("workflow shell/session persist failed (%s)", type(exc).__name__)
        return False
    finally:
        if pool is not None:
            await pool.close()


async def _persist_session(
    session_id: str | None,
    account_state: str,
    *,
    user_id: str | None = None,
    resident_id: str | None = None,
) -> bool:
    """Ghim session server-side. Trả True nếu ĐÃ lưu được.

    Caller PHẢI dùng giá trị trả về. Bản trước nuốt lỗi rồi trả None, nên
    `/start` vẫn trả 202 kể cả khi phiên không được ghim — và mọi lần đọc sau
    đó fail-closed về prospect mà không ai biết vì sao quyền bị mất.

    Chạy bên trong `_run_demo_job` — nơi đã có async context và được test
    monkeypatch. DB lỗi không được làm hỏng workflow: nếu không ghim được thì
    session chỉ đơn giản là không tồn tại và `_context_for_session` fail-closed
    về prospect ở mọi lần đọc sau.
    """
    if not session_id:
        return False
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001
        try:
            await create_session(
                pool,
                session_id=session_id,
                account_state=account_state,
                # `resident_id` đến từ liên kết đã VERIFIED của chính user này.
                # Trước đây chỗ này ghi cứng "RES-001", nên mọi phiên resident
                # đều trỏ về cùng một căn hộ — dữ liệu của người này hiện ra
                # dưới phiên của người kia.
                resident_id=resident_id,
                user_id=user_id,
            )
        finally:
            await pool.close()
        return True
    except Exception as exc:  # noqa: BLE001 - ghim session không được làm hỏng workflow
        # Chỉ TÊN LOẠI lỗi: message của driver có thể chứa DSN.
        logger.warning("session persist failed (%s)", type(exc).__name__)
        return False


# Message CÔNG KHAI cho từng stage.
#
# Stage code vẫn giữ nguyên cho log nội bộ, nhưng message thì không được chứa
# thuật ngữ kỹ thuật. Bản trước đưa nguyên văn "LLM đang phân tích",
# "Agent đã tạo TaskPlan", "Validator đang kiểm tra dependency, allowlist",
# "Executor đang gọi các dịch vụ" thẳng vào `events[].message` — tức là đúng
# những từ mà người dùng cuối không có cách nào hiểu, và cũng là chi tiết nội
# bộ không nên lộ ra ngoài.
_STAGE_MESSAGES = {
    "PLANNING": "Đang chuẩn bị kế hoạch thực hiện.",
    "PLANNED": "Đã xác định các bước cần thực hiện.",
    "VALIDATING": "Đang kiểm tra thông tin và điều kiện thực hiện.",
    "VALIDATED": "Kế hoạch đã sẵn sàng.",
    "RESIDENT_CHECKING": "Đang kiểm tra liên kết cư dân với ban quản lý.",
    "RESIDENT_VERIFIED": "Đã xác nhận tài khoản cư dân.",
    "WAITING_APPROVAL": "Đang chờ bạn xác nhận thanh toán.",
    "EXECUTING": "Đang thực hiện yêu cầu.",
    "TASK_RUNNING": "Đang thực hiện một bước trong yêu cầu.",
    "TASK_SUCCESS": "Đã hoàn thành một bước trong yêu cầu.",
    "TASK_FAILED": "Một bước không thể hoàn thành.",
    "NEEDS_INFORMATION": "Cần bạn bổ sung thêm thông tin.",
    "VALIDATION_FAILED": "Thông tin chưa đủ điều kiện để thực hiện.",
    "EXECUTION_FAILED": "Yêu cầu đã dừng lại giữa chừng.",
    "FINISHED": "Yêu cầu đã hoàn tất.",
}


def _event_message(stage: str, payload: dict[str, Any], plan: TaskPlan | None) -> str:
    task_id = payload.get("task_id")
    task = next((item for item in plan.tasks if item.task_id == task_id), None) if plan else None
    title = _TOOL_PRESENTATION.get(task.tool, ("tác vụ", ""))[0] if task else "tác vụ"
    if stage == "TASK_RUNNING":
        return f"Đang {title.lower()}."
    if stage == "TASK_SUCCESS":
        return f"Đã {title.lower()}."
    if stage == "TASK_FAILED":
        return f"Không thể {title.lower()}. Yêu cầu dừng lại an toàn."
    return _STAGE_MESSAGES.get(stage, "Đang xử lý yêu cầu.")


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
        ]
    elif task.tool == "book_parking":
        zone = _text(data.get("parking_zone")) or _text(inputs.get("parking_zone"))
        zone_label = {"ZONE_A": "Khu A", "ZONE_B": "Khu B"}.get(zone or "", zone)
        booking_date = _text(data.get("booking_date")) or _text(inputs.get("booking_date"))
        booking_fee = _money(data.get("amount"), data.get("currency"))
        place = " · ".join(value for value in (zone_label, booking_date) if value)
        message = f"Đã đặt chỗ đỗ xe{f' ({place})' if place else ''}."
        if booking_fee is not None:
            # Đây là báo phí authoritative do Parking Provider trả về, không
            # phải số tiền từ goal. Chỉ hiển thị; Payment vẫn cần approval.
            message += f" Phí đặt chỗ: {booking_fee}."
        candidates = [
            _detail("Mã đặt chỗ", data.get("booking_id")),
            _detail("Khu vực", zone_label),
            _detail("Ngày đặt", booking_date),
            _detail("Phí đặt chỗ", booking_fee),
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
        ("booking_date", "chọn một ngày đặt chỗ từ hôm nay trở đi"),
        ("parking_zone", "chọn Khu A hoặc Khu B"),
        ("plate_number", "bổ sung biển số xe"),
        ("vehicle_type", "chọn loại xe ô tô hoặc xe máy"),
        ("viewing_date", "chọn một ngày tham quan từ hôm nay trở đi"),
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
        elif status == "CANCELLED":
            views.append(
                DemoTaskResult(
                    task_id=task.task_id,
                    tool=task.tool,
                    status="CANCELLED",
                    title=title,
                    message="Bước này đã được huỷ trước khi hoàn tất.",
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
    *,
    session_id: str | None = None,
    parent_workflow_id: str | None = None,
) -> None:
    job = _DEMO_JOBS[workflow_id]

    # Workflow shell TRƯỚC khi Planner chạy. `workflow_clarifications` có khoá
    # ngoại tới `workflows`, mà Executor — nơi duy nhất gọi `create_workflow()`
    # — chỉ chạy trên nhánh READY (`route_after_plan` trả END khi
    # planner_status != READY). Không có shell thì mọi lần NEEDS_INFORMATION
    # đều INSERT clarification vào một workflow_id chưa tồn tại, vi phạm khoá
    # ngoại, exception bị nuốt, và ngữ cảnh không bao giờ được lưu.
    #
    # Đây là lane DỊCH VỤ: small-talk trả CHAT và return trước khi tới đây, nên
    # không tạo workflow nghiệp vụ cho lời chào.
    job["shell_persisted"] = await _ensure_workflow_shell(
        workflow_id,
        goal=goal,
        session_id=session_id or job.get("session_id"),
        parent_workflow_id=parent_workflow_id or job.get("parent_workflow_id"),
        owner_user_id=job.get("owner_user_id"),
    )

    repair_manager = RepairManager()

    # Ghim session server-side: persona của workflow này vào bảng `sessions`.
    # `account_state` ở đây là giá trị đã quyết định ở /start (và /continue giờ
    # truyền giá trị đọc từ session cũ). DB lỗi không được làm hỏng workflow —
    # không ghim được thì các lần đọc sau fail-closed về prospect.
    await _persist_session(
        session_id,
        account_state,
        user_id=job.get("owner_user_id"),
        resident_id=job.get("existing_context", {}).get("resident_id"),
    )

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
            user_answers=job.get("user_answers") or {},
            approve_mock_payment=approve_mock_payment,
            resident_url=service_urls["resident"],
            transport_url=service_urls["transport"],
            payment_url=service_urls["payment"],
            property_url=service_urls["property"],
            resident_services_url=service_urls["resident_services"],
            contact_profile=job.get("contact_profile"),
            session_id=session_id,
            parent_workflow_id=parent_workflow_id,
            on_failure=repair_manager,
        )
        # Chờ duyệt thanh toán: ghi ngữ cảnh xuống PostgreSQL TRƯỚC khi trả
        # response. Từ đây trở đi resume không còn phụ thuộc `_DEMO_JOBS`.
        if state.get("policy_error") == "PAYMENT_APPROVAL_REQUIRED":
            await persist_pending_approval(
                workflow_id,
                state.get("task_results") or {},
                state.get("plan") or state.get("draft_plan") or job.get("plan"),
            )

        # Repair Loop: gom hint từ RepairManager, merge vào state trước khi
        # render, rồi persist xuống DB. Executor.on_failure là sync callback nên
        # persist phải xảy ra ở đây (nơi có async context).
        repair_hints = repair_manager.hints_for(workflow_id)
        if repair_hints:
            state["repair_hints"] = repair_hints
            await _persist_repair_hints(workflow_id, repair_hints)

        response = _demo_response(state, approve_mock_payment)
        terminal_stage = "FINISHED"
        if response.status == "NEEDS_INFORMATION":
            terminal_stage = "NEEDS_INFORMATION"
        elif response.status == "WAITING_APPROVAL":
            terminal_stage = "WAITING_APPROVAL"
        elif response.status == "VALIDATION_ERROR":
            terminal_stage = "VALIDATION_FAILED"
        elif response.status in {"EXECUTION_ERROR", "PLANNING_ERROR"}:
            terminal_stage = "EXECUTION_FAILED"
        _append_job_event(job, terminal_stage)
        job["message"] = response.summary or response.question or job["message"]
        # `_demo_response()` dựng view model từ AgentState nên không biết
        # workflow_id. Gắn lại ngay khi cache, đừng để nhánh đọc phải tự đoán.
        job["response"] = response.model_copy(update={"workflow_id": workflow_id})

        # Chờ bổ sung thông tin: ghim ngữ cảnh xuống PostgreSQL NGAY. Từ đây
        # `/continue` không còn phụ thuộc `_DEMO_JOBS` nữa.
        if response.status == "NEEDS_INFORMATION":
            # Ghi trước, rồi phản ánh kết quả THẬT vào response — không báo
            # thành công dựa trên việc shell tồn tại.
            job["clarification_persisted"] = await _persist_clarification(
                workflow_id,
                session_id=job.get("session_id"),
                parent_workflow_id=job.get("parent_workflow_id"),
                goal=job.get("goal") or "",
                # Ghim theo tên CÔNG KHAI để sau restart `/continue` vẫn nhận
                # đúng contract mà client đã thấy trước đó.
                missing_fields=_to_public_missing_fields(list(response.missing_fields or [])),
                question=response.question,
                existing_context=job.get("existing_context") or {},
            )
            cached = job["response"]
            job["response"] = cached.model_copy(
                update={
                    "resumable": bool(job["clarification_persisted"]),
                    # Nói thẳng khi không lưu được: người dùng cần biết đừng
                    # rời trang. Câu chữ generic, không lộ lý do kỹ thuật.
                    "message": (
                        cached.message
                        if job["clarification_persisted"]
                        else "Mình chưa lưu được yêu cầu này. Bạn giữ nguyên trang và trả lời tiếp giúp mình nhé."
                    ),
                }
            )

        # ĐIỂM DỪNG: workflow đã kết thúc, hoặc đang chờ người dùng làm gì đó.
        # Sinh câu trả lời tự nhiên ở đây — nhưng KHÔNG chặn.
        #
        # Gọi LLM ngay trong luồng này sẽ cộng thêm cả một lượt round-trip vào
        # thời gian người dùng phải chờ, cho một việc đã làm xong: thẻ workflow
        # ngồi ở "Đang thực hiện" thêm vài giây sau khi chỗ đỗ xe đã được giữ.
        # Công bố kết quả trước, nói sau.
        _keep_demo_task(asyncio.create_task(_attach_answer(job, workflow_id, goal=job.get("goal") or goal)))

        # Release-on-failure (Phase B): workflow FAILED do máy, không repairable
        # (không có repair hint) → dọn side-effect giữ chỗ/thanh toán. FAILED có
        # repair hint là repairable — user sẽ /continue sửa input, release sẽ phá
        # thứ repair định tiếp tục nên KHÔNG chạy. Guard `not repair_hints`.
        if response.status in {"EXECUTION_ERROR", "PLANNING_ERROR"} and not repair_hints:
            await release_on_failure(workflow_id)
    except Exception as exc:  # noqa: BLE001 - không để raw exception vào job public
        # Phân loại TRƯỚC khi làm gì khác: mã lỗi quyết định cả câu nói với
        # người dùng lẫn việc có mời họ thử lại hay không.
        failure = classify_failure(exc)
        request_id = uuid4().hex[:12]
        # Log giữ đủ để đối chiếu — mã lỗi, loại exception, request_id — nhưng
        # KHÔNG giữ message của exception: message hay kèm URL, tên biến môi
        # trường, đôi khi cả một phần credential.
        logger.warning(
            "demo background workflow failed code=%s exc=%s request_id=%s",
            failure.code,
            type(exc).__name__,
            request_id,
        )
        _append_job_event(job, "EXECUTION_FAILED")
        job["message"] = failure.message

        # Ghim trạng thái kết thúc xuống PostgreSQL.
        #
        # Không có bước này, lỗi chỉ sống trong `_DEMO_JOBS` — RAM của một tiến
        # trình. `workflows.status` nằm nguyên ở PENDING, nên sau restart GET
        # đọc lên và map thành "đang chạy": giao diện poll mãi một workflow đã
        # chết, và bảng workflow đầy zombie.
        await _mark_workflow_failed_safely(workflow_id, failure.code)

        job["response"] = DemoWorkflowResponse(
            workflow_id=workflow_id,
            status="EXECUTION_ERROR",
            stage="EXECUTION_FAILED",
            message=failure.message,
            summary=failure.message,
            error_code=failure.code,
            retryable=failure.retryable,
            request_id=request_id,
        )
        # Job crash cũng là FAILED giữ booking — release luôn, không có repair
        # hint để cân nhắc.
        await release_on_failure(workflow_id)


def _keep_demo_task(task: asyncio.Task[None], *, workflow_id: str | None = None) -> None:
    _DEMO_TASKS.add(task)
    if workflow_id is not None:
        _DEMO_WORKFLOW_TASKS[workflow_id] = task

    def _forget(done: asyncio.Task[None]) -> None:
        _DEMO_TASKS.discard(done)
        if workflow_id is not None and _DEMO_WORKFLOW_TASKS.get(workflow_id) is done:
            _DEMO_WORKFLOW_TASKS.pop(workflow_id, None)

    task.add_done_callback(_forget)


async def _persist_clarification(
    workflow_id: str,
    *,
    session_id: str | None,
    parent_workflow_id: str | None,
    goal: str,
    missing_fields: list[str],
    question: str | None,
    existing_context: dict[str, Any],
) -> bool:
    """Ghim ngữ cảnh chờ bổ sung thông tin. Trả True nếu ĐÃ lưu được.

    Caller PHẢI dùng giá trị trả về: chỉ khi True mới được nói workflow này
    tiếp tục được sau restart.

    Không có nó, `/continue` chỉ chạy được khi `_DEMO_JOBS` còn trong RAM — một
    lần restart giữa lúc NEEDS_INFORMATION là mất hẳn hội thoại.
    """
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        await repository.save_clarification(
            workflow_id,
            session_id=session_id,
            parent_workflow_id=parent_workflow_id,
            goal=goal,
            missing_fields=_to_public_missing_fields(list(missing_fields or [])),
            question=question,
            existing_context=dict(existing_context or {}),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - ghim ngữ cảnh không được làm hỏng workflow
        logger.warning(
            "clarification persist failed (%s); this workflow is not resumable",
            type(exc).__name__,
        )
        return False
    finally:
        if pool is not None:
            await pool.close()


async def _load_clarification(workflow_id: str) -> dict[str, Any] | None:
    """Đọc lại ngữ cảnh chờ bổ sung từ PostgreSQL. DB lỗi → None."""
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        return await repository.get_clarification(workflow_id)
    except Exception:  # noqa: BLE001 - DB tạm lỗi không được lộ ra route
        return None
    finally:
        if pool is not None:
            await pool.close()


async def _load_clarification_safely(workflow_id: str) -> dict[str, Any] | None:
    """Đọc clarification còn mở cho đường GET — error boundary RIÊNG.

    Lỗi đọc bảng phụ không được biến một workflow có thật thành 404 hay 500;
    tệ nhất là mất phần "đang chờ bạn", phần dữ liệu chính vẫn hiển thị.
    """
    try:
        return await _load_clarification(workflow_id)
    except Exception:  # noqa: BLE001 - bảng phụ hỏng không được kéo theo record
        return None


async def _consume_and_create_child(
    parent_workflow_id: str,
    *,
    child_workflow_id: str,
    owner_user_id: str | None,
    session_id: str | None,
    goal: str,
) -> dict[str, Any] | None:
    """Claim clarification + tạo child shell TRONG MỘT transaction.

    Tách hai bước để lại khe hở thật: consume xong, tiến trình chết, child chưa
    kịp tạo — câu trả lời của người dùng biến mất cùng lượt hỏi duy nhất, và họ
    không trả lời lại được vì clarification đã bị đánh dấu đã xử lý.

    Trả None khi không claim được; khi đó KHÔNG có child nào tồn tại.
    """
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        return await repository.consume_clarification_and_create_child(
            parent_workflow_id,
            child_workflow_id=child_workflow_id,
            owner_user_id=owner_user_id,
            session_id=session_id,
            goal=goal,
        )
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("consume clarification + create child failed (%s)", type(exc).__name__)
        return None
    finally:
        if pool is not None:
            await pool.close()


# Câu mặc định theo TRẠNG THÁI, dùng khi chưa có gì cụ thể hơn để nói.
#
# Không có bảng này thì mọi trạng thái dùng chung một câu, và câu đó từng là
# "Yêu cầu đã hoàn tất." — nói với người đang chờ bổ sung thông tin rằng việc
# của họ đã xong.
_DEFAULT_BASELINE: dict[str, str] = {
    "PENDING": "Mình đang chuẩn bị kế hoạch cho yêu cầu của bạn.",
    "RUNNING": "Mình đang thực hiện yêu cầu của bạn.",
    "SUCCESS": "Yêu cầu của bạn đã hoàn tất.",
    "FAILED": "Yêu cầu đã dừng lại giữa chừng.",
    "CANCELLED": "Mình đã huỷ yêu cầu. Các bước đã hoàn thành trước đó vẫn được giữ lại.",
    "NEEDS_INFORMATION": "Mình cần bạn bổ sung thêm một vài thông tin.",
    "WAITING_APPROVAL": "Đang chờ bạn xác nhận khoản thanh toán.",
    "PLANNING_ERROR": "Mình chưa hiểu được yêu cầu này.",
    "VALIDATION_ERROR": "Thông tin chưa đủ điều kiện để thực hiện.",
    "PAYMENT_APPROVAL_REQUIRED": "Đang chờ bạn xác nhận khoản thanh toán.",
    "EXECUTION_ERROR": "Yêu cầu đã dừng lại giữa chừng.",
    "CHAT": "Mình đã trả lời bạn ở trên.",
}


def _reply_view(response: DemoWorkflowResponse, *, goal: str, capabilities: list[str]) -> ReplyView:
    """View model cho Response Agent, dựng TỪ response công khai.

    Đây là toàn bộ trust boundary của lớp đó, và nó gọn được vì response công
    khai đã đi qua allowlist rồi: `_task_presentation()` chỉ đưa ra field nghiệp
    vụ đã chọn, `error_code` đã được normalize, `payment_quote` đọc từ booking.

    Nói cách khác: Response Agent không thấy gì mà người dùng chưa thấy. Không
    có raw connector response, không có `input_data` đầy đủ, không có exception,
    không có token — không phải vì đã lọc chúng ra, mà vì chúng chưa từng có mặt
    trong thứ được truyền vào.
    """
    return ReplyView(
        goal=goal,
        status=response.status,
        # `question` phải đứng trước `message`: ở NEEDS_INFORMATION, câu hỏi
        # CHÍNH LÀ nội dung, còn `message` lúc này thường chưa được gán.
        #
        # Mặc định cuối tra theo TRẠNG THÁI, không phải hằng số. Bản trước rơi
        # về `_STAGE_MESSAGES["FINISHED"]` — nghĩa là một workflow đang chờ
        # người dùng bổ sung thông tin nhận câu "Yêu cầu đã hoàn tất.". Vừa sai,
        # vừa đúng loại sai nguy hiểm nhất: khẳng định đã xong khi chưa xong.
        baseline_message=(
            response.summary or response.question or response.message or _DEFAULT_BASELINE[response.status]
        ),
        steps=[{"title": task.title, "status": task.status, "message": task.message} for task in response.tasks],
        missing_fields=[MISSING_FIELD_LABELS.get(f, f) for f in response.missing_fields],
        payment_quote=(
            {"amount": response.payment_quote.get("amount"), "currency": response.payment_quote.get("currency")}
            if response.payment_quote
            else None
        ),
        # Tiền chỉ coi là đã đi khi CÓ bước pay_fee và bước đó SUCCESS.
        # Suy từ `payment_quote` là sai: báo giá có mặt ngay từ lúc giữ chỗ,
        # trước khi người dùng bấm duyệt — đó chính là cách một khoản chưa thu
        # được thuật lại như đã thu.
        payment_settled=any(task.tool == "pay_fee" and task.status == "SUCCESS" for task in response.tasks),
        error_code=response.error_code,
        retryable=response.retryable,
        capabilities=capabilities,
    )


async def _attach_answer(job: dict[str, Any], workflow_id: str, *, goal: str) -> None:
    """Sinh câu trả lời, GHI XUỐNG workflow, rồi gắn vào cache. Chạy ngoài luồng chính.

    Ghi xuống PostgreSQL là điểm quan trọng nhất ở đây: câu trả lời là thuộc
    tính TRÌNH BÀY của workflow, nên nó phải sống cùng workflow. Chỉ để trong
    `_DEMO_JOBS` thì F5 hay restart là mất, còn workflow thì vẫn còn — người
    dùng quay lại thấy thẻ nhưng P-118 im lặng.

    Quyền sinh được chốt trong DATABASE (`claim_assistant_response`), không
    bằng cờ RAM: cờ RAM chỉ chặn trong cùng tiến trình và mất sau restart.

    Không raise: đây là tác vụ nền, và một exception thoát ra chỉ để lại dòng
    "Task exception was never retrieved" trong log.
    """
    try:
        current = job.get("response")
        if not isinstance(current, DemoWorkflowResponse):
            # Không có cache RAM (ví dụ sau restart): dựng lại trạng thái công
            # khai từ database. Câu trả lời không được phụ thuộc vào việc tiến
            # trình này có tình cờ còn giữ job hay không.
            current = await _public_view_from_db(workflow_id)
            if current is None:
                return
        for_status = current.status

        if not await _claim_answer_safely(workflow_id, for_status):
            # Trạng thái này đã có câu trả lời, hoặc một tiến trình khác đang
            # sinh. Đọc lại thứ đã ghi thay vì gọi mô hình thêm lần nữa.
            await _refresh_answer_from_db(job, workflow_id, current)
            return

        capabilities = await _capability_names_safely(job.get("owner_user_id"))
        spoken = await _speak(current, goal=goal, capabilities=capabilities)
        if spoken.answer is None:
            # `_speak` không dựng được client (thiếu key chẳng hạn). Dùng CHÍNH
            # câu deterministic mà Response Agent vốn lấy làm bản dự phòng, để
            # hai đường không nói hai kiểu khác nhau cho cùng một tình huống.
            baseline = _reply_view(current, goal=goal, capabilities=capabilities).baseline_message
            spoken = spoken.model_copy(update={"answer": baseline})
            state = "FALLBACK"
        else:
            baseline = _reply_view(current, goal=goal, capabilities=capabilities).baseline_message
            state = "FALLBACK" if spoken.answer == baseline else "READY"

        await _save_answer_safely(
            workflow_id,
            answer=spoken.answer or "",
            suggestions=list(spoken.suggestions),
            state=state,
            for_status=for_status,
        )
        spoken = spoken.model_copy(update={"response_state": state})
        # Chỉ ghi đè nếu chưa có ai thay response trong lúc chờ mô hình.
        if job.get("response") is current:
            job["response"] = spoken
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.info("không gắn được câu trả lời cho %s (%s)", workflow_id[:8], type(exc).__name__)


async def _with_stored_answer(response: DemoWorkflowResponse, workflow_id: str) -> DemoWorkflowResponse:
    """Bổ sung câu trả lời ĐÃ GHI vào một response dựng lại từ database.

    Đây là đường sống sót qua F5 và qua restart: `_DEMO_JOBS` mất, nhưng
    `workflows.assistant_answer` thì còn.

    Chỉ nhận câu viết cho ĐÚNG trạng thái hiện tại. Một câu viết cho
    WAITING_APPROVAL trở thành sai ngay khi người dùng bấm duyệt — hiển thị lại
    nó nghĩa là nói với họ rằng vẫn đang chờ, trong khi tiền đã thu xong.
    """
    stored = await _load_answer_safely(workflow_id)
    if stored.get("for_status") != response.status:
        return response
    return response.model_copy(
        update={
            "answer": stored.get("answer"),
            "suggestions": list(stored.get("suggestions") or []),
            "response_state": stored.get("state"),
        }
    )


async def _public_view_from_db(workflow_id: str) -> DemoWorkflowResponse | None:
    """Dựng một view công khai TỐI THIỂU từ database, đủ cho lớp trả lời.

    Dùng khi `_DEMO_JOBS` không có gì — sau restart chẳng hạn. Không dựng lại
    toàn bộ (event, plan): Response Agent chỉ cần trạng thái, các bước và câu
    deterministic, và mỗi field thừa là một field nữa phải kiểm xem có rò gì
    không.
    """
    try:
        record = await read_demo_workflow(workflow_id)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.info("không đọc được workflow để sinh câu trả lời (%s)", type(exc).__name__)
        return None
    if record is None:
        return None

    database_status = record["workflow"]["status"]
    status = (
        "SUCCESS"
        if database_status == "SUCCESS"
        else (
            "CANCELLED" if database_status == "CANCELLED" else ("FAILED" if database_status == "FAILED" else "RUNNING")
        )
    )
    plan = _plan_from_job_or_record(None, record)
    tasks = _polling_task_views(plan, record)
    return DemoWorkflowResponse(
        workflow_id=workflow_id,
        status=status,
        message=_DEFAULT_BASELINE.get(status),
        summary=_DEFAULT_BASELINE.get(status) if status in {"SUCCESS", "FAILED", "CANCELLED"} else None,
        tasks=tasks,
        persisted=True,
    )


async def _claim_answer_safely(workflow_id: str, for_status: str) -> bool:
    """True nếu tiến trình này được phép gọi mô hình. DB lỗi → False (không gọi)."""
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        return await repository.claim_assistant_response(workflow_id, for_status=for_status)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.info("không chốt được quyền sinh câu trả lời (%s)", type(exc).__name__)
        return False
    finally:
        if pool is not None:
            await pool.close()


async def _save_answer_safely(workflow_id: str, **fields: Any) -> None:
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        await repository.save_assistant_response(workflow_id, **fields)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.info("không ghi được câu trả lời (%s)", type(exc).__name__)
    finally:
        if pool is not None:
            await pool.close()


async def _load_answer_safely(workflow_id: str) -> dict[str, Any]:
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        return await repository.get_assistant_response(workflow_id)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.info("không đọc được câu trả lời (%s)", type(exc).__name__)
        return {"answer": None, "suggestions": [], "state": None, "for_status": None}
    finally:
        if pool is not None:
            await pool.close()


async def _refresh_answer_from_db(job: dict[str, Any], workflow_id: str, current: DemoWorkflowResponse) -> None:
    """Lấy câu đã ghi cho ĐÚNG trạng thái hiện tại vào cache."""
    stored = await _load_answer_safely(workflow_id)
    if stored.get("for_status") != current.status or not stored.get("answer"):
        return
    if job.get("response") is current:
        job["response"] = current.model_copy(
            update={
                "answer": stored["answer"],
                "suggestions": list(stored.get("suggestions") or []),
                "response_state": stored.get("state"),
            }
        )


async def _capability_names_safely(owner_user_id: Any) -> list[str]:
    """Tên các dịch vụ tài khoản này ĐANG dùng được — để gợi ý việc tiếp theo.

    Gợi ý một dịch vụ đang bị khoá còn tệ hơn không gợi ý gì: người dùng bấm
    vào rồi nhận thông báo không có quyền.

    Lỗi đọc trả về danh sách rỗng. Response Agent khi đó không gợi ý gì — mất
    một tiện ích nhỏ, không mất câu trả lời.
    """
    if not owner_user_id:
        return []
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        is_resident = await get_verified_identity(pool, owner_user_id) is not None
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.info("không đọc được capability để gợi ý (%s)", type(exc).__name__)
        return []
    finally:
        if pool is not None:
            await pool.close()
    return [item.name for item in _CAPABILITY_CATALOGUE if is_resident or not item.requires_resident]


async def _speak(response: DemoWorkflowResponse, *, goal: str, capabilities: list[str]) -> DemoWorkflowResponse:
    """Gắn câu trả lời tự nhiên vào response. Không bao giờ raise.

    Chỉ gọi ở ĐIỂM DỪNG — lúc workflow đã kết thúc hoặc đang chờ người dùng.
    Gọi ở mỗi lượt poll sẽ tốn một request LLM mỗi 1.5 giây cho một trạng thái
    không đổi.
    """
    usage_logger = LlmUsageLogger()
    # Theo dõi token/cost riêng cho lớp trả lời. Không tách stage thì mọi chi
    # phí dồn vào "plan", và không ai biết lớp diễn đạt đang tốn bao nhiêu —
    # trong khi nó chạy MỘT lần cho mỗi điểm dừng của mọi workflow.
    usage_token = usage_context(workflow_id=response.workflow_id, stage="respond")
    try:
        agent = ResponseAgent(
            get_llm(callbacks=[usage_logger]),
            structured_output_method=structured_output_method(),
        )
        view = _reply_view(response, goal=goal, capabilities=capabilities)
        reply = await agent.reply(view)
        # `ResponseAgent.reply()` fail-closed về baseline khi provider lỗi hoặc
        # output bị guard loại. Thử đúng MỘT lần nữa: model không tất định và
        # lần hai thường sửa được lỗi diễn đạt, nhưng trần một retry giữ chi phí
        # hữu hạn. Không loop và không hạ guard an toàn chỉ để có câu văn đẹp.
        if reply.answer.strip() == view.baseline_message.strip():
            reply = await agent.reply(view)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        # `ResponseAgent.reply` đã fail-closed bên trong; chỗ này bắt nốt lỗi
        # khi dựng client (thiếu key chẳng hạn). Không có câu trả lời tự nhiên
        # thì giao diện hiển thị câu deterministic — workflow không hề hấn gì.
        logger.info("response agent không dựng được (%s)", type(exc).__name__)
        return response
    finally:
        reset_usage_context(usage_token)
        await usage_logger.flush()
    return response.model_copy(update={"answer": reply.answer, "suggestions": list(reply.suggestions)})


async def _mark_workflow_failed_safely(workflow_id: str, error_code: str) -> None:
    """Ghim trạng thái kết thúc + mã lỗi. Không được ném lỗi ra ngoài.

    Nơi gọi là handler `except` của background job. Một exception ở đây sẽ nuốt
    mất exception gốc, và workflow lại quay về đúng chỗ cũ: PENDING vĩnh viễn.

    DB đang hỏng thì không ghim được — đó chính là trường hợp
    `DATABASE_UNAVAILABLE`. Sweeper dọn workflow mồ côi là lưới an toàn thứ hai
    cho tình huống đó.
    """
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        await repository.mark_workflow_failed(workflow_id, error_code)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("could not persist workflow failure (%s)", type(exc).__name__)
    finally:
        if pool is not None:
            await pool.close()


async def _archive_workflow_safely(workflow_id: str) -> None:
    """Đánh dấu workflow đã bàn giao. Không được ném lỗi ra ngoài.

    Đây là dọn dẹp, không phải nghiệp vụ: không archive được thì workflow con
    vẫn phải chạy. Hậu quả duy nhất là một dòng thừa trong danh sách.
    """
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        await repository.archive_workflow(workflow_id)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("could not archive parent workflow (%s)", type(exc).__name__)
    finally:
        if pool is not None:
            await pool.close()


async def _load_failure_code(workflow_id: str) -> str | None:
    """Mã lỗi đã ghim, đọc từ PostgreSQL. Dùng cho đường đọc sau restart."""
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        return await repository.get_workflow_error_code(workflow_id)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("could not read workflow failure code (%s)", type(exc).__name__)
        return None
    finally:
        if pool is not None:
            await pool.close()


async def _consume_clarification(workflow_id: str) -> dict[str, Any] | None:
    """Claim ngữ cảnh chờ bổ sung — atomic, chỉ một request thắng.

    Trả None khi không còn gì để claim (đã bị request khác lấy, hoặc DB lỗi).
    Caller phải coi None là "không được phép tạo child".
    """
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        return await repository.consume_clarification(workflow_id)
    except Exception:  # noqa: BLE001 - DB lỗi không được lộ ra route
        return None
    finally:
        if pool is not None:
            await pool.close()


async def _ensure_workflow_shell(
    workflow_id: str,
    *,
    goal: str,
    session_id: str | None,
    parent_workflow_id: str | None,
    owner_user_id: str | None = None,
) -> bool:
    """Tạo row `workflows` TRƯỚC khi Planner chạy. Trả True nếu đã có row.

    Vì sao cần: `workflow_clarifications.workflow_id` có khoá ngoại tới
    `workflows`. Nhưng `create_workflow()` chỉ được gọi từ Executor, mà Executor
    chỉ chạy trên nhánh READY — `route_after_plan` trả END ngay khi
    planner_status != READY. Với NEEDS_INFORMATION thì `workflows` chưa có row,
    nên INSERT clarification vi phạm khoá ngoại, exception bị nuốt, và ngữ cảnh
    không bao giờ được lưu.

    Shell mang `task_plan = NULL`: chưa có kế hoạch nào ở thời điểm này.
    Executor gọi `create_workflow()` sau đó là idempotent và được phép bổ sung
    canonical TaskPlan; ON CONFLICT giữ nguyên session/parent/goal của shell.
    """
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        await repository.create_workflow(
            {
                "id": workflow_id,
                "goal": goal,
                "status": WorkflowStatus.PENDING.value,
                "task_plan": None,
                "session_id": session_id,
                "parent_workflow_id": parent_workflow_id,
                # Chủ sở hữu ghi ngay từ shell, tức là TRƯỚC khi Planner chạy.
                # Ghi muộn hơn sẽ có một khoảng workflow tồn tại mà chưa ai sở
                # hữu, và mọi guard đọc trong khoảng đó đều thấy owner NULL.
                "owner_user_id": owner_user_id,
            }
        )
        return True
    except Exception as exc:  # noqa: BLE001 - DB lỗi không được làm sập workflow
        # Giữ TÊN LOẠI lỗi: một `NameError` từng nằm im ở đây và bị hiểu nhầm
        # thành "DB tạm lỗi". Không log message gốc vì nó có thể chứa DSN.
        logger.warning(
            "workflow shell persist failed (%s); workflow will not be resumable",
            type(exc).__name__,
        )
        return False
    finally:
        if pool is not None:
            await pool.close()


async def _read_repair_hints(workflow_id: str) -> list[dict]:
    """Đọc repair hints từ DB; trả [] nếu không có hoặc DB lỗi.

    `acquire_repository()` nằm trong try vì lý do như `_load_session`: lỗi tạo
    pool phải được nuốt ở ĐÂY. Trước đây nó thoát ra ngoài và caller
    (`get_demo_workflow_status`) bắt được, rồi vứt luôn cả record workflow đã
    đọc thành công.
    """
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        return await repository.get_repair_hints(workflow_id)
    except Exception:  # noqa: BLE001 - poll không được lộ connection detail
        return []
    finally:
        if pool is not None:
            await pool.close()


async def _persist_repair_hints(workflow_id: str, repair_hints: dict[str, RepairHint]) -> None:
    """Persist hint generic {error_code, message} — KHÔNG có field/input echo."""
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        await repository.save_repair_hints(
            workflow_id,
            {
                task_id: {
                    "error_code": hint.error_code.value,
                    "message": hint.message,
                }
                for task_id, hint in repair_hints.items()
            },
        )
    finally:
        await pool.close()


async def _load_parent_success_context(workflow_id: str) -> dict[str, Any]:
    """Bơm kết quả SUCCESS của parent workflow vào context của child.

    Child workflow nhận goal mới + context cũ; kết quả các task đã SUCCESS
    của parent được merge vào existing_context để Planner tạo InputRef hợp lệ
    thay vì chạy lại từ đầu.
    """
    context: dict[str, Any] = {}
    try:
        record = await read_demo_workflow(workflow_id)
        if record is None:
            return context
        for row in record.get("tasks", []):
            if row.get("status") != "SUCCESS":
                continue
            result_data = row.get("result_data") or {}
            if not isinstance(result_data, dict):
                continue
            for key, value in result_data.items():
                # Ưu tiên giữ giá trị mới nhất (child sẽ override nếu cần).
                if value is not None:
                    context[key] = value
    except Exception:  # noqa: BLE001 - đây là enrichment, không được làm hỏng continue
        pass
    return context


def _build_repair_state_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Dựng state cần thiết cho `_demo_response` khi poll/continue từ DB.

    Trả state với repair_hints generic + plan từ workflow.task_plan để
    `_demo_response` có thể map error_code + task.tool → missing_fields.
    """
    plan = _plan_from_job_or_record(None, record)
    tasks = {row["task_id"]: row for row in record.get("tasks", [])}
    task_results: dict[str, Any] = {}
    for task_id, row in tasks.items():
        if row.get("status") == "SUCCESS":
            task_results[task_id] = SimpleNamespace(
                success=True,
                data=row.get("result_data") or {},
            )
    return {
        "plan": plan,
        "task_results": task_results,
        "repair_hints": {
            row["task_id"]: RepairHint(
                error_code=ErrorCode(row["error_code"]),
                message=row["message"],
                task_id=row["task_id"],
            )
            for row in record.get("repair_hints", [])
        },
    }


def _task_failure_message(task: Any, title: str, code: str) -> str:
    """Ủy quyền sang module dùng chung — Repair Loop và API dùng một nguồn."""
    return task_failure_message(task, title, code)


@router.post("/chat", response_model=ChatResponse)
async def chat(http_request: Request, request: ChatRequest) -> ChatResponse:
    """Chat nhẹ — speech lane deterministic; service goal được hướng dẫn."""
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Tin nhắn không được để trống.")

    small_talk = classify(message)
    if isinstance(small_talk, SmallTalk):
        if small_talk.speech_type == SpeechType.CAPABILITY:
            base_url = str(http_request.base_url).rstrip("/")
            capability = await answer_capability_question(
                message,
                base_url=base_url,
                account_state="prospect",
            )
            reply = capability.reply if capability else "Bạn cần hỗ trợ gì?"
        else:
            reply = small_talk.reply
        return ChatResponse(response=reply, analysis="")

    # Service goal — không chạy qua agent echo query; hướng dẫn dùng workflow demo.
    return ChatResponse(
        response="Bạn hãy mô tả mục tiêu cụ thể (ví dụ: đặt chỗ đỗ xe, đặt lịch tham quan) để mình bắt đầu một kế hoạch.",
        analysis="",
    )


# Từ khoá nhận ra người dùng đang hỏi một dịch vụ CHỈ dành cho cư dân.
#
# Chỉ dùng để chọn CÂU CHỮ, không bao giờ để cấp quyền: quyền do
# `ResidentAccessBoundary` quyết định dựa trên mapping đã xác minh trong
# database. Một từ khoá sót chỉ khiến câu trả lời kém rõ, không mở được cửa nào.
_RESIDENT_SERVICE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Đăng ký phương tiện và chỗ đỗ xe",
        # "thanh toán phí" gộp vào đây sau khi dịch vụ thanh toán độc lập bị gỡ:
        # trả phí là bước xác nhận trong luồng đặt chỗ. Tên dịch vụ nêu ra cho
        # người dùng phải là tên CÓ THẬT trong danh mục, nếu không câu trả lời
        # chỉ họ tới một mục không tồn tại.
        (
            "đỗ xe",
            "chỗ đỗ",
            "bãi xe",
            "đăng ký xe",
            "phương tiện",
            "biển số",
            "ô tô",
            "oto",
            "xe máy",
            "thanh toán phí",
            "trả phí",
            "đóng phí",
        ),
    ),
    ("Báo bảo trì / sửa chữa", ("bảo trì", "sửa chữa", "sửa nhà", "hỏng", "rò rỉ", "sự cố", "kỹ thuật viên")),
    ("Đặt lịch chuyển nhà", ("chuyển nhà", "chuyển đồ", "thang máy", "vận chuyển đồ")),
)

_LINK_REQUIRED_TEMPLATE = (
    "{service} chỉ dành cho cư dân đã liên kết căn hộ, nên mình chưa thực hiện được. "
    "Bạn vào mục Liên kết căn hộ để gửi yêu cầu; ban quản lý duyệt xong là mình hỗ trợ ngay."
)


def _resident_service_in(goal: str) -> str | None:
    """Tên dịch vụ chỉ-cư-dân mà câu này đang nhắc tới, nếu có."""
    lowered = (goal or "").casefold()
    for service, keywords in _RESIDENT_SERVICE_HINTS:
        if any(keyword in lowered for keyword in keywords):
            return service
    return None


def _resident_link_required_message(state: dict[str, Any]) -> str | None:
    """Câu giải thích ĐÚNG lý do, khi lý do là chưa liên kết căn hộ.

    Sự cố thật: tài khoản chưa liên kết gõ "đặt chỗ đỗ xe". Planner từ chối
    (`supported_goal`) — chặn đúng — nhưng người dùng đọc được "Mình chưa đủ cơ
    sở để hỏi thêm cho yêu cầu này. Bạn mô tả lại cụ thể hơn giúp mình nhé."

    Lời mời mô tả lại là một lời hứa sai: mô tả kiểu gì cũng không dùng được,
    vì thứ còn thiếu không phải chi tiết trong câu mà là quyền cư dân. Người
    dùng sẽ gõ lại vài lần rồi kết luận sản phẩm hỏng.

    Hàm này KHÔNG cấp và KHÔNG chặn quyền — nó chỉ chọn câu chữ cho một yêu cầu
    đã bị chặn ở tầng khác.
    """
    context = state.get("existing_context") or {}
    if context.get("resident_verification_status") == "VERIFIED":
        return None
    service = _resident_service_in(str(state.get("goal") or ""))
    return _LINK_REQUIRED_TEMPLATE.format(service=service) if service else None


def _demo_response(state: dict[str, Any], payment_approved: bool) -> DemoWorkflowResponse:
    """Chuyển AgentState thành view model chỉ chứa field nghiệp vụ allowlist."""
    plan = state.get("plan")
    # `draft_plan` chỉ phục vụ preview khi Validator phát hiện thiếu input.
    # Executor vẫn chỉ nhận `plan` đã có plan_validated=True.
    plan_view = _plan_view(plan or state.get("draft_plan"))

    # Repair Loop: nếu có repair hint (từ on_failure trong workflow vừa chạy,
    # hoặc được dựng lại từ DB khi poll/continue), map error_code + task.tool
    # sang missing_fields deterministic. Không tự đổi input — chỉ hỏi lại user.
    repair_hints = state.get("repair_hints") or {}
    if repair_hints and plan is not None:
        # Lấy hint đầu tiên (thứ tự dict: task_id → hint). Mỗi lần chỉ hỏi 1
        # field để user không bị quá tải; nếu còn lỗi, vòng tiếp theo lại hỏi.
        first_hint = next(iter(repair_hints.values()))
        task = next(
            (t for t in plan.tasks if t.task_id == first_hint.task_id),
            None,
        )
        if task is not None:
            missing_fields = repair_missing_fields(
                task.tool,
                first_hint.error_code,
                dict(task.input),
            )
            if missing_fields:
                # Câu hỏi lại phải NÊU LÝ DO. Dùng chung câu với nhánh thiếu
                # thông tin là nói với người vừa gõ "Khu A" rằng họ chưa cho
                # biết khu nào — họ sẽ trả lời "Khu A" lần nữa và lại hỏng.
                code = getattr(first_hint.error_code, "value", first_hint.error_code)
                question = repair_question(task.tool, str(code), dict(task.input))
                return DemoWorkflowResponse(
                    status="NEEDS_INFORMATION",
                    question=question or _follow_up_validation_message(missing_fields),
                    missing_fields=missing_fields,
                    plan=plan_view,
                )

    # Chưa liên kết căn hộ mà hỏi dịch vụ cư dân: nói đúng lý do, bất kể lỗi
    # xuất hiện ở nhánh nào phía dưới — nguyên nhân vẫn là một.
    link_required = _resident_link_required_message(state)
    if link_required is not None and not state.get("plan_validated"):
        return DemoWorkflowResponse(status="EXECUTION_ERROR", summary=link_required, plan=plan_view)

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
    if state.get("clarification_error"):
        # Còn thiếu input nhưng không hỏi được an toàn. Không render form rỗng,
        # không nêu field nội bộ.
        return DemoWorkflowResponse(
            status="VALIDATION_ERROR",
            summary=str(state["clarification_error"]),
            plan=plan_view,
        )
    if state.get("validation_error"):
        return DemoWorkflowResponse(
            status="VALIDATION_ERROR",
            summary=_validation_guidance(str(state["validation_error"])),
            plan=plan_view,
        )
    policy_error = state.get("policy_error")
    if policy_error == "RESIDENT_LINKING_OUTSIDE_AGENT":
        return DemoWorkflowResponse(
            status="EXECUTION_ERROR",
            summary=RESIDENT_LINKING_OUTSIDE_AGENT_MESSAGE,
            plan=plan_view,
        )
    if policy_error == "RESIDENT_ACCESS_REQUIRED":
        return DemoWorkflowResponse(
            status="EXECUTION_ERROR",
            summary=RESIDENT_ACCESS_REQUIRED_MESSAGE,
            plan=plan_view,
        )
    if policy_error == "RESIDENT_DIRECTORY_UNAVAILABLE":
        return DemoWorkflowResponse(
            status="EXECUTION_ERROR",
            summary=RESIDENT_DIRECTORY_UNAVAILABLE_MESSAGE,
            plan=plan_view,
        )
    if policy_error == "PAYMENT_APPROVAL_REQUIRED":
        # Báo giá lấy từ booking ĐÃ persist (kết quả book_parking vừa chạy),
        # không lấy từ goal hay browser.
        quote = quote_from_results(state.get("task_results") or {})
        return DemoWorkflowResponse(
            status="WAITING_APPROVAL",
            workflow_id=state.get("workflow_id"),
            summary=(
                f"Đã giữ chỗ đỗ xe. Phí cần thanh toán: {quote.amount:,.0f} {quote.currency}.".replace(",", ".")
                if quote is not None
                else "Đã giữ chỗ đỗ xe. Bạn xác nhận thanh toán để mình hoàn tất nhé."
            ),
            payment_quote=quote.as_public_dict() if quote is not None else None,
            plan=plan_view,
        )
    if policy_error is not None:
        return DemoWorkflowResponse(status="EXECUTION_ERROR", plan=plan_view)
    if state.get("execution_error"):
        # KHÔNG suy ra "cần duyệt thanh toán" từ việc plan có chứa pay_fee:
        # cách đó biến MỌI lỗi thực thi thành lời mời xác nhận thanh toán, và
        # lỗi thật bị giấu hoàn toàn. Chờ duyệt là một tín hiệu tường minh —
        # `policy_error == "PAYMENT_APPROVAL_REQUIRED"` — đã xử lý ở trên.
        return DemoWorkflowResponse(status="EXECUTION_ERROR", plan=plan_view)

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


async def _trusted_account_context(user: dict) -> tuple[str, dict[str, Any]]:
    """Dựng ngữ cảnh tin cậy từ token → user_resident_links → residents.

    Trả `("prospect", ...)` cho mọi trường hợp không phải VERIFIED: chưa liên
    kết, đang chờ duyệt, đã bị từ chối. Ba trạng thái đó khác nhau về mặt vận
    hành nhưng giống hệt nhau về mặt quyền, và gộp lại ở đây khiến không nhánh
    nào có thể vô tình mở quyền cho hai cái đầu.

    Admin KHÔNG được cộng thêm quyền cư dân: role và resident link là hai trục
    độc lập. Một tài khoản vận hành không phải chủ căn hộ nào.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        identity = await get_verified_identity(pool, user["id"])
    finally:
        await pool.close()

    if identity is None:
        return "prospect", dict(_DEMO_ACCOUNT_CONTEXTS["prospect"])

    # Chỉ các field do server tra được mới vào context. Không có đường nào cho
    # giá trị từ prompt hay TaskPlan chảy vào đây.
    context = dict(_DEMO_ACCOUNT_CONTEXTS["resident"])
    context.update(
        {
            "resident_id": identity.resident_id,
            "apartment_code": identity.apartment_code,
            "residential_area": identity.residential_area,
            "full_name": identity.full_name,
        }
    )
    return "resident", context


async def _require_workflow_owner(workflow_id: str, user: dict) -> None:
    """404 nếu workflow không thuộc về `user`.

    404 chứ không phải 403: 403 xác nhận workflow đó có tồn tại, và với ID đoán
    được thì riêng việc xác nhận đã là rò rỉ. Người không sở hữu phải thấy đúng
    thứ họ thấy khi ID hoàn toàn không tồn tại.

    Owner đọc từ PostgreSQL, KHÔNG từ `_DEMO_JOBS`: cache RAM trống sau restart,
    và khi đó mọi workflow sẽ trông như vô chủ.

    Row legacy (`owner_user_id IS NULL`, tạo trước Phase B) cũng trả 404 cho
    customer. Dữ liệu vẫn còn để truy vết, nhưng không rơi vào tay tài khoản nào.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        owner = await repository.get_workflow_owner(workflow_id)
    finally:
        await pool.close()

    if owner is None or str(owner) != str(user["id"]):
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu này.")


@router.post(
    "/workflows/demo/start",
    response_model=DemoWorkflowResponse,
    status_code=202,
)
async def start_demo_workflow(
    http_request: Request,
    request: DemoWorkflowRequest,
    user: dict = Depends(get_current_user),
) -> DemoWorkflowResponse:
    """Trả workflow_id ngay; workflow tiếp tục chạy trong background task.

    Session server-side: server tự sinh `session_id` (KHÔNG tin `request.session_id`
    từ body). `account_state` body là "persona mong muốn" CHỈ ở lần tạo session;
    `_run_demo_job` ghim persona xuống bảng `sessions`. Mọi lần sau (`/continue`,
    list) đọc từ session, không từ body — chặn leo thang đặc quyền giữa chuỗi.
    """
    # Speech lane: greeting/acknowledgement/capability → trả CHAT ngay, 0 LLM.
    small_talk = classify(request.goal)
    if isinstance(small_talk, SmallTalk):
        # Câu trả lời "bạn làm được gì" cũng phải theo quyền thật: liệt kê dịch
        # vụ cư dân cho người chưa liên kết là hứa một việc sẽ bị từ chối ngay sau đó.
        small_talk_state, small_talk_context = await _trusted_account_context(user)
        workflow_id = str(uuid4())
        session_id = str(uuid4())
        if small_talk.speech_type == SpeechType.CAPABILITY:
            base_url = str(http_request.base_url).rstrip("/")
            capability = await answer_capability_question(
                request.goal,
                base_url=base_url,
                account_state=small_talk_state,
            )
            reply = capability.reply if capability else "Bạn cần hỗ trợ gì?"
        else:
            reply = small_talk.reply
        _DEMO_JOBS[workflow_id] = {
            "stage": "CHAT",
            "message": reply,
            "plan": None,
            "response": DemoWorkflowResponse(
                workflow_id=workflow_id,
                status="CHAT",
                stage="CHAT",
                message=reply,
                session_id=session_id,
            ),
            "events": [],
            "goal": request.goal,
            "account_state": small_talk_state,
            "approve_mock_payment": False,
            "existing_context": small_talk_context,
            "contact_profile": {},
            "session_id": session_id,
            "parent_workflow_id": None,
        }
        _append_job_event(_DEMO_JOBS[workflow_id], "CHAT")
        return DemoWorkflowResponse(
            workflow_id=workflow_id,
            status="CHAT",
            stage="CHAT",
            message=reply,
            session_id=session_id,
        )

    workflow_id = str(uuid4())
    session_id = str(uuid4())
    settings = get_settings()
    # Quyền suy ra từ token + PostgreSQL, KHÔNG từ body. Đây là điểm mà một
    # dòng JSON `"account_state": "resident"` từng đủ để mở toàn bộ dịch vụ cư dân.
    account_state, context = await _trusted_account_context(user)
    if request.project_name is not None:
        selected_project_id = resolve_project_id(request.project_name)
        if selected_project_id is None:
            raise HTTPException(status_code=422, detail="Dự án chưa nằm trong danh sách được hỗ trợ.")
    else:
        selected_project_id = find_project_id(request.goal)
    if selected_project_id is not None:
        context["project_id"] = selected_project_id

    # Shell + session ĐƯỢC GHIM TRƯỚC khi trả 202.
    #
    # Trước đây shell được tạo bên trong `_run_demo_job`, tức là SAU khi route
    # đã trả `workflow_id` cho client. Client poll ngay lập tức thì workflow
    # chưa tồn tại, `_require_workflow_owner` không tìm thấy chủ, và GET trả
    # 404 cho chính workflow vừa được tạo. Đó là race, không phải chuyện thời
    # điểm: không có mốc nào để client biết khi nào an toàn để đọc.
    #
    # Persist thất bại thì KHÔNG schedule Planner và KHÔNG trả workflow_id —
    # trả một id client không đọc được còn tệ hơn báo lỗi.
    # Bọc cả lời gọi: helper có hợp đồng "trả bool", nhưng một route sinh ra
    # lỗi cho người dùng thì không được 500 chỉ vì tầng dưới ném ra thứ khác
    # hợp đồng. Mọi thất bại đều quy về cùng một câu trả lời an toàn.
    try:
        persisted = await _create_shell_and_session(
            workflow_id,
            owner_user_id=user["id"],
            session_id=session_id,
            goal=request.goal,
            account_state=account_state,
            resident_id=context.get("resident_id"),
        )
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("workflow start persist raised (%s)", type(exc).__name__)
        persisted = False

    if not persisted:
        raise HTTPException(
            status_code=503,
            detail="Hệ thống đang bận, chưa nhận được yêu cầu. Vui lòng thử lại sau ít phút.",
        )

    _DEMO_JOBS[workflow_id] = {
        "stage": "PLANNING",
        "message": _STAGE_MESSAGES["PLANNING"],
        "plan": None,
        "response": None,
        "events": [],
        "goal": request.goal,
        "account_state": account_state,
        "owner_user_id": user["id"],
        # Workflow LUÔN bắt đầu chưa được duyệt thanh toán. Một boolean trong
        # body có thể pre-approve nghĩa là /payment-decision thành tuỳ chọn —
        # mà bước duyệt tồn tại chính vì nó không được phép là tuỳ chọn.
        "approve_mock_payment": False,
        "existing_context": context,
        # Thông tin liên hệ lấy từ tài khoản/provider, không từ browser.
        "contact_profile": {},
        "session_id": session_id,
        "parent_workflow_id": None,
    }
    _append_job_event(_DEMO_JOBS[workflow_id], "PLANNING")
    task = asyncio.create_task(
        _run_demo_job(
            workflow_id,
            request.goal,
            # Không pre-approve. Mọi thanh toán đi qua /payment-decision.
            False,
            {
                "resident": settings.resident_service_url,
                "transport": settings.transport_service_url,
                "payment": settings.payment_service_url,
                "property": settings.property_service_url,
                "resident_services": settings.resident_services_service_url,
            },
            account_state,
            session_id=session_id,
        )
    )
    _keep_demo_task(task, workflow_id=workflow_id)
    return DemoWorkflowResponse(
        workflow_id=workflow_id,
        status="PENDING",
        stage="PLANNING",
        message=_STAGE_MESSAGES["PLANNING"],
        session_id=session_id,
    )


@router.post(
    "/workflows/demo/{workflow_id}/continue",
    response_model=DemoWorkflowResponse,
    status_code=202,
)
async def continue_demo_workflow(
    workflow_id: str,
    http_request: Request,
    request: DemoWorkflowContinueRequest,
    user: dict = Depends(get_current_user),
) -> DemoWorkflowResponse:
    """Tiếp tục goal gốc bằng field đã map deterministic ở backend."""
    # Kiểm quyền TRƯỚC khi chạm `_DEMO_JOBS`. Đọc cache trước rồi mới kiểm sẽ
    # để lộ qua thời gian phản hồi và qua thông báo lỗi rằng workflow đó có tồn
    # tại hay không.
    await _require_workflow_owner(workflow_id, user)
    previous = _DEMO_JOBS.get(workflow_id)
    response = previous.get("response") if previous is not None else None

    if previous is not None and isinstance(response, DemoWorkflowResponse):
        if response.status != "NEEDS_INFORMATION" or not response.missing_fields:
            raise HTTPException(status_code=409, detail="Workflow không chờ thêm thông tin.")
        # Cache RAM đi thẳng từ AgentState nên có thể còn tên nội bộ
        # ``project_id``; bản ghim PostgreSQL đã dùng ``project_name`` công
        # khai. Hai đường phải có cùng contract, nếu không cùng một câu hỏi sẽ
        # chạy sau restart nhưng 422 khi backend chưa restart.
        pending_missing_fields = _to_public_missing_fields(list(response.missing_fields))
        pending_goal = previous["goal"]
        pending_session_id = previous.get("session_id")
        pending_context = dict(previous["existing_context"])
    else:
        # `_DEMO_JOBS` trống sau restart. Đọc lại ngữ cảnh đã ghim — đây là
        # đường duy nhất giúp hội thoại sống sót qua một lần deploy.
        # PEEK trước — cần `missing_fields` để validate câu trả lời. Chỉ
        # consume SAU khi input đã hợp lệ, để một câu trả lời sai không đốt mất
        # lượt hỏi của người dùng.
        clarification = await _load_clarification(workflow_id)
        if clarification is None:
            raise HTTPException(status_code=409, detail="Workflow chưa sẵn sàng để tiếp tục.")
        pending_missing_fields = list(clarification.get("missing_fields") or [])
        if not pending_missing_fields:
            raise HTTPException(status_code=409, detail="Workflow không chờ thêm thông tin.")
        pending_goal = clarification.get("goal") or ""
        pending_session_id = clarification.get("session_id")
        pending_context = dict(clarification.get("existing_context") or {})

    # Sau restart `previous` là None. Hai giá trị này chỉ là tuỳ chọn hiển thị/
    # thanh toán, có mặc định an toàn — KHÔNG suy ra quyền từ chúng.
    pending_approve_payment = bool(previous.get("approve_mock_payment")) if previous else False
    pending_contact_profile = dict(previous.get("contact_profile", {})) if previous else {}

    goal = pending_goal
    # Quyền (account_state) đọc từ session server-side (ghim ở /start), KHÔNG từ
    # body hay `_DEMO_JOBS`. Context nghiệp vụ giữ nguyên từ job parent — nó đã
    # được dựng lúc /start từ persona tại thời điểm tạo session, và chỉ chứa
    # delta của riêng parent (project_id…) + base persona. Không session (DB lỗi
    # lúc ghim) → fail-closed về prospect cho quyết định quyền.
    session_id = pending_session_id or workflow_id
    session = await _load_session(session_id, user_id=user["id"])
    account_state = (session or {}).get("account_state", "prospect")
    context = dict(pending_context)
    # Bơm kết quả SUCCESS của parent từ DB (nếu parent đã persist). Giúp child
    # không chạy lại các bước đã xong.
    context.update(await _load_parent_success_context(workflow_id))
    parent_workflow_id = workflow_id
    missing_fields = pending_missing_fields
    if missing_fields == ["supported_goal"]:
        if request.message is None:
            raise HTTPException(status_code=422, detail=_follow_up_validation_message(missing_fields))
        goal = request.message.strip()
        # Speech lane: nếu user đổi goal thành greeting/acknowledgement/capability,
        # trả CHAT thay vì chạy workflow vô nghĩa.
        small_talk = classify(goal)
        if isinstance(small_talk, SmallTalk):
            new_workflow_id = str(uuid4())
            if small_talk.speech_type == SpeechType.CAPABILITY:
                base_url = str(http_request.base_url).rstrip("/")
                capability = await answer_capability_question(
                    goal,
                    base_url=base_url,
                    account_state=account_state,
                )
                reply = capability.reply if capability else "Bạn cần hỗ trợ gì?"
            else:
                reply = small_talk.reply
            _DEMO_JOBS[new_workflow_id] = {
                "owner_user_id": user["id"],
                "stage": "CHAT",
                "message": reply,
                "plan": None,
                "response": DemoWorkflowResponse(
                    workflow_id=new_workflow_id,
                    status="CHAT",
                    stage="CHAT",
                    message=reply,
                    session_id=session_id,
                ),
                "events": [],
                "goal": goal,
                "account_state": account_state,
                "approve_mock_payment": pending_approve_payment,
                "existing_context": dict(pending_context),
                "contact_profile": dict(pending_contact_profile),
                "session_id": session_id,
                "parent_workflow_id": parent_workflow_id,
            }
            _append_job_event(_DEMO_JOBS[new_workflow_id], "CHAT")
            return DemoWorkflowResponse(
                workflow_id=new_workflow_id,
                status="CHAT",
                stage="CHAT",
                message=reply,
                session_id=session_id,
                parent_workflow_id=parent_workflow_id,
            )
    elif "payment_quote" in missing_fields:
        raise HTTPException(status_code=409, detail="Hệ thống chưa lấy được báo phí; người dùng không thể tự nhập.")
    else:
        # Một câu hỏi tra cứu không phải dữ liệu field. Trả lời ngay trên cùng
        # workflow và GIỮ clarification mở; nếu consume ở đây, người dùng vừa
        # hỏi danh sách xong đã mất luôn chỗ để chọn dự án.
        if request.message is not None and _asks_for_project_catalog(request.message, missing_fields):
            if isinstance(response, DemoWorkflowResponse):
                current = response
            else:
                current = await _public_view_from_db(workflow_id)
            if current is None:
                raise HTTPException(status_code=409, detail="Workflow chưa sẵn sàng để tiếp tục.")
            answer = _project_catalog_answer()
            conversational = current.model_copy(
                update={
                    "workflow_id": workflow_id,
                    "answer": answer,
                    "missing_fields": list(missing_fields),
                    "response_state": "READY",
                }
            )
            if previous is not None:
                previous["response"] = conversational
            await _save_answer_safely(
                workflow_id,
                answer=answer,
                suggestions=[],
                state="READY",
                for_status="NEEDS_INFORMATION",
            )
            return conversational

        if request.fields:
            answers, unresolved = _extract_structured_follow_up_answers(request.fields, missing_fields)
        elif request.message is not None:
            answers, unresolved = _extract_follow_up_answers(request.message, missing_fields)
        else:
            answers, unresolved = {}, missing_fields
        # Form có đủ tất cả ô đang hiển thị. Chấp nhận một phần ở đây sẽ tạo
        # workflow con chỉ để Planner hỏi tiếp những ô còn trống, kéo theo một
        # bubble Response Agent mới cho mỗi lần bấm gửi. Câu chat tự do vẫn có
        # thể bổ sung từng phần; chỉ contract form structured là all-or-none.
        if request.fields and unresolved:
            # Chỉ log TÊN field để chẩn đoán contract UI/API. Không log giá trị
            # người dùng nhập, goal, token hay body request.
            logger.info(
                "clarification rejected (expected=%s received=%s unresolved=%s)",
                sorted(missing_fields),
                sorted(request.fields),
                sorted(unresolved),
            )
            raise HTTPException(status_code=422, detail=_follow_up_validation_message(unresolved))
        if not answers:
            logger.info(
                "clarification contained no usable answer (expected=%s received=%s)",
                sorted(missing_fields),
                sorted((request.fields or {}).keys()),
            )
            raise HTTPException(status_code=422, detail=_follow_up_validation_message(unresolved))

        # Người dùng ĐÃ gửi tên dự án nhưng nó không khớp danh mục → phải nói
        # ra. Bỏ qua im lặng còn tệ hơn 422: workflow đi tiếp thiếu dự án, rồi
        # hỏi lại đúng câu vừa hỏi, và người dùng không biết mình sai ở đâu.
        if "project_name" in (request.fields or {}) and "project_name" not in answers:
            raise HTTPException(status_code=422, detail=_UNSUPPORTED_PROJECT_MESSAGE)

        # Dịch trước khi consume clarification: tên dự án không hỗ trợ phải trả
        # 422 mà KHÔNG đốt mất lượt hỏi — người dùng còn sửa lại được.
        answers, bad_field = _resolve_public_answers(answers)
        if bad_field is not None:
            raise HTTPException(status_code=422, detail=_UNSUPPORTED_PROJECT_MESSAGE)
        context.update(answers)

    # CONSUME atomic ngay trước khi tạo child. Đây là điểm duy nhất quyết định
    # ai được đi tiếp: `UPDATE ... WHERE resolved_at IS NULL ... RETURNING *`
    # là một câu lệnh, nên PostgreSQL tự tuần tự hoá hai request đồng thời và
    # người đến sau nhận 0 row.
    #
    # Không dùng `_DEMO_JOBS` để chọn người thắng: RAM không chia sẻ giữa các
    # worker và biến mất sau restart.
    #
    # Đặt SAU bước validate là có chủ ý — câu trả lời sai không được đốt mất
    # lượt hỏi, người dùng vẫn sửa lại được.
    # Child id sinh TRƯỚC để nó nằm trong cùng transaction với việc claim.
    new_workflow_id = str(uuid4())
    claimed = await _consume_and_create_child(
        workflow_id,
        child_workflow_id=new_workflow_id,
        owner_user_id=user["id"],
        session_id=session_id,
        goal=goal,
    )
    if claimed is None:
        # Không claim được row đã ghim. Hai khả năng:
        #
        #  a) Request khác đã thắng  → phải từ chối.
        #  b) Chưa từng ghim được (persistence lỗi, hoặc job chỉ sống trong
        #     RAM) → vẫn phải bảo đảm one-shot, nhưng bằng cờ trên job.
        #
        # GIỚI HẠN: cờ RAM chỉ chặn được trong CÙNG tiến trình. Chỉ đường claim
        # trong PostgreSQL mới an toàn khi có nhiều worker. Workflow rơi vào
        # nhánh (b) không phải là workflow resume được sau restart.
        if previous is None or previous.get("clarification_claimed"):
            raise HTTPException(status_code=409, detail="Workflow không chờ thêm thông tin.")
        previous["clarification_claimed"] = True

    # Đóng workflow CHA, kể cả khi việc claim đi qua nhánh dự phòng RAM.
    #
    # `consume_clarification_and_create_child` đã archive cha trong transaction
    # của nó, nhưng nhánh (b) ở trên — chưa từng ghim được clarification —
    # không đi qua đó. Thiếu dòng này, đúng những workflow không resume được
    # sau restart lại là những workflow để lại một dòng `PENDING` vĩnh viễn.
    #
    # Idempotent nhờ `WHERE archived_at IS NULL`, nên gọi hai lần không sao.
    await _archive_workflow_safely(parent_workflow_id)

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
        "account_state": account_state,
        # Workflow con PHẢI kế thừa chủ sở hữu của cha.
        #
        # Thiếu dòng này, con được tạo với `owner_user_id = NULL` và rơi vào
        # nhóm "legacy không chủ" — `_require_workflow_owner` trả 404 cho CHÍNH
        # người vừa trả lời câu hỏi bổ sung. Workflow vẫn chạy tới SUCCESS ở
        # phía sau, nhưng người dùng không bao giờ nhìn thấy kết quả.
        "owner_user_id": user["id"],
        "approve_mock_payment": pending_approve_payment,
        "existing_context": context,
        # Tách riêng khỏi `existing_context`: đây là điều người dùng vừa nói
        # SAU KHI biết lựa chọn đầu không dùng được, nên nó phải thắng câu chữ
        # trong goal cũ — goal của workflow con vẫn mang nguyên "lúc 12:30".
        "user_answers": dict(answers),
        "contact_profile": dict(pending_contact_profile),
        "session_id": session_id,
        "parent_workflow_id": parent_workflow_id,
    }
    _append_job_event(_DEMO_JOBS[new_workflow_id], "PLANNING")
    task = asyncio.create_task(
        _run_demo_job(
            new_workflow_id,
            goal,
            pending_approve_payment,
            service_urls,
            account_state,
            session_id=session_id,
            parent_workflow_id=parent_workflow_id,
        )
    )
    _keep_demo_task(task, workflow_id=new_workflow_id)
    return DemoWorkflowResponse(
        workflow_id=new_workflow_id,
        status="PENDING",
        stage="PLANNING",
        message=_STAGE_MESSAGES["PLANNING"],
        session_id=session_id,
        parent_workflow_id=parent_workflow_id,
    )


async def _load_pending_payment(workflow_id: str) -> dict[str, Any] | None:
    """Khoản thanh toán đang chờ duyệt của workflow, đọc từ PostgreSQL.

    Dùng cho CẢ hai đường dựng response — job còn trong RAM và job đã mất sau
    restart. Sửa mỗi đường restart sẽ để lại đúng nửa lỗi: người dùng chưa
    reload vẫn không thấy báo giá.

    DB lỗi trả None: mất báo giá thì màn hình thiếu thông tin, còn ném lỗi ra
    route thì mất luôn cả workflow.
    """
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        return await repository.get_pending_payment_view(workflow_id)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("load pending payment failed (%s)", type(exc).__name__)
        return None
    finally:
        if pool is not None:
            await pool.close()


def _waiting_approval_view(
    workflow_id: str,
    *,
    plan: TaskPlan | None,
    record: dict[str, Any] | None,
    pending: dict[str, Any],
    events: list[DemoWorkflowEvent],
) -> DemoWorkflowResponse:
    """View công khai cho workflow đang chờ người dùng duyệt thanh toán.

    Một chỗ duy nhất dựng view này, để đường RAM và đường restart không lệch
    nhau. `WAITING_APPROVAL` được SUY RA từ bảng `payment_approvals` — giống
    cách clarification suy ra từ bảng con — chứ không đọc `workflows.status`,
    cột đó vẫn là RUNNING trong lúc chờ.
    """
    tasks = _polling_task_views(plan, record)
    for task in tasks:
        # Bước thanh toán phải hiện rõ là đang chờ, không phải "đang chạy".
        if task.task_id == pending.get("task_id"):
            task.status = "WAITING_APPROVAL"

    return DemoWorkflowResponse(
        workflow_id=workflow_id,
        status="WAITING_APPROVAL",
        stage="WAITING_APPROVAL",
        message=_STAGE_MESSAGES.get("WAITING_APPROVAL", "Đang chờ bạn xác nhận thanh toán."),
        payment_quote={
            "booking_id": pending["booking_id"],
            "amount": pending["amount"],
            "currency": pending["currency"],
        },
        plan=_plan_view(plan),
        tasks=tasks,
        persisted=True,
        resumable=True,
        events=events,
    )


@router.get(
    "/workflows/demo/{workflow_id}",
    response_model=DemoWorkflowResponse,
)
async def get_demo_workflow_status(
    workflow_id: str,
    user: dict = Depends(get_current_user),
) -> DemoWorkflowResponse:
    """Kết hợp stage của Agent với task status thật đọc từ PostgreSQL."""
    await _require_workflow_owner(workflow_id, user)
    job = _DEMO_JOBS.get(workflow_id)

    # HAI error boundary tách rời. Trước đây cả hai lần đọc nằm chung một try,
    # nên repair hints lỗi sẽ xoá luôn record workflow đã đọc THÀNH CÔNG —
    # response mất `persisted=True`, mất task status, và nếu cache RAM cũng
    # trống thì trả 404 cho một workflow có thật trong database.
    try:
        record = await read_demo_workflow(workflow_id)
    except Exception:  # noqa: BLE001 - DB tạm lỗi không được lộ connection detail
        record = None

    if record is not None:
        # Repair hints là dữ liệu PHỤ TRỢ, nên có error boundary riêng: đọc
        # hỏng thì hiển thị rỗng, không được kéo theo phần dữ liệu chính.
        try:
            record["repair_hints"] = await _read_repair_hints(workflow_id)
        except Exception:  # noqa: BLE001 - hint hỏng không được làm mất record
            record["repair_hints"] = []
    if job is None and record is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    # Khoản thanh toán đang chờ duyệt quyết định view, bất kể job còn trong RAM
    # hay đã mất sau restart. `workflows.status` vẫn là RUNNING trong lúc chờ,
    # nên đọc cột đó sẽ báo "đang chạy" cho một workflow thực ra đang đợi người
    # dùng bấm nút — và giao diện mất cả báo giá lẫn hai nút quyết định.
    pending_payment = await _load_pending_payment(workflow_id)
    if pending_payment is not None:
        return await _with_stored_answer(
            _waiting_approval_view(
                workflow_id,
                plan=_plan_from_job_or_record(job, record),
                record=record,
                pending=pending_payment,
                events=_public_events(job),
            ),
            workflow_id,
        )

    if job is not None and isinstance(job.get("response"), DemoWorkflowResponse):
        response = job["response"]
        # Cache chưa có câu trả lời (ví dụ tiến trình này vừa khởi động lại và
        # chỉ dựng lại một phần) thì đọc bản đã ghi trên workflow.
        if response.answer is None:
            response = await _with_stored_answer(response, workflow_id)
        return response.model_copy(
            update={
                # workflow_id phải bám theo path, kể cả khi response terminal
                # được lấy từ job cache. Thiếu nó, UI mất pendingWorkflowId và
                # lần submit kế tiếp rơi nhầm sang /start.
                "workflow_id": workflow_id,
                "stage": job["stage"],
                "message": job["message"],
                "persisted": record is not None,
                "resumable": bool(response.resumable),
                "events": _public_events(job),
            }
        )

    plan = _plan_from_job_or_record(job, record)

    # Repair Loop survive restart: nếu DB FAILED nhưng có repair hint, dựng
    # state từ DB + plan để `_demo_response` map sang NEEDS_INFORMATION + fields.
    if (
        record is not None
        and record.get("workflow", {}).get("status") in {"FAILED", "CANCELLED"}
        and record.get("repair_hints")
    ):
        repair_state = _build_repair_state_from_record(record)
        if repair_state["repair_hints"]:
            return _demo_response(repair_state, payment_approved=False).model_copy(
                update={
                    "workflow_id": workflow_id,
                    "stage": "NEEDS_INFORMATION",
                    "message": _STAGE_MESSAGES["NEEDS_INFORMATION"],
                    "persisted": True,
                    "events": _public_events(job),
                }
            )

    # Clarification survive restart: `_DEMO_JOBS` trống nhưng bảng
    # `workflow_clarifications` còn row chưa được trả lời → workflow này đang
    # CHỜ NGƯỜI DÙNG, không phải đang chạy.
    #
    # Không có nhánh này, GET trả status=RUNNING + stage=FINISHED và mất cả
    # `question` lẫn `missing_fields` — giao diện không có gì để hiển thị và
    # người dùng không biết mình cần làm gì.
    #
    # NEEDS_INFORMATION KHÔNG phải giá trị trong `WorkflowStatus`: nó được SUY
    # RA từ bảng con, đúng cách repair hint đang làm ở khối ngay trên.
    database_status = record["workflow"]["status"] if record is not None else None
    if job is None and record is not None and database_status not in {"SUCCESS", "FAILED", "CANCELLED"}:
        pending = await _load_clarification_safely(workflow_id)
        if pending is not None:
            return DemoWorkflowResponse(
                workflow_id=workflow_id,
                status="NEEDS_INFORMATION",
                stage="NEEDS_INFORMATION",
                message=_STAGE_MESSAGES["NEEDS_INFORMATION"],
                question=pending.get("question"),
                missing_fields=list(pending.get("missing_fields") or []),
                persisted=True,
                # Đọc được từ PostgreSQL chính là bằng chứng resume được.
                resumable=True,
                plan=_plan_view(plan),
                tasks=_polling_task_views(plan, record),
                # Không bịa event: lượt chạy đó thuộc tiến trình đã chết.
                events=[],
            )

    task_views = _polling_task_views(plan, record)
    stage = job["stage"] if job is not None else "FINISHED"
    message = job["message"] if job is not None else _STAGE_MESSAGES["FINISHED"]
    if database_status == "SUCCESS":
        status = "SUCCESS"
    elif database_status == "CANCELLED":
        status = "CANCELLED"
    elif database_status == "FAILED":
        status = "FAILED"
    else:
        status = "RUNNING"

    # Lỗi đã ghim thì đọc lại từ PostgreSQL, kể cả khi `_DEMO_JOBS` đã mất.
    #
    # Đây là điểm khác biệt giữa "workflow hỏng" và "workflow đang chạy" sau
    # một lần restart. Thiếu nhánh này, mọi thứ không SUCCESS đều thành RUNNING
    # và giao diện poll vô hạn.
    failure = failure_for_code(await _load_failure_code(workflow_id)) if status == "FAILED" else None
    if failure is not None:
        message = failure.message
        stage = "EXECUTION_FAILED"

    return await _with_stored_answer(
        DemoWorkflowResponse(
            workflow_id=workflow_id,
            status=status,
            stage=stage,
            message=message,
            summary=message if status in {"SUCCESS", "FAILED", "CANCELLED"} else None,
            persisted=record is not None,
            plan=_plan_view(plan),
            tasks=task_views,
            events=_public_events(job),
            error_code=failure.code if failure else None,
            retryable=failure.retryable if failure else None,
        ),
        workflow_id,
    )


@router.get(
    "/workflows/demo/session/{session_id}",
    response_model=DemoSessionListResponse,
)
async def list_demo_workflows_by_session(
    session_id: str,
    user: dict = Depends(get_current_user),
) -> DemoSessionListResponse:
    """Lịch sử một cuộc hội thoại: workflow cùng session_id CỦA CHÍNH user này.

    `session_id` là giá trị client biết và gửi lại được, nên nó chỉ là khoá
    nhóm — không phải bằng chứng về quyền. Lọc theo mỗi nó nghĩa là ai cầm được
    session của người khác thì đọc được toàn bộ thread của họ.

    Lọc NGAY TRONG SQL, không đọc hết rồi bỏ bớt ở Python: lọc ở tầng trên vẫn
    kéo mọi row của người khác lên khỏi database, và `limit` thì đã áp trước
    khi lọc.

    Session của người khác và session không tồn tại trả về CÙNG một kết quả
    (danh sách rỗng). Khác nhau ở bất kỳ điểm nào cũng đủ để dò xem một session
    có thật hay không.

    Lazy zombie sweep (Phase B): poll danh sách cũng là nơi dọn workflow mồ côi
    (payment approval hết hạn, RUNNING không còn process). Live workflow trong
    `_DEMO_JOBS` được loại khỏi danh sách sweep.
    """
    await sweep_zombie_workflows(live_ids=set(_DEMO_JOBS))
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        rows = await repository.list_workflows_by_session(session_id, owner_user_id=user["id"])
    finally:
        await pool.close()

    items = []
    for row in rows:
        workflow_id = str(row["workflow_id"])
        items.append(
            DemoWorkflowListItem(
                workflow_id=workflow_id,
                title=_goal_to_title(row.get("goal")),
                status=row["status"],
                current_step=None,
                completed_tasks=int(row.get("completed_tasks") or 0),
                total_tasks=int(row.get("total_tasks") or 0),
                needs_attention=row["status"] in _ATTENTION_STATUSES,
                created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                updated_at=row["updated_at"].isoformat() if row.get("updated_at") else None,
            )
        )
    return DemoSessionListResponse(session_id=session_id, workflows=items)


# `POST /workflows/demo` (biến thể đồng bộ) ĐÃ BỊ XOÁ — Phase B.
#
# Nó không đòi xác thực nhưng vẫn gọi LLM và chạy runtime thật. "Chạy ở quyền
# thấp nhất" không cứu được điều đó: bất kỳ ai chạm tới cổng vẫn tiêu thụ được
# quota LLM, vẫn tạo được lịch xem nhà và phiếu quan tâm, và workflow sinh ra
# không gắn với chủ sở hữu nào nên nằm ngoài mọi kiểm tra quyền lẫn audit của
# nhánh async.
#
# Không caller nào cần nó: `static/demo.html` chỉ GET danh sách rồi POST
# `/start`, `/continue`, `/payment-decision`; frontend React không tham chiếu.
# Đường chính thức là `POST /workflows/demo/start` — có auth, có owner, có session.


@router.get("/status")
async def agent_status():
    """Kiểm tra trạng thái agent."""
    return {"status": "ready", "agent": "LangGraph Agent v1.0"}
    (DemoDetailItem,)


@router.get("/projects", response_model=DemoProjectListResponse)
async def list_supported_projects() -> DemoProjectListResponse:
    """Danh mục dự án public cho UI/CLI; ID nội bộ không rời backend."""
    return DemoProjectListResponse(projects=[project["project_name"] for project in PROJECTS])


# Danh mục capability — MỘT nguồn duy nhất.
#
# Endpoint `/capabilities` và gợi ý của Response Agent đều đọc từ đây. Chép
# thành hai bản thì sớm muộn giao diện mời người dùng một dịch vụ mà gợi ý
# không biết tới, hoặc ngược lại.
_CAPABILITY_CATALOGUE: tuple[DemoCapabilityItem, ...] = (
    DemoCapabilityItem(
        name="Đặt lịch tham quan dự án",
        description="Chọn dự án, ngày và giờ muốn tham quan.",
    ),
    DemoCapabilityItem(
        name="Đăng ký quan tâm / nhận tư vấn",
        description="Gửi nhu cầu để bộ phận tư vấn liên hệ.",
    ),
    DemoCapabilityItem(
        name="Đăng ký phương tiện và chỗ đỗ xe",
        description="Liên kết phương tiện và đặt chỗ tại Khu A hoặc Khu B.",
        requires_resident=True,
    ),
    DemoCapabilityItem(
        name="Báo bảo trì / sửa chữa",
        description="Tạo yêu cầu và hẹn lịch kỹ thuật viên.",
        requires_resident=True,
    ),
    DemoCapabilityItem(
        name="Đặt lịch chuyển nhà",
        description="Đăng ký thời gian, thang máy và hỗ trợ vận chuyển.",
        requires_resident=True,
    ),
)

# "Thanh toán phí đỗ xe" đã được gỡ khỏi danh mục.
#
# Nó chưa bao giờ là một dịch vụ người dùng đi TÌM. Từ khi bước thanh toán được
# ghép tự động vào mọi lần đặt chỗ (`_ensure_payment_is_offered`), việc trả phí
# là một bước XÁC NHẬN nằm trong luồng đặt chỗ, không phải một mục để chọn từ
# menu. Để lại nó mời người dùng bắt đầu một yêu cầu "thanh toán" rời rạc mà
# Planner không có booking nào để gắn vào.
#
# Tool `pay_fee` KHÔNG bị gỡ: nó vẫn nằm trong `ALLOWED_TOOLS` và vẫn chạy —
# chỉ là không còn được rao như một dịch vụ độc lập.


@router.get("/capabilities", response_model=DemoCapabilityListResponse)
async def list_supported_capabilities(
    user: dict = Depends(get_current_user),
) -> DemoCapabilityListResponse:
    """Mục tiêu người dùng giao được, kèm cái nào đang mở với CHÍNH tài khoản này.

    Cần token: `available` phụ thuộc liên kết cư dân, nên trả nó cho người ẩn
    danh là vừa vô nghĩa vừa tiết lộ mô hình quyền.

    Capability bị khoá vẫn được LIỆT KÊ, kèm lý do. Ẩn hẳn thì người dùng không
    biết dịch vụ tồn tại và cũng không biết cần làm gì để mở nó.

    Admin KHÔNG được cộng quyền cư dân: role và liên kết căn hộ là hai trục
    độc lập. Người vận hành hệ thống không phải chủ căn hộ nào.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        identity = await get_verified_identity(pool, user["id"])
        status = await get_link_status(pool, user["id"])
    finally:
        await pool.close()

    is_resident = identity is not None
    blocked_reason = {
        "PENDING": "Hồ sơ cư dân của bạn đang chờ ban quản lý duyệt.",
        "REJECTED": "Hồ sơ cư dân chưa được duyệt. Vui lòng liên hệ ban quản lý.",
    }.get(status.value if status is not None else "", "Cần liên kết hồ sơ cư dân đã xác minh.")

    def _resolve(item: DemoCapabilityItem) -> DemoCapabilityItem:
        if not item.requires_resident or is_resident:
            return item
        return item.model_copy(update={"available": False, "blocked_reason": blocked_reason})

    return DemoCapabilityListResponse(capabilities=[_resolve(item) for item in _CAPABILITY_CATALOGUE])


@router.delete("/workflows/demo/{workflow_id}", status_code=204)
async def delete_demo_workflow(
    workflow_id: str,
    user: dict = Depends(get_current_user),
) -> Response:
    """Xoá một yêu cầu đã kết thúc khỏi danh sách của chính mình.

    Xoá MỀM: dữ liệu nghiệp vụ và bằng chứng thanh toán được giữ lại, chỉ ẩn
    khỏi danh sách. Yêu cầu chưa kết thúc phải huỷ trước — một workflow đang
    chờ duyệt thanh toán mà biến mất khỏi màn hình thì khoản tiền vẫn treo và
    người dùng không còn đường nhìn thấy nó.
    """
    await _require_workflow_owner(workflow_id, user)

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        outcome = await repository.delete_workflow_for_owner(workflow_id, owner_user_id=user["id"])
    finally:
        await pool.close()

    if outcome is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu này.")
    if not outcome.get("deleted"):
        raise HTTPException(
            status_code=409,
            detail="Yêu cầu này chưa kết thúc. Bạn huỷ yêu cầu trước rồi xoá nhé.",
        )
    # Bỏ job trong RAM để lượt poll đang chạy không dựng lại dòng vừa ẩn.
    _DEMO_JOBS.pop(workflow_id, None)
    return Response(status_code=204)


@router.post(
    "/workflows/demo/{workflow_id}/cancel",
    response_model=DemoWorkflowResponse,
)
async def cancel_demo_workflow(
    workflow_id: str,
    user: dict = Depends(get_current_user),
) -> DemoWorkflowResponse:
    """Huỷ một yêu cầu chưa kết thúc, không giả lập rollback.

    Task đã SUCCESS và side effect nghiệp vụ của nó được giữ nguyên. Task chưa
    hoàn tất chuyển CANCELLED. Nếu workflow đang chờ thanh toán, thao tác này
    dùng cùng policy với quyết định từ chối: không thu tiền và vẫn giữ booking.
    """
    await _require_workflow_owner(workflow_id, user)

    pending_payment = await _load_pending_payment(workflow_id)
    if pending_payment is not None:
        try:
            await reject_payment(workflow_id)
            outcome: dict[str, Any] | None = {
                "cancelled": True,
                "previous_status": "WAITING_APPROVAL",
            }
        except ResumeError as exc:
            if exc.code != "ALREADY_DECIDED":
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            outcome = {"cancelled": False, "previous_status": "CANCELLED"}
    else:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        try:
            outcome = await repository.cancel_workflow(workflow_id, owner_user_id=user["id"])
        finally:
            await pool.close()

    if outcome is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu này.")
    previous_status = outcome.get("previous_status")
    if not outcome.get("cancelled") and previous_status != "CANCELLED":
        raise HTTPException(status_code=409, detail="Yêu cầu đã kết thúc nên không thể huỷ.")

    # Dừng coroutine đúng workflow trong tiến trình hiện tại. Sau restart không
    # có task RAM nào; PostgreSQL vừa được chốt CANCELLED là nguồn sự thật.
    running_task = _DEMO_WORKFLOW_TASKS.get(workflow_id)
    if running_task is not None and not running_task.done():
        running_task.cancel()

    record = await read_demo_workflow(workflow_id)
    plan = _plan_from_job_or_record(_DEMO_JOBS.get(workflow_id), record)
    response = DemoWorkflowResponse(
        workflow_id=workflow_id,
        status="CANCELLED",
        stage="FINISHED",
        message=_DEFAULT_BASELINE["CANCELLED"],
        summary=_DEFAULT_BASELINE["CANCELLED"],
        persisted=True,
        plan=_plan_view(plan),
        tasks=_polling_task_views(plan, record),
    )

    job = _DEMO_JOBS.get(workflow_id)
    answer_job = job if job is not None else {"owner_user_id": user["id"], "goal": ""}
    answer_job["response"] = response
    answer_job["stage"] = "FINISHED"
    answer_job["message"] = response.summary
    answer_job["cancel_requested"] = True
    if job is not None:
        _append_job_event(job, "FINISHED")
    _keep_demo_task(asyncio.create_task(_attach_answer(answer_job, workflow_id, goal=answer_job.get("goal") or "")))
    return response


@router.post(
    "/workflows/demo/{workflow_id}/payment-decision",
    response_model=DemoWorkflowResponse,
)
async def decide_demo_payment(
    workflow_id: str,
    request: DemoPaymentDecisionRequest,
    user: dict = Depends(get_current_user),
) -> DemoWorkflowResponse:
    """Duyệt hoặc từ chối thanh toán cho một workflow đang chờ.

    Toàn bộ ngữ cảnh đọc từ PostgreSQL, nên endpoint này vẫn hoạt động sau khi
    backend restart và `_DEMO_JOBS` đã trống.
    """
    # Response đang cache trong `_DEMO_JOBS` được dựng lúc workflow còn chờ
    # duyệt. Sau quyết định, nó là ảnh cũ: nếu không bỏ đi, mọi lần poll tiếp
    # theo vẫn trả "chờ xác nhận" dù database đã ghi SUCCESS, và giao diện mắc
    # kẹt vĩnh viễn ở màn chờ. Bỏ cache để GET đọc lại trạng thái đã lưu.
    # Kiểm quyền TRƯỚC khi đọc báo giá hoặc trạng thái. Đọc trước rồi mới kiểm
    # sẽ tạo side-channel: thông báo lỗi và thời gian phản hồi khác nhau tuỳ
    # workflow của người khác đang ở trạng thái nào.
    await _require_workflow_owner(workflow_id, user)

    job = _DEMO_JOBS.get(workflow_id)
    if job is not None:
        job["response"] = None

    try:
        if request.decision == "reject":
            await reject_payment(workflow_id)
            response = DemoWorkflowResponse(
                workflow_id=workflow_id,
                status="FAILED",
                summary=(
                    "Mình đã huỷ bước thanh toán. Chỗ đỗ xe vẫn được giữ ở trạng thái "
                    "chưa thanh toán, bạn có thể quay lại thanh toán sau."
                ),
            )
            if job is not None:
                _append_job_event(job, "FINISHED")
                job["message"] = response.summary
            return response

        outcome = await resume_payment_after_approval(
            workflow_id,
            payment_url=get_settings().payment_service_url,
        )
    except ResumeError as exc:
        # Message của ResumeError được viết sẵn cho người dùng cuối: không chứa
        # SQL, payload hay tên bảng.
        status_code = 404 if exc.code == "NOT_FOUND" else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    result = outcome["result"]
    quote = outcome["quote"]
    if not result.success:
        return DemoWorkflowResponse(
            workflow_id=workflow_id,
            status="EXECUTION_ERROR",
            summary="Thanh toán chưa thực hiện được. Bạn thử lại giúp mình nhé.",
        )

    amount = f"{quote.amount:,.0f}".replace(",", ".")
    response = DemoWorkflowResponse(
        workflow_id=workflow_id,
        status="SUCCESS",
        summary=f"Đã thanh toán {amount} {quote.currency}. Chỗ đỗ xe của bạn đã được xác nhận.",
    )
    if job is not None:
        _append_job_event(job, "FINISHED")
        job["message"] = response.summary
        job["response"] = response

    # Duyệt thanh toán là ĐIỂM DỪNG người dùng chú ý nhất — họ vừa đồng ý trả
    # tiền. KHÔNG đặt trong `if job is not None`: sau một lần restart,
    # `_DEMO_JOBS` trống, nên đúng những workflow sống sót qua restart lại là
    # những workflow không bao giờ có câu trả lời cho trạng thái cuối.
    #
    # Response trả NGAY cho người vừa bấm nút; câu trả lời tới ở lượt poll kế
    # tiếp, cùng đường với background job.
    _keep_demo_task(
        asyncio.create_task(
            _attach_answer(job if job is not None else {}, workflow_id, goal=(job or {}).get("goal") or "")
        )
    )
    return response


# Trạng thái nào thuộc nhóm nào trên giao diện tổng quan.
_ACTIVE_STATUSES = ("PENDING", "RUNNING")
_ATTENTION_STATUSES = ("WAITING_APPROVAL",)
_COMPLETED_STATUSES = ("SUCCESS", "FAILED", "CANCELLED")

_LIST_FILTERS: dict[str, tuple[str, ...]] = {
    "active": _ACTIVE_STATUSES + _ATTENTION_STATUSES,
    "running": _ACTIVE_STATUSES,
    "attention": _ATTENTION_STATUSES,
    "completed": _COMPLETED_STATUSES,
    "all": (),
}

_LIST_LIMIT_MAX = 50


def _assistant_fields(row: Any) -> dict[str, Any]:
    """Câu trả lời đã ghi, ở dạng dùng được cho danh sách.

    Chỉ trả câu viết cho ĐÚNG trạng thái hiện tại của workflow. Một câu viết
    cho WAITING_APPROVAL mà workflow đã SUCCESS thì đã lỗi thời — hiển thị
    lại nó nghĩa là nói với người dùng rằng vẫn đang chờ, trong khi tiền đã
    thu xong.

    Trả kèm trong DANH SÁCH, không bắt gọi thêm một request cho từng
    workflow: màn hình chính dựng lại vài cuộc hội thoại cùng lúc, và N+1 ở
    đó là N+1 người dùng phải ngồi chờ.
    """
    if row.get("assistant_for_status") != row.get("status"):
        return {"answer": None, "suggestions": [], "response_state": None}
    raw = row.get("assistant_suggestions")
    return {
        "answer": row.get("assistant_answer"),
        "suggestions": json.loads(raw) if isinstance(raw, str) else list(raw or []),
        "response_state": row.get("assistant_response_state"),
    }


def _goal_to_title(goal: str | None) -> str:
    """Tiêu đề ngắn cho danh sách.

    Goal là câu người dùng nhập; cắt ngắn để danh sách đọc được, KHÔNG diễn
    giải lại hay đoán ý.
    """
    text = (goal or "").strip()
    if not text:
        return "Yêu cầu dịch vụ"
    return text if len(text) <= 70 else text[:69].rstrip() + "…"


@router.get("/workflows/demo", response_model=DemoWorkflowListResponse)
async def list_demo_workflows(
    status: str = "active",
    limit: int = 20,
    session_id: str | None = None,
    user: dict = Depends(get_current_user),
) -> DemoWorkflowListResponse:
    """Danh sách workflow cho màn Tổng quan — đọc thẳng PostgreSQL.

    GIỚI HẠN DEMO, ĐỌC KỸ: `workflows` hiện KHÔNG có cột chủ sở hữu, nên
    endpoint này mặc định trả workflow của TOÀN BỘ hệ thống — chỉ an toàn cho
    demo một người. Trước khi có auth thật và một cột account/resident trên
    `workflows`, đây KHÔNG phải endpoint an toàn cho production.

    Session scope: khi truyền `session_id` (demo.html truyền `state.sessionId`),
    endpoint lọc về đúng thread của session đó — chặn đọc workflow của người
    khác qua tổng quan. Không truyền → giữ hành vi cũ (demo single-user).

    Bù lại, nó không trả bất kỳ dữ liệu cá nhân nào: chỉ id, tiêu đề cắt từ
    goal, trạng thái, số bước và mốc thời gian.
    """
    if status not in _LIST_FILTERS:
        raise HTTPException(status_code=422, detail="Bộ lọc trạng thái không hợp lệ.")
    if limit < 1 or limit > _LIST_LIMIT_MAX:
        raise HTTPException(status_code=422, detail="Giới hạn số dòng không hợp lệ.")

    # Lazy zombie sweep (Phase B): poll overview là trigger dọn workflow mồ côi.
    # Live workflow trong `_DEMO_JOBS` không bị sweep.
    await sweep_zombie_workflows(live_ids=set(_DEMO_JOBS))

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        if session_id:
            # Session phải thuộc chính user này. `session_id` là giá trị client
            # biết và gửi lại được, nên nó không phải bằng chứng về quyền. Lọc
            # trong SQL, không đọc hết rồi lọc ở Python.
            rows = await repository.list_workflows_by_session(session_id, owner_user_id=user["id"])
        else:
            statuses = _LIST_FILTERS[status] or None
            rows = await repository.list_workflows(statuses=statuses, limit=limit, owner_user_id=user["id"])
        step_tools = await repository.current_step_titles([str(row["workflow_id"]) for row in rows])
    finally:
        await pool.close()

    items = []
    for row in rows:
        workflow_id = str(row["workflow_id"])
        tool = step_tools.get(workflow_id)
        items.append(
            DemoWorkflowListItem(
                workflow_id=workflow_id,
                title=_goal_to_title(row.get("goal")),
                status=row["status"],
                # Tên bước hiện tại lấy từ bảng trình bày nghiệp vụ, không phải tên tool.
                current_step=_TOOL_PRESENTATION.get(tool, (None, ""))[0] if tool else None,
                completed_tasks=int(row.get("completed_tasks") or 0),
                total_tasks=int(row.get("total_tasks") or 0),
                needs_attention=row["status"] in _ATTENTION_STATUSES,
                created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                updated_at=row["updated_at"].isoformat() if row.get("updated_at") else None,
                goal=row.get("goal"),
                **_assistant_fields(row),
            )
        )
    return DemoWorkflowListResponse(items=items)


# =====================================================================
# API workflow của nhánh Hoàng Anh (`src/api/**` thuộc quyền anh ấy).
#
# Khôi phục sau khi đổi nền tích hợp sang gate2: giải xung đột routes.py
# về phía gate2 đã làm rơi bốn endpoint này và 20 test của chúng chuyển
# thành 404.
#
# LƯU Ý CHO PHASE C: đây là API workflow THỨ HAI, song song với
# `/workflows/demo` của gate2. Hai thiết kế cho cùng một việc. Phase C
# (canonical `/api/v1/workflows`) phải hợp nhất chúng — giữ cả hai lâu dài
# là để hai nguồn sự thật cùng ghi vào một bảng.
# =====================================================================


# ---------------------------------------------------------------------------
# Trust boundary pay_fee — cưỡng chế ở tầng API.
# ---------------------------------------------------------------------------
#
# `Planner._reject_untrusted_payment_values` chỉ chạy khi plan do LLM sinh.
# Plan chỉnh sửa trên review canvas (hoặc build thủ công) đi qua
# `/workflow/start` (có tasks) và `/workflow/{id}/execute` — những chỗ đó KHÔNG
# qua Planner, chỉ qua TaskPlanValidator (kiểm đủ input + InputRef∈depends_on,
# không kiểm provenance). Guard dưới đây mirror `_check_single_booking_provenance`:
# mọi pay_fee phải có booking_id/amount/currency là InputRef trỏ tới CÙNG MỘT
# task book_parking, `.field` khớp tên input. Ngăn "thanh toán 1 đồng" tự khai.

_PAYMENT_FIELDS = ("booking_id", "amount", "currency")


def _reject_untrusted_pay_fee(plan: TaskPlan) -> None:
    """Chặn pay_fee dùng giá trị không đến từ book_parking (trust boundary).

    Message lỗi chỉ nêu tên field/tool — tập cố định, không echo giá trị.
    """
    tasks_by_id = {task.task_id: task for task in plan.tasks}

    for task in plan.tasks:
        if task.tool != "pay_fee":
            continue

        for name in _PAYMENT_FIELDS:
            value = task.input.get(name)
            if not isinstance(value, InputRef) or value.field != name:
                raise ValueError(f"pay_fee '{name}' phải lấy từ book_parking (InputRef).")
            source = tasks_by_id.get(value.from_task)
            if source is None or source.tool != "book_parking":
                raise ValueError(f"pay_fee '{name}' phải trỏ tới task book_parking.")

        if len({task.input[name].from_task for name in _PAYMENT_FIELDS}) != 1:
            raise ValueError("pay_fee booking_id/amount/currency phải lấy từ cùng một book_parking.")


def _validate_plan(plan: TaskPlan) -> None:
    """TaskPlanValidator + trust boundary pay_fee — chung cho start/execute."""
    try:
        TaskPlanValidator.validate(plan)
        _reject_untrusted_pay_fee(plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


# ---------------------------------------------------------------------------
# Workflow API — review-ai-plan (Direction 2)
# ---------------------------------------------------------------------------


async def _persist_draft(repository: Any, goal: str, plan: TaskPlan) -> dict:
    """Tạo workflow PENDING với task_plan đã persist, trả payload draft."""
    workflow_id = await repository.create_workflow(
        {
            "id": str(uuid.uuid4()),
            "goal": goal,
            "status": "PENDING",
            "task_plan": plan,
        }
    )
    return {
        "workflow_id": workflow_id,
        "status": "PENDING",
        "plan": plan.model_dump(mode="json"),
    }


# API workflow legacy `/workflow/*` và `GET /workflows` ĐÃ BỊ XOÁ — Phase C.
#
# Bốn route đó (`/workflow/start`, `/workflow/{id}/status`,
# `/workflow/{id}/execute`, `GET /workflows`) là một đường vòng hoàn chỉnh qua
# guard Phase B: chúng có `Depends(get_current_user)` nên trông như đã được bảo
# vệ, nhưng KHÔNG kiểm chủ sở hữu — `/status` đọc workflow của bất kỳ ai, và
# `GET /workflows` liệt kê workflow của toàn hệ thống. Đã tái hiện được cả hai.
#
# Chúng cũng nhận `tasks` do browser dựng, tức là cho client tự viết TaskPlan —
# trái với mô hình "LLM đề xuất, code quyết định".
#
# Đường chính thức là `/workflows/demo/*`: có auth, có owner, có session, có
# clarification và payment approval. React đã chuyển sang đó.
#
# `_reject_untrusted_pay_fee` phía trên được GIỮ LẠI: nó mã hoá luật provenance
# "pay_fee phải lấy booking_id/amount/currency từ cùng một book_parking", và
# luật đó vẫn đúng cho mọi plan. Coverage của nó chuyển sang unit test.
