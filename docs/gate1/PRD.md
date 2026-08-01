# Product Requirements Document — P-118

## 1. Document Information

| Mục | Nội dung |
|---|---|
| **Dự án** | P-118 |
| **Đề tài** | AI Agent orchestrate đa dịch vụ hoàn thành tác vụ liên hoàn (đặt nhà → xe → dịch vụ) |
| **Mã đề tài** | PTNT-02 — STT 158 |
| **Nhóm** | Lâm Thành Bảo · Phí Hoàng Anh · Nguyễn Mạnh Hiệp |
| **Mentor** | Bùi Trung Hiếu |
| **Chương trình** | VinUni AI20K Cohort 3 — Build Phase |
| **Ngày** | 02/08/2026 |
| **Gate** | Gate 1 |

---

## 2. Executive Summary

P-118 xây dựng một AI Agent có khả năng nhận mục tiêu nhiều bước từ người dùng bằng ngôn ngữ tự nhiên, tự lập kế hoạch, gọi tuần tự các dịch vụ có phụ thuộc lẫn nhau, truyền dữ liệu giữa các bước, theo dõi trạng thái, phục hồi khi một bước gặp lỗi, và yêu cầu người dùng xác nhận trước những hành động quan trọng.

Giá trị cốt lõi là **goal-oriented multi-service orchestration với failure-aware execution và controlled autonomy** — không phải "LLM gọi nhiều API".

MVP tập trung vào một business domain (dịch vụ nhà ở/cư dân), sử dụng một khu đô thị giả lập làm bối cảnh minh họa. End-to-end hero journey: `Register Resident → Register Vehicle → Book Parking → Pay Fee`. Người dùng có thể yêu cầu toàn bộ chuỗi hoặc chỉ một phần tùy theo mục tiêu và dữ liệu hiện có. Các dịch vụ trong MVP sử dụng mock implementation.

---

## 3. Problem Statement

Trong một hệ sinh thái có nhiều dịch vụ, người dùng muốn đạt một mục tiêu duy nhất thường phải:

- Tự xác định đúng dịch vụ nào cần dùng và theo thứ tự nào
- Nhập lại thông tin tương tự ở nhiều hệ thống khác nhau
- Thực hiện từng tác vụ riêng lẻ và chờ kết quả
- Tự phát hiện khi một bước thất bại và tự tìm cách xử lý
- Mất nhiều thời gian cho thao tác thủ công thay vì tập trung vào mục tiêu

**Ví dụ cụ thể:** Một cư dân mới chuyển vào căn hộ cần thực hiện: đăng ký cư dân, đăng ký phương tiện, đặt chỗ đậu xe, và thanh toán phí. Mỗi bước là một thao tác riêng và phụ thuộc vào bước trước — ví dụ, không thể đăng ký phương tiện nếu chưa có Resident ID.

Nếu một bước thất bại (ví dụ, Zone A hết chỗ), người dùng phải tự phát hiện và tự tìm phương án thay thế.

---

## 4. Target User

**Primary user:** Cư dân hoặc người dùng cá nhân trong hệ sinh thái dịch vụ nhà ở/cư dân cần thực hiện một hoặc nhiều tác vụ liên quan đến các dịch vụ sinh hoạt.

**Đặc điểm:**
- Có mục tiêu rõ ràng nhưng không muốn tự điều phối từng bước
- Không cần biết hệ thống backend hoạt động thế nào
- Muốn biết tiến trình đang ở đâu và có thể kiểm soát những quyết định quan trọng

**Không phải primary user của MVP:** Admin, operator, developer. Các vai trò này thuộc Extension.

---

## 5. User Pain Points

