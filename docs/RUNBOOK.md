# Runbook - Sổ tay Vận hành

Tài liệu này cung cấp các kịch bản khắc phục sự cố (troubleshooting) cơ bản khi hệ thống gặp lỗi liên quan đến Agent và LLM.

## 1. Sự cố: Quá tải tỷ lệ gọi LLM (LLM Rate Limit)
**Triệu chứng:**
- Dashboard hiển thị tỷ lệ lỗi (Error Rate) tăng vọt.
- Logs ghi nhận các lỗi mã `429 Too Many Requests` từ OpenAI / DeepSeek.
- Trạng thái các workflow chuyển sang `FAILED` hoặc treo ở `PENDING`.

**Cách xử lý:**
1. Mở Admin Dashboard, kiểm tra biểu đồ `LLM Usage` xem lượng Tokens/Calls có tăng đột biến so với bình thường không.
2. Kiểm tra log chi tiết: `docker compose logs backend | grep -i 429`
3. Nếu do lượng người dùng tăng thật, liên hệ provider để tăng hạn mức Rate Limit (Quota).
4. Nếu nghi ngờ một Agent lặp vòng lặp vô hạn (Looping), xác định ID của workflow đó trong tab *Lịch sử* và ngừng hệ thống tạm thời để debug luồng Planner.
5. Sau khi khắc phục nguyên nhân, click nút `Retry` trên các luồng bị `FAILED`.

## 2. Sự cố: LLM Provider Offline
**Triệu chứng:**
- Các Agent trả về lỗi `500` hoặc timeout.
- Workflow hỏng ngay ở bước `plan` hoặc `replan`.

**Cách xử lý:**
1. Kiểm tra trang trạng thái (status page) của LLM Provider (ví dụ: status.openai.com).
2. Nếu hệ thống LLM gốc lỗi, thay đổi model cấu hình trong `.env` (ví dụ chuyển từ `openai` sang `groq` làm dự phòng).
3. Khởi động lại dịch vụ `docker compose restart backend`.
4. Sau khi provider hoạt động lại bình thường, chuyển lại cấu hình và Retry luồng lỗi.

## 3. Quản lý trạng thái kẹt luồng (Zombie Workflows)
**Triệu chứng:**
- Một số workflow nằm trạng thái `RUNNING` nhưng sau nhiều giờ vẫn không đổi.

**Cách xử lý:**
1. Tìm các workflow_id qua Admin Dashboard.
2. Kiểm tra lịch sử log để hiểu nguyên nhân tại sao quá trình thực thi treo (thường do database deadlock hoặc API thứ 3 không phản hồi).
3. Đánh dấu thủ công luồng thành `FAILED` thông qua kịch bản hỗ trợ.
