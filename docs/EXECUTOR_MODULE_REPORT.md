git status

# BÁO CÁO KỸ THUẬT & TRÌNH BÀY MENTOR

## Module P118-002: Tầng Thực Thi (Executor & Connectors Layer)

> **Dự án:** P-118 — AI Agent điều phối đa dịch vụ nhà ở / cư dân
> **Chương trình:** PTNT-02 · STT 158 · VinUni AI20K Cohort 3
> **Người thực hiện:** Mạnh Hiệp (Owner tầng Executor)
> **Phạm vi code:** `src/executor/`, `src/connectors/`, `src/common/results.py`, `src/common/enums.py`, `src/common/repository.py`
> **Chi tiết branch:** `fix/P118-002-executor-contract`

---

## 1. Tổng quan & Mục tiêu Module

Tầng Executor đóng vai trò **"Xương sống Thực thi"** trong kiến trúc AI Agent P-118, nhận bản kế hoạch (`TaskPlan`) được tạo bởi Planner (Thành Bảo) và phối hợp với các dịch vụ backend (Hoàng Anh) để hoàn thành mục tiêu của người dùng.

```
[Planner] ---> TaskPlan ---> [Policy Engine] ---> [Executor] ---> Connectors ---> [Mock APIs]
                                                      │
                                                      └───> [WorkflowStateRepository]
```

### Các nhiệm vụ chính của Executor:

1. **Điều phối theo phụ thuộc (Dependency Order Execution):** Phân tích và thực thi các task theo đúng đồ thị phụ thuộc (DAG). Task chỉ được chạy khi tất cả các task phụ thuộc đã `SUCCESS`.
2. **Truyền dữ liệu động (Dynamic Data Propagation):** Tự động bóc tách và giải mã `InputRef` từ kết quả của các task trước đó (ví dụ: lấy `resident_id` từ Task 1 truyền làm input cho Task 2).
3. **Ranh giới chuẩn hóa hệ thống (Connector Pattern):** Executor không bao giờ gọi HTTP/API trực tiếp. Mọi tương tác external đều thông qua các `Connector` chuyên biệt để chuyển đổi dữ liệu về `StandardResult` nội bộ.
4. **Lưu vết trạng thái (State Persistence & Idempotency):** Cập nhật trạng thái từng bước vào `WorkflowStateRepository`. Đảm bảo các task đã `SUCCESS` không bao giờ bị chạy lại khi Re-planner tái lập kế hoạch.
5. **Quản lý lỗi & Phục hồi:** Phân loại lỗi chính xác (`retryable` vs `non-retryable`) và gửi tín hiệu thất bại qua callback để Re-planner đưa ra quyết định xử lý phù hợp.

---

## 2. Kiến trúc & Thiết kế Kỹ thuật (Key Architectural Decisions)

### 2.1 Pattern cô lập Connector (Boundary Isolation)

* **Vấn đề:** Các API bên ngoài có thể thay đổi định dạng response, thêm các field thừa hoặc trả về các HTTP status code khác nhau.
* **Giải pháp:** Xây dựng `Connector` đóng vai trò adapter.
  * Chỉ trích xuất các **canonical fields** theo đúng `docs/shared_contracts.md`.
  * Đơn giản hóa response bằng cách gói toàn bộ vào đối tượng `StandardResult`.
  * Chuẩn hóa `payment_status` về đúng allowlist (`PENDING`, `PAID`, `FAILED`, `REFUNDED`), chủ động map legacy status `SUCCESS` -> `PAID`.

### 2.2 Đảm bảo An toàn Kiểu dữ liệu & Fallback Lỗi (Type Safety & Robust Error Handling)

* **Mã lỗi chuẩn hóa (`ErrorCode`):** Loại bỏ hoàn toàn các enum cũ/lỗi (`UNKNOWN_ERROR`, `VALIDATION_ERROR`). Chuẩn hóa thành `INVALID_INPUT`, `UNKNOWN_EXTERNAL_ERROR`, `NO_AVAILABILITY`, v.v.
* **Property `is_retryable` an toàn tuyệt đối:** Đảm bảo `StandardResult.is_retryable` luôn trả về giá trị `bool` (`True`/`False`), loại bỏ rủi ro trả về `NoneType` gây crash khi truyền callback `on_failure`.

### 2.3 Thiết kế Kiểm thử Độc lập (Independent Testability)

* Áp dụng **Dependency Injection** đối với `httpx.AsyncClient` và `WorkflowStateRepository`.
* Triển khai lớp giả lập `FakeConnector` và `InMemoryWorkflowStateRepository` trong thư mục `tests/fakes/`.
* Cho phép toàn bộ 92 unit tests chạy siêu nhanh (**< 0.3s**) trên RAM mà không phụ thuộc vào PostgreSQL hay backend API thật.

---

## 3. Cấu trúc File & Phân công Chi tiết

