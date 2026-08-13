# P-118 — AI Agent orchestrate đa dịch vụ hoàn thành tác vụ liên hoàn

**Đề tài chương trình:** AI Agent orchestrate đa dịch vụ hoàn thành tác vụ liên hoàn (đặt nhà → xe → dịch vụ)

**Mã đề tài:** PTNT-02 — STT 158

P-118 là AI Agent nhận natural-language goal và orchestrate nhiều service có dependency để hoàn thành một mục tiêu của người dùng.

Core value: **goal-oriented multi-service orchestration** + **failure-aware execution** + **controlled autonomy**

---

## Problem

Trong hệ sinh thái có nhiều dịch vụ, người dùng thường phải tự:

- Tìm đúng service theo đúng thứ tự
- Nhập lại dữ liệu ở từng bước
- Theo dõi trạng thái riêng lẻ
- Xử lý failure mà không có hỗ trợ

**Business scenario (mô phỏng):**

```
Cư dân mới chuyển vào VinHomes
→ Register Resident
→ Register Vehicle
→ Book Parking
→ Pay Fee
```

Đây là business scenario mô phỏng cho bài toán multi-service orchestration. Project không kết nối production VinHomes API.

---

## Solution

```
User Goal
→ Goal Parser / LLM Planner
→ TaskPlan Validator
→ Scheduler / Executor
→ Tool Registry
→ Service Adapters
→ Mock Services
→ Persistent State
```

**Khi failure xảy ra:**

```
Failure
→ Recovery Handler
→ Replan / Retry / Ask Human / Compensation
→ Continue or Rollback
```

**Boundary rõ ràng:**

| LLM                | Deterministic Orchestration |
| ------------------ | --------------------------- |
| Goal understanding | TaskPlan validation         |
| Planning           | Dependency scheduling       |
| Replanning         | Execution                   |
|                    | Policy enforcement          |
|                    | State management            |
|                    | Compensation                |

---

## MVP Customer Journey

```
Register Resident
    ↓ resident_id

Register Vehicle (dùng resident_id)
    ↓ vehicle_id

Book Parking (dùng vehicle_id)
    ↓ booking_id, payment_info

Pay Fee (dùng payment_info)
    ↓ receipt
```

**Planner tool allowlist:**

- `search_properties`
- `schedule_property_viewing`
- `register_property_interest`
- `create_maintenance_request`
- `schedule_move`
- `register_resident`
- `register_vehicle`
- `book_parking`
- `pay_fee`

Compensation actions (`cancel_resident`, `refund_payment`...) không xuất hiện trong Planner plan — chỉ được hệ thống nội bộ gọi khi rollback.

Luồng tìm nhà là read-only: `search_properties` chỉ trả gợi ý. Sau khi người
dùng tự chọn `property_id`, họ có thể chạy `schedule_property_viewing` hoặc
`register_property_interest`. Hai task độc lập có thể chạy song song.
Agent không tự đặt cọc, ký hợp đồng hoặc hoàn tất giao dịch thuê/mua.
Bảo trì và chuyển nhà chỉ dành cho tài khoản cư dân đã liên kết; hai yêu cầu
độc lập có thể được Executor chạy song song.

> **Partial goals:** Người dùng không bắt buộc phải chạy đủ 4 bước. Agent tạo TaskPlan dựa trên mục tiêu hiện tại và dữ liệu đã có — không chạy lại bước đã hoàn thành hoặc không cần thiết. The MVP focuses on one housing-services domain; the connector-based architecture allows future integration with other residential service providers.

---

## Features

- Natural-language goal input — user đưa mục tiêu bằng tiếng Việt
- AI-generated TaskPlan với deterministic validation (schema, allowlist, dependency, cycle detection)
- Dependency-aware execution — output bước trước tự động là input bước sau
- Persistent workflow state (PostgreSQL)
- Failure recovery — Hero REPLAN scenario (`NO_AVAILABILITY` → alternative)
- Policy Engine — phân loại action thành `AUTO_ALLOWED` / `REQUIRES_APPROVAL` / `DENIED`
- HITL — agent dừng chờ user approve với action `REQUIRES_APPROVAL`
- Retry, Saga Compensation, Idempotency
- React Workflow Timeline; WebSocket realtime là hạng mục Demo Day, chưa triển khai

