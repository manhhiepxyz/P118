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
- Nghiên cứu bối cảnh ứng dụng: dịch vụ cư dân và bất động sản, nơi một mục tiêu của người dùng cắt ngang NHIỀU đơn vị cung cấp độc lập (ban quản lý bãi xe, đội bảo trì, đơn vị chuyển nhà, bộ phận kinh doanh…) — mỗi đơn vị có quy trình duyệt riêng, không có ai đứng giữa nối họ lại
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
| Chưa xác định được customer journey phù hợp — vừa có giá trị thực tế vừa khả thi trong thời gian chương trình            | Lùi lại phân tích các dịch vụ cư dân có thật, chọn journey có dependency rõ ràng và dễ demo                | Chuyển sang tuần 2 để chốt                  |
| Cần phân biệt phần AI reasoning và phần logic deterministic                                                              | Đặt nguyên tắc: LLM đề xuất, deterministic system quyết định và thực thi                                   | Định hướng kiến trúc rõ ngay từ tuần 1      |

### Bài học

- Scope cần được thu hẹp sớm để tránh xây dựng quá nhiều thành phần nhưng không có workflow hoàn chỉnh
- Một Agent thực tế cần được thiết kế xoay quanh goal của người dùng, không chỉ xoay quanh từng API riêng lẻ
- LLM phù hợp với các tác vụ như hiểu mục tiêu và lập kế hoạch, nhưng các thao tác liên quan đến transaction, trạng thái và quyền thực thi cần được kiểm soát bằng code
- Trước khi triển khai, cần xác định rõ user journey, service boundaries và trách nhiệm của từng component

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

Use case mô phỏng một cư dân mới phải đi qua nhiều đơn vị cung cấp khác nhau để
hoàn thành một mục tiêu duy nhất.

> **Ghi chú về bối cảnh (bổ sung tuần 6).** Dự án KHÔNG phục vụ riêng một hệ
> sinh thái nào. Nó là nền tảng điều phối **đa nhà cung cấp**: mỗi dịch vụ thuộc
> về một đơn vị độc lập, có hàng đợi duyệt riêng và cổng kiểm quyền sở hữu
> fail-closed (`src/orchestration/provider_directory.py` —
> `BQL-PARK`, `BQL-SHUTTLE`, `BQL-SALES`, `FIX-01`, `MOV-01`). Tên dự án bất động
> sản trong dữ liệu demo (`src/common/projects.py`) chỉ là **dữ liệu mẫu** để câu
> tiếng Việt của người dùng có thứ để khớp, không phải đối tượng phục vụ và không
> kết nối API production của bất kỳ đơn vị nào.

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

---

## Week 3: 04/08/2026 - 10/08/2026

**Deterministic core + LLM Planner**

> Nội dung tuần này được dựng lại từ lịch sử git (30 commit, 05–11/08) vì nhật ký
> chưa được ghi lúc đó. Các mốc kỹ thuật là chính xác; phần cảm nhận và khó khăn
> của từng người thì nhóm cần đọc lại và bổ sung.

### Mục tiêu tuần này

- [x] Chạy happy path end-to-end với deterministic core
- [x] Implement mock services với StandardResult
- [x] Setup PostgreSQL + workflow state persistence
- [x] Tích hợp LLM Planner sau khi core ổn định

### Đã hoàn thành

**Executor và hợp đồng lỗi**

- Chuẩn hoá mã lỗi giữa Executor và connector: `UNKNOWN_ERROR` → `UNKNOWN_EXTERNAL_ERROR`, `VALIDATION_ERROR` → `INVALID_INPUT`; chốt hợp đồng `payment_status`
- `is_retryable` trả về `bool` thật thay vì giá trị truthy — một hàm quyết định "có thử lại không" mà trả về kiểu mơ hồ là chỗ để một lần thử lại vô hạn lọt qua

**Mock service và xác minh quyền sở hữu**

- Hoàn thiện bộ mock service theo `StandardResult`
- Xác minh quyền sở hữu căn hộ (hub thuần) — nền cho mọi ràng buộc "chỉ cư dân đã liên kết mới dùng được dịch vụ này"

