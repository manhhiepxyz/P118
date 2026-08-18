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
# Điền ĐÚNG key của provider đang chọn — xem bảng "Biến môi trường" bên dưới.
# Không commit file .env.

# 5. Cài AI logging hooks (chạy một lần)
bash scripts/setup_hooks.sh
```

---

## Biến môi trường

`.env.example` là bản đầy đủ có chú thích. Bảng dưới là những biến **bắt buộc**
để chạy được:

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `LLM_PROVIDER` | `deepseek` | `deepseek` \| `openai` \| `openrouter` \| `groq` |
| `DEEPSEEK_API_KEY` | *(trống)* | Bắt buộc khi provider là `deepseek` |
| `DEEPSEEK_MODEL_NAME` | `deepseek-v4-flash` | Ghim đúng model này |
| `DATABASE_URL` | `postgresql://p118:p118pass@localhost:5432/p118_db` | Docker Compose tự override sang host `postgres` |
| `TEST_DATABASE_URL` | `…/p118_test_db` | **Phải khác** `DATABASE_URL` — fixture pytest có `TRUNCATE` |
| `JWT_SECRET` | `change-me-…` | **Bắt buộc đổi** — auth trả 500 nếu để trống |
| `P118_LLM_TRACE` | `0` | `1` để xem log model lúc demo (đặt trong shell hoặc `docker-compose.yml`) |

Cấu hình sai thì hệ thống **dừng ngay lúc khởi động**, không đợi tới lúc người
dùng bấm nút: `check_llm_configuration()` từ chối provider không có key tương
ứng, và `/ready` báo đỏ. Đây là bài học từ một lần Docker Compose báo mọi
service healthy trong khi backend chạy với provider không có key.

Key **không bao giờ** nằm trong `docker-compose.yml` — file đó chỉ tham chiếu
biến môi trường.

---

## Sample queries

Người dùng gõ tiếng Việt tự nhiên vào ô chat. Ba nhóm dưới đây phủ đủ các nhánh
đáng xem trong một buổi demo.

> **Một khung giờ chỉ đặt được một lần.** Chạy lại y nguyên câu bên dưới lần thứ
> hai sẽ nhận *"Khung giờ … đã có người đặt"* — đó là hệ thống làm đúng, không
> phải lỗi. Đổi ngày hoặc giờ là chạy tiếp được. Tương tự, mỗi tài khoản chỉ
> đăng ký quan tâm một dự án một lần.

### Nhóm 1 — Chạy thẳng (không cần liên kết căn hộ)

```
Đặt lịch tham quan dự án Vinhomes Ocean Park ngày 2026-09-20 lúc 10:00
```
→ `READY` · 1 tác vụ · `SUCCESS`.

```
Đăng ký quan tâm dự án Vinhomes Pearl Bay, tôi muốn mua, đồng ý để bộ phận tư vấn liên hệ lúc 14:30
```
→ `READY` · 1 tác vụ · `SUCCESS`.

Chữ **"đồng ý"** là bắt buộc: `consent` không được suy diễn hộ người dùng. Bỏ nó
đi thì agent hỏi lại chứ không tự đánh dấu là đã đồng ý cho người khác liên hệ.

### Nhóm 2 — Agent hỏi lại rồi chạy tiếp

```
đặt lịch tham quan dự án
```
→ `NEEDS_INFORMATION` — thiếu dự án, ngày, giờ. Trả lời ngay trong chat:
```
vinhomes sài gòn park ngày 2026-09-20 lúc 10:00
```
→ tạo workflow con và chạy tiếp. Câu trả lời bổ sung **đè** lên giá trị suy từ
goal cũ, nên đổi giờ giữa chừng cũng không mất ngày đã nói.

### Nhóm 3 — Ca hỏng, để xem agent giải thích thế nào

