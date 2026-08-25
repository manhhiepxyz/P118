# Service Level Objectives (SLO)
## Overview
Tài liệu định nghĩa các chỉ tiêu chất lượng dịch vụ (Service Level Objectives - SLO) cho AI Agent điều phối đa dịch vụ. Hệ thống được đánh giá qua 4 khía cạnh: Availability (Tính khả dụng), Latency (Độ trễ), LLM Token Usage (Tiêu thụ tài nguyên AI), và Error Rate (Tỷ lệ lỗi).

## Chỉ số SLO
1. **Tính khả dụng (Availability)**: 99.9% uptime (thời gian hoạt động liên tục mỗi tháng).
2. **Độ trễ (Latency)**: 95% số yêu cầu phản hồi dưới 5 giây.
3. **Tiêu thụ tài nguyên AI (LLM Token Usage)**: Trung bình tiêu thụ dưới 5,000 tokens cho mỗi lần chạy thành công.
4. **Tỷ lệ lỗi (Error Rate)**: Nhỏ hơn 1% tổng số yêu cầu hệ thống xử lý trong ngày.

## Hướng dẫn theo dõi
Sử dụng **Admin Dashboard** để theo dõi liên tục 4 chỉ số trên.
- Theo dõi `Avg. Latency (ms)` hàng ngày. Nếu mức trễ trung bình > 2.5 giây, cần tiến hành tối ưu.
- Báo động tự động sẽ kích hoạt khi có trên 10 workflow `FAILED` trong 1 giờ.
