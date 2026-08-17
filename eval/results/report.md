# P-118 — Manual eval Gate 2

**Thời điểm chạy:** 2026-08-17 — chạy lại sau nhóm thay đổi kiến trúc:
`approval_actor` phân biệt ai đang cần hành động · cổng duyệt lịch tham quan của ĐƠN VỊ ·
đảo thứ tự ghi khi duyệt (kết quả → câu chốt → RỒI MỚI SUCCESS) · `book_shuttle` và `pay_fee`
rời khỏi danh mục người dùng chọn · `register_vehicle` bất biến với chính chủ ·
`AgentState.user_answers` (thiếu khai báo nên mọi câu trả lời bổ sung bị LangGraph loại bỏ im lặng)
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

**Cấu hình khi chạy:** `P118_AUTO_APPROVE_VIEWING_SECONDS=0` — chế độ tự duyệt lịch tham quan
(tiện ích demo) **TẮT**. Bằng chứng phải phản ánh luồng thật, trong đó lịch tham quan chỉ thành
sự thật khi đơn vị dịch vụ bấm duyệt.

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
| Bối cảnh tài khoản | customer đã được ban quản lý xác minh cư dân (VERIFIED) |
| Model / provider / method | deepseek-v4-flash · deepseek · json_mode |
| Planner status | `NEEDS_INFORMATION → READY sau khi người dùng bổ sung` |
| Missing fields | biển số xe, loại xe, ngày đặt chỗ, khu vực đỗ xe |
| Chuỗi việc (user-facing) | Đăng ký phương tiện → Đặt chỗ đỗ xe → Thanh toán phí |
| Kết quả workflow | WAITING_APPROVAL · báo giá 150000 VND |
| Kết quả từng bước | Đăng ký phương tiện=SUCCESS · Đặt chỗ đỗ xe=SUCCESS · Thanh toán phí=WAITING_APPROVAL |
| Số lần gọi mô hình | 3 (plan=2, respond=1) |

**Bằng chứng PostgreSQL (đã mask)**

- workflow cha e986a051… → con 062a64a6…
- vòng hỏi bổ sung = 1
- approval AWAITING = 1
- biển số canary 51E-*** · ngày 2030-01-24 · Khu A

**PASS.** Dừng đúng ở điểm chờ người dùng quyết định, kèm báo giá đọc từ chỗ đỗ xe đã giữ.

---

## Case 2 — Duyệt khoản thanh toán

| Mục | Giá trị |
|---|---|
| Mục tiêu đầu vào | "(tiếp tục workflow của case 1) — người dùng bấm Xác nhận thanh toán" |
| Bối cảnh tài khoản | chính chủ workflow, đã VERIFIED |
| Model / provider / method | deepseek-v4-flash · deepseek · json_mode |
| Planner status | `không gọi lại Planner — quyết định là hành động của người dùng` |
| Missing fields | — |
| Chuỗi việc (user-facing) | — |
| Kết quả workflow | HTTP 200 → SUCCESS |
| Kết quả từng bước | — |
| Số lần gọi mô hình | 1 (plan=1) |

**Bằng chứng PostgreSQL (đã mask)**

- payments toàn hệ thống 3 → 4
- duyệt lần hai: HTTP 409, payments 4 → 4
- approval APPROVED = 1

**PASS.** Đúng một khoản thu được tạo; lần duyệt thứ hai bị chặn 409 chứ không thu thêm.

---

## Case 3 — Từ chối khoản thanh toán

| Mục | Giá trị |
|---|---|
| Mục tiêu đầu vào | "Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí." |
| Bối cảnh tài khoản | customer đã VERIFIED |
| Model / provider / method | deepseek-v4-flash · deepseek · json_mode |
| Planner status | `NEEDS_INFORMATION → READY` |
| Missing fields | biển số xe, loại xe, ngày đặt chỗ, khu vực đỗ xe |
| Chuỗi việc (user-facing) | Đăng ký phương tiện → Đặt chỗ đỗ xe → Thanh toán phí |
| Kết quả workflow | HTTP 200 → hiển thị CANCELLED (DB: CANCELLED) |
| Kết quả từng bước | Đăng ký phương tiện=SUCCESS · Đặt chỗ đỗ xe=SUCCESS · Thanh toán phí=CANCELLED |
| Số lần gọi mô hình | 3 (plan=2, respond=1) |

**Bằng chứng PostgreSQL (đã mask)**

- workflow con 91912052… · DB status = CANCELLED
- payments 4 → 4 (không đổi)
- chỗ đỗ xe đã giữ vẫn còn = 1
- biển số canary 51F-*** · ngày 2030-01-25 · Khu B

