# P-118 — AI Agent orchestrate đa dịch vụ hoàn thành tác vụ liên hoàn

**Đề tài chương trình:** AI Agent orchestrate đa dịch vụ hoàn thành tác vụ liên hoàn (đặt nhà → xe → dịch vụ)

**Mã đề tài:** PTNT-02 — STT 158

P-118 là AI Agent nhận natural-language goal và orchestrate nhiều service có dependency để hoàn thành một mục tiêu của người dùng.

Core value: **goal-oriented multi-service orchestration** + **failure-aware execution** + **controlled autonomy**

---

## Problem

Một mục tiêu của người dùng thường cắt ngang **nhiều đơn vị cung cấp không quen
nhau** — ban quản lý bãi xe, đội bảo trì, đơn vị chuyển nhà, bộ phận kinh doanh.
Không ai đứng giữa nối họ lại, nên người dùng phải tự:

- Tìm đúng đơn vị theo đúng thứ tự
- Nhập lại dữ liệu ở từng bước
- Theo dõi trạng thái riêng lẻ ở từng nơi
- Xử lý failure mà không có hỗ trợ

**Business scenario (mô phỏng):**

```
Một cư dân mới chuyển vào
→ Register Resident   (hồ sơ cư dân)
→ Register Vehicle    ─┐ ban quản lý bãi xe
→ Book Parking        ─┘
→ Pay Fee             (cổng thanh toán)
```