| # | Pain Point | Biểu hiện |
|---|---|---|
| P1 | Phải nhập lại thông tin | Họ tên, địa chỉ nhập lại ở nhiều hệ thống |
| P2 | Phải biết thứ tự đúng | Không đăng ký cư dân trước thì không đăng ký xe được |
| P3 | Không biết bước nào đang chờ | Không có màn hình tổng hợp trạng thái |
| P4 | Phải tự xử lý khi lỗi | Nếu Zone A hết chỗ, người dùng tự tìm Zone B |
| P5 | Mất thời gian không cần thiết | Thao tác thủ công nhiều bước lặp đi lặp lại |

---

## 6. Product Goal

> **Cho phép người dùng mô tả một mục tiêu nhiều bước bằng ngôn ngữ tự nhiên và để Agent tự lập kế hoạch, thực hiện các dịch vụ theo đúng thứ tự phụ thuộc, truyền dữ liệu giữa các bước, theo dõi trạng thái, xử lý lỗi trong phạm vi cho phép, yêu cầu xác nhận khi cần, và trả kết quả cuối cùng.**

---

## 7. Value Proposition

| Góc nhìn | Giá trị |
|---|---|
| **Người dùng** | Một goal thay cho nhiều tác vụ thủ công |
| **Correctness** | Output của bước trước tự động trở thành input bước sau |
| **Resilience** | Agent xử lý lỗi và tìm phương án thay thế thay vì dừng lại |
| **Control** | Người dùng được yêu cầu xác nhận trước các action được Policy Engine phân loại REQUIRES_APPROVAL — bao gồm financial, destructive hoặc sensitive actions |
| **Transparency** | Mỗi bước có trạng thái rõ ràng — không phải black box |

---

## 8. Primary Customer Journey

**MVP Customer Journey:**

```
Register Resident
      ↓ (resident_id)
Register Vehicle
      ↓ (vehicle_id)
Book Parking
      ↓ (booking_id)
Pay Fee
```

**3 service domains:**
1. Resident Service
2. Transport / Parking Service
3. Payment Service

**Agent tools (Planner được phép dùng):**
- `register_resident`
- `register_vehicle`
- `book_parking`
- `pay_fee`

> **Lưu ý:** Đây là end-to-end hero journey được dùng cho demo. Agent tạo TaskPlan phù hợp với mục tiêu và dữ liệu hiện có của người dùng — không nhất thiết chạy đủ 4 bước nếu một số bước đã hoàn thành hoặc không cần thiết cho goal cụ thể đó.

> MVP sử dụng **mock services**. Hệ thống không kết nối với production API của VinHomes hoặc bất kỳ nhà cung cấp nào. Dữ liệu không phải dữ liệu cư dân thực.

---

## 9. User Stories

### US-01 — Submit Goal in Natural Language

> *As a resident, I want to describe what I need in natural language so that I do not have to navigate multiple service systems manually.*

**Acceptance Criteria:**
- Người dùng nhập mục tiêu bằng tiếng Việt tự nhiên
- Hệ thống tạo workflow mới
- Người dùng được chuyển sang màn hình theo dõi tiến trình
- Goal được lưu và có thể xem lại sau

---

### US-02 — View Agent Task Plan

> *As a user, I want to see the steps the Agent has planned so that I understand what it intends to do and can follow the workflow progress.*

**Acceptance Criteria:**
- Task list hiển thị khi workflow được tạo và chạy
- Thứ tự phụ thuộc giữa các bước rõ ràng
- Trạng thái từng bước (PENDING / RUNNING / COMPLETED / FAILED) phản ánh trạng thái thực trong hệ thống

---

### US-03 — Multi-Service Execution with Data Propagation

> *As a user, I want the Agent to automatically pass information between steps so that I do not have to re-enter the same data.*

**Acceptance Criteria:**
- Agent thực hiện ≥3 service calls trong một workflow
- Output quan trọng của bước trước (ví dụ: `resident_id`) được tự động dùng làm input bước sau
- Người dùng không cần nhập lại thông tin đã có

---

### US-04 — Track Workflow Progress

> *As a user, I want to track the current status of each step so that I know how the workflow is progressing.*

