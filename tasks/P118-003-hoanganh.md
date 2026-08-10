# P118-003 — Hoàng Anh · Tầng Dịch vụ + Dữ liệu + Giao diện

> Đọc `shared_contracts.md` và `AGENTS.md` trước khi bắt đầu.

---

## Phạm vi

Hoàng Anh chịu trách nhiệm toàn bộ tầng **Dịch vụ, Dữ liệu và Giao diện**:

- Mock API (3 services: Resident, Transport/Parking, Payment)
- PostgreSQL schema + migration
- `WorkflowStateRepository` implementation
- FastAPI workflow routes
- HITL UI (giao diện xác nhận)
- React frontend (timeline workflow)
- Deploy cloud + Live URL

**Phạm vi code:** `src/services/mock/`, `src/db/`, `src/api/`, `frontend/`

**Schema Hoàng Anh import (không sửa):**
- `StandardResult` từ `src/common/results.py`
- `WorkflowStatus`, `TaskStatus`, `ErrorCode` từ `src/common/enums.py`
- `WorkflowStateRepository` Protocol từ `src/common/repository.py`

**Implementation Hoàng Anh viết:**
- `PostgreSQLWorkflowStateRepository` tại `src/db/postgres_repository.py`

---

## Tuần 1 (03–10/08) — Mock API + Database

### Việc cần làm

| Việc | File | Hoàn thành khi |
|---|---|---|
| Tạo `ResidentService` FastAPI app | `src/services/mock/resident.py` | `POST /api/residents` trả JSON response hợp lệ |
| Tạo `TransportService` FastAPI app | `src/services/mock/transport.py` | `POST /api/vehicles` và `POST /api/parking/bookings` — 2 endpoint riêng |
| Tạo `PaymentService` FastAPI app | `src/services/mock/payment.py` | `POST /api/payments` trả JSON response hợp lệ |
| Thêm failure injection cho Parking | `src/services/mock/transport.py` | `?fail=NO_AVAILABILITY` trả đúng error format |
| Swagger UI cho cả 3 service | Auto từ FastAPI | Mạnh Hiệp có thể test bằng Swagger |
| Thiết kế PostgreSQL schema | `src/db/schema.sql` | Bảng `workflows`, `tasks`, `execution_log` |
| Tạo migration / init script | `src/db/init.py` | Chạy được, tạo bảng thành công |
| Implement `PostgreSQLWorkflowStateRepository` | `src/db/postgres_repository.py` | Lưu và đọc lại được workflow + task state |

### Hai loại dữ liệu Hoàng Anh quản lý

**Dữ liệu nghiệp vụ của Mock Service** (Mock API tự lưu):
- residents, vehicles, bookings, payments
- Lưu trong bảng riêng của từng service

**Workflow state** (chỉ cập nhật qua `WorkflowStateRepository`):
- workflows, tasks, task results, execution log
- Executor gọi repository để cập nhật — Mock API và Connector không tự ghi workflow state

> FastAPI workflow routes gọi tầng orchestration, không tự chạy tool hay gọi Connector trực tiếp.

---

### Mock API response format

Mock API trả JSON response gần giống `StandardResult` để đơn giản hóa tích hợp Gate 2:

```json
{
  "success": true,
  "data": { "resident_id": "RES-001" },
  "error_code": null,
  "message": "Created",
  "retryable": false
}
```

> **Quan trọng:** Connector (Mạnh Hiệp) vẫn phải parse response này và tạo `StandardResult` object nội bộ. Executor không nhận raw JSON trực tiếp từ Mock API. Sau này Real API có thể trả format khác mà Executor không cần thay đổi.

### Làm độc lập trong tuần 1

Hoàng Anh không cần đợi Executor hay Planner. Tự test bằng:
- FastAPI `TestClient` cho từng endpoint
- Swagger UI / Postman
- Repository unit test với PostgreSQL test database

