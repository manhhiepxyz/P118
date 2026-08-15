# P-118 — Manual eval Gate 2

**Thời điểm chạy:** 2026-08-15 (chạy lại trên Docker sau khi thêm /ready, phân loại lỗi, luồng liên kết căn hộ)
**Môi trường:** **stack Docker Compose** — backend container cổng 8080 · PostgreSQL `p118_db`
· 7 mock provider container trên cổng canonical (8001 resident / 8002 transport / 8003 payment /
8005 tour / 8006 resident-services / 8007 consultation / 8008 property)
· auth thật (JWT) · API canonical `/workflows/demo/*`

Lượt trước chạy trên backend **local** với cấu hình đúng, nên nó không thể phát hiện một
Docker Compose sai cấu hình. Lần này chạy đúng image và đúng network của stack.

**Mô hình:** provider `deepseek`, model `deepseek-v4-flash`, structured output bằng `json_mode`.
Không fallback sang provider khác — `src/services/llm.py` raise `LLMConfigurationError` thay vì đoán.

`json_mode` là lựa chọn bắt buộc chứ không phải sở thích: DeepSeek V4 Flash chạy thinking mode,
nó từ chối forced `tool_choice` và chưa mở `response_format: json_schema`. Output vẫn được
validate bằng đúng Pydantic model như trước — đổi cách truyền, không nới kiểm tra.

**Database:** `p118_db` của stack Docker. `p118_test_db` KHÔNG được dùng ở đây — nó dành riêng cho pytest (fixture có `TRUNCATE`), và trộn hai thứ là cách nhanh nhất để mất dữ liệu demo.

**Che dữ liệu:** UUID rút gọn 8 ký tự; biển số chỉ giữ 4 ký tự đầu; không ghi API key, token,
DSN, prompt nội bộ, chain-of-thought hay dữ liệu cá nhân thật. Biển số / mã cư dân / căn hộ
trong báo cáo này đều là canary sinh riêng cho lượt chạy.

**Cách lấy số:** mọi giá trị dưới đây đến từ HTTP response của backend Docker và từ truy vấn
`p118_db` sau khi chạy. Chạy lại bằng `python eval/run_manual_eval.py`. Không có case nào được viết tay, copy từ fixture hay dựng từ mock.
Số lần gọi mô hình đọc từ bảng `llm_usage`, không phải ước lượng.

**Bối cảnh tài khoản** (mô tả, không ghi định danh): một tài khoản `customer` được ban quản lý
xác minh liên kết cư dân ở trạng thái `VERIFIED`, gắn với một căn hộ canary tại Vinhomes Ocean Park.
Danh tính cư dân do server dựng từ token — client không gửi và không sửa được.

---

## Case 1 — Đăng ký xe + đặt chỗ đỗ xe, có vòng hỏi bổ sung

| Mục | Giá trị |
|---|---|
| Mục tiêu đầu vào | "Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí." |
| Bối cảnh tài khoản | customer, liên kết cư dân đã VERIFIED |
| Model / provider / method | deepseek-v4-flash · deepseek · json_mode |
| Planner status | `NEEDS_INFORMATION` → `READY` sau khi người dùng bổ sung |
| Missing fields | biển số xe, loại xe, ngày đặt chỗ, khu vực đỗ xe |
| Chuỗi việc (user-facing) | Đăng ký phương tiện → Đặt chỗ đỗ xe → Thanh toán phí |
| Kết quả workflow | `WAITING_APPROVAL` · báo giá **150.000 VND** |
| Kết quả từng bước | Đăng ký phương tiện = SUCCESS · Đặt chỗ đỗ xe = SUCCESS · Thanh toán phí = WAITING_APPROVAL |
| Số lần gọi mô hình | 2 (đều ở stage `plan`) · 0 lượt sửa sai |

**Bằng chứng PostgreSQL (đã mask)**

