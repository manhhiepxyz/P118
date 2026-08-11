# P118-002 — Mạnh Hiệp · Tầng Thực thi

> Đọc `shared_contracts.md` và `AGENTS.md` trước khi bắt đầu.

---

## Phạm vi

Mạnh Hiệp chịu trách nhiệm toàn bộ tầng **Thực thi**:

- Executor — chạy TaskPlan đúng thứ tự phụ thuộc
- Connector — gọi service qua interface chuẩn, tạo `StandardResult`
- Data propagation — truyền output bước trước sang bước sau
- Gọi `WorkflowStateRepository` để lưu state (Hoàng Anh implement)
- Recovery — xử lý lỗi, retry
- Compensation — sau Gate 2

**Phạm vi code:** `src/executor/`, `src/connectors/`

**Schema Mạnh Hiệp sở hữu:**
- `src/common/results.py` — `StandardResult`
- `src/common/enums.py` — `WorkflowStatus`, `TaskStatus`, `ErrorCode`
- `src/common/repository.py` — `WorkflowStateRepository` Protocol

**Không sửa:** `src/common/task_plan.py` (của Thành Bảo)

**Test fakes cần tạo:**
- `tests/fakes/fake_connector.py`
- `tests/fakes/in_memory_repository.py`
- `tests/fixtures/task_plans.py`

---

## Tuần 1 (03–10/08) — Nền tảng thực thi

### Việc cần làm

| Việc | File | Hoàn thành khi |
|---|---|---|
| Định nghĩa `StandardResult` | `src/common/results.py` | Hoàng Anh và Thành Bảo import được |
| Định nghĩa `WorkflowStatus`, `TaskStatus`, `ErrorCode` | `src/common/enums.py` | Cả nhóm import được |
| Định nghĩa `WorkflowStateRepository` Protocol | `src/common/repository.py` | Hoàng Anh implement, Executor inject |
| Viết `InMemoryWorkflowStateRepository` | `tests/fakes/in_memory_repository.py` | Executor unit test không cần PostgreSQL |
| Viết `FakeConnector` | `tests/fakes/fake_connector.py` | Executor test không cần Mock API |
| Tạo `TaskPlan` fixtures | `tests/fixtures/task_plans.py` | Test không cần LLM Planner |
| Định nghĩa `Connector` abstract class | `src/connectors/base.py` | Interface chuẩn có `tool_name` |
| Viết `ResidentConnector` stub | `src/connectors/resident.py` | Xử lý `register_resident`, gọi HTTP, tạo `StandardResult` |
| Viết `TransportConnector` stub | `src/connectors/transport.py` | Xử lý `register_vehicle` VÀ `book_parking` (2 tool, 2 endpoint khác nhau) |
| Viết `PaymentConnector` stub | `src/connectors/payment.py` | Xử lý `pay_fee`, tạo `StandardResult` |
| Viết Executor skeleton | `src/executor/executor.py` | Nhận `TaskPlan`, chạy được 1 task |
| Implement dependency check | `src/executor/executor.py` | Chỉ chạy task khi dependency `SUCCESS` |
| Implement data propagation | `src/executor/executor.py` | `resident_id` → `register_vehicle`, `vehicle_id` → `book_parking` |
| Gọi repository sau mỗi task | `src/executor/executor.py` | Update status và lưu result vào DB |
| Test dependency order | `tests/test_executor.py` | Parking không chạy trước Vehicle |
| Test tool routing | `tests/test_executor.py` | `register_vehicle` và `book_parking` route đúng Connector |

### Connector interface

```python
class Connector(ABC):
    @abstractmethod
    async def execute(
        self,
        tool_name: str,
        input_data: dict
    ) -> StandardResult:
        ...
```

**Quy tắc Connector:**
- Nhận `tool_name` và `input_data` theo internal contract.
- Map `input_data` sang request format của API đích.
- Gọi API, nhận raw response.
- Parse raw response → tạo `StandardResult` object.
- Map error code của API về chuẩn nội bộ.
- Không trả raw JSON response — Executor chỉ nhận `StandardResult`.
- Không quyết định task tiếp theo.
- Không sửa `TaskPlan`.

**TransportConnector xử lý 2 tool:**
```
tool_name = "register_vehicle" → POST /api/vehicles
tool_name = "book_parking"     → POST /api/parking/bookings
```

**Mock API response flow:**
```
Mock API JSON response
→ Connector parse và validate
→ StandardResult object
→ Executor nhận StandardResult
```

### Làm độc lập trong tuần 1

Mạnh Hiệp không cần đợi LLM Planner hay Mock API:

```python
# FakeConnector — cấu hình trả kết quả khác nhau
class FakeConnector:
    def __init__(self, response: StandardResult):
        self.response = response

    async def execute(self, tool_name: str, input_data: dict) -> StandardResult:
        return self.response

# InMemoryWorkflowStateRepository — lưu trong dict
class InMemoryWorkflowStateRepository:
    def __init__(self):
        self._workflows = {}
        self._tasks = {}

    async def create_workflow(self, workflow_data: dict) -> str:
        wid = str(uuid.uuid4())
        self._workflows[wid] = workflow_data
        return wid
    # ...
```

