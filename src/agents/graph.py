"""LangGraph orchestration cho tầng Quyết định.

Luồng planner graph:

    START → plan ─┬─ NEEDS_INFORMATION ──────────→ END   (trả câu hỏi)
                  ├─ planning_error ─────────────→ END
                  └─ READY → validate ─┬─ invalid → END   (KHÔNG thực thi)
                                       └─ valid → execute → END

Ranh giới ba tầng:
  - Planner (LLM) chỉ ĐỀ XUẤT kế hoạch.
  - TaskPlanValidator (deterministic) quyết định plan có được chạy hay không.
    Không nhánh READY nào được đi tới execute mà bỏ qua Validator.
  - Execution boundary thực thi. Graph KHÔNG import Executor/Connector/DB —
    boundary được inject, nên unit test dùng fake và production truyền
    Executor thật vào.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)

from src.agents.nodes.example_node import analyze_node, respond_node
from src.agents.planner import Planner, PlannerError, build_question
from src.agents.state import AgentState
from src.agents.validator import MissingRequiredInputError, TaskPlanValidator
from src.common.policy import PolicyInterruptionError
from src.common.results import StandardResult
from src.common.task_plan import InputRef, Task, TaskPlan


def _ensure_payment_is_offered(plan: Any) -> None:
    """Đặt chỗ đỗ xe có phí thì luôn phải hỏi người dùng, không tuỳ Planner.

    Sự cố thật: goal "đăng ký ô tô và đặt chỗ đỗ xe" (không có chữ "thanh
    toán") ra plan 3 bước KHÔNG có `pay_fee`. Chỗ đỗ được giữ thật, phí 150.000
    VND phát sinh thật, workflow báo SUCCESS — và người dùng không hề được hỏi
    có trả hay không, cũng không biết mình đang nợ một khoản.

    Vấn đề không phải một bước bị thiếu. Cổng duyệt thanh toán là cơ chế bảo vệ
    duy nhất đứng giữa người dùng và tiền của họ, mà nó chỉ hoạt động khi
    Planner tình cờ nghĩ ra `pay_fee`. Một cơ chế bảo vệ phụ thuộc vào cách LLM
    diễn đạt thì không phải cơ chế bảo vệ — vậy nên nó được ghép vào ở đây,
    bằng code.

    Thêm `pay_fee` KHÔNG có nghĩa là thu tiền: nó đưa workflow tới
    WAITING_APPROVAL và người dùng vẫn phải bấm duyệt. Không đồng nào đi trước
    khi họ đồng ý — chỉ là bây giờ họ được hỏi.

    Ba field lấy bằng InputRef từ chính bước đặt chỗ, đúng quy tắc đang có:
    báo giá là dữ liệu authoritative của provider, không phải thứ LLM hay người
    dùng khai.
    """
    tasks = list(getattr(plan, "tasks", ()) or ())
    if not tasks:
        return

    def _refers_to(task: Any, booking_task_id: str) -> bool:
        for value in task.input.values():
            if getattr(value, "from_task", None) == booking_task_id:
                return True
            if isinstance(value, dict) and value.get("from_task") == booking_task_id:
                return True
        return False

    used_ids = {task.task_id for task in tasks}
    for booking in [task for task in tasks if task.tool == "book_parking"]:
        if any(task.tool == "pay_fee" and _refers_to(task, booking.task_id) for task in tasks):
            continue
        index = len(used_ids) + 1
        while f"T{index}" in used_ids:
            index += 1
        new_id = f"T{index}"
        used_ids.add(new_id)
        plan.tasks.append(
            Task(
                task_id=new_id,
                tool="pay_fee",
                depends_on=[booking.task_id],
                input={
                    field: InputRef(field=field, from_task=booking.task_id)
                    for field in ("booking_id", "amount", "currency")
                },
            )
        )


def _inject_trusted_identity(plan: Any, existing_context: dict[str, Any]) -> None:
    """Điền `resident_id` cho `register_vehicle` từ trusted context nếu LLM bỏ sót.

    `resident_id` là dữ liệu HỆ THỐNG: backend dựng nó từ `user_resident_links`
    VERIFIED, không phải thứ LLM (hay người dùng) khai. Prompt Planner đã yêu cầu
    lấy từ existing_context (nguồn 2), nhưng nếu LLM vẫn trả plan thiếu field này,
    Validator sẽ hạ READY xuống NEEDS_INFORMATION — và vì resident_id là field nội
    bộ, graph không thể hỏi người dùng, phải trả câu "chưa đủ cơ sở" khó hiểu.

    Code điền literal từ context thay thế, đúng tinh thần `_ensure_payment_is_offered`
    và `_apply_user_answers`: sửa plan bằng code, không tin LLM.

    Chỉ điền khi THIẾU (None/rỗng) và không ghi đè InputRef — nếu LLM đã trỏ
    `resident_id` sang task khác, plan sai chỗ khác, không vá ở đây.
    """
    resident_id = existing_context.get("resident_id")
    if not resident_id:
        return
    for task in getattr(plan, "tasks", ()) or ():
        if getattr(task, "tool", None) != "register_vehicle":
            continue
        current = task.input.get("resident_id")
        is_reference = isinstance(current, dict) and "from_task" in current
        is_reference = is_reference or getattr(current, "from_task", None) is not None
        if not is_reference and not current:
            task.input["resident_id"] = resident_id


def _apply_user_answers(plan: Any, user_answers: dict[str, Any]) -> None:
    """Ép giá trị người dùng VỪA trả lời đè lên giá trị Planner suy từ goal.

    Sự cố thật: khung 12:30 đã kín, hệ thống hỏi lại, người dùng đáp "13h".
    Workflow con giữ nguyên goal cũ — trong đó vẫn ghi "lúc 12:30" — còn 13:00
    chỉ nằm trong context. Planner đọc thấy hai giá trị mâu thuẫn và chọn cái
    viết trong đề bài, nên lượt chạy lại hỏng y hệt lượt trước. Người dùng thấy
    câu trả lời của mình bị bỏ qua.

    Câu trả lời tường minh của người dùng có thẩm quyền cao hơn văn bản goal cũ:
    goal là điều họ nói LÚC ĐẦU, `user_answers` là điều họ nói SAU KHI biết
    lựa chọn đầu không dùng được.

    Hai giới hạn cố ý:
      - chỉ đè khi task ĐÃ CÓ field đó, tức Planner cũng cho rằng nó thuộc về
        bước này. Thêm field mới là sửa kế hoạch, không phải sửa giá trị.
      - không đụng vào InputRef (dict trỏ sang task khác). Ghi đè một reference
        bằng literal sẽ cắt đứt dây chuyền dữ liệu giữa các bước.
    """
    if not user_answers or plan is None:
        return
    for task in getattr(plan, "tasks", ()):
        for field, value in user_answers.items():
            if field not in task.input:
                continue
            current = task.input[field]
            # InputRef là model Pydantic, KHÔNG phải dict — kiểm bằng
            # `isinstance(..., dict)` sẽ hụt và ghi đè mất reference. Kiểm theo
            # `from_task` để bắt được cả dạng model lẫn dạng dict thô.
            is_reference = isinstance(current, dict) and "from_task" in current
            is_reference = is_reference or getattr(current, "from_task", None) is not None
            if not is_reference:
                task.input[field] = value


class ExecutionBoundary(Protocol):
    """Phần API của tầng thực thi mà graph cần.

    Khớp chữ ký `Executor.execute()` của Mạnh Hiệp, nhưng khai báo dưới dạng
    Protocol để graph không phụ thuộc vào implementation — không import
    `src.executor`, `src.connectors`, `src.db` hay Mock API.
    """

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]: ...


StageCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


# Chỉ các field người dùng thực sự biết và có quyền cung cấp mới được đổi từ
# validation failure thành một lượt hỏi bổ sung. ID nội bộ và dữ liệu thanh
# toán authoritative tuyệt đối không nằm trong tập này.
_USER_PROVIDED_FIELDS: frozenset[str] = frozenset(
    {
        "transaction_type",
        "property_type",
        "residential_area",
        "max_price",
        "project_id",
        "viewing_date",
        "viewing_time",
        "tour_date",
        "passenger_count",
        "interest_type",
        "preferred_contact_time",
        "consent",
        "issue_type",
        "description",
        "location",
        "preferred_date",
        "preferred_time",
        "move_date",
        "move_time",
        "needs_elevator",
        "needs_loading_support",
        "move_vehicle",
        "full_name",
        "apartment_code",
        "plate_number",
        "vehicle_type",
        "booking_date",
        "parking_zone",
    }
)

_USER_FIELD_ORDER: tuple[str, ...] = (
    "project_id",
    "transaction_type",
    "property_type",
    "residential_area",
    "max_price",
    "viewing_date",
    "viewing_time",
    "tour_date",
    "passenger_count",
    "interest_type",
    "preferred_contact_time",
    "consent",
    "plate_number",
    "vehicle_type",
    "booking_date",
    "parking_zone",
    "issue_type",
    "description",
    "location",
    "preferred_date",
    "preferred_time",
    "move_date",
    "move_time",
    "needs_elevator",
    "needs_loading_support",
    "move_vehicle",
    "full_name",
    "apartment_code",
)


def _missing_fields_for_user(
    missing_fields: tuple[str, ...],
    existing_context: dict[str, Any],
) -> tuple[str, ...] | None:
    """Đổi field contract sang thông tin mà người dùng hiểu và có thể nhập.

    `vehicle_id` là ID nội bộ. Khi account đã có resident context nhưng chưa có
    phương tiện, người dùng chỉ cần cung cấp biển số và loại xe; Planner sẽ tạo
    bước register_vehicle rồi truyền ID bằng InputRef. `viewing_id` của
    book_shuttle cũng là ID nội bộ nhưng KHÔNG có thông tin thay thế để hỏi —
    nguồn duy nhất là output task schedule_property_viewing qua InputRef, nên
    thiếu là lỗi lập kế hoạch và rơi vào câu hỏi chung. Các ID nội bộ khác và
    dữ liệu thanh toán không được biến thành câu hỏi cho người dùng.
    """
    public_fields: list[str] = []
    for name in missing_fields:
        if name == "viewing_id":
            return None
        if name == "vehicle_id":
            if not existing_context.get("resident_id"):
                return None
            replacements = ("plate_number", "vehicle_type")
        elif name in _USER_PROVIDED_FIELDS:
            replacements = (name,)
        else:
            return None

        for replacement in replacements:
            if replacement not in public_fields:
                public_fields.append(replacement)

    ordered = [name for name in _USER_FIELD_ORDER if name in public_fields]
    return tuple(ordered) or None


# Khi không thể chuyển missing fields thành câu hỏi an toàn, người dùng nhận
# một câu chung. Không nêu field, tool, status hay lý do kỹ thuật.
CLARIFICATION_UNAVAILABLE_MESSAGE = (
    "Mình chưa đủ cơ sở để hỏi thêm cho yêu cầu này. Bạn mô tả lại cụ thể hơn giúp mình nhé."
)

# Thiếu `resident_id` KHÔNG phải "không hỏi được", mà là "chưa đủ điều kiện".
#
# `resident_id` không nằm trong `_USER_PROVIDED_FIELDS` — đúng, không ai được
# hỏi người dùng một ID nội bộ. Nhưng vì thế nó rơi vào nhánh từ chối chung, và
# một khách chưa liên kết căn hộ hỏi "đăng ký ô tô, đặt chỗ đỗ xe" nhận được
# "Mình chưa đủ cơ sở để hỏi thêm… Bạn mô tả lại cụ thể hơn" — một câu đổ lỗi
# cho cách họ diễn đạt, trong khi mô tả của họ hoàn toàn rõ ràng. Họ sẽ viết
# lại, rõ hơn nữa, và nhận đúng câu đó lần nữa.
#
# Thiếu `resident_id` chỉ có MỘT nghĩa: tài khoản chưa có liên kết cư dân đã
# xác minh. Nói thẳng ra, kèm việc cần làm.
RESIDENT_LINK_REQUIRED_MESSAGE = (
    "Các dịch vụ này chỉ dành cho cư dân đã xác minh căn hộ. "
    "Bạn vào mục Xác minh căn hộ, gửi mã căn hộ kèm ảnh giấy tờ để ban quản lý duyệt, "
    "rồi quay lại nhé. Trong lúc chờ, mình vẫn giúp bạn đặt lịch tham quan hoặc "
    "đăng ký nhận tư vấn được."
)


def needs_information_update(
    missing_fields: tuple[str, ...] | None,
    existing_context: dict[str, Any],
) -> dict:
    """Nguồn sự thật DUY NHẤT biến missing fields thành state hướng ra người dùng.

    Cả hai đường vào NEEDS_INFORMATION đều phải đi qua đây:

    1. Planner trả NEEDS_INFORMATION trực tiếp (`plan_node`).
    2. Validator hạ READY xuống vì thiếu input (`validate_node`).

    Nếu chỉ chuẩn hoá ở một nhánh thì nhánh kia vẫn đẩy được ID nội bộ
    (`resident_id`, `vehicle_id`, `booking_id`) hay dữ liệu thanh toán
    (`amount`, `currency`) ra UI. Prompt không được dùng làm lớp chặn duy nhất.

    `question` luôn được dựng lại từ danh sách đã chuẩn hoá, không tái sử dụng
    câu hỏi do LLM sinh ra: câu đó có thể nhắc tên field nội bộ ngay cả khi
    danh sách field đã sạch.
    """
    public_fields = _missing_fields_for_user(tuple(missing_fields or ()), existing_context)
    if public_fields is None:
        # Không hỏi được thì từ chối an toàn, không render form rỗng cho user.
        #
        # Nhưng phân biệt HAI lý do khác nhau: "thiếu một thứ không được phép
        # hỏi" và "tài khoản chưa đủ điều kiện dùng dịch vụ". Gộp chúng vào một
        # câu khiến trường hợp thứ hai — trường hợp phổ biến hơn hẳn — nhận một
        # lời khuyên vô dụng.
        needs_link = "resident_id" in (missing_fields or ()) and not existing_context.get("resident_id")
        return {
            "clarification_error": (
                RESIDENT_LINK_REQUIRED_MESSAGE if needs_link else CLARIFICATION_UNAVAILABLE_MESSAGE
            ),
            "plan_validated": False,
        }

    return {
        "planner_status": "NEEDS_INFORMATION",
        "missing_fields": public_fields,
        "question": build_question(public_fields),
        "plan_validated": False,
    }


# ---------------------------------------------------------------------------
# Legacy graph mẫu — giữ nguyên hành vi.
# `src/api/routes.py` và `tests/test_agents/test_graph.py` đang import `agent`.
# Không gộp vào planner graph: planner graph cần LLM, còn `agent` phải import
# được mà không cần API key.
# ---------------------------------------------------------------------------


def should_continue(state: AgentState) -> str:
    """Route based on whether an error occurred during analysis."""
    if state.get("error"):
        return END
    return "respond"


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("analyze", analyze_node)
    graph.add_node("respond", respond_node)

    # Add edges
    graph.set_entry_point("analyze")
    graph.add_conditional_edges("analyze", should_continue)
    graph.add_edge("respond", END)

    return graph.compile()


agent = build_graph()


# ---------------------------------------------------------------------------
# Planner graph
# ---------------------------------------------------------------------------


def _may_execute(state: AgentState) -> bool:
    """Plan đã có bằng chứng DƯƠNG là được Validator chấp nhận hay chưa.

    Không dùng "vắng mặt `validation_error`" làm điều kiện: một state chưa bao
    giờ đi qua `validate` cũng không có `validation_error`. Nếu topology bị nối
    nhầm `plan → execute`, điều kiện phủ định sẽ cho plan chạy thẳng.

    `route_after_validate` và `execute_node` dùng chung hàm này để không thể
    lệch nhau khi ai đó sửa một chỗ mà quên chỗ kia.
    """
    if state.get("validation_error"):
        return False
    if state.get("plan_validated") is not True:
        return False
    return state.get("plan") is not None


def build_planner_graph(
    planner: Planner,
    execution_boundary: ExecutionBoundary,
    on_stage: StageCallback | None = None,
    *,
    parent_workflow_id: str | None = None,
    session_id: str | None = None,
) -> StateGraph:
    """Dựng graph Planner → Validator → Execution.

    Cả `planner` lẫn `execution_boundary` đều được inject, nên hàm này không
    đọc API key, không tạo `ChatOpenAI` và không chạm tầng thực thi thật.
    """

    async def emit(stage: str, payload: dict[str, Any] | None = None) -> None:
        """Phát trạng thái quan sát an toàn; lỗi UI không được làm hỏng workflow."""
        if on_stage is None:
            return
        try:
            await on_stage(stage, payload or {})
        except Exception:  # noqa: BLE001 - callback quan sát không thuộc critical path
            return

    async def plan_node(state: AgentState) -> dict:
        """Gọi Planner. Không log goal hay existing_context."""
        await emit("PLANNING")
        try:
            result = await planner.plan(
                state.get("goal", ""),
                state.get("existing_context", {}),
                recalled=state.get("recalled") or None,
            )
        except PlannerError as exc:
            # `PlannerError` được thiết kế để message luôn an toàn: chỉ mô tả
            # chung và tên loại exception, không echo goal/context/LLM output.
            return {"planning_error": str(exc), "plan_validated": False}
        except Exception as exc:  # noqa: BLE001 — lỗi ngoài dự kiến
            # Exception khác chưa chắc an toàn — chỉ giữ tên loại.
            return {
                "planning_error": f"Planner lỗi không mong đợi ({type(exc).__name__}).",
                "plan_validated": False,
            }

        if result.is_ready:
            _apply_user_answers(result.plan, state.get("user_answers") or {})
            _inject_trusted_identity(result.plan, state.get("existing_context", {}))
            _ensure_payment_is_offered(result.plan)
            # READY: không đặt `question` — không có gì để hỏi người dùng.
            # `plan_validated=False` ghi đè mọi giá trị caller truyền vào initial
            # state: chỉ `validate_node` mới có quyền đặt cờ này thành True.
            await emit("PLANNED", {"plan": result.plan})
            return {
                "planner_status": "READY",
                "plan": result.plan,
                "missing_fields": (),
                "plan_validated": False,
            }

        if result.status == "QUESTION":
            # Câu hỏi, không phải việc cần làm. Dừng ở đây: không plan, không
            # missing_fields, không thực thi gì.
            #
            # KHÔNG đặt `question`: đó là câu HỎI LẠI người dùng, dựng từ
            # `missing_fields`. Ở đây ta không hỏi lại gì cả — ta trả lời. Câu
            # trả lời do Response Agent viết ở tầng trên, từ dữ liệu nó đã có
            # (danh mục quyền theo tài khoản, ngày hôm nay).
            await emit("QUESTION")
            return {
                "planner_status": "QUESTION",
                "missing_fields": (),
                "plan_validated": False,
            }

        # NEEDS_INFORMATION: không đưa `plan` vào state, tránh mọi khả năng một
        # nhánh sau này nhặt được plan chưa tồn tại.
        #
        # `result.missing_fields` và `result.question` là output LLM, KHÔNG được
        # trả thẳng ra ngoài. Planner._clean_missing_fields() đã lọc một lượt,
        # nhưng Graph vẫn phải tự bảo vệ: đây là ranh giới cuối trước UI.
        update = needs_information_update(
            result.missing_fields,
            state.get("existing_context", {}),
        )
        if update.get("clarification_error"):
            await emit("VALIDATION_FAILED")
        else:
            await emit("NEEDS_INFORMATION", {"question": update["question"]})
        return update

    async def validate_node(state: AgentState) -> dict:
        """Cổng deterministic duy nhất trước khi thực thi."""
        await emit("VALIDATING")
        plan = state.get("plan")

        # Phòng thủ: routing đã đảm bảo điều này, nhưng nếu ai đó nối lại cạnh
        # sai thì phải chặn ở đây chứ không được rơi xuống execute.
        if state.get("planner_status") != "READY" or plan is None:
            return {
                "validation_error": "Không có kế hoạch hợp lệ để kiểm tra.",
                "plan_validated": False,
            }

        try:
            TaskPlanValidator.validate(plan)
        except MissingRequiredInputError as exc:
            # Dùng chung policy với `plan_node` — hai nhánh không thể lệch nhau.
            update = needs_information_update(
                exc.missing_fields,
                state.get("existing_context", {}),
            )
            if update.get("clarification_error"):
                await emit("VALIDATION_FAILED")
                # Hạ plan xuống draft ở cả nhánh này: plan đã trượt Validator
                # thì không được để lại trong `plan` cho bất kỳ cạnh nào nhặt.
                return {**update, "plan": None, "draft_plan": plan}

            await emit("NEEDS_INFORMATION", {"question": update["question"]})
            # Hạ plan xuống draft: chỉ để preview, execution guard không đọc.
            return {**update, "plan": None, "draft_plan": plan}
        except ValueError as exc:
            # Message của Validator chỉ nêu vị trí vi phạm và tên pattern khớp,
            # không echo giá trị nhạy cảm — an toàn để đưa vào state.
            # Plan sai KHÔNG được sửa hay "chữa": chỉ từ chối.
            await emit("VALIDATION_FAILED")
            return {"validation_error": str(exc), "plan_validated": False}

        # Đây là chỗ DUY NHẤT đặt `plan_validated=True`.
        # Giữ nguyên canonical plan, không thay thế bằng bản sao.
        await emit("VALIDATED")
        return {"plan_validated": True}

    async def execute_node(state: AgentState) -> dict:
        """Gọi execution boundary đã được inject."""
        plan = state.get("plan")

        # Phòng thủ: đòi bằng chứng DƯƠNG đã qua Validator, không chỉ dựa vào
        # việc `validation_error` vắng mặt.
        if not _may_execute(state):
            return {"execution_error": "Không có kế hoạch đã được kiểm tra để thực thi."}

        await emit("EXECUTING")
        try:
            workflow_id, task_results = await execution_boundary.execute(
                plan,
                state.get("workflow_id"),
                parent_workflow_id=parent_workflow_id or state.get("parent_workflow_id"),
                session_id=session_id or state.get("session_id"),
            )
        except PolicyInterruptionError as exc:
            # Policy guard deterministic (quyền cư dân, duyệt thanh toán) chạy
            # TRƯỚC executor thật, nên khi tới đây chưa có lời gọi dịch vụ nào
            # cho phần bị chặn.
            #
            # Giữ nguyên workflow_id và partial_results: bỏ chúng đi thì tầng
            # API không có báo giá để hiển thị, và người dùng bị hỏi có đồng ý
            # trả một khoản tiền mà họ không nhìn thấy.
            # Chờ người dùng duyệt không phải lỗi thực thi. Ghi nó thành lỗi
            # khiến UI vừa hiện báo giá vừa nói workflow đã dừng giữa chừng.
            if exc.code in {"PAYMENT_APPROVAL_REQUIRED", "VIEWING_APPROVAL_REQUIRED"}:
                await emit("WAITING_APPROVAL")
            else:
                await emit("EXECUTION_FAILED")
            update: dict = {"policy_error": exc.code}
            if exc.workflow_id is not None:
                update["workflow_id"] = exc.workflow_id
            if exc.partial_results:
                update["task_results"] = exc.partial_results
            if exc.context:
                update["policy_context"] = exc.context
            return update
        except Exception as exc:  # noqa: BLE001 — không để raw exception thoát ra UI
            # Chỉ giữ tên loại: message của tầng thực thi có thể chứa payload,
            # connection string hay dữ liệu người dùng.
            await emit("EXECUTION_FAILED")
            return {"execution_error": f"Thực thi thất bại ({type(exc).__name__})."}

        await emit("FINISHED")
        return {"workflow_id": workflow_id, "task_results": task_results}

    def route_after_plan(state: AgentState) -> str:
        if state.get("planning_error"):
            return END
        if state.get("planner_status") != "READY":
            return END
        return "validate"

    def route_after_validate(state: AgentState) -> str:
        # Cùng predicate với `execute_node` — routing và node không thể lệch nhau.
        return "execute" if _may_execute(state) else END

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("validate", validate_node)
    graph.add_node("execute", execute_node)

    graph.set_entry_point("plan")
    graph.add_conditional_edges("plan", route_after_plan, {"validate": "validate", END: END})
    graph.add_conditional_edges("validate", route_after_validate, {"execute": "execute", END: END})
    graph.add_edge("execute", END)

    return graph.compile()