> Policy Engine, HITL, Saga Compensation và React UI thuộc Demo Day Final MVP. Gate 2 tập trung vào core orchestration và Live URL.

---

## Hero Recovery Scenario

```
Book Parking — Zone A
→ NO_AVAILABILITY
→ workflow: RECOVERING
→ Agent replan
→ Book Parking — Zone B
→ SUCCESS
→ tiếp tục Pay Fee
```

Completed tasks không bị chạy lại. Data propagation từ các bước trước được giữ nguyên.

---

## Project Structure

```
├── src/
│   ├── agents/              # LangGraph Agent
│   │   ├── graph.py         # State graph (nodes + edges)
│   │   ├── state.py         # WorkflowState schema
│   │   ├── nodes/           # planner, executor, hitl, compensator
│   │   └── tools/           # Agent tools (@tool)
│   ├── api/                 # FastAPI routes
│   │   └── routes.py
│   ├── services/            # Business logic
│   │   └── mock/            # Mock services: Resident, Transport, Payment
│   ├── models/              # Pydantic schemas
│   ├── config.py
│   └── main.py
├── tests/
│   ├── test_agents/
│   └── test_api/
├── scripts/                 # AI Logging Hooks
│   ├── log_hook.py
│   ├── log_manual.py
│   ├── submit_log.py
│   └── setup_hooks.sh
├── docs/
│   ├── architecture_diagram.md
│   └── gate1/
│       ├── brief.md
│       ├── prd.md
│       └── wireframe.md
├── eval/
│   └── results/
├── presentation/
├── .ai-log/                 # AI usage logs (auto-generated)
├── .github/workflows/       # CI/CD
├── .claude/ .codex/ .cursor/ .gemini/   # Per-tool hook configs
├── JOURNAL.md
├── Dockerfile
└── docker-compose.yml
```

---

## Setup

**Yêu cầu:** Python 3.11+

```bash
# 1. Clone repo
git clone https://github.com/AI20K-Build-Phase-Cohort-3/P-118.git
cd P-118

# 2. Tạo virtual environment
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Cài dependencies
pip install -e ".[dev]"

# 4. Cấu hình environment
cp .env.example .env
# Chọn một LLM provider trong .env:
#   LLM_PROVIDER=openai      + OPENAI_API_KEY=sk-...
# hoặc
#   LLM_PROVIDER=openrouter  + OPENROUTER_API_KEY=sk-or-v1-...
# OpenRouter mặc định dùng openrouter/free để smoke test structured output.
# Không commit file .env.

# 5. Cài AI logging hooks (chạy một lần)
bash scripts/setup_hooks.sh
```

---

## Running the Project

```bash
# Starter application — P-118 implementation bắt đầu sau Gate 1
uvicorn src.main:app --reload --port 8000

# Mở Swagger UI
# http://localhost:8000/docs
```

---

## Testing & Linting

```bash
# Run tests
pytest

# Lint
ruff check src/

# Format
ruff format src/
```

---

## AI Logging

Hooks tự động log mọi AI interaction (Claude Code, Codex, Cursor, Gemini CLI, Copilot):

```bash
# Cài hooks (một lần sau khi clone)
bash scripts/setup_hooks.sh

# Logs lưu tại
.ai-log/session.jsonl

# Submit thủ công
bash scripts/_pyrun.sh scripts/submit_log.py

# Log ChatGPT / web tools thủ công
bash scripts/_pyrun.sh scripts/log_manual.py --tool chatgpt --prompt "..."
```

Logs tự động submit lên grading server mỗi khi `git push`.