**LLM Planner**

- Planner sinh TaskPlan có cấu trúc (structured output), không parse chuỗi tự do
- Nối Planner vào graph validation: kế hoạch LLM đề xuất phải qua Validator trước khi tới Executor
- Thêm OpenRouter làm provider LLM cấu hình được, cạnh OpenAI
- Dựng execution boundary cho runtime Gate 2 — ranh giới giữa "LLM đề xuất" và "hệ thống thực thi"

### Khó khăn & Giải pháp

| Khó khăn | Giải pháp | Kết quả |
| -------- | --------- | ------- |
| Mã lỗi giữa Executor và connector không khớp nhau, cùng một sự cố mang hai tên | Chốt một bảng mã dùng chung, đổi tên ở cả hai phía trong một lần | Một sự cố có đúng một tên; đường retry đọc được nó |
| Kế hoạch do LLM sinh có thể chứa tool hoặc dependency không hợp lệ | Đặt Validator giữa Planner và Executor, fail-closed | Kế hoạch sai bị chặn trước khi chạm provider |
| Phụ thuộc vào một provider LLM duy nhất | Thêm OpenRouter cấu hình được | Đổi provider không phải sửa code |

### Bài học

- Hợp đồng lỗi phải được chốt trước khi có nhiều tầng cùng đọc nó. Đổi tên mã lỗi sau khi ba tầng đã dùng là ba lần sửa và một lần bỏ sót.
- Ranh giới "LLM đề xuất / hệ thống quyết định" chỉ có giá trị khi có một Validator thật đứng giữa. Không có nó thì ranh giới ấy chỉ là một câu trong tài liệu.

---

## Week 4: 11/08/2026 - 17/08/2026

**Gate 2 — mở rộng dịch vụ, auth, và giao diện**

> Dựng lại từ lịch sử git (27 commit, 12–17/08). Xem ghi chú ở Week 3.

### Mục tiêu tuần này

- [x] Gate 2 — MVP chạy được end-to-end
- [x] Deploy lên cloud (Live URL)
- [x] 1 recovery scenario hoạt động

### Đã hoàn thành

**Ba dịch vụ mới, và hệ thống thôi là journey một chiều**

- Tích hợp end-to-end 3 dịch vụ: đặt lịch tham quan (tour), xe đưa đón (shuttle), đăng ký nhận tư vấn (consultation) — cùng với auth
- Thêm cổng phê duyệt xem nhà và portal cho đơn vị cung cấp: lần đầu có một người thứ hai (đơn vị) tham gia vào workflow, không chỉ khách và hệ thống
- Notifications

**Quyền và cô lập**

- Ràng buộc quyền sở hữu cư dân và cô lập workflow theo người dùng — một tài khoản không đọc được yêu cầu của tài khoản khác
- Gỡ một JWT bị commit nhầm và chặn loại file này quay lại repo

**Giao diện**

- Workspace hành trình nối vào backend thật (không còn dữ liệu giả)
- Chat-in-place, Response Agent, readiness check, luồng liên kết căn hộ
- Sửa trang lịch sử và trang kết quả nói sai về việc đã xong

**Chất lượng lời nói của hệ thống**

- Thay "vui lòng thử lại" bằng câu nói rõ VÌ SAO một bước thất bại
- Sửa lỗi câu trả lời bổ sung của người dùng bị bỏ im lặng, và tách rõ ai là người duyệt (khách hay đơn vị)
- Sáu lỗi lộ ra khi demo, kèm một kênh log để nhìn thấy chúng

### Khó khăn & Giải pháp

| Khó khăn | Giải pháp | Kết quả |
| -------- | --------- | ------- |
| Thêm ba dịch vụ cùng lúc trong khi core vừa ổn định | Canonical hoá service và làm cứng runtime Gate 2 trước, rồi mới nối dịch vụ mới vào | Ba dịch vụ chạy end-to-end trong tuần |
| Người dùng nhận "vui lòng thử lại" cho mọi loại lỗi | Ánh xạ từng mã lỗi sang một câu nói rõ nguyên nhân và việc cần làm | Câu từ chối trở thành câu hướng dẫn |
| Một JWT bị commit vào repo | Gỡ khỏi lịch sử và thêm luật chặn loại file này | Bộ quét secret sạch |
| Không biết chuyện gì xảy ra khi demo hỏng | Thêm kênh log riêng cho đường demo | Sáu lỗi được tìm ra và sửa trong cùng tuần |

