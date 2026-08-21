"""WorkflowStateRepository Protocol cho P-118.

Owner: Mạnh Hiệp (interface), Hoàng Anh (implementation)
File: src/common/repository.py

Vai trò trong luồng:
  Executor gọi repository SAU MỖI task để persist state. Đây là điểm
  đảm bảo idempotency: nếu Executor crash giữa chừng, state đã được
  lưu cho tất cả task đã chạy → Replanner chỉ cần chạy lại các task
  chưa SUCCESS.

Tách interface / implementation:
  Interface (file này) → Mạnh Hiệp định nghĩa để Executor dùng.
  Implementation       → Hoàng Anh viết PostgreSQLWorkflowStateRepository.
  Test fake            → InMemoryWorkflowStateRepository (tests/fakes/).

  Nhờ dùng Protocol, Executor không cần import Postgres; unit test
  chạy hoàn toàn in-memory không cần DB thật.

Thứ tự Executor gọi repository trong một task:
  1. create_workflow()          – một lần khi bắt đầu workflow
  2. create_task() x N          – tạo record cho từng task trong plan
  3. update_workflow_status(RUNNING)
  4. (lặp theo từng task):
       update_task_status(RUNNING)
       update_task_status(SUCCESS|FAILED)
       save_task_result()
  5. update_workflow_status(SUCCESS|FAILED)  – kết thúc workflow
"""

from typing import Any, Protocol

from src.common.enums import TaskStatus, WorkflowStatus
from src.common.results import StandardResult


