# Kế hoạch nhóm — P-118

---

## 1. Phân công theo tầng

| Người         | Tầng phụ trách                | Công việc chính                                                                                                                                                                 | Kết quả cuối cùng                                                                                        |
| ------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| **Thành Bảo** | Quyết định                    | Hiểu mục tiêu người dùng; tạo `TaskPlan`; kiểm tra kế hoạch; phát hiện thiếu thông tin; lập kế hoạch lại khi lỗi; xây `Policy Engine`; xác định khi nào cần người dùng xác nhận | Goal → TaskPlan hợp lệ; phương án thay thế; quyết định `AUTO_ALLOWED`, `REQUIRES_APPROVAL`, `DENIED`     |
| **Mạnh Hiệp** | Thực thi                      | Xây Executor; chạy đúng thứ tự phụ thuộc; truyền dữ liệu giữa các bước; gọi dịch vụ qua Connector; xử lý lỗi; Retry; chống chạy trùng; hoàn tác khi cần                         | TaskPlan được thực hiện đúng; không chạy lại bước đã thành công; workflow tiếp tục hoặc phục hồi an toàn |
| **Hoàng Anh** | Dịch vụ, dữ liệu và giao diện | Xây mock API; PostgreSQL; lưu trạng thái workflow; giao diện người dùng; giao diện xác nhận; triển khai cloud; Live URL                                                         | Dịch vụ giả lập hoạt động; dữ liệu được lưu; người dùng theo dõi và xác nhận được; sản phẩm có link chạy |

---

## 2. Kế hoạch tổng thể theo 5 tuần

| Tuần       | Mục tiêu                                | Kết quả cần đạt                                                              |
| ---------- | --------------------------------------- | ---------------------------------------------------------------------------- |
| **Tuần 1** | Chốt thiết kế và làm nền kỹ thuật       | TaskPlan schema, API contract, mã lỗi, database schema, mock API skeleton    |
| **Tuần 2** | Chạy được luồng thành công bằng backend | Resident → Vehicle → Parking → Payment; truyền dữ liệu và lưu trạng thái     |
| **Tuần 3** | Thêm AI và xử lý lỗi                    | Goal tự nhiên → TaskPlan; partial goal; hỏi thiếu thông tin; Zone A → Zone B |
| **Tuần 4** | Thêm quyền kiểm soát và giao diện       | Policy Engine, HITL, Retry, giao diện theo dõi workflow                      |
| **Tuần 5** | Hoàn thiện và đánh giá                  | Idempotency, Compensation, kiểm thử metrics, deploy, video và slide          |

---

## 3. Chi tiết Tuần 1 — Phát triển song song

> Ba người bắt đầu cùng lúc sau khi contract đã chốt. Không ai phải chờ module của người khác.
> Mỗi người có Module Definition of Done riêng. Tích hợp là bước cuối tuần, không phải điều kiện để bắt đầu.

### Chiến lược độc lập

| Người | Không cần đợi | Dùng thay thế |
|---|---|---|
| Thành Bảo | Executor, Connector, Mock API | Hardcoded plans, fake `StandardResult`, fake failure signal |
| Mạnh Hiệp | LLM Planner, Mock API thật, PostgreSQL thật | `TaskPlan` fixture, `FakeConnector`, `InMemoryWorkflowStateRepository` |
| Hoàng Anh | Executor, Planner | FastAPI `TestClient`, Swagger, repository unit tests |

### Ngày 1 — Cả nhóm chốt contract (bắt buộc trước khi tách)

Contract đã có sẵn trong `docs/shared_contracts.md`. Ngày 1 chỉ cần confirm:

- 4 tool và input/output: đúng như `shared_contracts.md`
- File ownership: ai sở hữu file nào trong `src/common/`
- Branch naming và PR rules
- Integration checkpoint cuối tuần diễn ra thế nào

### Ngày 2–4 — Ba người phát triển độc lập

**Thành Bảo** — `src/common/task_plan.py` + `src/agents/`

| Việc | File | Module DoD |
|---|---|---|
| Định nghĩa `TaskPlan`, `Task`, `InputRef` | `src/common/task_plan.py` | Pydantic validate đúng |
| Viết Validator (allowlist, dependency, cycle, required fields) | `src/agents/validator.py` | Reject plan sai |
| Tạo plan mẫu full flow (T1→T2→T3→T4) | `src/agents/examples/plans.py` | Validator pass |
| Tạo plan mẫu partial (chỉ `book_parking`) | `src/agents/examples/plans.py` | Validator pass |
| Tạo plan mẫu partial (`book_parking → pay_fee`) | `src/agents/examples/plans.py` | Validator pass |
| Unit test Validator với hardcoded plans + fake results | `tests/test_validator.py` | Test pass, không cần Executor |