```
Đặt lịch tham quan dự án Vinhomes Sky Garden ngày 2026-09-20 lúc 10:00
```
→ Agent **không bịa** một dự án không tồn tại. Kết quả rơi vào một trong hai
nhánh, tuỳ Planner nhận ra sớm hay muộn — cả hai đều liệt kê 7 dự án đang hỗ trợ:

- `NEEDS_INFORMATION` — nhận ra ngay lúc lập kế hoạch, hỏi lại tên dự án
- `FAILED` — provider từ chối, agent nêu đúng tên bạn đã gõ và danh mục thay thế

Planner là LLM nên nhánh nào xảy ra là không tất định; điều **được bảo đảm** là
không có lịch tham quan nào được tạo, và câu trả lời không bảo bạn "thử lại" một
việc không bao giờ chạy được.

```
đặt chỗ đỗ xe Khu A ngày 2026-08-22     # cần tài khoản ĐÃ liên kết căn hộ
```
→ Khu A hết chỗ ngày đó (sức chứa 3, đã đặt 3). Agent nêu lý do và chỉ đường đi
tiếp: *"Khu A đã hết chỗ ngày 2026-08-22. Bạn thử Khu B hoặc chọn ngày khác"* —
không hỏi lại thông tin bạn vừa cho.

Với tài khoản **chưa** liên kết, cùng câu này dừng sớm hơn ở tầng quyền (xem dưới).

```
đặt chỗ đỗ xe          # với tài khoản CHƯA liên kết căn hộ
```
→ bị chặn ở tầng quyền, và giải thích đúng lý do: cần liên kết căn hộ trước.

### Gọi thẳng bằng HTTP

```bash
BASE=http://localhost:8080/api/v1

# 1. Đăng ký + đăng nhập
curl -s -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"username":"demo01","password":"Matkhau123!"}'
TOKEN=$(curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"demo01","password":"Matkhau123!"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 2. Gửi goal — body CHỈ mang goal, không mang gì quyết định quyền
WF=$(curl -s -X POST $BASE/workflows/demo/start -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"goal":"Đặt lịch tham quan dự án Vinhomes Ocean Park ngày 2026-09-20 lúc 10:00"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["workflow_id"])')

# 3. Poll trạng thái
curl -s $BASE/workflows/demo/$WF -H "Authorization: Bearer $TOKEN" | python -m json.tool

# 4. Trả lời câu hỏi bổ sung (khi status = NEEDS_INFORMATION)
curl -s -X POST $BASE/workflows/demo/$WF/continue -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"message":"vinhomes ocean park ngày 2026-09-20 lúc 10:00"}'

# 5. Duyệt thanh toán (khi status = WAITING_APPROVAL)
curl -s -X POST $BASE/workflows/demo/$WF/payment-decision -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"decision":"APPROVED"}'
```

Đặt chỗ đỗ xe cần tài khoản đã được ban quản lý duyệt liên kết căn hộ — gửi yêu
cầu ở trang **Liên kết căn hộ**, rồi duyệt bằng tài khoản `admin`.

---

## Running the Project

### Đường chạy canonical

Bốn bước, theo đúng thứ tự. Không cần biết tên compose project, không cần biết
volume cũ, không cần sửa gì bằng tay.

```bash
# 1. Dựng toàn bộ stack và KIỂM trước khi nói là xong.
#
#    Script dừng lại kèm hướng dẫn nếu: Docker chưa chạy, một tiến trình local
#    đang giữ cổng của stack, container tên cố định thuộc compose project khác,
#    migration lỗi, /ready đỏ, hoặc provider và backend không cùng một kho dữ liệu.
#    Nó KHÔNG BAO GIỜ tự xoá volume hay container dữ liệu.
sh scripts/stack_up.sh

# 2. Giao diện React, trỏ vào backend Docker.
cd frontend && npm install
VITE_API_PROXY_TARGET=http://127.0.0.1:8080 npm run dev -- --port 5273

# 3. Browser E2E thật (Playwright + Chromium), chạy trên stack ở bước 1.
cd tests/e2e && npm run setup     # một lần: cài Playwright + Chromium
npm test

# 4. Manual eval với LLM thật (có tính phí).
python eval/run_manual_eval.py > eval/results/raw.json
```

