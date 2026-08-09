"""Service layer cho mock services (ResidentService, TransportService, PaymentService).

Owner: Hoàng Anh

Khác với `src/mock/` (FastAPI routers + store in-memory), lớp này là service
layer tuỳ chọn giao tiếp trực tiếp với PostgreSQL — dùng khi cần thay
store in-memory bằng DB thật cho dữ liệu nghiệp vụ của mock service.
"""
