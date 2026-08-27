# Báo Cáo Đánh Giá Dự Án P-118
**AI Agent Điều Phối Đa Dịch Vụ Trong Hệ Sinh Thái Dịch Vụ Cư Dân (VinHomes Mockup)**
*Khóa: VinUni AI20K Cohort 3 - STT 158 - Nhóm PTNT-02*

---

## 1. Problem Fit (Độ Phù Hợp Của Giải Pháp)

### 1.1 Bài toán thực tế
Trong các khu đô thị quy mô lớn, cư dân thường phải tương tác với hàng loạt các dịch vụ phân mảnh. Một kịch bản điển hình như "chuyển vào ở" yêu cầu cư dân phải thao tác trên nhiều phân hệ khác nhau: Đăng ký cư dân -> Đăng ký phương tiện -> Đăng ký bãi đỗ xe -> Thanh toán phí dịch vụ. Việc này gây ra sự đứt gãy trong trải nghiệm người dùng, tốn thời gian và dễ dẫn đến sai sót.

### 1.2 Giải pháp AI Agent
Thay vì buộc người dùng phải học cách sử dụng từng phân hệ (navigate UI), giải pháp của dự án P-118 cung cấp một điểm chạm duy nhất (Single Point of Contact) thông qua ngôn ngữ tự nhiên. 
Người dùng chỉ cần nhập một "Mục tiêu" (Goal) như: *"Tôi mới chuyển đến, muốn đăng ký cư dân, đăng ký xe VFast và thuê bãi đỗ xe"*. 
Hệ thống AI Agent sẽ đóng vai trò là một "Người điều phối" (Orchestrator):
- Tự động suy luận ra các bước cần làm.
- Sắp xếp các bước theo đúng trình tự logic (Dependencies).
- Gọi các dịch vụ ngầm.
- Xử lý các ngoại lệ (hết chỗ, lỗi thanh toán) và báo cáo lại.

---

## 2. Tư Duy Sản Phẩm (Product Mindset)

### 2.1 Product Requirements Document (PRD) Tóm lược
- **Mục tiêu:** Xây dựng một AI Agent có khả năng "Action" (hành động), không chỉ là "Chat" (trả lời).
- **Phạm vi (Scope):** Tập trung vào luồng 4 dịch vụ cốt lõi: `Resident` (Cư dân), `Vehicle` (Phương tiện), `Parking` (Bãi đỗ) và `Payment` (Thanh toán). Mở rộng Gate 2: `Property Discovery`.
- **Ràng buộc (Constraints):** Agent không được phép tự ý thực hiện các hành động rủi ro cao như ký hợp đồng hay mua nhà (Limitation of Scope).

### 2.2 Customer Journey (Luồng Người Dùng MVP)
**Happy Path:**
1. Người dùng ra lệnh bằng văn bản (NL Input).
2. Agent phân giải và trả về kế hoạch thực thi rõ ràng (Task Plan).
3. Agent thực hiện đăng ký Resident.
4. Agent tiếp tục đăng ký Vehicle.
5. Agent đặt chỗ Parking thành công.
6. Khi đến bước Payment, Agent tạm dừng (Pause) và yêu cầu người dùng xác nhận (HITL).
7. Người dùng bấm "Approve" -> Agent hoàn tất thanh toán và kết thúc quy trình.

---

## 3. Chất Lượng Giải Pháp (Kiến Trúc Hệ Thống & SAD)

### 3.1 Sơ Đồ Kiến Trúc (Architecture Diagram)
Hệ thống tuân thủ chặt chẽ mô hình **Separation of Concerns** (Phân tách Trách nhiệm), chia làm 3 tầng độc lập:

1. **Tầng Cognitive (Nhận thức - LLM & Planner):**
   - Chạy trên nền tảng LangGraph.
   - Nhận Context và Goal, sinh ra `TaskPlan`.
   - Có `Validator` để chặn các plan sai logic/format.
   - Có `Policy Engine` quyết định task nào chạy tự động, task nào cần xin phép.
2. **Tầng Executor (Thực thi & Quản lý State):**
   - Nhận `TaskPlan` từ Planner. Không có logic AI ở đây.
   - Gọi tuần tự hoặc song song các dịch vụ thông qua Connector.
   - Quản lý trạng thái bằng `PostgreSQL` (`WorkflowStateRepository`).
   - Kích hoạt `Replanner` nếu nhận được *failure signal* từ Connectors.
