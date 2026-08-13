from __future__ import annotations

from typing import Any, TypedDict

from src.agents.planner import PlannerStatus
from src.common.results import StandardResult
from src.common.task_plan import TaskPlan


class AgentState(TypedDict, total=False):
    """State schema cho LangGraph agent.

    Mỗi node đọc và ghi vào state này.
    total=False cho phép tất cả fields là optional.

    Không định nghĩa lại shared schema: `TaskPlan` và `StandardResult` được
    import từ `src/common/`. Không có `ExecutionResult` — kết quả thực thi đi
    qua `task_results: dict[task_id, StandardResult]`.
    """

    # --- Legacy: graph mẫu trong graph.py và src/api/routes.py vẫn dùng ------
    query: str
    context: str
    analysis: str
    response: str
    error: str
    metadata: dict

    # --- Input của planner graph --------------------------------------------
    goal: str
    existing_context: dict[str, Any]

    # --- Kết quả bước Planner -----------------------------------------------
    planner_status: PlannerStatus
    plan: TaskPlan
    # Kế hoạch LLM đã nhận diện nhưng còn thiếu input. Chỉ dùng để preview;
    # execution guard tuyệt đối không đọc field này.
    draft_plan: TaskPlan
    missing_fields: tuple[str, ...]
    question: str
    # Message an toàn: không chứa goal, context hay raw LLM response.
    planning_error: str
    # Có giá trị nghĩa là còn thiếu input NHƯNG không hỏi người dùng được một
    # cách an toàn (ID nội bộ, dữ liệu thanh toán, field lạ). Khác
    # NEEDS_INFORMATION ở chỗ không có form nào để render: phải từ chối an toàn.
    clarification_error: str

    # --- Kết quả bước Validator ---------------------------------------------
    # Có giá trị nghĩa là plan bị từ chối và KHÔNG được thực thi.
    validation_error: str
    # Bằng chứng DƯƠNG rằng `TaskPlanValidator` đã chạy và chấp nhận plan.
    # Vắng mặt `validation_error` là chưa đủ: nếu topology bị nối nhầm
    # plan → execute thì state sẽ không có lỗi nào mà cũng chưa hề validate.
    # Chỉ `plan_validated is True` mới cho phép thực thi.
    plan_validated: bool

    # --- Kết quả bước Execution ---------------------------------------------
    workflow_id: str
    task_results: dict[str, StandardResult]
    # Chỉ chứa tên loại exception, không chứa message gốc.
    execution_error: str
    # Dữ liệu phụ đã làm sạch kèm theo policy guard (ví dụ báo giá thanh toán).
    policy_context: dict[str, Any]
    # Mã ổn định của policy guard đã chặn (quyền cư dân, duyệt thanh toán).
    # Guard chạy trước executor thật nên khi field này có giá trị thì KHÔNG có
    # lời gọi dịch vụ nào đã xảy ra.
    policy_error: str
