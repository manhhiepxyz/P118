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

- `register_resident`
- `register_vehicle`
- `book_parking`
- `pay_fee`

Compensation actions (`cancel_resident`, `refund_payment`...) không xuất hiện trong Planner plan — chỉ được hệ thống nội bộ gọi khi rollback.

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
- React Workflow Timeline (WebSocket realtime)

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
│   ├── api/                 # FastAPI routes + WebSocket
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
# Điền vào .env:
#   OPENAI_API_KEY=sk-...
#   AI_LOG_API_KEY=<key từ phoenix.note.transformerlabs.ai>

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
# 1. Khởi động PostgreSQL + 3 Mock Provider + Backend
docker compose up -d

# 2. Kiểm tra health (chờ tất cả healthy)
docker compose ps
for p in 8000 8001 8002 8003; do curl -s http://localhost:$p/health; done
```

| Service | Cổng | Mô tả |
| --- | --- | --- |
| Backend | 8000 | FastAPI app (API + WebSocket) |
| Mock Resident | 8001 | `POST /api/residents` |
| Mock Transport | 8002 | `POST /api/vehicles` + `POST /api/parking/bookings` |
| Mock Payment | 8003 | `POST /api/payments` |
| PostgreSQL | 5432 | Workflow state persistence |

### Smoke test (tái hiện happy path)

```bash
# Cần: Docker Compose đang chạy + healthy containers
python scripts/smoke_runtime.py

# Tùy chọn
python scripts/smoke_runtime.py --goal "Đăng ký cư dân, xe, chỗ đậu, thanh toán"
python scripts/smoke_runtime.py --seed "demo-01"   # prefix unique để tái chạy được
```

Smoke test chạy full flow 4 task (resident → vehicle → parking → payment) qua execution boundary, in kết quả từng bước, exit code 0 nếu SUCCESS / 1 nếu fail.

### Full regression

```bash
# Unit test (không cần Docker)
pytest tests/test_executor.py tests/test_connectors.py -v

# Integration test (cần PostgreSQL thật + TEST_DATABASE_URL)
TEST_DATABASE_URL=postgresql://p118:p118pass@localhost:5432/p118_db \
  pytest tests/test_integration/ -v

# Lint + format check
ruff check .
ruff format --check .
```

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