3. **Tầng Integration (Kết nối ngoại vi):**
   - Các `Connectors` làm nhiệm vụ giao tiếp với hệ thống mock (API).
   - Chuẩn hóa toàn bộ dữ liệu trả về thành `StandardResult` để Executor dễ dàng tiêu hóa.

### 3.2 Lựa Chọn Thiết Kế (Design Choices)
- **LangGraph vs LangChain Agent:** Chọn LangGraph để có khả năng kiểm soát state linh hoạt, dễ dàng implement cơ chế Human-In-The-Loop (HITL) và time-travel.
- **Tách biệt Planner và Executor:** Đảm bảo LLM không bao giờ có thể tự ý thực thi các HTTP Request nguy hiểm. Đây là kiến trúc phòng thủ sâu (Defense in Depth).

---

## 4. Chất Lượng Triển Khai (Implementation Quality)

- **End-to-End Flow:** Sản phẩm đã nối thông toàn bộ luồng từ UI (React) -> Backend (FastAPI) -> Agent Graph -> DB. 
- **Khả Năng Chống Lỗi (Resilience):** Khi test kịch bản `NO_AVAILABILITY` (bãi đỗ xe hết chỗ), hệ thống không sụp đổ mà tự động bắt tín hiệu lỗi, giữ lại các task đã thành công (Resident, Vehicle), và nhờ Planner tính toán lại bước tiếp theo. Điều này khớp hoàn toàn với thiết kế đã vẽ ra.

---

## 5. Chất Lượng UI / UX

- **Real-time Feedback:** Giao diện sử dụng WebSockets hoặc Polling để cập nhật trạng thái của từng Task trong thời gian thực. Người dùng không phải f5 trang để xem kết quả.
- **Human-In-The-Loop (HITL) Mượt Mà:** Khi một workflow cần xác nhận, UI lập tức hiển thị Modal hoặc khu vực chờ duyệt rất rõ ràng (hiển thị số tiền, dịch vụ cần thanh toán), đảm bảo nguyên tắc minh bạch của Responsible AI.
- **Quản lý đa phiên:** Giao diện Admin/Workspace cho phép theo dõi nhiều Workflow cùng lúc.

---

## 6. Chất Lượng Code (Code Quality)

### 6.1 Tổ Chức Thư Mục (Directory Structure)
Sử dụng cấu trúc theo Feature/Layer rõ ràng, chống giẫm chân nhau:
- `src/agents/`: Logic ra quyết định.
- `src/executor/`: Logic điều phối.
- `src/connectors/`: Giao tiếp API.
- `src/common/`: Shared contracts (tránh Git conflict khi định nghĩa Schema).

### 6.2 Bảo Mật & Quản Lý Thông Tin
- **Secret Management:** Mọi API keys (LLM, VNPay) được nạp qua biến môi trường (`.env`), không commit lên Github. Config class được dùng để validate các biến này lúc startup.
- **Guardrails:** AI bị "nhốt" trong một sandbox ảo. Các tool chỉ giới hạn trong danh sách được cấp phép.

### 6.3 Sự Phối Hợp Giữa Con Người Và AI
- **Con người (Team):** Chốt luồng nghiệp vụ, định nghĩa Shared Contracts, thiết kế kiến trúc, setup Unit Tests, và định hướng rule cho dự án.
- **AI (Tools):** Hỗ trợ sinh boilerplate, gen dữ liệu mock, tối ưu logic hàm xử lý, và hỗ trợ refactor UI/UX dựa trên mô tả. **Tuyệt đối không để AI tự ý thay đổi contract dùng chung nếu chưa được team duyệt.**

---

## 7. Kỷ Luật & Làm Việc Nhóm

- **Parallel Development:** Team áp dụng phương pháp phát triển song song xuất sắc. Bằng cách chốt trước `shared_contracts.md` (định nghĩa `TaskPlan`, `StandardResult`), cả 3 thành viên Thành Bảo, Mạnh Hiệp, Hoàng Anh có thể code song song mà không block nhau, dùng fake objects để test độc lập.
- **Git Workflow Nghiêm Ngặt:** Tuân thủ luồng `feature/ -> develop -> main`. Mọi PR phải có review, phải pass Unit Test mới được tích hợp vào nhánh chính.
- **Tài Liệu Hóa (Documentation):** Sở hữu file `AGENTS.md` - trái tim của dự án, mọi quy tắc đều được ghi chép minh bạch. Đạt chuẩn Definition of Done khắt khe trước khi merge code.