**PASS.** Từ chối huỷ workflow và không thu tiền, nhưng KHÔNG xoá chỗ đã giữ — huỷ chỗ là quyết định khác.

---

## Case 4 — Đặt lịch tham quan dự án với ngày/giờ hợp lệ

| Mục | Giá trị |
|---|---|
| Mục tiêu đầu vào | "Tôi muốn đặt lịch tham quan căn hộ tại Vinhomes Ocean Park." |
| Bối cảnh tài khoản | customer đã VERIFIED (dịch vụ này vốn mở cho cả khách chưa liên kết) |
| Model / provider / method | deepseek-v4-flash · deepseek · json_mode |
| Planner status | `NEEDS_INFORMATION → READY` |
| Missing fields | dự án, ngày xem nhà, giờ xem nhà |
| Chuỗi việc (user-facing) | Đặt lịch tham quan |
| Kết quả workflow | SUCCESS |
| Kết quả từng bước | Đặt lịch tham quan=SUCCESS |
| Số lần gọi mô hình | 2 (plan=2) |

**Bằng chứng PostgreSQL (đã mask)**

- workflow 5c4b6fe1… · vòng hỏi = 1
- ngày hẹn 2030-02-23 lúc 10:30 (giữ nguyên phút, không quy về buổi)
- dừng ở cổng duyệt đơn vị = True · đơn vị đã duyệt = True
- kết quả provider đã ghi vào workflow_tasks: 2030-02-23 10:30 · Vinhomes Ocean Park (PRJ-007)
- số điện thoại đầu mối do provider giữ, không đưa vào báo cáo

**PASS.** Dừng đúng ở cổng duyệt của ĐƠN VỊ (approval_actor=PROVIDER, khách không có nút quyết định), chỉ thành công sau khi đơn vị duyệt. Giờ hẹn đi qua tới provider ở dạng HH:MM, không bị quy về MORNING/AFTERNOON.

---

## Case 5 — Lỗi có chủ ý — đặt chỗ cho một ngày đã qua

| Mục | Giá trị |
|---|---|
| Mục tiêu đầu vào | "Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí. (người dùng chọn ngày 2026-07-17, đã qua)" |
| Bối cảnh tài khoản | customer đã VERIFIED |
| Model / provider / method | deepseek-v4-flash · deepseek · json_mode |
| Planner status | `NEEDS_INFORMATION cho vòng hỏi đầu; input sai bị chặn trước khi gọi provider` |
| Missing fields | biển số xe, loại xe, ngày đặt chỗ, khu vực đỗ xe |
| Chuỗi việc (user-facing) | — |
| Kết quả workflow | HTTP_422 — Ngày đặt chỗ chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi. |
| Kết quả từng bước | — |
| Số lần gọi mô hình | 1 (plan=1) |

**Bằng chứng PostgreSQL (đã mask)**

- vòng hỏi = 0
- chỗ đỗ xe được tạo cho ngày quá khứ = 0 (phải là 0)
- biển số canary 51G-***

**PASS.** Không tạo chỗ đỗ xe cho ngày đã qua, và nói cho người dùng biết cần sửa gì.

---

## Tổng kết

**5/5 PASS.**

Case 4 lần này đi hết đường thay vì dừng nửa chừng: xác nhận yêu cầu dừng đúng ở cổng duyệt với
`approval_actor = PROVIDER` (khách KHÔNG có nút quyết định), rồi đóng vai đơn vị bấm duyệt, rồi
mới đòi `SUCCESS`. Bản trước chấm PASS ngay khi khách gửi yêu cầu — kỳ vọng ấy có từ thời chưa
có cổng duyệt, và giữ nguyên thì eval sẽ báo XANH kể cả khi ai đó lỡ bỏ mất cổng duyệt.

**Một quan sát về độ ổn định, ghi lại thay vì giấu:** lượt chạy ngay trước lượt này cho 4/5, với
Case 3 đỏ vì workflow còn `PENDING` khi harness gọi từ chối thanh toán (nhận HTTP 409). Chạy lại
mà không đổi gì thì 5/5. Nguyên nhân là độ trễ của mô hình chứ không phải logic — nhưng nó có
nghĩa là `poll()` với trần 150 giây chưa đủ chắc cho mọi lượt, và một lần chạy đơn lẻ không nên
được coi là bằng chứng tuyệt đối.

**Dữ liệu đã mask:** cư dân `RES-EV-08…` · căn hộ `E-8827` ·
người dùng `feb6def1…`. Biển số trong báo cáo là canary sinh riêng cho lượt chạy.
