# Weekly Journal — P-118

Đề tài: AI Agent orchestrate đa dịch vụ hoàn thành tác vụ liên hoàn (đặt nhà → xe → dịch vụ)
Mã đề tài: PTNT-02 — STT 158
Nhóm: P-118
Thành viên: Lâm Thành Bảo, Phí Hoàng Anh, Nguyễn Mạnh Hiệp
Mentor: Bùi Trung Hiếu
Ngày bắt đầu chương trình: 23/07/2026
Đề tài: PTNT-02 — STT 158

---

## Week 1: 23/07/2026 - 27/07/2026

**Kick-off & Lập đội**

### Mục tiêu tuần này

- [x] Hoàn tất quá trình kick-off và thành lập nhóm
- [x] Hiểu yêu cầu tổng quan của đề tài PTNT-02
- [x] Xác định bài toán thực tế mà dự án cần giải quyết
- [x] Thiết lập repository và công cụ phục vụ quá trình phát triển
- [x] Hình thành định hướng kỹ thuật ban đầu cho hệ thống AI Agent orchestration

### Đã hoàn thành

- Tham gia kick-off chương trình Cohort 3
- Thành lập nhóm P-118 gồm: Lâm Thành Bảo, Phí Hoàng Anh, Nguyễn Mạnh Hiệp
- Nhận đề tài PTNT-02 — STT 158 về AI Agent orchestration đa dịch vụ
- Phân tích yêu cầu ban đầu của đề tài và thảo luận các hướng triển khai khả thi
- Nghiên cứu bối cảnh ứng dụng trong hệ sinh thái dịch vụ VinHomes
- Xác định pain point ban đầu: người dùng thường phải thao tác trên nhiều hệ thống hoặc dịch vụ riêng biệt để hoàn thành một mục tiêu duy nhất
- Xác định định hướng sản phẩm:
  - Agent không chỉ trả lời câu hỏi
  - Agent cần có khả năng lập kế hoạch và thực hiện chuỗi tác vụ thay người dùng thông qua các service/API
  - Hệ thống cần theo dõi trạng thái của từng tác vụ và có khả năng xử lý khi một bước gặp lỗi
- Thiết lập GitHub repository P-118
- Thiết lập AI logging hooks theo yêu cầu của chương trình
- Thảo luận và phân công trách nhiệm ban đầu trong nhóm
- Bắt đầu tìm hiểu các công nghệ dự kiến sử dụng: LangGraph, FastAPI, PostgreSQL, REST API

### Quyết định và định hướng quan trọng

Trong quá trình phân tích, nhóm nhận thấy scope ban đầu của đề tài tương đối rộng nếu hướng tới một Agent có thể orchestration mọi loại dịch vụ.

Nhóm thống nhất rằng giá trị chính của dự án không nằm ở việc:

> "LLM có thể gọi nhiều API."

Mà nằm ở khả năng:

> "Người dùng đưa ra một mục tiêu, hệ thống lập kế hoạch, thực hiện nhiều tác vụ liên quan và tiếp tục xử lý workflow khi có sự cố."

Do đó, nhóm quyết định sẽ tập trung vào một customer journey cụ thể thay vì xây dựng một hệ thống orchestration tổng quát ngay từ đầu.

### Khó khăn & Giải pháp

| Khó khăn                                                                                                                 | Giải pháp                                                                                                  | Kết quả                                     |
| ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| Phạm vi đề tài ban đầu rộng, bao gồm nhiều vấn đề: planning, API orchestration, failure recovery, state management, HITL | Quyết định tập trung vào một customer journey cụ thể, không xây dựng hệ thống orchestration tổng quát ngay | Có hướng thu hẹp rõ ràng để thiết kế tuần 2 |
| Chưa xác định được customer journey phù hợp — vừa có giá trị thực tế vừa khả thi trong thời gian chương trình            | Lùi lại phân tích các dịch vụ VinHomes thực tế, chọn journey có dependency rõ ràng và dễ demo              | Chuyển sang tuần 2 để chốt                  |
| Cần phân biệt phần AI reasoning và phần logic deterministic                                                              | Đặt nguyên tắc: LLM đề xuất, deterministic system quyết định và thực thi                                   | Định hướng kiến trúc rõ ngay từ tuần 1      |

### Bài học

- Scope cần được thu hẹp sớm để tránh xây dựng quá nhiều thành phần nhưng không có workflow hoàn chỉnh
- Một Agent thực tế cần được thiết kế xoay quanh goal của người dùng, không chỉ xoay quanh từng API riêng lẻ
- LLM phù hợp với các tác vụ như hiểu mục tiêu và lập kế hoạch, nhưng các thao tác liên quan đến transaction, trạng thái và quyền thực thi cần được kiểm soát bằng code
- Trước khi triển khai, cần xác định rõ user journey, service boundaries và trách nhiệm của từng component

### Kế hoạch tuần sau

