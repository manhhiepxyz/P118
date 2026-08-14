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

### Review AI Plan (Direction 2) — đã triển khai 13/08/2026

Chủ sở hữu dự án chốt Direction 2: LLM Planner sinh TaskPlan → hiện lên canvas
chỉnh sửa được (tái dùng builder) → người dùng duyệt → Executor chạy. Home
giữ nguyên nhập mục tiêu (không phải blank-canvas entry).

**Backend (sở hữu Hoàng Anh):**
- `POST /workflow/start` — có `tasks` → validate + persist draft PENDING; chỉ
  `goal` → gọi Planner (NEEDS_INFORMATION / READY→draft). LLMConfigurationError→503,
  PlannerError→502.
- `GET /workflow/{id}/status` — trả workflow + tasks + `task_plan` đã parse.
- `POST /workflow/{id}/execute` — duyệt & chạy (thay vì dùng `/approve` là HITL
  task-level). Gate status==PENDING (409), snapshot task_plan TRƯỚC execute,
  gọi boundary.
- Trust boundary pay_fee: guard API `_reject_untrusted_pay_fee` mirror Planner —
  booking_id/amount/currency phải là InputRef trỏ CÙNG 1 task book_parking
  (plan chỉnh sửa bypass Planner nên phải cưỡng chế ở tầng API).
- `src/db/workflow_repository.py::update_workflow_task_plan`, passthrough +
  `close()` ở `postgres_repository.py`.
- `src/main.py` lifespan: dựng runtime, DB chưa lên → `/health` vẫn 200.

**Frontend:**
- `planToDraftSteps` (task_id→stepId, refMode cho InputRef, cascade vị trí).
- `useBuilderDraft` hook trích từ BuilderPage — dùng chung cho Builder + Review.
- `ReviewPlanPage` (/review/:id) — canvas chỉnh sửa + "Duyệt & chạy".
- Dashboard "Lập kế hoạch" → NEEDS_INFORMATION hiện question inline.
- Builder submit → tạo draft → /review/:id (luồng hợp nhất).

**Tests:** `tests/test_api/fakes.py` + 16 route test (dependency_overrides);
`tests/test_db` roundtrip `task_plan`. Verify: `pytest`, `ruff`, `npm run build`,
`npm run lint` đều pass.

### Auth (login / register / phân quyền) — đã triển khai 14/08/2026

Đăng nhập, đăng ký và RBAC cho backend — quyết định với chủ sở hữu: 2 vai trò
`resident` + `admin`, **stdlib only** (scrypt + HMAC token, KHÔNG thêm bcrypt/pyjwt).

**DB:**
- `src/db/schema.sql` v0.4.0: bảng `users` (`id` UUID PK, `username` UNIQUE,
  `email` partial-unique, `password_hash` TEXT `scrypt:N:r:p:salt:hash`,
  `role` CHECK `('resident','admin')` default resident, `created_at`/`updated_at`/`archived_at`).
- `src/db/user_repository.py`: `create_user` (trả KHÔNG kèm hash) /
  `get_user_by_username` / `get_user_by_id` (kèm hash cho login verify);
  `UserAlreadyExistsError` map từ `UniqueViolationError`. Compose vào facade
  thành `repository.users` + passthrough `create_user`/`get_user_*`.

**Auth primitives (`src/api/auth.py`):** `hash_password`/`verify_password`
(hashlib.scrypt, salt 16B, `hmac.compare_digest` constant-time);
`create_access_token`/`decode_access_token` (JWT-shaped HS256 stdlib, payload
`sub/username/role/iat/exp`, TTL 24h). `JWT_SECRET` trong `.env` — rỗng → 500.

**API:**
- `POST /auth/register` (201, mặc định resident, 409 username trùng, lowercase).
- `POST /auth/login` (200 `TokenResponse`, 401 cùng message chống enumeration).
- `GET /auth/me` (Bearer).
- Bảo vệ: 5 route nghiệp vụ `/api/v1` thêm `Depends(get_current_user)` —
  `/health` + auth endpoints public.
- `deps.py`: `get_user_repository` (đọc `repository.users` qua tuple `(boundary,
  repository)` KHÔNG đổi shape), `get_current_user`, `require_roles(*roles)` factory.

**Khác:** `scripts/create_admin.py` (tạo/reset admin, upsert idempotent —
getpass interactive, không chạy trong CI). `shared_contracts.md` §16 Auth.
`.env` đã thêm `JWT_SECRET` (gitignored).

**Tests:** `tests/test_api/test_auth_routes.py` (14 test: register/login/me +
bảo vệ route + require_roles qua route thật); `workflow_env` fixture thêm
override `get_current_user=FAKE_USER`; `tests/test_routes.py` 2 test thêm auth
override; `tests/test_db/test_user_repository.py` (7 test DB thật);
`tests/test_db/conftest.py` TRUNCATE thêm `users`.

**Verify:** API 33 test pass; DB 35 test pass (real Postgres); full suite
411 pass — 2 failure pre-existing `test_runtime_packaging.py` (cp1252). `ruff
check` + `ruff format` pass.

### Tích hợp API + Frontend (happy path) — đã triển khai 14/08/2026

Chủ sở hữu chốt: **tích hợp happy path trước** — nối đúng luồng chính
auth → plan → review → execute → status → list về backend thật; giữ HITL
(approve/reject/cancel) ở mock, không đụng scope Tuần 3.

**Backend fix (nguyên nhân 502):**
- `src/agents/planner.py`: `llm.with_structured_output(_PlannerResponse)` mặc
  định chuyển sang strict structured-output (`json_schema`) ở langchain-openai
  1.4.2, mode này TỪ CHỐI schema vì `Task.input` là dict tự do. Fix bằng
  `method="function_calling"` → `POST /workflow/start` (chỉ `goal`) chạy được,
  trả plan 4 task với chuỗi `depends_on` đúng.
- File thuộc Thành Bảo theo task file, nhưng fix cần thiết để happy path LLM
  planning hoạt động từ UI.

**Frontend wiring:**
- `frontend/.env`: `VITE_USE_MOCK=false` → `client.ts` chuyển mock → API thật
  qua proxy `/api/v1` (vite.config.ts → `http://localhost:8000`).
- `frontend/src/lib/client.ts`: `approveTask`/`rejectTask`/`cancelWorkflow`
  mock-only; chế độ real throw lỗi rõ ràng (`HITL_NOT_READY`) thay vì 404 mù mờ.
- `frontend/src/components/AppLayout.tsx`: badge **MOCK DATA** chỉ hiện khi
  `USE_MOCK` (real mode không nhầm với data thật).
- `frontend/README.md`: cập nhật bảng trạng thái tích hợp (auth, start, status,
  workflows, execute ✅; HITL ⏳ Tuần 3).

**Verify end-to-end:** register → login → LLM plan (4 task, chuỗi depends_on)
→ status PENDING → execute SUCCESS → status phản ánh 4 task SUCCESS kèm
`resident_id`/`vehicle_id`/`booking_id`/`payment_id` thật. UI: dashboard (real,
không badge MOCK) → nhập goal → review (đủ 4 task) → "Duyệt & Thực thi ngay" →
timeline workflow. `npm run build` pass; `ruff check` + `ruff format --check`
pass trên planner fix.

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