Đây chỉ là **một** trong nhiều đường đi. Hệ thống phục vụ 8 dịch vụ thuộc 5 đơn
vị độc lập (xem [Features](#features)) — mỗi đơn vị có hàng đợi duyệt riêng và
chỉ quyết định được việc của mình.

> **Bối cảnh.** Dự án là nền tảng điều phối **đa nhà cung cấp** cho dịch vụ cư
> dân và bất động sản. Nó không phục vụ riêng một hệ sinh thái nào và không kết
> nối API production của bất kỳ đơn vị nào. Tên dự án bất động sản xuất hiện
> trong sample query là **dữ liệu demo** (`src/common/projects.py`), có mặt để
> câu tiếng Việt của người dùng có thứ để khớp.

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

**Planner tool allowlist** — nguồn sự thật: `AGENT_REACHABLE_TOOLS` trong
[`src/common/agent_tool_policy.py`](src/common/agent_tool_policy.py), tính bằng
`PROVIDER_TOOLS - AGENT_FORBIDDEN_TOOLS`. Không phải một danh sách viết tay:

- `schedule_property_viewing`
- `register_property_interest`
- `create_maintenance_request`
- `schedule_move`
- `register_vehicle`
- `book_parking`
- `book_shuttle`
- `pay_fee`

**Có connector nhưng Agent KHÔNG chạm được** (`AGENT_FORBIDDEN_TOOLS`):

| Tool                                          | Vì sao đóng                                                                                                          |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `register_resident`                           | Đăng ký / liên kết hồ sơ cư dân xảy ra ngoài Agent (đường admin/provider); giao diện không có chỗ nhập ba ô nó sẽ hỏi |
| `search_properties`                           | Quyết định sản phẩm — listing là chức năng marketplace, không phải tác vụ của Agent                                  |
| `change_parking_zone`                         | Thao tác SỬA trên chỗ đã giữ; chỉ có nghĩa khi đã có `booking_id` thật từ bước trước                                 |
| `cancel_*` (viewing, parking, maintenance, move, shuttle) | Cùng lý do: mã định danh chỉ có thật khi đến từ một bước đã chạy. Để model tự viết ra là để nó huỷ lịch của người khác |

Ràng buộc nằm ở tầng chính sách, **không** ở prompt — đã quan sát trên model
thật: nó tự thêm một tool mà prompt đã dặn không dùng.

Đường sửa lỗi và đường huỷ dựng task từ **kết quả đã chạy**, không từ câu người
dùng gõ. Agent không tự đặt cọc, ký hợp đồng hoặc hoàn tất giao dịch thuê/mua.
Bảo trì và chuyển nhà chỉ dành cho tài khoản cư dân đã liên kết; hai yêu cầu
độc lập có thể được Executor chạy song song.

> **Partial goals:** Người dùng không bắt buộc phải chạy đủ 4 bước. Agent tạo TaskPlan dựa trên mục tiêu hiện tại và dữ liệu đã có — không chạy lại bước đã hoàn thành hoặc không cần thiết. The MVP focuses on one housing-services domain; the connector-based architecture allows future integration with other residential service providers.

---

## Features

**Orchestration**

- Natural-language goal input — user đưa mục tiêu bằng tiếng Việt
- AI-generated TaskPlan với deterministic validation (schema, allowlist, dependency, cycle detection)
- Dependency-aware execution — output bước trước tự động là input bước sau
- Persistent workflow state (PostgreSQL) — resume được sau restart
- Failure recovery — Hero REPLAN scenario (`NO_AVAILABILITY` → alternative), tối đa 1 lượt sửa
- Retry, Saga Compensation, Idempotency key theo từng lời gọi provider

**Hai cổng người thật, KHÁC nhau**

- **HITL — người dùng duyệt.** Policy Engine phân loại action thành
  `AUTO_ALLOWED` / `REQUIRES_APPROVAL` / `DENIED`; tiền là quyết định của khách.
- **Cổng đơn vị cung cấp.** `SERVICE_GATED_TOOLS` — một chỗ đỗ, một buổi bảo
  trì, một chuyến chuyển nhà là cam kết ở phía đơn vị, nên đơn vị duyệt trước
  khi hệ thống gọi ra ngoài. Mỗi dòng chờ duyệt có **chủ sở hữu** cụ thể
  (`service_provider_id`, xem `src/orchestration/provider_directory.py`) và cổng
  quyết định fail-closed: không phải đơn vị của dòng đó thì nhận 404, không phải
  403 — 403 xác nhận dòng ấy có tồn tại.

**Sau khi việc đã xong**

- Thẻ kết quả dựng **một thẻ cho mỗi dịch vụ** có mốc thời gian (ngày giờ, điểm
  gặp, người liên hệ, tải `.ics` riêng từng buổi).
- **Không có nút đổi/huỷ.** Mỗi dịch vụ đều cần một lượt xác nhận của đơn vị và
  đơn vị gọi điện để làm việc ấy, nên đổi/huỷ đi bằng chính cuộc gọi đó. Đường
  huỷ phía sau (`POST /support-requests` → đơn vị duyệt →
  `run_approved_requests` gọi `cancel_*` thật) vẫn còn nguyên và còn test phủ —
  một "đã huỷ" trên màn hình mà lịch bên kia vẫn nằm nguyên là lỗi mà cả module
  `src/orchestration/support_request.py` được viết ra để chặn.

**Giao diện**

- React SPA (`frontend/`) — workspace hội thoại, trang chi tiết yêu cầu, hàng
  đợi duyệt cho đơn vị, màn giám sát cho admin. Poll trạng thái; WebSocket
  realtime chưa triển khai.

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
│   │   ├── state.py         # AgentState schema
│   │   ├── planner.py       # LLM Planner
│   │   ├── validator.py     # TaskPlan validation (deterministic)
│   │   ├── fast_lane.py     # Đường tắt cho goal đã đủ dữ kiện
│   │   ├── prompts/         # Prompt của từng agent
│   │   ├── nodes/ tools/
│   │   └── *_intent.py      # Đọc ý định người dùng ở từng loại lượt chờ
│   ├── common/              # Tầng DƯỚI CÙNG — mọi tầng khác dựng trên nó
│   │   ├── task_plan.py     # `AllowedTool`, schema TaskPlan
│   │   ├── agent_tool_policy.py  # Agent được phép chạm tool nào
│   │   ├── policy.py        # AUTO_ALLOWED / REQUIRES_APPROVAL / DENIED
│   │   └── schedule_policy.py, field_parsers.py, results.py, ...
│   ├── connectors/          # MỘT connector cho MỖI dịch vụ ngoài
│   │   ├── base.py          # ABC: tool_names, execute, idempotency_key_for
│   │   ├── resident.py transport.py payment.py property.py
│   │   ├── resident_services.py tour.py shuttle.py consultation.py
│   │   └── vnpay.py
│   ├── executor/            # Chạy TaskPlan theo dependency
│   ├── orchestration/       # Tầng quyết định (KHÔNG gọi LLM)
│   │   ├── demo_service.py       # Vòng đời một yêu cầu, resume
│   │   ├── deps.py               # build_connectors() — nơi ĐĂNG KÝ duy nhất
│   │   ├── service_approval.py   # Cổng duyệt của đơn vị cung cấp
│   │   ├── provider_directory.py # tool → đơn vị chịu trách nhiệm
│   │   ├── provider_gateway.py   # Mọi lời gọi ra ngoài đi qua đây
│   │   ├── support_request.py    # "Đồng ý cho huỷ" → gọi cancel_* thật
│   │   ├── payment_approval.py compensation.py proposal*.py
│   │   └── provider_selection.py, provider_matching.py, ...
│   ├── api/                 # FastAPI routes
│   │   ├── routes.py                  # Đường của người dùng
│   │   ├── service_approval_routes.py # Đường của đơn vị cung cấp
│   │   ├── admin_routes.py, auth*.py, verification_routes.py, ...
│   ├── db/                  # PostgreSQL: schema.sql, migrations, repository
│   ├── services/mock/       # Mock provider (in-process, dùng chung DB pool)
│   ├── mock/                # Mock provider chạy như service riêng (routers/)
│   ├── models/schemas.py    # Pydantic schema của API — contract với frontend
│   ├── monitoring/          # LLM trace, chi phí, usage
│   ├── config.py
│   └── main.py
├── frontend/                # React + Vite + Tailwind
│   └── src/
│       ├── pages/           # WorkflowPage, JourneyWorkspacePage, ProviderReviewPage, ...
│       ├── components/      # workspace/ (ResultSummary, StepList, JourneyCanvas), journey/
│       └── lib/             # agentApi.ts (client), types.ts (contract), status.ts
├── tests/                   # ~4.6k test
│   ├── test_db/             # Cần PostgreSQL thật (TEST_DATABASE_URL)
│   ├── test_api/ test_agents/ test_orchestration/ test_integration/
│   ├── test_mock/ test_demo/ unit/ matrix/ e2e/
│   └── fakes/ fixtures/     # Fake LLM, fake connector — test KHÔNG gọi LLM thật
├── scripts/                 # AI Logging Hooks + tiện ích vận hành
├── docs/                    # RUNBOOK, SLO, architecture_diagram, gate1/
├── eval/results/
├── ARCHITECTURE.md JOURNAL.md AGENTS.md
├── .github/workflows/       # CI: ruff + pytest + build frontend + guard không-skip
├── .claude/ .codex/ .cursor/ .gemini/   # Per-tool hook configs
├── Dockerfile
└── docker-compose.yml       # postgres + migrate + backend + 9 mock provider
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

Khi test fail, phân loại lỗi theo tầng trước khi sửa: Planner (kế hoạch sai) →
Executor (chạy sai thứ tự / thiếu dữ kiện) → Connector (đường dẫn, tên field) →
Provider (mock trả gì) → DB (schema, migration) → Docker (cổng, container).
Đọc log của đúng tầng đầu tiên hỏng — các tầng sau thường chỉ là hệ quả.

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
- [Architecture](ARCHITECTURE.md) · [Architecture Diagram](docs/architecture_diagram.md)
- [Runbook](docs/RUNBOOK.md) · [SLO](docs/SLO.md)
- [Đề xuất đơn vị cung cấp](docs/DE_XUAT_DON_VI_CUNG_CAP.md)
- [Weekly Journal](JOURNAL.md)
- [Evaluation](eval/results/report.md) · [Evaluation report (chi tiết)](docs/P118_EVALUATION_REPORT.md)

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