### Bài học

- **Một người thứ hai trong workflow đổi cả kiến trúc.** Khi đơn vị cung cấp bắt đầu phải duyệt, "workflow đang chạy" không còn là một chuỗi tuyến tính — nó có những điểm dừng mà hệ thống không kiểm soát được thời gian.
- Secret lọt vào repo là chuyện của quy trình, không phải của người. Chặn bằng luật tự động rẻ hơn nhắc nhau.

---

## Week 5: 18/08/2026 - 24/08/2026

**HITL, cổng duyệt của đơn vị, và CI**

> Lịch sử git KHÔNG có commit nào trong khoảng 18–24/08: toàn bộ 19 commit của
> giai đoạn này được gộp và đẩy lên trong ngày 25/08. Nội dung dưới đây dựng lại
> từ nhóm commit ấy. Nhóm cần xác nhận lại phần nào thuộc tuần 5, phần nào thuộc
> tuần 6.

### Mục tiêu tuần này

- [x] HITL flow hoàn chỉnh
- [x] Saga Compensation hoạt động
- [x] React Timeline
- [ ] WebSocket realtime — chưa triển khai, giữ poll
- [ ] Video Demo + Pitch Deck

### Đã hoàn thành

**Cổng duyệt của đơn vị cung cấp — hạng mục lớn nhất**

- Mọi dịch vụ đi qua đơn vị cung cấp: MỘT hàng đợi duyệt, và đóng các cổng trước đây bị bỏ qua
- Bảo mật quyền sở hữu: đơn vị chỉ thấy và chỉ quyết định được việc của mình

**Bền vững trạng thái**

- Dòng thời gian sống sót qua restart
- Vá kế hoạch cũ khi người dùng `/continue` một yêu cầu đã dựng từ trước
- Chặn đường tắt quanh cổng thanh toán
- Sửa hàng loạt lỗi resume/retry/compensation, idempotency thanh toán, và `workflow_id` gãy ở boundary

**Giao diện**

- Khung hành trình (journey canvas): hiện từ giây đầu, có cột và đường nối, có nút Dừng
- Trang Lịch sử: giữ 15 yêu cầu gần nhất, 4 tab theo việc còn phải làm
- Trang chi tiết, tin nhắn gộp hội thoại, hạn ngạch theo người dùng
- Trang Quản trị / Hỗ trợ / đăng nhập; trả lời hội thoại tự nhiên
- Responsive cho workspace shell, admin shell và panel hành trình

**Fast planning lane và trí nhớ hội thoại**

- Thêm đường lập kế hoạch nhanh cho goal đã đủ dữ kiện
- Trí nhớ hội thoại cho planner; thêm Groq làm provider LLM

**CI**

- Chuyển sang runner self-hosted của BTC; cổng Postgres động vì runner dùng chung
- Dựng `.env` trước khi chạy test (`docker-compose.yml` đòi file tồn tại)
- Build frontend trong CI — bài kiểm bundle-sạch không được phép skip vĩnh viễn
- Ghi đè `LLM_PROVIDER` và `DAILY_WORKFLOW_QUOTA` vì `.env.example` đặt default khác giá trị test
- Dọn 70 lỗi `ruff check` và 34 file lệch `ruff format`

### Khó khăn & Giải pháp