Kiểm nhanh khi nghi ngờ:

```bash
curl -s http://127.0.0.1:8080/ready | python -m json.tool   # cấu hình/DB/migration/connector
docker compose exec backend python scripts/smoke_llm.py     # khoá LLM còn dùng được không
python scripts/check_data_plane.py                          # provider và backend cùng kho?
```

`/health` chỉ nói tiến trình còn sống — **đừng** dùng nó để kết luận hệ thống chạy
được. Đó chính là cách một Compose "toàn healthy" từng che một cấu hình LLM sai
trong khi mọi workflow đều chết ở bước lập kế hoạch.

### Ba database, ba mục đích

| Database | Dùng cho | Ai được ghi |
|---|---|---|
| `p118_db` | stack Docker, demo, browser E2E, manual eval | ứng dụng |
| `p118_test_db` | `pytest` (fixture có `TRUNCATE`) | chỉ test |
| `p118_e2e_db` | thí nghiệm tách biệt | chỉ khi cần |

`tests/_dbcheck.py` chặn fail-closed: fixture test chỉ chạy trên `p118_test_db`.
Trỏ `TEST_DATABASE_URL` sang chỗ khác thì suite dừng chứ không `TRUNCATE` nhầm.

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

## Xem model làm gì lúc demo

Mặc định backend chỉ ghi **số** cho mỗi lần gọi LLM (bảng `llm_usage`: stage,
token, độ trễ). Prompt và câu trả lời không vào DB — chúng mang dữ liệu người
dùng, và một bảng nghiệp vụ không phải chỗ để chúng nằm lại.

Khi cần đứng cạnh máy vừa bấm UI vừa đọc log model, bật trace:

```bash
P118_LLM_TRACE=1 docker compose up -d --build backend
docker compose logs -f backend | grep -A5 ───
```

Mỗi lần gọi in ba khối, gắn nhãn `stage/workflow`:

```
─── LLM VÀO (plan/9b7182a0) ───          prompt gửi đi
─── MODEL SUY LUẬN (plan/9b7182a0) ───   484 token, provider không trả nội dung
─── LLM RA (plan/9b7182a0) ───           TaskPlan model trả về
```

Về dòng giữa: DeepSeek V4 Flash **có** chạy thinking, nhưng endpoint tương
thích OpenAI không trả `reasoning_content` — chỉ trả `reasoning_tokens`. Nên
trace ghi số token thay vì bịa nội dung. Nếu sau này đổi sang provider có trả
chuỗi suy luận, `LlmTraceLogger` đọc sẵn `additional_kwargs.reasoning_content`
và sẽ in ra nguyên văn, không cần sửa gì.

Trace không đi ra giao diện: người dùng cuối vẫn chỉ thấy câu đã qua guard của
Response Agent. Đừng bật trong môi trường chung — nó in cả nội dung người dùng gõ.

## Runtime (Gate 2)

### Khởi động full stack

```bash
# 1. Khởi động PostgreSQL + 4 Mock Provider + Backend
docker compose up -d

# 2. Kiểm tra health (chờ tất cả healthy)
docker compose ps
for p in 8080 8001 8002 8003 8005; do curl -s http://localhost:$p/health; done

# Giao diện: React app (frontend/), chạy riêng
cd frontend && npm install && npm run dev
```

Trang HTML một file `static/demo.html` và route `/demo` đã bị xoá. Giao diện
chạy thật là React app trong `frontend/`, nói chuyện với backend qua đúng một
bộ API canonical `/api/v1/workflows/demo/*` — bộ có kiểm chủ sở hữu.

| Service | Cổng | Mô tả |
| --- | --- | --- |
| Backend | 8080 | FastAPI app + API canonical `/api/v1/*` |
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

CLI dùng cùng workflow API với giao diện React: tự hiển thị tiến độ, hỏi
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
