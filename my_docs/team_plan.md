
# Kế hoạch nhóm — P-118

---

## 1. Phân công theo tầng

| Người               | Tầng phụ trách                   | Công việc chính                                                                                                                                                                                               | Kết quả cuối cùng                                                                                                               |
| --------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Thành Bảo** | Quyết định                       | Hiểu mục tiêu người dùng; tạo`TaskPlan`; kiểm tra kế hoạch; phát hiện thiếu thông tin; lập kế hoạch lại khi lỗi; xây `Policy Engine`; xác định khi nào cần người dùng xác nhận | Goal → TaskPlan hợp lệ; phương án thay thế; quyết định`AUTO_ALLOWED`, `REQUIRES_APPROVAL`, `DENIED`                 |
| **Mạnh Hiệp** | Thực thi                           | Xây Executor; chạy đúng thứ tự phụ thuộc; truyền dữ liệu giữa các bước; gọi dịch vụ qua Connector; xử lý lỗi; Retry; chống chạy trùng; hoàn tác khi cần                               | TaskPlan được thực hiện đúng; không chạy lại bước đã thành công; workflow tiếp tục hoặc phục hồi an toàn      |
| **Hoàng Anh**  | Dịch vụ, dữ liệu và giao diện | Xây mock API; PostgreSQL; lưu trạng thái workflow; giao diện người dùng; giao diện xác nhận; triển khai cloud; Live URL                                                                              | Dịch vụ giả lập hoạt động; dữ liệu được lưu; người dùng theo dõi và xác nhận được; sản phẩm có link chạy |

---

## 2. Kế hoạch tổng thể theo 5 tuần

| Tuần             | Mục tiêu                                      | Kết quả cần đạt                                                                |
| ----------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------- |
| **Tuần 1** | Chốt thiết kế và làm nền kỹ thuật       | TaskPlan schema, API contract, mã lỗi, database schema, mock API skeleton         |
| **Tuần 2** | Chạy được luồng thành công bằng backend | Resident → Vehicle → Parking → Payment; truyền dữ liệu và lưu trạng thái  |
| **Tuần 3** | Thêm AI và xử lý lỗi                       | Goal tự nhiên → TaskPlan; partial goal; hỏi thiếu thông tin; Zone A → Zone B |
| **Tuần 4** | Thêm quyền kiểm soát và giao diện         | Policy Engine, HITL, Retry, giao diện theo dõi workflow                           |
| **Tuần 5** | Hoàn thiện và đánh giá                    | Idempotency, Compensation, kiểm thử metrics, deploy, video và slide              |

---

## 3. Chi tiết Tuần 1

> Tuần này chưa cần giao diện hoàn chỉnh và chưa cần AI chạy thật.
>
> Đến cuối tuần, nhóm phải có: TaskPlan mẫu + API contract thống nhất + ít nhất 3 mock API dạng khung + PostgreSQL schema + thử gọi một API và lưu kết quả.

### Ngày 1 — Cả nhóm làm chung

Cần chốt:

- Bốn công cụ: `register_resident`, `register_vehicle`, `book_parking`, `pay_fee`
- Input và output của từng công cụ
- Cấu trúc `TaskPlan`
- Cấu trúc kết quả chung `StandardResult`
- Trạng thái task
- Mã lỗi
- Cấu trúc database
- Quy tắc branch và pull request

### Luồng dữ liệu thống nhất

| Tác vụ              | Đầu vào chính                    | Đầu ra chính              |
| --------------------- | ------------------------------------ | ---------------------------- |
| `register_resident` | tên, căn hộ, khu đô thị        | `resident_id`              |
| `register_vehicle`  | `resident_id`, biển số, loại xe | `vehicle_id`               |
| `book_parking`      | `vehicle_id`, ngày, khu vực      | `booking_id`, `amount`   |
| `pay_fee`           | `booking_id`, `amount`           | `payment_id`, trạng thái |

---

### Thành Bảo — Nền tảng quyết định