**Mạnh Hiệp** — `src/common/results.py`, `src/common/enums.py`, `src/common/repository.py` + `src/executor/`, `src/connectors/`

| Việc | File | Module DoD |
|---|---|---|
| Định nghĩa `StandardResult`, `WorkflowStatus`, `TaskStatus`, `ErrorCode` | `src/common/results.py`, `src/common/enums.py` | Thành Bảo + Hoàng Anh import được |
| Định nghĩa `WorkflowStateRepository` Protocol | `src/common/repository.py` | Interface rõ ràng |
| Viết `InMemoryWorkflowStateRepository` | `tests/fakes/in_memory_repository.py` | Lưu/đọc state trong dict |
| Viết `FakeConnector` (cấu hình trả success/`NO_AVAILABILITY`/timeout) | `tests/fakes/fake_connector.py` | Executor test không cần HTTP |
| Tạo `TaskPlan` fixtures | `tests/fixtures/task_plans.py` | Không cần Planner thật |
| Viết Executor skeleton + dependency check + data propagation | `src/executor/executor.py` | Chạy với FakeConnector + InMemory |
| Unit test: dependency order, data propagation, failure signal | `tests/test_executor.py` | Test pass, không cần Mock API thật |

**Hoàng Anh** — `src/services/mock/`, `src/db/`, `src/api/`

| Việc | File | Module DoD |
|---|---|---|
| Implement `ResidentService`, `TransportService`, `PaymentService` | `src/services/mock/*.py` | Endpoint chạy độc lập |
| Failure injection `?fail=NO_AVAILABILITY` | `src/services/mock/transport.py` | Error đúng format |
| Thiết kế PostgreSQL schema | `src/db/schema.sql` | Bảng workflow, task, execution |
| Implement `PostgreSQLWorkflowStateRepository` | `src/db/postgres_repository.py` | Lưu/đọc state qua Protocol |
| Test endpoint bằng FastAPI `TestClient` / Swagger | `tests/test_mock_services.py` | Response đúng, failure đúng |
| Test repository với PostgreSQL test DB | `tests/test_repository.py` | `create_workflow()` trả UUID, lưu/đọc đúng |

---

### Ngày 5 / Cuối tuần — Integration checkpoint

Chỉ diễn ra sau khi unit test của cả ba module đều pass. Đây là bước ghép, không phải điều kiện để từng người bắt đầu.

```
Thành Bảo cung cấp TaskPlan mẫu
→ Mạnh Hiệp chạy Executor với plan đó
→ Connector gọi Mock API của Hoàng Anh (HTTP thật)
→ Executor lưu state qua PostgreSQLWorkflowStateRepository
```

Chạy ít nhất:
- Happy path: T1→T2→T3→T4 thành công
- Dependency order: Parking không chạy trước Vehicle
- Failure: `NO_AVAILABILITY` → failure signal đúng

**Chưa tích hợp tuần 1:** LLM thật · Policy/HITL · Compensation · frontend hoàn chỉnh

---

## 4. Tiêu chí hoàn thành tuần 1

### Module Definition of Done (từng người tự đạt)

**Thành Bảo:**
- [ ] `TaskPlan`, `Task`, `InputRef` trong `src/common/task_plan.py` validate được
- [ ] Validator reject plan có tool ngoài allowlist, dependency sai, cycle
- [ ] Có plan mẫu full flow + 2 partial goal
- [ ] Unit test pass, không cần Executor hay Mock API

**Mạnh Hiệp:**
- [ ] `StandardResult`, `WorkflowStatus`, `TaskStatus`, `ErrorCode` trong `src/common/`
- [ ] `WorkflowStateRepository` Protocol trong `src/common/repository.py`
- [ ] `InMemoryWorkflowStateRepository` và `FakeConnector` hoạt động
- [ ] Executor chạy với fake, dependency và data propagation đúng
- [ ] Unit test pass, không cần Mock API hay PostgreSQL thật

**Hoàng Anh:**
- [ ] 3 mock service chạy độc lập, endpoint trả đúng schema
- [ ] `?fail=NO_AVAILABILITY` hoạt động
- [ ] `PostgreSQLWorkflowStateRepository` lưu/đọc đúng, `create_workflow()` trả UUID
- [ ] Test endpoint và repository pass, không cần Executor hay Planner

### Integration Definition of Done (cuối tuần)

- [ ] Executor chạy TaskPlan mẫu qua Connector → Mock API thật → PostgreSQL
- [ ] Happy path T1→T2→T3→T4 end-to-end
- [ ] Dependency test pass
- [ ] `NO_AVAILABILITY` failure signal đúng
- [ ] Không còn mâu thuẫn field giữa ba module
- [ ] Tất cả thay đổi lên GitHub qua pull request