Các test cần có:
- `POST /api/residents` → trả `resident_id`
- `POST /api/vehicles` → trả `vehicle_id`
- `POST /api/parking/bookings` → trả `booking_id`
- `POST /api/parking/bookings?fail=NO_AVAILABILITY` → trả error đúng format
- `POST /api/payments` → trả `payment_id`
- Repository: `create_workflow()` trả `workflow_id`
- Repository: lưu và đọc lại workflow + task state

### Chưa cần tuần này

- Frontend hoàn chỉnh
- HITL UI
- WebSocket
- Deploy cloud

### Tiêu chí hoàn thành tuần 1

- [ ] 3 mock service chạy được độc lập
- [ ] `TransportService` có đủ 2 endpoint: `/api/vehicles` và `/api/parking/bookings`
- [ ] Tất cả endpoint trả JSON theo format đã thống nhất
- [ ] `?fail=NO_AVAILABILITY` hoạt động đúng
- [ ] PostgreSQL schema tạo được, `WorkflowStateRepository` lưu và đọc lại được state
- [ ] Mạnh Hiệp gọi được Mock API qua HTTP từ Connector

---

## WorkflowStateRepository interface

Hoàng Anh implement đúng interface Mạnh Hiệp sẽ gọi:

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

> - Repository không quyết định thứ tự task.
> - Repository không điều khiển execution flow.
> - Hoàng Anh không gọi Connector hay Executor.

---

## Tuần 2 (10–14/08) — Gate 2: Workflow API + Database Wiring

> **Mục tiêu nội bộ:** hoàn thành và freeze trước 23:59 ngày 14/08; ngày
> 15/08 chỉ dùng để sửa lỗi, kiểm tra README và quay/nộp demo. Workflow route
> chỉ gọi orchestration boundary, không tự gọi Connector hay chọn task.

### Trạng thái đầu tuần

- Ba Mock Provider và PostgreSQL repository đã hoạt động.
- Workflow state, task result và `depends_on` đã persist và đọc lại được.
- Docker Compose đã khai báo PostgreSQL cùng Resident, Transport và Payment
  provider nhưng chưa được kiểm chứng full stack trên máy có Docker.
- `src/api/routes.py` hiện là route starter `/chat`; chưa có workflow API theo
  contract Gate 2.

### Việc cần làm

| Việc | File | Hoàn thành khi |
|---|---|---|
| Định nghĩa request/response cho workflow API | `src/api/` | Request nhận `goal`; response không lộ raw provider response hoặc secret |
| Viết `POST /workflow/start` | `src/api/routes.py` | Gọi orchestration boundary và trả tối thiểu `workflow_id`, `status` |
| Viết `GET /workflow/{workflow_id}/status` | `src/api/routes.py` | Đọc PostgreSQL và trả workflow cùng trạng thái/kết quả từng task |
| Khởi tạo database trong FastAPI lifespan | `src/main.py`, `src/db/connection.py` | Pool được mở/đóng đúng vòng đời và migration chạy trước khi nhận request |
| Inject repository/orchestration dependency | `src/api/` | API test được bằng fake; production wiring dùng implementation thật |
| Duy trì cấu hình API/DB service trong Docker Compose | `docker-compose.yml` | Backend, PostgreSQL và Mock Provider có đúng env/healthcheck; Mạnh Hiệp chịu trách nhiệm chạy smoke full stack |
| Viết hướng dẫn API và database | `README.md`, `.env.example` | Có env vars, migration, workflow endpoint và sample request; không chứa secret thật |

### Làm độc lập

- Unit test API bằng fake orchestration service trả `workflow_id`; không cần
  đợi Planner hoặc Executor thật.
- Test repository bằng PostgreSQL test DB như Week 1.
- Không gọi trực tiếp Connector/Executor từ logic route. Route chỉ gọi boundary
  đã inject và đọc state qua repository.
- Mạnh Hiệp sở hữu full-stack integration test, smoke test và kiểm chứng Docker
  runtime; Hoàng Anh chỉ xử lý lỗi thuộc API, database hoặc cấu hình service
  mình sở hữu.

### Tiêu chí hoàn thành Week 2

