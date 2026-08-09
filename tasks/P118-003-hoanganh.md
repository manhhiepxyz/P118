# P118-003 — Hoàng Anh · Tầng Dịch vụ + Dữ liệu + Giao diện

> Đọc `docs/shared_contracts.md` và `AGENTS.md` trước khi bắt đầu.

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

## Tuần 2 (11–17/08) — API + State persistence

| Việc | File |
|---|---|
| Viết FastAPI routes cho workflow | `src/api/routes.py` |
| `POST /workflow/start` — nhận goal, trả workflow_id | `src/api/routes.py` |
| `GET /workflow/{id}/status` — trả trạng thái hiện tại | `src/api/routes.py` |
| Lưu đầy đủ workflow state sau mỗi task | `src/db/postgres_repository.py` |
| Đảm bảo `resident_id`, `vehicle_id`, `booking_id` được lưu | `src/db/postgres_repository.py` |

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