- workflow cha 95ff4eb1… → con 920790db…
- vòng hỏi bổ sung = 1
- approval AWAITING = 1
- biển số canary 51E-*** · ngày 2028-05-30 · Khu A

**PASS.** Workflow dừng đúng ở điểm chờ người dùng quyết định, kèm báo giá đọc từ chỗ đỗ xe
đã giữ chứ không phải từ một con số Planner tự nêu.

---

## Case 2 — Duyệt khoản thanh toán

| Mục | Giá trị |
|---|---|
| Mục tiêu đầu vào | (tiếp workflow của Case 1) người dùng bấm Xác nhận thanh toán |
| Bối cảnh tài khoản | chính chủ workflow, đã VERIFIED |
| Model / provider / method | deepseek-v4-flash · deepseek · json_mode |
| Planner status | không gọi lại Planner — quyết định là hành động của người dùng |
| Missing fields | — |
| Chuỗi việc (user-facing) | Đăng ký phương tiện → Đặt chỗ đỗ xe → Thanh toán phí |
| Kết quả workflow | HTTP 200 → `SUCCESS` |
| Kết quả từng bước | cả ba bước = SUCCESS |
| Số lần gọi mô hình | 1 (stage `plan`, thuộc lượt lập kế hoạch trước đó) |

**Bằng chứng PostgreSQL (đã mask)**

- payments toàn hệ thống 8 → 9
- duyệt lần hai: HTTP 409, payments 9 → 9
- approval APPROVED = 1

**PASS.** Đúng một khoản thu được tạo. Lần duyệt thứ hai bị chặn ở 409 chứ không âm thầm
thu thêm — bảo vệ nằm ở PostgreSQL, không phải ở việc giao diện giấu nút.

---

## Case 3 — Từ chối khoản thanh toán

| Mục | Giá trị |
|---|---|
| Mục tiêu đầu vào | "Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí." |
| Bối cảnh tài khoản | customer, đã VERIFIED |
| Model / provider / method | deepseek-v4-flash · deepseek · json_mode |
| Planner status | `NEEDS_INFORMATION` → `READY` |
| Missing fields | biển số xe, loại xe, ngày đặt chỗ, khu vực đỗ xe |
| Chuỗi việc (user-facing) | Đăng ký phương tiện → Đặt chỗ đỗ xe → Thanh toán phí |
| Kết quả workflow | HTTP 200 → hiển thị `FAILED` (DB: `CANCELLED`) |
| Kết quả từng bước | Đăng ký phương tiện = SUCCESS · Đặt chỗ đỗ xe = SUCCESS · Thanh toán phí = PENDING |
| Số lần gọi mô hình | 2 (stage `plan`) |

**Bằng chứng PostgreSQL (đã mask)**

- workflow con a4d663ca… · DB status = CANCELLED
- payments 9 → 9 (không đổi)
- chỗ đỗ xe đã giữ vẫn còn = 1
- biển số canary 51F-*** · ngày 2028-05-31 · Khu B

**PASS.** Từ chối huỷ workflow và không thu tiền, nhưng **không xoá chỗ đã giữ** — huỷ chỗ là
một quyết định khác, người dùng chưa yêu cầu.

**Ghi chú contract:** DB lưu `CANCELLED`, API công khai trả `FAILED`. Contract công khai chỉ có
ba trạng thái kết thúc, nên `CANCELLED` được gộp vào `FAILED`; giao diện hiển thị "Không thành công".

---

## Case 4 — Đặt lịch tham quan dự án với ngày/giờ hợp lệ

| Mục | Giá trị |
|---|---|
| Mục tiêu đầu vào | "Tôi muốn đặt lịch tham quan căn hộ tại Vinhomes Ocean Park." |
| Bối cảnh tài khoản | customer đã VERIFIED (dịch vụ này vốn mở cho cả khách chưa liên kết) |
| Model / provider / method | deepseek-v4-flash · deepseek · json_mode |
| Planner status | `NEEDS_INFORMATION` → `READY` |
| Missing fields | dự án, ngày xem nhà, giờ xem nhà |
| Chuỗi việc (user-facing) | Đặt lịch tham quan |
| Kết quả workflow | `SUCCESS` |
| Kết quả từng bước | Đặt lịch tham quan = SUCCESS |
| Số lần gọi mô hình | 2 (stage `plan`) |