- [ ] Chốt customer journey cho MVP
- [ ] Phân tích use case chi tiết
- [ ] Xây dựng Project Brief và PRD
- [ ] Thiết kế architecture và service contract
- [ ] Nghiên cứu sâu hơn LangGraph, Saga Pattern, Human-in-the-Loop và failure recovery
- [ ] Hoàn thiện các deliverable phục vụ Gate 1

---

## Week 2: 28/07/2026 - 02/08/2026

**Gate 1 — Chốt đề tài**

### Mục tiêu tuần này

- [x] Thu hẹp scope dự án thành một MVP khả thi
- [x] Xác định customer journey và các service cần orchestration
- [x] Hoàn thiện architecture ở mức đủ rõ để bắt đầu implementation
- [ ] Chuẩn bị đầy đủ tài liệu Gate 1
- [x] Điều chỉnh roadmap kỹ thuật theo timeline Gate 2 và Demo Day

### Đã hoàn thành

**1. Chốt customer journey MVP**

Sau khi phân tích nhiều hướng ứng dụng, nhóm chọn customer journey:

```
Register Resident → Register Vehicle → Book Parking → Pay Fee
```

Use case mô phỏng quá trình một cư dân mới sử dụng nhiều dịch vụ trong hệ sinh thái VinHomes.

Nhóm thu gọn hệ thống còn 3 mock services: Resident Service, Transport/Parking Service, Payment Service.

Các tool chính mà Planner có thể sử dụng: `register_resident`, `register_vehicle`, `book_parking`, `pay_fee`.

Healthcare và các service khác được đưa ra khỏi MVP để tránh mở rộng scope không cần thiết.

**2. Phân tích use case và user flow**

Nhóm xây dựng luồng tổng quát:

```
User Goal → Agent hiểu mục tiêu → Tạo TaskPlan → Kiểm tra dependency
→ Thực hiện từng task → Gọi các service tương ứng → Lưu trạng thái
→ Xử lý nếu task gặp lỗi
```

Điểm khác biệt chính so với chatbot thông thường là Agent có khả năng thực hiện action, thay vì chỉ đưa ra hướng dẫn cho người dùng.

**3. Thiết kế architecture**

Nhóm phân tách hệ thống thành hai phần chính:

```
LLM Layer:              Goal understanding, Task planning, Replanning
Deterministic Layer:    TaskPlan validation, dependency scheduling,
                        policy, execution, workflow state,
                        retry, recovery, compensation, persistence
```

Architecture theo luồng:

```
LLM Planner → Proposed TaskPlan → TaskPlan Validator
→ Scheduler → Policy → Executor → Tool Registry → Service Adapter → Mock Service
```

**4. Thiết kế Tool Registry và Adapter**

Executor không gọi trực tiếp HTTP endpoint — mọi call đi qua Tool Registry → Adapter → Service API.

Tool Registry giới hạn action mà Agent được phép sử dụng (allowlist). Các compensation action như `cancel_resident`, `refund_payment` không được phép xuất hiện trong plan của LLM — chỉ được hệ thống nội bộ gọi khi rollback.

**5. Thiết kế Standard Service Result**

```json
// Success
{
  "success": true,
  "status": "CONFIRMED",
  "transaction_id": "txn_001",
  "data": {},
  "error": null
}

// Failure
{
  "success": false,
  "status": "FAILED",
  "transaction_id": null,
  "data": null,
  "error": {
    "code": "NO_AVAILABILITY",
    "message": "No parking slot available",
    "retryable": false
  }
}
```

Việc xác định thành công/thất bại và recovery strategy được thực hiện deterministic dựa trên các field có cấu trúc — không phụ thuộc vào LLM đọc natural language.

**6. Nghiên cứu failure recovery**

Nhóm nghiên cứu và đưa vào architecture các hướng xử lý: Retry, Replan, Human escalation, Compensation, Fail workflow.

Hero scenario dự kiến cho Gate 2:

```
Book Parking Zone A → NO_AVAILABILITY → Recovery Handler
→ REPLAN → Replanner chọn Zone B → Book Parking Zone B → SUCCESS
```

**7. Nghiên cứu Saga Pattern và HITL**

Nghiên cứu Saga Pattern để xử lý side effects khi workflow gặp lỗi giữa chừng:

```
T1 COMPLETED → T2 COMPLETED → T3 FAILED
→ Compensate T2 → Compensate T1
```

Nghiên cứu HITL cho các action cần xác nhận (thanh toán, thao tác rủi ro). Các phần này hiện mới ở mức thiết kế kiến trúc, chưa được triển khai.

**8. Điều chỉnh scope theo timeline**

```
Gate 2 (~17/08):   Natural language goal, TaskPlan, Validator, Scheduler,
                   Executor, ≥3 services, data propagation,
                   persistent state, 1 recovery scenario, Live URL

Demo Day:          Policy Engine, HITL, Retry, Saga Compensation,
                   Idempotency, React Timeline, WebSocket, Evaluation
```

### Khó khăn & Giải pháp

