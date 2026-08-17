# AGENTS.md — P-118

> Đọc file này trước khi làm bất cứ thứ gì trong repo.
> Áp dụng cho cả AI coding tools và thành viên nhóm.

---

## Dự án là gì

**P-118 — AI Agent điều phối đa dịch vụ trong hệ sinh thái dịch vụ nhà ở/cư dân**
PTNT-02 · STT 158 · VinUni AI20K Cohort 3

User nhập goal bằng ngôn ngữ tự nhiên → Agent tự lập kế hoạch → gọi các service theo đúng thứ tự phụ thuộc → truyền dữ liệu giữa bước → xử lý lỗi → yêu cầu xác nhận khi cần.

**MVP customer journey:**
```
Register Resident → Register Vehicle → Book Parking → Pay Fee
```

**Gate 2 property-discovery extension:**
```
Search Properties → user selects a property → Schedule Property Viewing
```

Hai tool mở rộng chỉ hỗ trợ tìm kiếm và liên hệ. Agent không tự thuê/mua,
giữ căn, đặt cọc hoặc ký hợp đồng bất động sản.

**4 mock services:** Property · Resident · Transport/Parking · Payment

> **Lưu ý phạm vi:** VinHomes chỉ là bối cảnh minh họa giả lập. Dự án không phải sản phẩm chính thức của VinHomes. Không sử dụng API production hoặc dữ liệu thật của VinHomes. Các tên khu đô thị trong ví dụ chỉ là dữ liệu mock.

---

## Đọc bắt buộc trước khi code

| File | Trả lời câu hỏi |
|---|---|
| `shared_contracts.md` | Các module giao tiếp với nhau như thế nào? |
| `tasks/P118-001-thanhbao.md` | Thành Bảo cần làm gì? |
| `tasks/P118-002-manhhiep.md` | Mạnh Hiệp cần làm gì? |
| `tasks/P118-003-hoanganh.md` | Hoàng Anh cần làm gì? |

---

## Kiến trúc tổng quan

```
User Goal (ngôn ngữ tự nhiên)
    ↓
[Thành Bảo] Planner → TaskPlan
    ↓
[Thành Bảo] TaskPlan Validator
    ↓
[Thành Bảo] Policy Engine
    ├── AUTO_ALLOWED
    │       ↓
    │   [Mạnh Hiệp] Executor
    │       ├── gọi Connector
    │       │       ↓
    │       │   [Mạnh Hiệp] Connector
    │       │       ↓
    │       │   [Hoàng Anh] Mock API
    │       │       ↓
    │       │   raw response
    │       │       ↓
    │       │   Connector chuẩn hóa → StandardResult
    │       │       ↓
    │       │   trả về Executor
    │       │
    │       └── gọi WorkflowStateRepository
    │               ↓
    │           [Hoàng Anh] PostgreSQL
    │
    ├── REQUIRES_APPROVAL
    │       ↓
    │   Pause workflow
    │       ↓
    │   [Hoàng Anh] HITL UI
    │       ↓
    │   Approve → Executor tiếp tục
    │   Reject  → Workflow dừng
    │
    └── DENIED
            ↓
        Không thực thi action
```

**Khi Executor nhận lỗi có thể phục hồi:**
```
Executor
→ failure signal
→ [Thành Bảo] Replanner
→ TaskPlan mới (giữ task đã SUCCESS)
→ Validator → Policy → Executor tiếp tục
```

> - Policy phải kiểm tra **trước** khi action được thực thi.
> - Executor là thành phần điều phối việc lưu state — Connector và Mock API không ghi workflow state.
> - Mock API có thể tự lưu dữ liệu nghiệp vụ riêng (resident, vehicle, booking, payment).
> - Replanner chỉ chạy khi có failure signal phù hợp.
> - Task đã `SUCCESS` không chạy lại sau replan.
> - Không có model `ExecutionResult` — dùng `StandardResult` + `TaskStatus` + danh sách task đã `SUCCESS`.

---

## Shared schemas — vị trí dùng chung

Schema tách thành file riêng để tránh Git conflict:

| File | Nội dung | Owner |
|---|---|---|
| `src/common/task_plan.py` | `TaskPlan`, `Task`, `InputRef` | Thành Bảo |
| `src/common/results.py` | `StandardResult` | Mạnh Hiệp |
| `src/common/enums.py` | `WorkflowStatus`, `TaskStatus`, `ErrorCode` | Mạnh Hiệp |
| `src/common/repository.py` | `WorkflowStateRepository` Protocol | Mạnh Hiệp (interface) |

- Mỗi người chỉ sửa file schema mình sở hữu.
- Không định nghĩa lại schema trong module riêng.
- Mọi thay đổi schema phải cập nhật `shared_contracts.md` và có review.

---

## File ownership

| Path | Owner |
|---|---|
| `src/common/task_plan.py` | Thành Bảo |
| `src/common/results.py` | Mạnh Hiệp |
| `src/common/enums.py` | Mạnh Hiệp |
| `src/common/repository.py` | Mạnh Hiệp |
| `src/agents/**` | Thành Bảo |
| `src/executor/**` | Mạnh Hiệp |
| `src/connectors/**` | Mạnh Hiệp |
| `src/services/mock/**` | Hoàng Anh |
| `src/db/**` | Hoàng Anh |
| `src/api/**` | Hoàng Anh |
| `frontend/**` | Hoàng Anh |

> Không sửa file thuộc owner khác nếu chưa trao đổi. `src/common/__init__.py` chỉ cập nhật trong PR integration.

---

## Phát triển song song

Ba thành viên không làm theo thứ tự tuần tự. Cả ba phát triển song song dựa trên shared contract:

| Thành viên | Tự test bằng |
|---|---|
| Thành Bảo | Hardcoded plans, fake `StandardResult`, fake failure signal |
| Mạnh Hiệp | `TaskPlan` fixture, `FakeConnector`, `InMemoryWorkflowStateRepository` |
| Hoàng Anh | FastAPI `TestClient`, Swagger/Postman, repository unit tests với PostgreSQL test DB |

**Quy tắc:**
- Thành Bảo không cần đợi Executor hay Mock API.
- Mạnh Hiệp không cần đợi Planner thật hay Mock API của Hoàng Anh.
- Hoàng Anh không cần đợi Executor hay Planner.
- Unit test không được gọi module của thành viên khác nếu có thể thay bằng fake.
- Chỉ block nếu shared contract chưa chốt hoặc có xung đột contract thực sự.

---

## Phân công theo tầng

| Thành viên | Tầng | Phạm vi code |
|---|---|---|
| **Thành Bảo** | Quyết định | `src/agents/` — Planner, Validator, Replanner, Policy |
| **Mạnh Hiệp** | Thực thi | `src/executor/`, `src/connectors/`, `src/common/results.py`, `src/common/enums.py`, `src/common/repository.py` |
| **Hoàng Anh** | Dịch vụ + Dữ liệu + UI | `src/services/mock/`, `src/db/`, `src/api/`, `frontend/` |

---

## Quy tắc bắt buộc cho AI

1. **Đọc `shared_contracts.md` trước khi sinh code** liên quan đến tool name, field, status, error code.
2. **Không tự đổi tên** tool, field, trạng thái hoặc error code.
3. **Không thêm tool mới** vào allowlist mà không có yêu cầu rõ ràng.
4. **Tất cả field nội bộ dùng `snake_case`.**
5. **Connector là ranh giới duy nhất** giữa hệ thống và API ngoài — Executor không gọi API trực tiếp.
6. **Mọi Connector phải tạo `StandardResult` object** — không trả raw API response vào Executor.
7. **Policy chạy trước Executor** — không thực thi action chưa qua Policy.
8. **Không tự sửa code của thành viên khác** mà không có yêu cầu.
9. **Nếu phát hiện xung đột contract** → dừng lại và báo, không tự sửa.
10. **Import schema từ đúng file tương ứng trong `src/common/`:** `task_plan.py`, `results.py`, `enums.py`, `repository.py` — không định nghĩa lại schema trong module riêng.

---

## Git workflow

```
feature/*  →  develop  →  main
hotfix/*   →  main (khẩn cấp)
```