**Bằng chứng PostgreSQL (đã mask)**

- workflow 5537a3f1… · vòng hỏi = 1
- ngày hẹn 2028-06-29 lúc 10:30 (giữ nguyên phút, không quy về buổi)
- kết quả provider đã ghi vào workflow_tasks: 2028-06-29 10:30 · Vinhomes Ocean Park (PRJ-007)
- số điện thoại đầu mối do provider giữ, không đưa vào báo cáo

**PASS.** Giờ hẹn đi tới provider ở dạng `HH:MM` và quay về nguyên vẹn — **không bị quy về
MORNING/AFTERNOON**. Tên dự án người dùng nhập được phân giải thành `project_id` nội bộ ở
biên API; người dùng không phải biết mã dự án.

**Giới hạn đo:** provider tour giữ lịch trong bộ nhớ tiến trình mock, không ghi bảng
`tour_bookings`. Bằng chứng bền vững vì vậy lấy ở phía P-118 (`workflow_tasks.result_data`),
là nơi kết quả provider được lưu lại.

---

## Case 5 — Lỗi có chủ ý: đặt chỗ cho một ngày đã qua

| Mục | Giá trị |
|---|---|
| Mục tiêu đầu vào | "Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí." — người dùng chọn một ngày đã qua 30 ngày |
| Bối cảnh tài khoản | customer, đã VERIFIED |
| Model / provider / method | deepseek-v4-flash · deepseek · json_mode |
| Planner status | `NEEDS_INFORMATION` cho vòng hỏi đầu; input sai bị chặn **trước khi** gọi provider |
| Missing fields | biển số xe, loại xe, ngày đặt chỗ, khu vực đỗ xe |
| Chuỗi việc (user-facing) | (không có bước nào chạy) |
| Kết quả workflow | **HTTP 422** — "Ngày đặt chỗ chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi." |
| Kết quả từng bước | — |
| Số lần gọi mô hình | 2 (stage `plan`) |

**Bằng chứng PostgreSQL (đã mask)**

- vòng hỏi = 1
- chỗ đỗ xe được tạo cho ngày quá khứ = 0 (phải là 0)
- biển số canary 51G-***

**PASS.** Hệ thống nêu **lý do** ("ngày chưa phù hợp") kèm **hướng sửa an toàn** ("chọn một ngày
từ hôm nay trở đi"), không lộ tên field nội bộ, không lộ thông báo Pydantic thô, và không tạo
bản ghi nào. Câu trả lời sai cũng **không đốt mất lượt hỏi** — người dùng sửa lại được ngay.

---

## Tổng kết

| Case | Kết quả |
|---|---|
| 1. Parking có clarification → chờ duyệt | **PASS** |
| 2. Duyệt thanh toán → hoàn thành | **PASS** |
| 3. Từ chối thanh toán → huỷ, không thu tiền | **PASS** |
| 4. Đặt lịch tham quan, ngày/giờ hợp lệ | **PASS** |
| 5. Lỗi có chủ ý: ngày quá khứ | **PASS** |

**5/5 PASS.** Không case nào dùng fixture, output tự viết, hay dữ liệu copy lại.

**Quan sát về tính ổn định:** cả năm case đều hội tụ trong đúng một vòng hỏi bổ sung, và
`llm_usage` ghi 1–2 lần gọi mô hình cho mỗi lượt lập kế hoạch — không có vòng sửa sai nào
phải kích hoạt. Không lượt nào Planner đề xuất `register_resident`: việc liên kết hồ sơ cư dân
nằm NGOÀI Agent và bị chặn ở tầng code, không phải chỉ dặn trong prompt.