| Khó khăn                                                    | Giải pháp                                                                | Kết quả                       |
| ----------------------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------- |
| Cân bằng giữa kiến trúc đủ tốt và thời gian triển khai ngắn | Phân tầng rõ theo gate — chỉ làm đủ cho Gate 2, nâng cao để Demo Day     | Roadmap khả thi               |
| Failure handling có thể nhanh chóng trở nên phức tạp        | Chỉ handle failure mode thực tế trong hero scenario, tránh over-engineer | Scope failure handling rõ     |
| Xác định ranh giới LLM vs deterministic                     | Nguyên tắc: LLM đề xuất, Validator quyết định, Executor thực thi         | Kiến trúc rõ ràng và testable |
| Timeline Gate 1 → Gate 2 ngắn                               | Bắt đầu bằng deterministic skeleton, thêm LLM sau                        | Giảm rủi ro debug đồng thời   |

### Bài học

- LLM chỉ nên đề xuất kế hoạch — hệ thống deterministic phải validate trước khi thực thi
- Không nên coi Agent là một LLM có quyền gọi mọi API
- Tool allowlist và Policy Engine là boundary quan trọng để Agent hoạt động an toàn
- Structured service response giúp error handling đáng tin cậy hơn natural language
- Failure recovery là điểm khác biệt chính giữa orchestration Agent và tool-calling chatbot
- Working system > feature count — một workflow end-to-end tốt hơn nhiều component dở dang
- Nên hoàn thành deterministic happy path trước, sau đó mới tích hợp LLM

### Trạng thái cuối tuần 2

| Hạng mục                          | Trạng thái                     |
| --------------------------------- | ------------------------------ |
| Chốt đề tài và problem            | ✅ Hoàn thành                  |
| Customer journey MVP              | ✅ Hoàn thành                  |
| Scope 3 services                  | ✅ Hoàn thành                  |
| Tool allowlist                    | ✅ Hoàn thành thiết kế         |
| StandardResult / service contract | ✅ Hoàn thành thiết kế         |
| Architecture Diagram              | ✅ Hoàn thành                  |
| Gate 2 roadmap                    | ✅ Hoàn thành                  |
| Project Brief                     | 🔄 Đang hoàn thiện             |
| PRD                               | 🔄 Đang hoàn thiện             |
| Wireframe / UI Flow               | 🔄 Đang hoàn thiện             |
| README                            | ✅ Hoàn thành                  |
| AI Log                            | ✅ Đã setup, tiếp tục cập nhật |
| JOURNAL tuần 1 & 2                | ✅ Hoàn thành                  |

### Kế hoạch tuần sau

Bắt đầu Gate 2 Phase A — Deterministic Core (chưa tích hợp LLM Planner):

```
Hardcoded TaskPlan → Task Scheduler → Executor
→ Tool Registry → Service Adapter → 3 Mock Services → PostgreSQL
```

Mục tiêu tuần 3: chạy được happy path end-to-end, có data propagation giữa các bước và lưu được workflow state — trước khi thay hardcoded plan bằng LLM-generated TaskPlan.

---

## Week 3: 04/08/2026 - 10/08/2026

### Mục tiêu tuần này

- [ ] Chạy happy path end-to-end với deterministic core
- [ ] Implement 3 mock services với StandardResult
- [ ] Setup PostgreSQL + workflow state persistence
- [ ] Tích hợp LLM Planner sau khi core ổn định

### Đã hoàn thành

-

### Khó khăn & Giải pháp

| Khó khăn | Giải pháp | Kết quả |
| -------- | --------- | ------- |
|          |           |         |

### Bài học

-

### Kế hoạch tuần sau

-

## Week 4: 11/08/2026 - 17/08/2026

### Mục tiêu tuần này

- [ ] Gate 2 — MVP chạy được end-to-end
- [ ] Deploy lên cloud (Live URL)
- [ ] 1 recovery scenario hoạt động

### Đã hoàn thành

-

### Khó khăn & Giải pháp

| Khó khăn | Giải pháp | Kết quả |
| -------- | --------- | ------- |
|          |           |         |

### Bài học

-

### Kế hoạch tuần sau

-

## Week 5: 18/08/2026 - 24/08/2026

### Mục tiêu tuần này

- [ ] HITL flow hoàn chỉnh
- [ ] Saga Compensation hoạt động
- [ ] React Timeline + WebSocket
- [ ] Video Demo + Pitch Deck

### Đã hoàn thành

-

### Khó khăn & Giải pháp

| Khó khăn | Giải pháp | Kết quả |
| -------- | --------- | ------- |
|          |           |         |

### Bài học

-

### Kế hoạch tuần sau

-

## Week 6: 25/08/2026 - 02/09/2026

### Mục tiêu tuần này

- [ ] Hoàn thiện 10 deliverables
- [ ] Rehearsal demo 2 lần
- [ ] Nộp `/gate submit` Demo Day

### Đã hoàn thành

-

### Khó khăn & Giải pháp

| Khó khăn | Giải pháp | Kết quả |
| -------- | --------- | ------- |
|          |           |         |

### Bài học

-

### Kế hoạch tuần sau

- Demo Day 03–05/09/2026
