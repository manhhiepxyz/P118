# P-118: Project Context & Code Rules

Đây là file ngữ cảnh cung cấp cái nhìn tổng quan về kiến trúc, quy tắc và phong cách code của dự án P-118. Bất kỳ AI Agent nào khi làm việc với dự án này cần đọc kỹ file này trước tiên.

## 1. Tổng quan dự án (Project Overview)
- **Tên dự án**: P-118 — AI Agent orchestrate đa dịch vụ hoàn thành tác vụ liên hoàn.
- **Mục tiêu**: Xây dựng AI Agent có khả năng nhận yêu cầu bằng ngôn ngữ tự nhiên (tiếng Việt), lập kế hoạch (TaskPlan) và điều phối (orchestrate) nhiều dịch vụ phụ thuộc nhau để hoàn thành chuỗi tác vụ (ví dụ: đăng ký cư dân -> đăng ký xe -> đặt chỗ đỗ xe -> thanh toán).
- **Điểm cốt lõi**: Goal-oriented orchestration, Failure-aware execution, Controlled autonomy.

## 2. Kiến trúc hệ thống (Architecture)
- **Backend Framework**: FastAPI, Python 3.11+.
- **AI/Agent Framework**: LangGraph.
- **Workflow State Management**: Sử dụng PostgreSQL.
- **Cấu trúc luồng (Flow)**:
  1. Goal Parser / LLM Planner
  2. TaskPlan Validator (Deterministic Validation)
  3. Scheduler / Executor
  4. Tool Registry / Service Adapters / Mock Services
- **Thành phần nâng cao**:
  - **Failure Recovery (Saga Compensation)**: Hỗ trợ tự động hồi phục hoặc rollback khi có lỗi (VD: Hệ thống trả về `NO_AVAILABILITY` -> Agent tự lên kế hoạch lại - Replan).
  - **Policy Engine**: Phân loại mức độ can thiệp cho các hành động (`AUTO_ALLOWED`, `REQUIRES_APPROVAL`, `DENIED`).
  - **Human-in-the-loop (HITL)**: Agent tạm dừng luồng và đợi sự phê duyệt của con người khi gặp các action `REQUIRES_APPROVAL`.

## 3. Cấu trúc thư mục (Directory Structure)
- `src/agents/`: Chứa logic của LangGraph (`graph.py`, `state.py`, `nodes/`, `tools/`).
- `src/api/`: Các endpoint của FastAPI (API & WebSocket).
- `src/services/`: Logic nghiệp vụ và các Mock services (Resident, Transport, Payment).
- `src/models/`: Định nghĩa các cấu trúc dữ liệu bằng Pydantic.
- `tests/`: Chứa các bài unit tests cho agents và api (`test_agents/`, `test_api/`).
- `scripts/`: Các script hỗ trợ, đặc biệt là hook ghi log AI.

## 4. Quy tắc Code & Formatting (Code Style)
- **Linting & Formatting**: Bắt buộc sử dụng `ruff` (Cấu hình đã có tại `ruff.toml`).
  - Lệnh kiểm tra code: `ruff check src/ tests/` (Hoặc dùng `make lint`)
  - Lệnh format tự động: `ruff format src/ tests/` (Hoặc dùng `make format`)
- **Type Checking**: Sử dụng `mypy`. Mọi hàm/phương thức cần được định nghĩa Type Hints đầy đủ. (Lệnh: `make typecheck`).
- **Testing**: Bắt buộc viết unit tests và đảm bảo code qua bài test của `pytest`.
- **Đặt tên (Naming Convention)**:
  - Tên Class: `PascalCase`
  - Tên Hàm, Tên Biến: `snake_case`
  - Tên Hằng số: `UPPER_SNAKE_CASE`

## 5. Quy trình chạy và tương tác (Development Workflow)
- **Chạy local**: Dùng lệnh `uvicorn src.main:app --reload` hoặc `make run` (Cổng mặc định: 8000).
- **Log AI**: Mọi tương tác của AI phải được tự động ghi log vào thư mục `.ai-log/` theo cấu hình có sẵn của hệ thống.