**Acceptance Criteria:**
- Trạng thái workflow tổng hiển thị (RUNNING / COMPLETED / FAILED / RECOVERING)
- Từng task có trạng thái riêng (PENDING / RUNNING / COMPLETED / FAILED)
- UI phản ánh trạng thái persisted của hệ thống

> **Lưu ý:** Gate 2 cho phép polling / manual refresh. Realtime update qua WebSocket thuộc Demo Day Final MVP (FR-15).

---

### US-05 — Failure Recovery without Full Restart *(Gate 2)*

> *As a user, I want the Agent to try alternative approaches when a step fails so that I do not have to restart the entire workflow from scratch.*

**Hero scenario:** `book_parking(Zone A)` → `NO_AVAILABILITY` → Agent replan → `book_parking(Zone B)` → SUCCESS → workflow tiếp tục

**Acceptance Criteria:**
- User thấy bước đầu thất bại và lý do
- Workflow chuyển sang trạng thái RECOVERING
- Agent tự tìm phương án thay thế mà không cần user can thiệp
- Các bước đã COMPLETED không bị chạy lại
- Nếu recovery thành công, workflow tiếp tục từ chỗ dừng

---

### US-06 — Human Approval for Sensitive Actions *(Demo Day Final MVP)*

> *As a user, I want the Agent to ask for my confirmation when an action requires a decision that it is not allowed to make autonomously.*

**Acceptance Criteria:**
- Action được Policy Engine phân loại `REQUIRES_APPROVAL` KHÔNG được execute trước khi user approve — bất kể là financial, destructive hay sensitive action
- UI hiển thị action và context phù hợp: với payment hiển thị số tiền; với alternative decision hiển thị các lựa chọn hoặc trade-off cần thiết
- User có thể Duyệt hoặc Từ chối
- Approve → Agent thực thi action → workflow tiếp tục
- Reject → workflow xử lý theo ATOMIC rule
- Action được phân loại `AUTO_ALLOWED` có thể execute mà không cần HITL
- Action được phân loại `DENIED` không được execute bất kể user request

> **Demo Day implementation example:** payment ≥ 300,000 VND → `REQUIRES_APPROVAL`; payment < 300,000 VND → `AUTO_ALLOWED`; destructive action → `REQUIRES_APPROVAL`. Đây là hardcoded demonstration rules, không phải định nghĩa tổng quát của HITL.

> **Phạm vi:** HITL thuộc Demo Day Final MVP, không là yêu cầu bắt buộc cho Gate 2.

---

### US-07 — View Workflow History and Result

> *As a user, I want to review past workflows to understand what was done, including any recovery actions taken.*

**Acceptance Criteria:**
- Có thể xem lại goal, trạng thái, từng bước và kết quả
- Recovery history hiển thị (lần thử, lý do, kết quả từng lần)
- Kết quả cuối cùng rõ ràng (thành công / thất bại / đã hoàn tác)
- Không hiển thị system internals (raw JSON, prompt, LangGraph state)

---

### US-08 — Partial Task Execution

> *As a resident, I want the system to execute only the tasks required for my current goal, so that I do not repeat services that have already been completed or are not needed.*

**Acceptance Criteria:**
- Agent tạo TaskPlan dựa trên mục tiêu hiện tại và dữ liệu đã có — không chạy lại bước đã COMPLETED
- Nếu goal chỉ yêu cầu một phần công việc (ví dụ: chỉ đặt chỗ đậu, đã có resident và vehicle), plan chỉ gồm các bước cần thiết
- Nếu thiếu dữ liệu bắt buộc, Agent hỏi thêm hoặc đề xuất bước cần thực hiện trước — không tự đoán dữ liệu

---

## 10. Functional Requirements