| Công việc tuần đầu                                          | Đầu ra                                              |
| ---------------------------------------------------------------- | ----------------------------------------------------- |
| Định nghĩa cấu trúc`TaskPlan`                             | JSON schema hoặc model rõ ràng                     |
| Định nghĩa task, dependency, input reference và trạng thái | Tài liệu/schema dùng chung                         |
| Viết 3–5 goal mẫu                                             | Bộ câu đầu vào thử nghiệm                      |
| Tạo plan mẫu cho luồng đầy đủ                             | Resident → Vehicle → Parking → Payment             |
| Tạo plan mẫu cho yêu cầu một phần                          | Chỉ đăng ký xe hoặc chỉ đặt chỗ              |
| Định nghĩa quy tắc Validator cơ bản                        | Tool hợp lệ, dependency đúng, không thiếu input |
| Liệt kê điều kiện cần hỏi thêm người dùng             | Danh sách trường bắt buộc                        |

> Chưa cần làm tuần này: gọi LLM thật, Replanner hoàn chỉnh, Policy Engine hoàn chỉnh.

---

### Mạnh Hiệp — Nền tảng thực thi

| Công việc tuần đầu                                   | Đầu ra                                              |
| --------------------------------------------------------- | ----------------------------------------------------- |
| Thiết kế giao diện của Executor                       | Nhận`TaskPlan` và trả execution result           |
| Định nghĩa cách chọn task tiếp theo                 | Chỉ chạy task có dependency đã`SUCCESS`        |
| Định nghĩa cơ chế truyền output sang input          | `resident_id` → Vehicle, `vehicle_id` → Parking |
| Tạo Connector interface dùng chung                      | Chuẩn gọi từng dịch vụ                           |
| Định nghĩa xử lý kết quả`SUCCESS` và `FAILED` | Luồng trạng thái cơ bản                          |
| Tạo Executor skeleton với service giả                  | Chạy thử plan viết sẵn                            |
| Chuẩn bị test cho dependency                            | Không cho Parking chạy trước Vehicle              |

> Chưa cần làm tuần này: Recovery hoàn chỉnh, Retry, Idempotency, Saga Compensation.

---

### Hoàng Anh — Dịch vụ giả lập và dữ liệu

| Công việc tuần đầu                            | Đầu ra                               |
| -------------------------------------------------- | -------------------------------------- |
| Tạo skeleton cho Resident, Vehicle, Parking API   | Ít nhất 3 endpoint có Swagger       |
| Định nghĩa request/response theo contract chung | Schema thống nhất                    |
| Tạo một trường hợp thành công cho mỗi API  | Trả đúng ID                         |
| Tạo lỗi mẫu cho Parking                         | Zone A trả`NO_AVAILABILITY`         |
| Thiết kế PostgreSQL schema                       | Bảng workflow, task, execution        |
| Tạo migration hoặc script khởi tạo database    | Database chạy được                 |
| Thử lưu một workflow và một task result       | Đọc lại được dữ liệu đã lưu |

> Chưa cần làm tuần này: frontend, HITL UI, WebSocket và deploy hoàn chỉnh.

---

## 4. Mốc tích hợp trong tuần

| Thời điểm  | Hoạt động                                              |
| ------------- | --------------------------------------------------------- |
| Cuối ngày 1 | Chốt toàn bộ schema và contract                       |
| Cuối ngày 3 | Mỗi người demo module riêng                           |
| Ngày 4       | Ghép Executor → Connector → Resident API               |
| Ngày 5       | Lưu`resident_id` và trạng thái task vào PostgreSQL |
| Cuối tuần   | Demo kỹ thuật nội bộ và chốt việc tuần 2          |

### Demo tối thiểu cuối tuần

```
Khởi tạo TaskPlan mẫu
→ Executor chọn register_resident
→ Connector gọi Resident API qua HTTP
→ API trả resident_id
→ lưu task SUCCESS và resident_id vào PostgreSQL
→ đọc lại đúng trạng thái
```

Nếu hoàn thành sớm thì nối thêm: `Resident → Vehicle`

> Không nên cố chạy đủ bốn dịch vụ nếu contract và state chưa ổn định.

---

## 5. Tiêu chí hoàn thành tuần đầu

- [ ] `TaskPlan` có cấu trúc thống nhất
- [ ] Request/response của 4 dịch vụ đã được chốt
- [ ] Có ít nhất 3 mock API skeleton
- [ ] Executor chạy được một task theo plan mẫu
- [ ] Connector gọi API qua HTTP
- [ ] PostgreSQL lưu và đọc được trạng thái
- [ ] Không còn mâu thuẫn tên field giữa ba phần
- [ ] Tất cả thay đổi được đưa lên GitHub qua pull request