class WorkflowStateRepository(Protocol):
    """Protocol cho repository lưu trữ workflow state.

    Executor gọi interface này để persist state.
    Hoàng Anh implement PostgreSQLWorkflowStateRepository.

    Tất cả method đều async để tương thích với asyncpg / SQLAlchemy async.
    """

    async def create_workflow(self, workflow_data: dict) -> str:
        """Tạo workflow mới, trả về workflow_id.

        Gọi một lần duy nhất khi Executor bắt đầu thực thi TaskPlan.
        workflow_data phải có: {"id": str, "goal": str, "status": "PENDING"}.

        Returns:
            workflow_id: ID đã được persist (thường trùng với data["id"]).
        """
        ...

    async def update_workflow_status(
        self,
        workflow_id: str,
        status: WorkflowStatus,
    ) -> None:
        """Cập nhật trạng thái workflow.

        Executor gọi 2 lần:
          - update_workflow_status(RUNNING)  → sau khi tạo xong tất cả task record
          - update_workflow_status(SUCCESS|FAILED) → sau khi tất cả task hoàn thành
        """
        ...

    async def create_task(
        self,
        workflow_id: str,
        task_data: dict,
    ) -> None:
        """Tạo task trong workflow.

        Gọi N lần (N = số task trong TaskPlan) trước khi bắt đầu thực thi.
        task_data phải có: {"id", "tool", "depends_on", "input", "status": "PENDING"}.

        Tất cả task được tạo trước → đảm bảo có thể query đầy đủ trạng thái
        ngay cả khi một số task chưa chạy.
        """
        ...

    async def update_task_status(
        self,
        workflow_id: str,
        task_id: str,
        status: TaskStatus,
    ) -> None:
        """Cập nhật trạng thái task.

        Executor gọi 2 lần cho mỗi task:
          1. update_task_status(RUNNING)         – trước khi gọi Connector
          2. update_task_status(SUCCESS|FAILED)  – sau khi Connector trả kết quả
        """
        ...

    async def prepare_submission(self, workflow_id: str, task_id: str, *, candidate_key: str | None) -> Any:
        """Cấp phép gửi MỘT lần, hoặc từ chối. Ghi `SUBMITTING` trong cùng transaction.

        Nằm trong Protocol chứ không phải một tiện ích tuỳ chọn: Executor gọi nó
        NGAY TRƯỚC mỗi lời gọi connector và từ chối gửi khi không có phép. Một
        repository thiếu method này sẽ làm mọi lần gửi bị chặn — đúng hướng
        fail-closed, nhưng nó là lỗi lắp ráp và phải lộ ra ở đây.
        """
        ...

    async def record_submission_outcome(self, workflow_id: str, task_id: str, tool: str, result: Any) -> None:
        """Ghi kết luận sau MỘT lời gọi connector. Trạng thái cuối không bị viết đè."""
        ...

    async def save_task_result(
        self,
        workflow_id: str,
        task_id: str,
        result: StandardResult,
    ) -> None:
        """Lưu kết quả task (StandardResult).

        Gọi sau update_task_status(SUCCESS|FAILED), lưu toàn bộ
        StandardResult (success, data, error_code, message, retryable).

        data trong result là canonical output (đã lọc extra field) →
        Executor dùng để resolve InputRef của task sau.
        """
        ...

    async def get_workflow(self, workflow_id: str) -> dict | None:
        """Lấy thông tin workflow.

        Dùng trong:
          - Integration test để assert WorkflowStatus cuối cùng.
          - API layer (Hoàng Anh) để trả trạng thái cho frontend.
        """
        ...

    async def get_task(
        self,
        workflow_id: str,
        task_id: str,
    ) -> dict | None:
        """Lấy thông tin task.

        Dùng trong:
          - Integration test để assert TaskStatus và result từng task.
          - HITL UI (Hoàng Anh) để hiển thị chi tiết task cần phê duyệt.
        """
        ...

    async def list_tasks(self, workflow_id: str) -> list[dict]:
        """Liệt kê tất cả task của workflow.

        Trả về list dict, mỗi dict có: id, tool, status, result, ...
        Dùng cho dashboard hiển thị tiến độ workflow.
        """
        ...

    async def get_completed_task_ids(self, workflow_id: str) -> list[str]:
        """Lấy danh sách task_id đã SUCCESS (dùng cho Replanner).

        Replanner gọi method này để biết task nào đã xong → không tạo
        lại trong TaskPlan mới. Đây là cơ chế đảm bảo idempotency:
        task đã SUCCESS không bao giờ chạy lại.
        """
        ...

    async def list_workflows_by_session(self, session_id: str) -> list[dict]:
        """Lấy tất cả workflow cùng session_id, sắp xếp từ cũ đến mới.

        Dùng cho endpoint GET /workflows/demo/session/{session_id} để UI
        hiển thị lịch sử một cuộc hội thoại (chat thread).
        """
        ...

    async def save_repair_hints(self, workflow_id: str, hints: dict[str, dict]) -> None:
        """Persist repair hints sau khi workflow FAILED với lỗi repairable.

        hints: {task_id: {"error_code": str, "message": str}}. Chỉ lưu mã lỗi
        chuẩn hoá + message nghiệp vụ generic — KHÔNG có field, không có input
        echo. Gọi ở `_run_demo_job` (có async context), không gọi trong
        Executor.on_failure (sync).
        """
        ...

    async def get_repair_hints(self, workflow_id: str) -> list[dict]:
        """Đọc repair hints của workflow, mới nhất trước.

        Trả list dict mỗi phần tử có: task_id, error_code, message, created_at.
        Dùng trong `get_demo_workflow_status` để phân biệt "FAILED cứng" và
        "FAILED nhưng repairable → NEEDS_INFORMATION" sau restart (DB là nguồn
        sự thật, không dựa vào `_DEMO_JOBS`).
        """
        ...

    async def log_execution(
        self,
        workflow_id: str,
        task_id: str,
        attempt_number: int,
        connector_name: str | None,
        http_status: int | None,
        raw_error_code: str | None,
        standard_result: StandardResult,
        duration_ms: int | None,
    ) -> None:
        """Ghi audit mỗi lần Connector gọi API (kể cả retry).

        Logging là best-effort: lỗi ghi log không được làm hỏng workflow.
        Executor gọi method này sau mỗi attempt, trước khi quyết định retry.
        """
        ...

    async def list_execution_logs(self, limit: int = 10_000) -> list[dict]:
        """Đọc execution logs để aggregate metrics.

        Trả list dict với keys: connector_name, attempt_number, duration_ms,
        standard_result (dict), created_at. Metrics module dùng để tính retry
        rate, success rate, error breakdown.
        """
        ...