| Khó khăn | Giải pháp | Kết quả |
| -------- | --------- | ------- |
| Runner self-hosted dùng chung cho mọi team, cổng 5432 thường bị chiếm | Để Docker tự chọn cổng trống, đọc lại cổng thật ở một step riêng | Hết lỗi "address already in use" |
| Test PostgreSQL tự skip khi thiếu DB → CI xanh giả | Thêm guard: `TEST_DATABASE_URL` phải được set, và không test nào được phép skip trong CI | Thiếu DB thì CI đỏ, không xanh giả |
| Bài kiểm bundle-sạch tự skip khi chưa build frontend, nên nó skip vĩnh viễn | Build frontend ngay trong CI | Bài kiểm chạy thật thay vì skip |
| Trạng thái workflow mất khi backend restart | Ghi dòng thời gian xuống DB thay vì giữ trong RAM | Người dùng mở lại trang vẫn thấy đúng tiến trình |

### Bài học

- **Skip âm thầm là rủi ro lớn nhất của một bộ test.** Thiếu DB thì cả tầng PostgreSQL không được kiểm mà CI vẫn xanh — và không ai nhìn vào một dấu tích xanh để hỏi "nó đã chạy gì?".
- Cổng duyệt của đơn vị không phải một màn hình, nó là một mô hình quyền sở hữu. Câu hỏi phải trả lời trước là "ai được quyết định việc này", không phải "nút Duyệt đặt ở đâu".

---

## Week 6: 25/08/2026 - 02/09/2026

### Mục tiêu tuần này

- [ ] Hoàn thiện 10 deliverables
- [ ] Rehearsal demo 2 lần
- [ ] Nộp `/gate submit` Demo Day

### Trạng thái hệ thống cuối chương trình

Journey MVP chốt ở tuần 2 (`Register Resident → Register Vehicle → Book Parking
→ Pay Fee`) chỉ còn là **một trong nhiều đường đi**. Hệ thống hiện phục vụ **8
dịch vụ người dùng chạm được**, thuộc **5 đơn vị cung cấp độc lập** — mỗi đơn vị
một hàng đợi duyệt riêng:

| Dịch vụ (tool) | Tên hiển thị | Đơn vị chịu trách nhiệm |
| --- | --- | --- |
| `schedule_property_viewing` | Đặt lịch tham quan | `BQL-SALES` |
| `register_property_interest` | Đăng ký nhận tư vấn | `BQL-SALES` |
| `book_shuttle` | Xe đưa đón tham quan | `BQL-SHUTTLE` |
| `register_vehicle` | Đăng ký phương tiện | `BQL-PARK` |
| `book_parking` | Giữ chỗ đỗ xe | `BQL-PARK` |
| `create_maintenance_request` | Yêu cầu bảo trì | `FIX-01` |
| `schedule_move` | Đăng ký chuyển nhà | `MOV-01` |
| `pay_fee` | Thanh toán phí (VNPay sandbox) | — cổng riêng của khách |

Ngoài ra có **6 tool hệ thống Planner KHÔNG chạm được** (`AGENT_FORBIDDEN_TOOLS`):
`change_parking_zone` (đổi khu cho chỗ đã giữ) và 5 lệnh huỷ `cancel_*`. Chúng chỉ
được dựng từ **kết quả đã chạy**, không từ câu người dùng gõ — để model tự viết ra
một `booking_id` là để nó huỷ lịch của người khác. Hai tool khác
(`register_resident`, `search_properties`) còn connector nhưng đã đóng đường tới
từ Agent.

Kiến trúc vì thế **không** là "một hệ sinh thái, nhiều dịch vụ" mà là **một bộ
điều phối đứng giữa nhiều nhà cung cấp không quen nhau**: `provider_directory.py`
gán chủ sở hữu cho từng dòng chờ duyệt, `provider_gateway.py` là chỗ duy nhất mọi
lời gọi ra ngoài đi qua, và cổng quyết định fail-closed — không phải đơn vị của
dòng đó thì nhận 404, không phải 403.

### Đã hoàn thành

**Đóng vòng hồi quy schedule-conflict**

- Thêm `title` cho `ScheduleConflictAction` ở cả backend (`src/models/schemas.py`) và frontend (`frontend/src/lib/types.ts`) — ba loại action kia đều có, thiếu một cái làm `WorkflowPage.tsx` vỡ build với `TS2339`
- Sửa `src/db/schema_migrations.sql`: thêm guard `IF to_regclass('workflows') IS NULL THEN RETURN` cho migration chạy trên database trống
- Thiết kế lại Test 12 (`test_schedule_conflict_route.py`) dùng `build_planner_graph` + fake Planner thay vì mock cả graph — bài kiểm cũ xanh kể cả khi đường thật hỏng