| Thư mục / File                          | Chức năng & Mục đích sử dụng                                                                                                                                            |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`src/executor/executor.py`**    | Class`Executor`: chứa thuật toán lập lịch công việc, resolve `InputRef`, quản lý trạng thái task, và gọi callback khi lỗi.                                   |
| **`src/connectors/base.py`**      | Abstract Class`Connector`: định nghĩa khung chuẩn cho toàn bộ Connector (`tool_names`, `execute()`).                                                               |
| **`src/connectors/resident.py`**  | `ResidentConnector`: xử lý tool `register_resident` (`POST /api/residents`). Trả về `resident_id`.                                                                 |
| **`src/connectors/transport.py`** | `TransportConnector`: xử lý tool `register_vehicle` (`POST /api/vehicles`) và `book_parking` (`POST /api/parking/bookings`). Trả về canonical booking metadata. |
| **`src/connectors/payment.py`**   | `PaymentConnector`: xử lý tool `pay_fee` (`POST /api/payments`). Chuẩn hóa `payment_status` về `PAID`.                                                          |
| **`src/common/results.py`**       | Dataclass`StandardResult`: đại diện cho kết quả chuẩn hóa từ Connector gửi về cho Executor.                                                                        |
| **`src/common/enums.py`**         | Định nghĩa các enum trạng thái:`WorkflowStatus`, `TaskStatus`, `ErrorCode`.                                                                                        |
| **`src/common/repository.py`**    | Protocol`WorkflowStateRepository`: interface lưu trữ vết trạng thái workflow & task.                                                                                    |
| **`tests/fakes/`**                | Thư mục chứa các Fake class (`FakeConnector`, `InMemoryWorkflowStateRepository`) phục vụ unit test.                                                                  |
| **`tests/test_executor.py`**      | Bộ unit test kiểm tra luồng thực thi, truyền dữ liệu, xử lý lỗi và partial goal của Executor.                                                                      |
| **`tests/test_connectors.py`**    | Bộ unit test kiểm tra URL endpoint, HTTP payload, mapping status code và normalize payment status.                                                                          |

---

## 4. Kết quả Kiểm thử & Chất lượng Code (Quality Assurance)

| Tiêu chí kiểm thử                | Kết quả đạt được     | Chi tiết                                            |
| ------------------------------------ | --------------------------- | ---------------------------------------------------- |
| **Unit Test Suite**            | **92 / 92 PASS**      | 100% test pass trong khoảng time ~0.25 giây        |
| **Linter Check (`ruff`)**    | **All checks passed** | Không có lỗi syntax, unused import hay code style |
| **Formatter Check (`ruff`)** | **Formatted 100%**    | Toàn bộ các file được format đạt chuẩn PEP8 |
| **Whitespace & Line Endings**  | **Clean**             | Không có trailing whitespace hay lỗi EOF          |

---

## 5. Kịch bản Trình bày / Demo với Mentor

Khi trình bày với Mentor, bạn có thể minh họa 3 kịch bản chính đã được bao phủ bởi Unit Test:

### 🎭 Kịch bản 1: Full Flow Customer Journey (Happy Path)

* **Goal:** Đăng ký cư dân -> Đăng ký xe -> Đặt chỗ đỗ xe -> Thanh toán phí.
* **Minh họa:** `Executor` khởi chạy 4 task theo thứ tự T1 -> T2 -> T3 -> T4. Trích xuất thành công `resident_id` (từ T1) đưa vào T2, `vehicle_id` (từ T2) đưa vào T3, `booking_id` + `amount` (từ T3) đưa vào T4.
* **Test kiểm chứng:** `test_execute_full_flow` & `test_data_propagation_*`.

### 🎭 Kịch bản 2: Partial Goal (User đã có dữ liệu sẵn)

* **Goal:** Đặt chỗ đỗ xe và thanh toán (User đã có `vehicle_id` trong context).
* **Minh họa:** Executor tự động bỏ qua bước đăng ký cư dân & đăng ký xe, chỉ thực hiện T1 (`book_parking`) -> T2 (`pay_fee`).
* **Test kiểm chứng:** `test_partial_goal_book_parking_only` & `test_partial_goal_book_and_pay`.

### 🎭 Kịch bản 3: Service Error & Signal cho Replanner

* **Goal:** Xử lý khi bãi xe hết chỗ (`NO_AVAILABILITY`) hoặc lỗi mạng (`SERVICE_TIMEOUT`).
* **Minh họa:** `TransportConnector` bắt lỗi từ HTTP response, map thành `ErrorCode.NO_AVAILABILITY` với `retryable=False`. Executor cập nhật task status thành `FAILED`, dừng workflow và phát signal qua callback `on_failure` để Re-planner tiếp nhận.
* **Test kiểm chứng:** `test_no_availability_mapping` & `test_failure_callback_receives_fallback_error_code`.

---

## 6. Kết luận & Bước tiếp theo

1. **Module sẵn sàng Integration:** Tầng Executor & Connectors đã hoàn thiện độc lập và tuân thủ 100% contract chung.
2. **Sẵn sàng Merge:** Branch `fix/P118-002-executor-contract` đã ổn định, sẵn sàng mở PR hợp nhất vào `develop`.
3. **Phối hợp Checkpoint Cuối tuần:** Sẵn sàng kết hợp với Mock API của Hoàng Anh và TaskPlan thật của Thành Bảo cho kịch bản Integration Test end-to-end.