| ID | Requirement | Mô tả | Priority | Gate |
|---|---|---|---|---|
| FR-01 | Natural Language Goal Input | User nhập goal bằng tiếng Việt; hệ thống tạo workflow | P0 | Gate 2 |
| FR-02 | AI Task Planning | Agent tạo TaskPlan có dependency rõ ràng từ goal | P0 | Gate 2 |
| FR-03 | TaskPlan Validation | Plan được validate (schema, tool allowlist, dependency) trước khi execute | P0 | Gate 2 |
| FR-04 | Multi-Service Execution | Agent thực hiện ≥3 service calls trong một workflow | P0 | Gate 2 |
| FR-05 | Dependency & Data Propagation | Output bước N tự động là input bước N+1 theo dependency | P0 | Gate 2 |
| FR-06 | Persistent Workflow State | Trạng thái workflow và từng task được lưu vào database | P0 | Gate 2 |
| FR-07 | Workflow Timeline / Status View | User xem được trạng thái từng task và overall workflow | P0 | Gate 2 |
| FR-08 | Failure Recovery — REPLAN | `NO_AVAILABILITY` → Agent replan và thử alternative | P0 | Gate 2 |
| FR-09 | Cloud Deployment / Live URL | API deploy cloud, có Live URL | P0 | Gate 2 |
| FR-10 | Policy-based Action Control | Rule-based engine phân loại mỗi ProposedAction thành AUTO_ALLOWED, REQUIRES_APPROVAL hoặc DENIED — bao gồm financial, destructive và sensitive actions | P1 | Demo Day |
| FR-11 | HITL Approval Flow | Agent dừng và yêu cầu user approve trước action được Policy Engine phân loại REQUIRES_APPROVAL; AUTO_ALLOWED không cần HITL; DENIED không được execute | P1 | Demo Day |
| FR-12 | Retry on Transient Failure | Tự động retry khi service timeout hoặc tạm thời không phản hồi | P1 | Demo Day |
| FR-13 | Saga Compensation | Hoàn tác các bước có side effect theo reverse order khi cần | P1 | Demo Day |
| FR-14 | Idempotency | Tránh duplicate side effect (ví dụ: không charge 2 lần khi retry) | P1 | Demo Day |
| FR-15 | Realtime UI Update | Timeline cập nhật realtime qua WebSocket | P1 | Demo Day |
| FR-16 | Workflow Detail / Result View | Xem chi tiết goal, tasks, recovery history, kết quả cuối | P1 | Demo Day |
| FR-17 | Admin Dashboard | Xem toàn bộ workflow, filter, audit log | P2 | Extension |

---

## 11. Non-Functional Requirements

| ID | Requirement | Mô tả |
|---|---|---|
| NFR-01 | **Reliability** | Workflow state không phụ thuộc vào LLM memory. Sau mỗi bước, kết quả được persist vào database. |
| NFR-02 | **Safety** | LLM không được gọi tool ngoài allowlist. TaskPlan phải qua validation trước khi execute. Action REQUIRES_APPROVAL không được execute trước khi user approve qua HITL. Action DENIED bị block trực tiếp bởi Policy Engine — không execute và không cần user approval để override. |
| NFR-03 | **Auditability** | Có thể reconstruct workflow status từ database — goal, từng task, payload, result, recovery history. |
| NFR-04 | **Recoverability** | Hero scenario (NO_AVAILABILITY → REPLAN → alternative) không yêu cầu restart toàn bộ workflow. |
| NFR-05 | **Idempotency** | *(Final MVP)* Retry cùng một action không tạo duplicate side effect nhờ idempotency key. |
| NFR-06 | **Responsiveness** | Sau khi user submit goal, hệ thống phản hồi xác nhận workflow đã được tạo. Long-running action hiển thị trạng thái thay vì block UI. |
| NFR-07 | **Usability** | User không cần hiểu API, service endpoint hoặc orchestration internals. |
| NFR-08 | **Maintainability** | Service integration được abstraction qua Adapter layer. Chuyển từ mock sang real API không yêu cầu rewrite orchestration logic. |

