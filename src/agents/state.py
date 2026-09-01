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
    # Ký ức hội thoại — các lượt hỏi–đáp TRƯỚC của cùng người dùng.
    #
    # PHẢI khai báo ở đây. `AgentState` là TypedDict và LangGraph bỏ IM LẶNG mọi
    # khoá không có trong schema: thiếu dòng này thì `recalled` vẫn truyền được
    # vào graph, vẫn không có lỗi nào, và Planner chỉ đơn giản không bao giờ
    # nhận được ký ức.
    #
    # KHÁC `existing_context`: đó là dữ kiện của lần này, còn đây là chuyện cũ.
    # Xem `Planner._fields_taken_from_recall` — chuyện cũ không được phép trở
    # thành hành động mà chưa ai xác nhận.
    recalled: list[dict[str, Any]]

    # Câu người dùng vừa trả lời cho câu hỏi bổ sung — PHẢI khai ở đây.
    #
    # `AgentState` là TypedDict của LangGraph, và LangGraph LOẠI BỎ mọi khoá
    # không được khai báo khi dựng state. Thiếu dòng này, `run_demo_workflow`
    # truyền `user_answers` vào `initial_state` một cách hoàn toàn hợp lệ, không
    # có lỗi nào ở đâu cả, và plan node đọc ra một dict RỖNG.
    #
    # Hệ quả nhìn thấy được: người dùng đáp "Khu B" sau khi Khu A hết chỗ,
    # backend nhận đúng câu trả lời (`answers=['parking_zone']`), nhưng Planner
    # lập lại kế hoạch từ goal cũ và đặt ZONE_A lần nữa — hỏng y hệt lượt trước.
    # Đo được bằng ba mốc log liền nhau:
    #     CONTINUE answers= ['parking_zone']
    #     RUNJOB    answers= ['parking_zone']
    #     APPLY_ANSWERS keys= []      ← mất ở đây
    user_answers: dict[str, Any]

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
    # Điều người dùng đã NÓI RÕ trong goal, do Planner trích ra và code kiểm
    # (`graph._facts_as_context()`) — PHẢI khai ở đây, cùng lý do `recalled`/
    # `user_answers`: LangGraph loại bỏ IM LẶNG mọi khoá node trả về mà
    # `AgentState` không khai báo. Thiếu dòng này, `plan_node` VẪN trả
    # `explicit_facts` trong dict cập nhật của MỌI nhánh (READY/QUESTION/
    # NEEDS_INFORMATION) mà không có lỗi nào — nhưng nó không bao giờ tới
    # `state` cuối cùng, và `api/routes.py` (đọc `state.get("explicit_facts")`
    # để ghim vào `existing_context` cho lượt hỏi lại sau) luôn nhận `{}`: một
    # xác nhận rõ ràng của người dùng ở lượt 1 ("cần thang máy") không bao giờ
    # được nhớ sang lượt 2.
    explicit_facts: dict[str, bool]
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