- [ ] `/workflow/start` nhận goal tự nhiên và kích hoạt orchestration flow.
- [ ] `/workflow/{workflow_id}/status` phản ánh đúng PostgreSQL state.
- [ ] Full flow lưu được `resident_id`, `vehicle_id`, `booking_id`, payment
  result và trạng thái của cả bốn task.
- [ ] Cấu hình Docker cho API/database đúng; smoke full stack do Mạnh Hiệp chạy
  có thể kết nối vào API và PostgreSQL.
- [ ] Phần README về API/database đủ để thành viên khác cấu hình và gọi sample
  request; Thành Bảo review bản README cuối.
- [ ] API/repository test, full regression, `ruff check` và
  `ruff format --check` pass.

### Không làm trong critical path Gate 2

- HITL approve/reject, WebSocket và React frontend hoàn chỉnh.
- eKYC/Didit thật hoặc lưu dữ liệu sinh trắc học.
- Compensation endpoint, deploy production hoặc Live URL nếu happy path bằng
  LLM thật chưa ổn định.

---

## Tuần 3 (18–24/08) — HITL + Frontend

| Việc | File |
|---|---|
| `POST /workflow/{id}/approve` — HITL approve | `src/api/routes.py` |
| `POST /workflow/{id}/reject` — HITL reject | `src/api/routes.py` |
| WebSocket `/ws/{workflow_id}` — push realtime update | `src/api/websocket.py` |
| React Timeline UI — hiển thị từng bước | `frontend/src/components/Timeline.tsx` |
| HITL Modal — hiện thông tin, 2 nút Approve/Reject | `frontend/src/components/HitlModal.tsx` |

---

## Tuần 4 (25/08–02/09) — Deploy + Evaluation

| Việc |
|---|
| Docker Compose chạy full stack |
| Deploy lên Render/Railway → Live URL |
| Evaluation report |

---

## Sau Gate 2 — Compensation operations

Sau khi Gate 2 đạt, bổ sung compensation operations vào Mock API nếu scope yêu cầu:

- `DELETE /api/residents/{id}` — cancel registration
- `DELETE /api/parking/bookings/{id}` — cancel booking
- `POST /api/payments/{id}/refund` — refund

> - Chỉ bổ sung khi Mạnh Hiệp cần để implement compensation stack.
> - Không rollback bằng cách xóa trực tiếp database ngoài service contract.
> - Compensation operations không xuất hiện trong main `TaskPlan`.

---

## Interface với các thành viên khác

**Hoàng Anh cung cấp cho Mạnh Hiệp:**
- Mock API endpoints, base URL, request format (OpenAPI/Swagger)
- JSON response format (Connector sẽ parse thành `StandardResult`)
- `PostgreSQLWorkflowStateRepository` implement `WorkflowStateRepository` Protocol

**Hoàng Anh cung cấp cho Thành Bảo:**
- HITL UI — nhận trigger `REQUIRES_APPROVAL`, hiển thị cho user, gửi kết quả approve/reject về API
- Workflow state đọc được để Replanner biết task nào đã `SUCCESS`

**Hoàng Anh nhận từ Mạnh Hiệp:**
- `StandardResult` từ `src/common/results.py`
- `WorkflowStatus`, `TaskStatus`, `ErrorCode` từ `src/common/enums.py`
- `WorkflowStateRepository` Protocol từ `src/common/repository.py`
- Executor gọi `WorkflowStateRepository` — Hoàng Anh không gọi Executor ngược lại

---

## Không làm

- Không viết Executor hay Connector (việc của Mạnh Hiệp)
- Không generate `TaskPlan` (việc của Thành Bảo)
- Không chọn task tiếp theo (việc của Executor)
- Không quyết định Policy (việc của Thành Bảo)
- Không tự đổi format response Mock API mà không cập nhật `shared_contracts.md`
- Không để Executor nhận raw JSON từ Mock API — phải qua Connector
- Mock API không tự cập nhật trạng thái workflow — chỉ Executor gọi repository mới được ghi workflow state