> **Không đặt SLA production** (uptime %, latency tuyệt đối) khi hệ thống chưa được benchmark.

---

## 12. Gate 2 Working MVP — ~17/08/2026

Chương trình yêu cầu Gate 2: *"Agent gọi được ≥3 services và có Live URL."*

### Scope Gate 2

```
User Goal (natural language)
        ↓
   Goal Parser / Planner (LLM)
        ↓
   TaskPlan Validator (deterministic)
        ↓
      Scheduler
        ↓
      Executor
        ↓
    Tool Registry
        ↓
  Service Adapters
        ↓
  3 Mock Services
        ↓
   Persistent State (PostgreSQL)
```

### Gate 2 phải chứng minh

- User nhập goal bằng natural language
- Agent tạo TaskPlan hợp lệ
- TaskPlan được validate trước execution
- Agent gọi ≥3 mock services theo dependency order
- Data propagation đúng theo dependency chain: `resident_id` T1→T2, `vehicle_id` T2→T3, booking/payment info T3→T4
- Workflow state persist trong PostgreSQL
- Happy path hoàn thành: `Register Resident → Register Vehicle → Book Parking → Pay Fee`
- Hero failure scenario: `book_parking(Zone A)` → `NO_AVAILABILITY` → REPLAN → `book_parking(Zone B)` → SUCCESS → workflow tiếp tục
- FastAPI deploy cloud với Live URL
- Basic integration tests

### Gate 2 KHÔNG bắt buộc

Policy Engine đầy đủ · HITL · Saga Compensation · Idempotency production-grade · React UI · WebSocket realtime

---

## 13. Demo Day Final MVP — 03-05/09/2026

Sau Gate 2, hoàn thiện toàn bộ target product.

### Bổ sung sau Gate 2

| Category | Additions |
|---|---|
| **Safety** | Policy Engine (5 rules), HITL Approval Modal, Policy Denied Handler |
| **Reliability** | Retry (transient failures), Saga Compensation (reverse order), Idempotency key |
| **Recovery** | Full recovery mapping (RETRY / REPLAN / REQUEST_HUMAN / COMPENSATE / FAIL) |
| **UI** | React Timeline (realtime WebSocket), HITL Modal, Workflow Detail đầy đủ |
| **Evidence** | Happy path test, recovery test, compensation test, HITL test, video ≤5 phút, pitch deck |

---

## 14. Success Metrics

### Gate 2 Metrics

| ID | Metric | Đo bằng | Target |
|---|---|---|---|
| M1 | **Multi-service completion rate** | % test workflow hoàn thành ≥3 service calls thành công | ≥90% happy-path test cases |
| M2 | **TaskPlan validity rate** | % LLM-generated plans pass TaskPlan Validator | ≥90% trên predefined test goals (cần benchmark) |
| M3 | **Hero recovery success rate** | % NO_AVAILABILITY scenarios recover thành công qua REPLAN | 100% trên deterministic demo test cases |
| M4 | **Data propagation correctness** | % workflow mà required ID/output được truyền đúng sang step phụ thuộc | 100% integration tests |
| M5 | **Workflow state traceability** | % executed task có persisted state đủ để reconstruct workflow | 100% integration tests |

### Demo Day Additional Metrics

| ID | Metric | Target |
|---|---|---|
| M6 | **HITL safety** | 0 action classified REQUIRES_APPROVAL executes before user approval in the test suite |
| M7 | **Retry idempotency** | 0 duplicate side effect trong retry test cases |
| M8 | **Compensation correctness** | Completed compensatable steps rollback đúng reverse order trong predefined failure tests |

> **Không dùng vanity metrics** ("AI accuracy 99%") nếu không có định nghĩa và phương pháp đo rõ ràng.

---

## 15. Safety & Control

Agent trong P-118 hoạt động dưới các ràng buộc kiểm soát bắt buộc:

1. **Tool Allowlist:** Agent chỉ được sử dụng 4 predefined tools (`register_resident`, `register_vehicle`, `book_parking`, `pay_fee`). Không thể gọi arbitrary endpoint.

2. **Plan Validation:** TaskPlan do LLM đề xuất phải qua validation deterministic (schema, allowlist, dependency) trước khi bất kỳ service nào được gọi.

3. **Policy Control:** *(Demo Day)* Mỗi ProposedAction được đánh giá bởi Policy Engine trước khi execute. Action được phân loại `AUTO_ALLOWED`, `REQUIRES_APPROVAL` hoặc `DENIED` theo hardcoded rules — bao gồm financial, destructive và sensitive actions. Demo Day implementation example: payment ≥ 300,000 VND → `REQUIRES_APPROVAL`; payment < 300,000 VND → `AUTO_ALLOWED`; destructive action → `REQUIRES_APPROVAL`. Đây là demonstration rules, không phải định nghĩa tổng quát.

4. **HITL Approval:** *(Demo Day)* Agent dừng và yêu cầu user approve trước khi execute action được Policy Engine phân loại `REQUIRES_APPROVAL`. Agent không pause với `AUTO_ALLOWED`. `DENIED` không được execute bất kể request. LLM không được tự quyết định bypass Policy — deterministic Policy/HITL layer kiểm soát việc execute.

5. **Compensation:** *(Demo Day)* Các bước có side effect có compensation function tương ứng. Khi workflow cần rollback, hệ thống hoàn tác theo đúng thứ tự ngược.

6. **No Arbitrary Execution:** LLM không trực tiếp kiểm soát transaction, state, retry hay compensation. Các phần này được xử lý bởi deterministic orchestration code.

---

## 16. Risks & Mitigations

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Scope quá rộng | Không đủ thời gian | Giữ 1 business domain · 1 hero journey · 3 mock services |
| R2 | LLM tạo plan không hợp lệ | Workflow lỗi hoặc gọi tool không cho phép | TaskPlan Validator + tool allowlist bắt buộc |
| R3 | Service failure giữa workflow | Workflow stuck, dữ liệu treo | Persistent state + recovery handler |
| R4 | Duplicate side effect khi retry | User bị charge 2 lần | Idempotency key (Demo Day) |
| R5 | Agent tự quyết định action nhạy cảm | Financial/destructive action ngoài ý muốn | Policy Engine phân loại REQUIRES_APPROVAL + HITL buộc user approve (Demo Day) |
| R6 | Không kịp Gate 2 | Miss Gate 2 | Implement deterministic core trước LLM; LLM thêm sau |
| R7 | UI chiếm quá nhiều thời gian | Không kịp core | Gate 2 UI tối giản; polish chỉ sau Gate 2 |

---

## 17. Assumptions

- MVP sử dụng **mock services** — không kết nối production API VinHomes.
- Mock services expose REST interface tương tự service thật.
- Customer journey mô phỏng một business flow hợp lý, không phải quy trình nghiệp vụ thực tế của VinHomes.
- Payment là mock transaction — không có giao dịch tài chính thực.
- Data sử dụng trong demo là dữ liệu giả, không phải thông tin cư dân thật.
- Authentication và security production không thuộc Gate 2 core scope.
- LLM provider sẵn sàng và có thể gọi từ môi trường deploy cloud.

---

## 18. Non-Goals / Out of Scope

**MVP không nhằm:**

- Orchestration mọi dịch vụ VinHomes
- Thay thế hệ thống backend hiện hữu
- Agent có quyền gọi arbitrary API ngoài allowlist
- LLM tự quyết định transaction tài chính không kiểm soát
- Production-scale distributed orchestration
- Multi-tenant enterprise platform

**Extension (sau Demo Day):**

- Healthcare Service (Scenario 2)
- Admin Dashboard
- `PARTIAL_ALLOWED` completion mode
- Parallel task execution
- Dynamic Policy Engine
- Production idempotency database (DB-backed với TTL)
- Distributed queue
- Multi-tenant support
- Integration với production VinHomes API