**Quản lý ngữ cảnh**

- Rà soát các tầng ngữ cảnh đang có: `existing_context` (trong một yêu cầu), `recalled` (giữa các yêu cầu, giới hạn 10 lượt), `repair_hints` (lỗi để Planner sửa), `account_context` (dữ liệu tài khoản đã tin cậy)
- Thêm luật vào `planner_prompt.py`: khi phải hỏi `supported_goal` (chưa biết người dùng muốn gì) thì `nho_lai` **không** được đề xuất giá trị cũ. Người dùng đang khai một ý định MỚI; gợi ý từ lịch sử làm họ xác nhận một việc không có trong đầu

**Sửa CI**

- Hai bài kiểm dùng ngày "29/8/2026" đã thành quá khứ → `_is_allowed_schedule_date()` từ chối, trường không được trích xuất. Đổi sang "20/12/2027"
- `ruff format` 5 file trong `tests/test_db/`
- Thêm `timeout-minutes: 20` vào job CI — trước đó không đặt nên nhận mặc định 6 giờ của GitHub, một lần treo là treo cả buổi. Thời gian **chờ hàng đợi** trên runner self-hosted dùng chung thì không sửa được từ phía repo

**Đổi/huỷ lịch: bỏ nút, đi bằng cuộc gọi**

- Truy vết báo cáo "bấm huỷ mà không gửi tới đơn vị". Kết quả: đường CANCEL nối đủ (`POST /support-requests` → đơn vị duyệt → `run_approved_requests` gọi `cancel_*` thật, có test phủ), nhưng **AMEND không có cặp nào trong `_ACTIONS`** — cố ý, vì "đồng ý cho đổi" chưa nói đổi sang lúc nào. Nghĩa là nút "Đổi lịch" ghim một hồ sơ mà đơn vị bấm Duyệt xong không có gì xảy ra
- Quyết định: bỏ **cả hai** nút. Mỗi dịch vụ đều cần một lượt xác nhận của đơn vị và đơn vị gọi điện để làm việc ấy, nên đổi/huỷ đi bằng chính cuộc gọi đó. Thay bằng một dòng nhắc, đặt MỘT lần dưới cả nhóm thẻ
- Giữ nguyên route và `run_approved_requests` phía backend: chúng chạy đúng, có test phủ, và là đường vào nếu sau này mở lại cổng đổi/huỷ

**Ba lỗi của workflow nhiều dịch vụ** (phát hiện khi bàn "huỷ 1 hay huỷ hết")

- `WorkflowPage` chọn thẻ kết quả bằng `[...successWithDetails].reverse().find(...)` — tức "bước có `Thời gian` đứng sau cùng trong mảng", là thứ tự Planner xếp bước chứ không phải mức quan trọng. Một yêu cầu hai buổi hẹn thì buổi còn lại rơi xuống mục "Các bước", gập sau nút "Chi tiết": không địa điểm, không người đón tiếp, không tải được `.ics`. Sửa: một thẻ cho mỗi bước có mốc thời gian
- `headline` lấy tên của một bước duy nhất, nên yêu cầu ba dịch vụ ra tiêu đề của một dịch vụ. Sửa: gọi tên từng cái
- Tên sự kiện `.ics` lấy từ tiêu đề chung, nên hai mốc vào lịch điện thoại dưới cùng một cái tên. Sửa: lấy từ `task.title` của chính thẻ ấy
- Bonus tìm thấy khi sửa: tên tệp tải về là chữ cứng `p118-lich-tham-quan.ics` — đúng lỗi `INT-003` mà file đó được viết lại để tránh, một chỗ đỗ xe tải về tệp mang tên "lịch tham quan". Nó lọt qua bài kiểm vì dấu gạch nối. Đổi thành `p118-${task_id}.ics`

**Tài liệu**