### Chưa cần tuần này

- Recovery hoàn chỉnh
- Retry logic
- Idempotency
- Saga Compensation

### Tiêu chí hoàn thành tuần 1

- [ ] `StandardResult` trong `src/common/results.py`, `WorkflowStatus`/`TaskStatus`/`ErrorCode` trong `src/common/enums.py`
- [ ] `WorkflowStateRepository` Protocol trong `src/common/repository.py`
- [ ] `InMemoryWorkflowStateRepository` trong `tests/fakes/` — Executor unit test không cần PostgreSQL
- [ ] `FakeConnector` trong `tests/fakes/` — có thể cấu hình trả success, `NO_AVAILABILITY`, `SERVICE_TIMEOUT`
- [ ] `Connector` abstract class có method `execute(tool_name, input_data) → StandardResult`
- [ ] 3 Connector stub implement được, `TransportConnector` xử lý 2 tool
- [ ] Executor nhận `TaskPlan` từ Thành Bảo và chạy được
- [ ] Data propagation đúng: `resident_id` → `register_vehicle`, `vehicle_id` → `book_parking`
- [ ] Task không chạy nếu dependency chưa `SUCCESS`
- [ ] Executor gọi repository sau mỗi task (dù Hoàng Anh chưa implement đủ)
- [ ] Test tool routing: `register_vehicle` và `book_parking` đi đúng endpoint
- [ ] Connector gọi được Mock API qua HTTP

---

## Tuần 2 (10–14/08) — Gate 2: nối Planner với Runtime

> **Mục tiêu nội bộ:** hoàn thành và freeze trước 23:59 ngày 14/08. Không
> viết lại phần deterministic core đã pass; tập trung tạo execution boundary
> ổn định để LangGraph và workflow API gọi được.

### Trạng thái đầu tuần

- Ba Connector đã gọi đúng Mock Provider và chuẩn hóa response về
  `StandardResult`.
- Executor đã chạy full flow T1→T2→T3→T4, resolve `InputRef` và lưu kết quả
  qua repository.
- Happy path và `NO_AVAILABILITY` đã có integration test xuyên stack với
  PostgreSQL thật.
- Phần còn thiếu là nối output Planner/LangGraph thật vào runtime và chuẩn
  hóa kết quả cho API.

### Việc cần làm

| Việc | File | Hoàn thành khi |
|---|---|---|
| Cung cấp execution boundary cho LangGraph/API | `src/executor/executor.py` hoặc module orchestration được nhóm chốt | Nhận `TaskPlan` đã validate và trả `workflow_id` + kết quả task theo contract hiện có |
| Nối plan do Planner tạo vào Executor | Integration layer/test | Không dùng hardcoded plan trong đường chạy demo Gate 2 |
| Chuẩn hóa success/failure output | `src/executor/executor.py` | Caller nhận được workflow status, `StandardResult` từng task và failure signal an toàn |
| Xác nhận data propagation với plan từ LLM | Integration test | `resident_id` → `vehicle_id` → `booking_id` → payment chạy đúng |
| Xác nhận failure scenario | Integration test | `NO_AVAILABILITY` làm task/workflow `FAILED`, không chạy task phụ thuộc và trả error code có ý nghĩa |
| Sở hữu full-stack integration test | `tests/test_integration/` | Phủ Planner boundary → Executor → Connector → Mock Provider → PostgreSQL; phối hợp Thành Bảo để cắm Planner thật |
| Viết smoke test cho runtime | `scripts/` hoặc lệnh được ghi trong README | Có một lệnh tái hiện được happy path trước khi quay demo |
| Chạy kiểm chứng Docker Compose full stack | Không sửa service contract nếu chưa trao đổi | PostgreSQL và 3 Mock Provider healthy; Connector gọi đúng cổng và full flow chạy được |
| Điều tra lỗi tích hợp liên tầng | Integration PR | Phân biệt rõ lỗi Planner, Executor, Connector, Provider hay DB và chuyển đúng owner xử lý |
| Viết hướng dẫn runtime | `README.md` | Có lệnh chạy Executor/Connector, full regression và smoke test; Thành Bảo review bản cuối |

### Làm độc lập

- Tiếp tục dùng `TaskPlan` fixture và fake Planner; không cần đợi LLM code của
  Thành Bảo để hoàn thiện execution boundary.
- Không sửa `src/agents/**` hoặc `src/api/**`. Chỉ cung cấp interface và test
  fixture để hai tầng đó tích hợp.
- Có thể chạy và kiểm chứng `docker-compose.yml`; nếu cần thay đổi cấu hình
  service/database thì phối hợp Hoàng Anh hoặc thực hiện trong PR integration
  có review, không tự đổi API contract.
- Không thay đổi `TaskPlan`, `StandardResult`, enum hoặc repository contract
  nếu không có blocker được cả nhóm duyệt.

### Tiêu chí hoàn thành Week 2