| Nhánh | Mục đích |
|---|---|
| `main` | Ổn định, dùng cho Gate submission và release |
| `develop` | Tích hợp, merge từ feature trước khi lên main |
| `feature/*` | Mỗi task hoặc tính năng |
| `hotfix/*` | Sửa lỗi khẩn cấp trực tiếp vào main |

**Luồng:**
```
feature/P118-001-taskplan-validator
→ Pull Request vào develop
→ review (ít nhất 1 thành viên khác)
→ integration test pass
→ merge vào develop

develop (khi đạt Gate milestone)
→ Pull Request vào main
→ review
→ merge → tag nếu cần
```

**Quy tắc:**
- Không push thẳng lên `main`.
- Không push thẳng lên `develop` trừ thay đổi nhỏ đã được nhóm đồng ý.
- Pull latest `develop` trước khi bắt đầu task mới.
- Resolve conflict trước khi request review.

**Branch naming:**
```
feature/P118-001-taskplan-validator
feature/P118-002-executor
feature/P118-003-mock-services
hotfix/<mô-tả-ngắn>
```

**Commit convention:**
```
feat(planner): add task plan validator
fix(connector): map no_availability error
test(executor): add dependency order tests
docs(contract): clarify partial goal behavior
```

---

## Integration checkpoint cuối tuần 1

Chỉ sau khi unit test của cả ba module đều pass:

```
Thành Bảo cung cấp TaskPlan mẫu
→ Mạnh Hiệp chạy Executor với plan đó
→ Connector gọi Mock API của Hoàng Anh
→ Executor lưu state qua PostgreSQLWorkflowStateRepository
```

Chạy ít nhất: happy path · dependency test · `NO_AVAILABILITY` failure.

> Integration checkpoint là bước ghép cuối tuần — không phải điều kiện để từng người bắt đầu làm.

---

## Definition of Done

### Module Definition of Done (từng người tự đạt)

Một module hoàn thành độc lập khi:
- [ ] Code đúng ownership, không lấn sang file của thành viên khác
- [ ] Unit test pass với fake/stub — không cần module thật của người khác
- [ ] Contract không bị thay đổi trái phép
- [ ] `ruff check` và `ruff format` pass

### Integration Definition of Done (chỉ áp dụng ở PR tích hợp)

- [ ] `TaskPlan` thật từ Planner
- [ ] Executor thật chạy plan
- [ ] Connector thật gọi Mock API thật
- [ ] `PostgreSQLWorkflowStateRepository` thật lưu state
- [ ] Integration tests pass (happy path + dependency + failure scenario)

### Full PR Definition of Done

Một task chỉ được merge khi:

- [ ] Code đúng phạm vi task, không lấn sang module khác
- [ ] Tuân thủ `shared_contracts.md`
- [ ] Không tự đổi tool, field, status hoặc error code
- [ ] Unit test liên quan pass
- [ ] Integration test pass nếu task giao tiếp module khác
- [ ] `ruff check` và `ruff format` pass
- [ ] `shared_contracts.md` đã cập nhật nếu có thay đổi contract
- [ ] Không chứa secret hoặc credential
- [ ] Pull request đã được ít nhất 1 thành viên khác review
- [ ] Acceptance criteria trong task file đã đạt
- [ ] Branch đã merge vào `develop`

> Không merge nếu chỉ "chạy được" nhưng chưa test hoặc chưa cập nhật contract.

---

## Stack kỹ thuật

| Layer | Technology |
|---|---|
| Agent | LangGraph + Python 3.11 |
| LLM | Claude / OpenAI (qua API) |
| Shared schemas | `src/common/task_plan.py`, `src/common/results.py`, `src/common/enums.py`, `src/common/repository.py` |
| Backend | FastAPI |
| Database | PostgreSQL |
| Frontend | React + WebSocket |
| Deploy | Docker + Render/Railway |

---

## Timeline

| Mốc | Ngày |
|---|---|
| Gate 1 (docs) | 02/08/2026 ✅ |
| Gate 2 (MVP) | ~17/08/2026 |
| Demo Day | 03–05/09/2026 |