---

## 19. UX Reference

| Artifact | Link |
|---|---|
| Wireframe & UI Flow | `docs/gate1/wireframe.md` |
| Architecture Diagram | `docs/architecture_diagram.md` |

**3 core screens:**
1. **Home / Goal Input** — nhập natural language goal, xem recent workflows
2. **Workflow Timeline** — theo dõi tiến trình từng bước và recovery state; realtime WebSocket ở Demo Day Final MVP
3. **Workflow Detail / Result** — xem chi tiết sau khi hoàn thành

**HITL Modal** — overlay trên Workflow Timeline, không phải màn hình riêng.

**Admin Dashboard** — Extension, không thuộc core UI.

---

## 20. Timeline

| Giai đoạn | Thời gian | Mục tiêu |
|---|---|---|
| Gate 1 Preparation | 31/07 – 02/08 | Chốt design, docs, architecture, wireframe |
| Gate 2 Phase A: Deterministic Core | 03/08 – 07/08 | Mock services, adapters, scheduler, executor, happy path |
| Gate 2 Phase B: LLM Planning | 08/08 – 11/08 | Goal parser, planner, TaskPlan validator |
| Gate 2 Phase C: Hero Recovery | 12/08 – 14/08 | NO_AVAILABILITY → REPLAN → Zone B |
| Gate 2 Release | 15/08 – 17/08 | Deploy cloud, Live URL, integration test, docs |
| Policy + HITL | 18/08 – 21/08 | Policy engine, HITL modal, approve/reject flow |
| Reliability | 22/08 – 25/08 | Retry, Saga Compensation, idempotency; nộp hồ sơ 25/08 |
| Demo Day Preparation | 26/08 – 02/09 | React Timeline UI, WebSocket, test evidence, video, pitch |
| Demo Day | 03–05/09 | Present |

---

## 21. Acceptance Criteria

### Gate 2 Definition of Done

- [ ] Live URL hoạt động (FastAPI deploy cloud)
- [ ] User nhập goal bằng natural language
- [ ] Agent tạo TaskPlan hợp lệ
- [ ] TaskPlan qua validation (schema + allowlist + dependency)
- [ ] Agent gọi ≥3 mock services theo dependency order
- [ ] Data propagation: output bước N → input bước N+1
- [ ] Workflow state persist trong PostgreSQL
- [ ] Happy path hoàn thành: `Register Resident → Register Vehicle → Book Parking → Pay Fee`
- [ ] Hero scenario: `book_parking(Zone A)` → `NO_AVAILABILITY` → REPLAN → `book_parking(Zone B)` → SUCCESS → workflow tiếp tục
- [ ] Basic integration tests (happy path + recovery scenario)
- [ ] README và Architecture Diagram cập nhật
- [ ] WORKLOG + Journal hoàn thành

### Demo Day Definition of Done

- [ ] Natural language → Plan → Validate → Execute
- [ ] ≥3 services, dependency, data propagation
- [ ] Persistent state, audit trail
- [ ] Policy control (auto-allowed vs requires-approval)
- [ ] HITL: action REQUIRES_APPROVAL không execute trước approval; AUTO_ALLOWED không cần HITL; DENIED không execute
- [ ] Retry: transient failure không restart workflow
- [ ] Saga Compensation: completed steps rollback đúng reverse order
- [ ] Idempotency: retry không tạo duplicate side effect
- [ ] React Timeline UI với realtime update
- [ ] Live URL
- [ ] Test/evaluation evidence (happy path, recovery, compensation, HITL)
- [ ] Video demo ≤5 phút
- [ ] Pitch Deck 10 slides
- [ ] Tài liệu kiểm thử
- [ ] JOURNAL 6 tuần + AI Log

---

*PRD — Gate 1 — 02/08/2026 — P-118 Build Phase*