📖 Chi tiết: [phoenix.note.transformerlabs.ai/technical-book](https://phoenix.note.transformerlabs.ai/technical-book)

---

## Runtime (Gate 2)

### Khởi động full stack

```bash
# 1. Khởi động PostgreSQL + 4 Mock Provider + Backend
docker compose up -d

# 2. Kiểm tra health (chờ tất cả healthy)
docker compose ps
for p in 8080 8001 8002 8003 8005; do curl -s http://localhost:$p/health; done

# Mở Agent Workspace dùng cho demo
open http://localhost:8080/demo
```

| Service | Cổng | Mô tả |
| --- | --- | --- |
| Backend | 8080 | FastAPI app + Agent Workspace (`/demo`) |
| Mock Resident | 8001 | `POST /api/residents` |
| Mock Transport | 8002 | `POST /api/vehicles` + `POST /api/parking/bookings` |
| Mock Payment | 8003 | `POST /api/payments` |
| Mock Property | 8005 | `POST /api/properties/search` + `POST /api/projects/viewings` + `POST /api/projects/interests` |
| PostgreSQL | 5432 | Workflow state persistence |

### Chat trong Terminal

Sau khi `docker compose up -d` và backend healthy, chạy:

```bash
PYTHONPATH=. .venv/bin/python scripts/demo_chat.py
```

CLI dùng cùng workflow API với giao diện `/demo`: tự hiển thị tiến độ, hỏi
thông tin còn thiếu và chỉ gửi quyết định `approve`/`reject` khi backend đã
trả báo giá thanh toán. Nhập `/quit` để thoát. Dùng persona khách bằng
`--account prospect`; mặc định là cư dân demo đã liên kết căn hộ. Khi Agent
đang hỏi thông tin và chưa chạy dịch vụ nào, dùng `/new <yêu cầu mới>` để bỏ
kế hoạch nháp hoặc `/cancel` để dừng yêu cầu đang soạn.

### Smoke test deterministic của runtime

```bash
# Cần: Docker Compose đang chạy + healthy containers
python scripts/smoke_runtime.py
```

Smoke test dựng sẵn full flow 4 task bằng code để kiểm tra Executor → Connector
→ Mock Provider → PostgreSQL. Đây **không phải** test LLM/Planner và không nhận
`--goal`. Mỗi lần chạy tự tạo resident, vehicle và booking date mới để không
đụng dữ liệu lần trước. Exit code là 0 khi mọi task thành công, ngược lại là 1.

### Full regression

```bash
# Unit test (không cần Docker)
pytest tests/test_executor.py tests/test_connectors.py -v

# Tạo DB test riêng một lần (bỏ qua nếu đã tồn tại)
docker compose exec postgres createdb -U p118 p118_test_db

# Integration test (cần PostgreSQL thật + TEST_DATABASE_URL)
TEST_DATABASE_URL=postgresql://p118:p118pass@localhost:5432/p118_test_db \
  pytest tests/test_integration/ -v

# Lint + format check
ruff check src/ tests/
ruff format --check src/ tests/
```

> **Cảnh báo:** integration fixture có chạy `TRUNCATE TABLE`. Chỉ trỏ
> `TEST_DATABASE_URL` tới `p118_test_db`; tuyệt đối không dùng DB phát triển
> `p118_db`.

### Debug lỗi liên tầng

Khi test fail, xem [docs/integration-debug-guide.md](docs/integration-debug-guide.md) để phân loại lỗi thuộc Planner / Executor / Connector / Provider / DB / Docker.

---

## Docker

```bash
# Chạy full stack
docker compose up -d

# Build lại image (nếu đổi code)
docker compose build

# Xem log
docker compose logs -f mock-resident
```

---

## Documentation

- [Project Brief](docs/gate1/brief.md)
- [PRD](docs/gate1/PRD.md)
- [Wireframe / UI Flow](docs/gate1/wireframe.md)
- [Architecture Diagram](docs/architecture_diagram.md)
- [Weekly Journal](JOURNAL.md)
- [Evaluation](eval/results/report.md)

---

## Team

- Lâm Thành Bảo
- Phí Hoàng Anh
- Nguyễn Mạnh Hiệp

**Mentor:** Bùi Trung Hiếu
**Chương trình:** VinUni AI20K Cohort 3 — Build Phase

---

## License

MIT
