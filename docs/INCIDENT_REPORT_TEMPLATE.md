# Báo Cáo Sự Cố (Incident Report) - Mẫu

**Mã sự cố (Incident ID):** #INC-YYYYMMDD-001
**Mức độ nghiêm trọng (Severity):** [SEV-1 (Nghiêm trọng), SEV-2 (Cao), SEV-3 (Trung bình), SEV-4 (Thấp)]
**Thời gian phát hiện:** YYYY-MM-DD HH:MM
**Người báo cáo / Phụ trách:** [Tên người xử lý]

---

## 1. Tóm tắt sự cố (Summary)
[Mô tả ngắn gọn trong 2-3 câu về chuyện gì đã xảy ra, ảnh hưởng tới ai và ở đâu.]

## 2. Tác động tới hệ thống (Impact)
- **Ảnh hưởng đến khách hàng:** [Bao nhiêu khách hàng bị ảnh hưởng? Dịch vụ nào không hoạt động?]
- **Thời gian Downtime:** [Ví dụ: 45 phút, từ 10:00 - 10:45]
- **Số lượng Workflow bị FAILED:** [Ví dụ: 120 workflow bị kẹt / lỗi]

## 3. Dòng thời gian (Timeline)
- **HH:MM** - Dấu hiệu đầu tiên phát hiện (qua Slack Alert / Log Dashboard).
- **HH:MM** - Xác định nguyên nhân lỗi (Ví dụ: Timeout khi gọi Payment Provider API).
- **HH:MM** - Bắt đầu quá trình khắc phục.
- **HH:MM** - Khắc phục xong, hệ thống ổn định, thông báo cho các bên liên quan.

## 4. Nguyên nhân gốc rễ (Root Cause Analysis - RCA)
- **Nguyên nhân chính:** [Tại sao sự cố xảy ra? Bằng chứng từ log là gì? (Ví dụ: correlation-id: abcd-1234)]
- **Trace/Log bằng chứng:** 
  ```json
  { "error_code": "PROVIDER_UNAVAILABLE", "latency": "30500ms", "correlation_id": "..." }
  ```

## 5. Hành động khắc phục (Action Items)
| Hành động | Người chịu trách nhiệm | Thời hạn | Trạng thái |
|---|---|---|---|
| Cấu hình lại timeout cho PaymentConnector từ 30s xuống 15s | [Tên] | YYYY-MM-DD | Hoàn tất |
| Thêm Retry mechanism (Exponential Backoff) | [Tên] | YYYY-MM-DD | Chờ thực hiện |
| Khôi phục / chạy lại các Workflow bị lỗi trong thời gian sự cố | [Tên] | YYYY-MM-DD | Đang thực hiện |
