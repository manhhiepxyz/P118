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

## Docker

```bash
# Chạy full stack
docker-compose up --build
```

---

## Documentation

- [Project Brief](docs/gate1/brief.md)
- [PRD](docs/gate1/prd.md)
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