- Cập nhật README cho khớp code: allowlist của Planner (bỏ `search_properties`/`register_resident` đã bị đóng, thêm `book_shuttle`), bảng tool bị cấm và lý do, mục Features tách hai cổng người thật (khách duyệt tiền / đơn vị duyệt dịch vụ), Project Structure viết lại theo cây thật

### Khó khăn & Giải pháp

| Khó khăn | Giải pháp | Kết quả |
| -------- | --------- | ------- |
| CI báo kết quả sau ~4 giờ, không rõ treo hay đang chờ | Đọc lại `ci.yml`: không có `timeout-minutes` nên nhận mặc định 6 giờ; phần còn lại là chờ hàng đợi runner self-hosted dùng chung | Đặt `timeout-minutes: 20` (thời gian chạy thật ~5–10 phút). Chờ hàng đợi vẫn còn — không sửa được từ phía repo |
| Hai bài kiểm đỏ mà code không đổi | Chúng viết cứng ngày "29/8/2026", đã thành quá khứ nên `_is_allowed_schedule_date()` từ chối | Đổi sang ngày còn xa (20/12/2027). Bài học: ngày viết cứng là một quả bom hẹn giờ |
| "Bấm huỷ mà không gửi tới đơn vị" — không rõ hỏng ở khâu nào | Truy vết cả chuỗi: nút → route → hàng đợi đơn vị → quyết định → `run_approved_requests` | CANCEL nối đủ; AMEND không có cặp trong `_ACTIONS` nên duyệt xong không có gì xảy ra. Gỡ cả hai nút |
| Bỏ nút thì hai bài kiểm đọc file TSX đỏ, vì chúng đòi nút phải tồn tại | Không xoá bài kiểm — lật ngược đúng bất biến: "thẻ không được bày ra một lối đi không dẫn tới đâu" | `test_the_buttons_actually_send_something` → `test_the_result_card_offers_no_change_or_cancel_button` |
| Bài kiểm mới đỏ vì ghi chú trong TSX **chép lại** đúng chuỗi nó đang cấm | Tách `_bo_ghi_chu()` dùng chung, lọc ghi chú trước khi so | Ghi chú trung thực ("trước đây sai thế nào") không còn làm đỏ bài kiểm |

### Bài học

- **Một nút bấm được nhưng không dẫn tới đâu tệ hơn không có nút.** "Đổi lịch" gửi hồ sơ thành công, hiện câu xác nhận, và đơn vị bấm Duyệt — nhưng `_ACTIONS` không có cặp `AMEND` nào nên không lời gọi nào đi ra ngoài. Cả hai bên đều tưởng đã xong. Không có lỗi nào được ném, không có log nào bất thường.
- **Đường ngắn nhất đôi khi là đường đã có sẵn.** Đơn vị vốn đã gọi điện xác nhận từng dịch vụ. Dựng thêm một hàng đợi để khách gửi yêu cầu vào rồi chờ duyệt là con đường DÀI hơn cho cùng một kết quả — trong khi khách chỉ cần nói một câu trong cuộc gọi đang có.
- **Bản sửa đúng cho ca một dịch vụ có thể mang nguyên giả định sai sang ca nhiều dịch vụ.** `reverse().find()` ra đời để sửa một lỗi thật (thẻ hiện biên lai `pay_fee` thay vì chỗ đỗ xe) và sửa đúng — nhưng nó khoá vào giả định "một workflow có một kết quả". Chính codebase đã bác giả định ấy: `ScheduleConflictAction` có `task_a`/`task_b` vì hai bước trong cùng một workflow đụng giờ nhau.
- **Bài kiểm đọc mã nguồn phải lọc ghi chú.** Một ghi chú giải thích "trước đây sai thế nào" buộc phải chép lại đoạn mã cũ. Không lọc thì viết ghi chú trung thực làm đỏ CI, và cách rẻ nhất để CI xanh trở lại là xoá ghi chú.
- **Chữ cứng lách qua bài kiểm bằng một dấu gạch nối.** Bài kiểm tìm `"tham quan"`; tên tệp là `p118-lich-tham-quan.ics`. Một bài kiểm khớp chuỗi chỉ chặn được đúng hình dạng nó biết.