- [ ] LangGraph/API gọi được Executor qua một execution boundary rõ ràng.
- [ ] TaskPlan do LLM tạo chạy được full flow mà không hardcode lại task.
- [ ] Executor tiếp tục lưu status/result sau mỗi task vào PostgreSQL.
- [ ] InputRef truyền đúng ID thực giữa bốn bước.
- [ ] Happy path kết thúc với workflow `SUCCESS`.
- [ ] `NO_AVAILABILITY` kết thúc có kiểm soát với workflow `FAILED`.
- [ ] Full-stack integration test và smoke test chạy được bằng một lệnh đã ghi
  trong README.
- [ ] Docker Compose được kiểm chứng runtime, không chỉ validate YAML.
- [ ] Unit test, integration test, `ruff check` và `ruff format --check` pass.

### Không làm trong critical path Gate 2

- Retry/recovery handler hoàn chỉnh, nhận plan mới từ Replanner.
- Compensation/Saga rollback.
- Tự generate TaskPlan hoặc quyết định Policy/HITL.

---

## Tuần 3 (18–24/08) — Recovery + Retry

| Việc | File |
|---|---|
| Implement retry khi `retryable: true` | `src/executor/executor.py` |
| Implement recovery handler | `src/executor/recovery.py` |
| Gửi failure signal cho Thành Bảo khi cần replan | `src/executor/executor.py` |
| Nhận `TaskPlan` mới từ Replanner, tiếp tục từ task chưa xong | `src/executor/executor.py` |

---

## Sau Gate 2 — Compensation

Compensation chỉ được implement sau khi Gate 2 đã đạt, và sau khi Hoàng Anh bổ sung các compensation operation trong Mock API.

| Việc | File |
|---|---|
| Thiết kế `CompensationAction` | `src/executor/compensation.py` |
| Implement compensation stack (LIFO) | `src/executor/compensation.py` |
| Test rollback: T1→T2→T3 fail → undo T2→T1 | `tests/test_compensation.py` |

> - Compensation không xuất hiện trong `TaskPlan` chính.
> - Không rollback bằng cách xóa trực tiếp database ngoài service contract.
> - Chỉ tích hợp rollback thật khi Mock API có operation tương ứng.

---

## Repository interface

Mạnh Hiệp gọi repository qua interface sau (Hoàng Anh implement):

```python
from typing import Protocol
from src.common.enums import WorkflowStatus, TaskStatus
from src.common.results import StandardResult

class WorkflowStateRepository(Protocol):
    async def create_workflow(self, workflow_data: dict) -> str: ...
    async def update_workflow_status(self, workflow_id: str, status: WorkflowStatus) -> None: ...
    async def create_task(self, workflow_id: str, task_data: dict) -> None: ...
    async def update_task_status(self, workflow_id: str, task_id: str, status: TaskStatus) -> None: ...
    async def save_task_result(self, workflow_id: str, task_id: str, result: StandardResult) -> None: ...
    async def get_workflow(self, workflow_id: str) -> dict: ...
```

**Luồng trạng thái trong Executor:**
```
Task → RUNNING → gọi repository.update_task_status()
→ Connector thực thi
→ Nhận StandardResult
→ Task → SUCCESS hoặc FAILED
→ gọi repository.save_task_result()
→ chọn bước tiếp theo hoặc gửi failure signal
```

> - Executor không viết SQL trực tiếp.
> - Repository không quyết định thứ tự task.
> - Connector không gọi repository.
> - Mock API không ghi workflow state — chỉ lưu dữ liệu nghiệp vụ riêng (resident, vehicle, booking, payment).

---

## Interface với các thành viên khác

**Mạnh Hiệp nhận từ Thành Bảo:**
- `TaskPlan`, `Task`, `InputRef` từ `src/common/task_plan.py` (Thành Bảo sở hữu, Mạnh Hiệp chỉ import)
- `TaskPlanValidator` để validate trước khi execute

**Mạnh Hiệp cung cấp cho Thành Bảo:**
- `StandardResult` từ `src/common/results.py`
- `WorkflowStatus`, `TaskStatus`, `ErrorCode` từ `src/common/enums.py`
- `WorkflowStateRepository` Protocol từ `src/common/repository.py`
- `StandardResult` của task vừa chạy
- `TaskStatus` mới
- Danh sách task đã `SUCCESS` (Replanner không lập lại)
- Failure signal: `error_code`, `message`, `retryable`

> Không có model `ExecutionResult`. Không truyền raw JSON từ Mock API vào Executor hay Thành Bảo.

**Mạnh Hiệp nhận từ Hoàng Anh:**
- Mock API endpoints và URL để Connector config
- `WorkflowStateRepository` implementation

---

## Không làm

- Không tự generate `TaskPlan` (việc của Thành Bảo)
- Không quyết định action nào cần approve (việc của Thành Bảo — Policy)
- Không viết Mock API (việc của Hoàng Anh)
- Không implement `WorkflowStateRepository` (việc của Hoàng Anh)
- Không truyền raw JSON từ Mock API vào Executor — phải qua Connector tạo `StandardResult`
