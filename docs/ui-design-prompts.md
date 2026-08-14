# P-118 — Prompt thiết kế giao diện (UI Design Prompts)

> Bộ prompt dành cho **Hoàng Anh** (owner `frontend/`) sinh giao diện bằng AI
> (v0 · Cursor · Claude · bolt). Mỗi prompt copy vào tool UI, bind đúng
> contract thật của P-118 — **không dùng tên status cũ** trong wireframe Gate 1.

---

## 0. Trước khi dùng — status CHÍNH XÁC theo contract

`docs/gate1/wireframe.md` (Gate 1) dùng tên cũ. Contract thật trong
`shared_contracts.md` + `src/common/enums.py` mới là chuẩn để nối API:

| Wireframe cũ (KHÔNG dùng) | Contract thật (DÙNG) |
|---|---|
| `COMPLETED` | `SUCCESS` |
| `RECOVERING` | không có — replan hiển thị bằng dòng UX "Agent đang tìm phương án thay thế…" |
| `AWAITING_APPROVAL` | `WAITING_APPROVAL` |
| `ROLLED_BACK` | không có trong enum hiện tại (dùng `CANCELLED` / `FAILED` + thông báo hoàn tác) |

**Cách dùng:** paste **Design System** (mục 1) vào đầu, rồi paste prompt màn hình tương ứng.

---

## 1. Design System chung — paste đầu mọi prompt

```
Bạn đang thiết kế giao diện cho P-118 — AI Agent điều phối đa dịch vụ dành cho
cư dân (đặt nhà → xe → dịch vụ). User nhập mục tiêu bằng tiếng Việt, Agent tự
lập kế hoạch rồi gọi chuỗi dịch vụ: Đăng ký cư dân → Đăng ký phương tiện →
Đặt chỗ đậu xe → Thanh toán phí.

NGÔN NGỮ UI: tiếng Việt.
STACK: React 18 + TypeScript + Vite + Tailwind CSS + lucide-react icons.
Responsive mobile-first, nội dung tối đa ~1100px trên desktop.

THIẾT KẾ:
- Nền #F8FAFC (gray-50); card trắng border #E2E8F0, rounded-2xl, shadow-sm, padding 24px.
- Brand teal #0F766E (hover #115E59). Tiêu đề slate-900, phụ đề slate-500/600. Cảnh báo amber-500.
- Font Inter/system-ui. Heading text-lg font-semibold. Spacing theo grid 4px.
- Icon chính dùng lucide-react, không dùng raw emoji. Status = màu + icon kết hợp.

STATUS WORKFLOW (contract chuẩn):
| Status | Màu | Icon | Label VN |
|---|---|---|---|
| PENDING | slate | Clock | Đang chờ |
| RUNNING | blue | Loader2 (spin) | Đang thực hiện |
| WAITING_APPROVAL | amber | PauseCircle | Chờ xác nhận |
| SUCCESS | emerald | CheckCircle2 | Hoàn thành |
| FAILED | red | XCircle | Thất bại |
| CANCELLED | slate | Ban | Đã hủy |

STATUS TASK:
| Status | Màu | Icon | Label VN |
|---|---|---|---|
| PENDING | slate | Hourglass | Chưa sẵn sàng |
| READY | blue (outline) | CircleDot | Sẵn sàng |
| RUNNING | blue | Loader2 (spin) | Đang thực hiện |
| WAITING_APPROVAL | amber | PauseCircle | Chờ xác nhận |
| SUCCESS | emerald | CheckCircle2 | Thành công |
| FAILED | red | XCircle | Thất bại |
| SKIPPED | slate (dashed) | SkipForward | Bỏ qua |
| CANCELLED | slate | Ban | Đã hủy |

4 TOOL và nội dung cần hiển thị:
| Tool | Tên hiển thị | Field hiện khi SUCCESS |
|---|---|---|
| register_resident | Đăng ký cư dân | resident_id |
| register_vehicle | Đăng ký phương tiện | vehicle_id |
| book_parking | Đặt chỗ đậu xe | booking_id, parking_zone, booking_date, amount, currency |
| pay_fee | Thanh toán phí | payment_id, payment_status |

payment_status hiển thị: PENDING=Đang xử lý · PAID=Đã thanh toán ·
FAILED=Thất bại · REFUNDED=Đã hoàn tiền.

Khi 1 task FAILED và workflow đang replan, hiển thị dòng UX kèm spinner:
"Agent đang tìm phương án thay thế…" — đây là thông báo trạng thái, không phải task status.

KHÔNG hiển thị: raw JSON, system prompt, database record, HTTP payload.
```

---

## 2. Màn hình User (core — tuần 3)

### Prompt 2.1 — Home / Goal Input

<!-- ================= PROMPT BẮT ĐẦU ================= -->
```
SCREEN: Home — nhập mục tiêu bằng ngôn ngữ tự nhiên, xem workflow gần đây.

API:
- POST /workflow/start   body { "goal": "<câu mô tả>" }
    → { "workflow_id": "…", "status": "PENDING" }
- GET /workflows   → danh sách workflow gần đây
    (nếu backend chưa có endpoint này, dùng mảng mock tạm)

BỐ CỤC:
1. Header (fixed): brand "P-118 — Trợ lý dịch vụ cư dân" + link "Trang quản trị"
   (dẫn /admin).
2. Hero (giữa màn hình):
   - Tiêu đề "Bạn muốn làm gì hôm nay?"
   - Textarea lớn (4–6 dòng), placeholder:
     "Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư dân, xe, chỗ đậu xe và
      thanh toán phí giúp tôi."
   - Đếm ký tự, tối đa 500.
   - Nút chính [ Bắt đầu ] — disabled khi textarea rỗng; khi submit hiện
     spinner trong nút, gọi POST /workflow/start, thành công → điều hướng
     /workflow/{workflow_id}; lỗi → alert đỏ dưới textarea, giữ nội dung nhập.
   - 3–4 chip gợi ý goal (click → điền vào textarea):
     • "Tôi mới chuyển vào căn hộ A1201, đăng ký cư dân và xe giúp tôi."
     • "Đặt chỗ đậu xe ZONE_A ngày mai cho xe của tôi."
     • "Đặt chỗ đậu xe và thanh toán phí."
     • "Đăng ký cư dân, xe, chỗ đậu xe và thanh toán phí."
3. Section "Workflow gần đây" (dưới hero):
   - Mỗi item là card: badge status (màu theo Design System) + trích goal
     (1 dòng ellipsis) + workflow_id (mono, 8 ký tự đầu) + thời gian tạo
     (format VN). Click → /workflow/{id}.
   - Trạng thái trống: "Chưa có workflow nào." kèm EmptyState.
   - Loading: skeleton cards.

KHÔNG hiển thị raw JSON hay chi tiết nội bộ.
```
<!-- ================= PROMPT KẾT THÚC ================= -->

### Prompt 2.2 — Workflow Timeline

<!-- ================= PROMPT BẮT ĐẦU ================= -->
```
SCREEN: Workflow Timeline — theo dõi tiến trình từng bước (màn hình chính).

API:
- GET /workflow/{workflow_id}/status
    → { "workflow": { workflow_id, goal, status, created_at, updated_at },
         "tasks": [ { task_id, tool, status, depends_on, input_data,
                      result_data, error_code, error_message, updated_at } ] }
- Polling mỗi 2–3s (Gate 2) HOẶC WebSocket /ws/{workflow_id} (Demo Day).
  Nếu WS có: dùng WS nhận update, polling làm fallback.

BỐ CỤC:
1. Header: nút ← Quay lại (về /) · "Workflow #<8 ký tự đầu của id>" (mono) ·
   nút [ Làm mới ] (manual refresh).
2. Thẻ thông tin workflow:
   - "Mục tiêu" (quote, tối đa 2 dòng).
   - Badge trạng thái tổng (màu theo Design System).
   - Thời gian bắt đầu (format VN); hiện thêm thời gian kết thúc nếu có.
3. Timeline dọc:
   - Mỗi task 1 node nối bằng đường dọc. Node RUNNING có spinner + glow nhẹ;
     FAILED đỏ; WAITING_APPROVAL amber.
   - Tiêu đề node: "T1 · Đăng ký cư dân" (task_id + tên tool hiển thị).
   - Nội dung node khi SUCCESS (từ result_data):
     • register_resident → "Resident ID: RES-001"
     • register_vehicle → "Vehicle ID: VEH-001"
     • book_parking → "Booking: BOOK-001 · Zone A · 2026-08-10 · 150.000 VND"
     • pay_fee → "Mã GD: PAY-001 · Đã thanh toán" (payment_status dịch VN)
   - Task FAILED: dòng "⚠ <error_message>" (vd "Zone A không còn chỗ trống").
   - Khi task thất bại và workflow đang replan: giữa 2 node hiện dòng
     "Agent đang tìm phương án thay thế…" + spinner (thông báo UX, không phải task status).
   - Node PENDING: mờ (opacity 60%), subtext "Chờ <depends_on> hoàn thành…".
   - Data propagation giữa 2 node: mũi tên nhỏ + label,
     vd "resident_id → T2", "booking_id · amount → T4".
4. Footer (khi SUCCESS/FAILED/CANCELLED):
   - SUCCESS: "✅ Workflow hoàn thành thành công." + số task thành công / tổng.
   - FAILED: "❌ Workflow không thể tiếp tục." + nút "Xem chi tiết".
   - Link "Xem chi tiết / kết quả" → /workflow/{id}/detail.
5. Khi workflow WAITING_APPROVAL → mở HITL Modal (overlay, màn hình kế).
6. Responsive: mobile timeline không hiện đường center-line, chuyển sang lề trái.

KHÔNG hiển thị raw JSON / prompt / database record.
```
<!-- ================= PROMPT KẾT THÚC ================= -->

### Prompt 2.3 — HITL Modal

<!-- ================= PROMPT BẮT ĐẦU ================= -->
```
SCREEN: HITL Modal — overlay xác nhận hành động nhạy cảm.
Mở khi workflow = WAITING_APPROVAL. KHÔNG phải page riêng.

API:
- POST /workflow/{workflow_id}/approve   body { "task_id": "T4" }
    → workflow tiếp tục
- POST /workflow/{workflow_id}/reject    body { "task_id": "T4" }
    → workflow xử lý (dừng / hủy / hoàn tác)

NỘI DUNG:
1. Overlay toàn màn hình (đen 60%, backdrop-blur nhẹ), card trắng rounded-2xl
   tối đa 480px, z-index cao. Nền phía sau (Timeline) mờ.
2. Header: icon PauseCircle amber + "XÁC NHẬN HÀNH ĐỘNG".
3. Dòng: "Agent muốn thực hiện:" + tên hành động (vd "Thanh toán phí").
4. Chi tiết dạng key-value grid:
   - Số tiền: "800.000 VND" (định dạng tiền VN, đậm, cỡ lớn)
   - Mô tả: <nội dung policy / error_message>
   - Dịch vụ: "Payment Service"
5. Box cảnh báo amber:
   "⚠ Hành động này phát sinh giao dịch tài chính. Agent cần xác nhận của bạn
   trước khi thực hiện." (text linh hoạt theo loại action: financial / destructive / sensitive)
6. Nút: [ Từ chối ] (ghost, đỏ) bên trái · [ ✓ Duyệt ] (primary) bên phải.
   Khi bấm: nút loading, gọi API tương ứng. Thành công → đóng modal, Timeline
   tự refresh. Lỗi → hiển thị lỗi trong modal.
7. Đóng bằng ESC / click outside CHỈ khi không đang submit.
8. ARIA: role="dialog", aria-modal="true", focus ban đầu vào nút Duyệt.
```
<!-- ================= PROMPT KẾT THÚC ================= -->

### Prompt 2.4 — Workflow Detail / Result

<!-- ================= PROMPT BẮT ĐẦU ================= -->
```
SCREEN: Workflow Detail / Result — review lại workflow (đã xong hoặc đang chạy).

API:
- GET /workflow/{workflow_id}/status → workflow + tasks
- Attempt/recovery history: nếu backend trả execution_logs
  (attempt_number, http_status, raw_error_code, duration_ms, created_at)
  thì hiển thị; nếu chưa, ẩn hoặc mock.

BỐ CỤC:
1. Header: ← Quay lại · "Workflow Detail #<id>" · nút [ Làm mới ].
2. Thẻ tổng quan: mục tiêu (quote) · bắt đầu / kết thúc (format VN) ·
   badge trạng thái tổng.
3. Danh sách task (card riêng từng task, expandable, mặc định mở):
   - Header card: task_id + tên tool + badge status.
   - "Kết quả": key-value theo tool (resident_id, vehicle_id, booking, payment).
   - "Dùng từ bước trước" (nếu có): vd "Dùng Resident ID từ T1: RES-001".
   - "Lịch sử thử lại" (recovery) nếu task có nhiều attempt:
     • Lần 1: Zone A — ❌ NO_AVAILABILITY · "Zone A không còn chỗ trống"
     • → Agent tự replan
     • Lần 2: Zone B — ✅ Booking ID: BOOK-001
     (mỗi attempt là dòng con, kèm thời gian và duration_ms nếu có)
4. Box tổng kết cuối ("KẾT QUẢ CUỐI CÙNG"):
   - SUCCESS: "Hoàn thành: 4/4 tác vụ" + "Phục hồi: 1 lần (Zone A → Zone B)"
     (nếu có) + "✅ Workflow hoàn thành thành công."
   - FAILED: nêu task thất bại + lý do.
   - CANCELLED: "Workflow đã bị hủy."

KHÔNG hiển thị raw JSON / prompt / HTTP payload.
```
<!-- ================= PROMPT KẾT THÚC ================= -->

---

## 2.5. Màn hình Auth (Login / Register)

### Prompt 2.5 — Login

<!-- ================= PROMPT BẮT ĐẦU ================= -->
```
SCREEN: Login — đăng nhập (không nằm trong AppLayout).

API:
- POST /auth/login   body { "username", "password" }
    → { "access_token", "token_type": "bearer", "expires_in", "user": { id, username, email, role } }
- Lưu access_token (localStorage) + gắn header `Authorization: Bearer <token>`
  cho mọi request sau. Vai trò: role='resident' | 'admin'.

BỐ CỤC (card giữa màn hình, max 420px):
1. Logo brand + tiêu đề "Đăng nhập" + phụ đề "P-118 — Trợ lý dịch vụ cư dân".
2. Form: Tên đăng nhập (text) · Mật khẩu (password, có nút hiện/ẩn).
3. Nút [ Đăng nhập ] full-width — disabled khi rỗng; submit hiện spinner.
   Lỗi 401 → alert đỏ "Tên đăng nhập hoặc mật khẩu không đúng." (không phân
   biệt sai username/password).
4. Link "Chưa có tài khoản? Đăng ký" → /register.
5. (Chế độ mock) hint tài khoản demo: admin/admin123 · resident/resident123.
6. Sau login → điều hướng / (hoặc route trước đó). Admin vẫn vào / nhưng
   sidebar hiện thêm mục "Quản trị".

KHÔNG hiển thị raw token / payload.
```
<!-- ================= PROMPT KẾT THÚC ================= -->

### Prompt 2.6 — Register

<!-- ================= PROMPT BẮT ĐẦU ================= -->
```
SCREEN: Register — tạo tài khoản cư dân (không nằm trong AppLayout).

API:
- POST /auth/register  body { "username", "password", "email"? }
    → user mới role='resident' (không trả token; tự login sau hoặc chuyển /login).
- 409 khi username đã tồn tại.

BỐ CỤC (card giữa màn hình, max 420px):
1. Tiêu đề "Tạo tài khoản" + phụ đề "Đăng ký để sử dụng trợ lý dịch vụ cư dân".
2. Form: Tên đăng nhập (≥3 ký tự) · Email (không bắt buộc) · Mật khẩu
   (≥8 ký tự) · Xác nhận mật khẩu.
3. Validate client: username ≥3, password ≥8, confirm khớp — lỗi hiện inline.
4. Nút [ Tạo tài khoản ] full-width. Thành công → login tự động → về /.
5. Link "Đã có tài khoản? Đăng nhập" → /login.

KHÔNG hiển thị raw token / payload.
```
<!-- ================= PROMPT KẾT THÚC ================= -->

---

## 3. Màn hình Admin (Extension — sau Demo Day)

### Prompt 3.1 — Admin Dashboard

<!-- ================= PROMPT BẮT ĐẦU ================= -->
```
SCREEN: Admin Dashboard — giám sát toàn bộ workflow (Extension).

API:
- GET /workflows?status=&q=&page=&limit=
    → { "items": [...], "total": N, "page": 1, "limit": 20 }
  (nếu backend chưa có, mock dữ liệu)

BỐ CỤC:
1. Header/Sidebar: "P-118 Admin" + link "Xem như người dùng" (về /).
2. KPI cards (4): Tổng workflow · Đang chạy (RUNNING) ·
   Chờ xác nhận (WAITING_APPROVAL) · Thất bại (FAILED).
   Mỗi card: số lớn + icon + màu theo status.
3. Filter bar: search theo goal/workflow_id · dropdown status
   (Tất cả / PENDING / RUNNING / WAITING_APPROVAL / SUCCESS / FAILED / CANCELLED) ·
   filter theo ngày tạo (optional) · nút Reset.
4. Bảng workflow:
   - Cột: workflow_id (mono, truncate 12) · Mục tiêu (trích 2 dòng) ·
     Trạng thái (badge) · Bắt đầu · Thời lượng (nếu có) · Hành động [Chi tiết].
   - Click row → /admin/workflow/{id}.
   - Phân trang (10–20 dòng/trang), hiển thị "X–Y / tổng".
   - Trạng thái trống: "Không có workflow phù hợp." + nút Reset.
   - Loading: skeleton rows.
```
<!-- ================= PROMPT KẾT THÚC ================= -->

### Prompt 3.2 — Admin Workflow Detail / Audit

<!-- ================= PROMPT BẮT ĐẦU ================= -->
```
SCREEN: Admin Workflow Detail / Audit — chi tiết + nhật ký thực thi (Extension).

API:
- GET /workflow/{workflow_id}/status → workflow + tasks
- execution_logs: attempt_number, connector_name, http_status,
  raw_error_code, duration_ms, created_at
- approval_decisions: task_id, decided_by, decision, comment, decided_at
  (nếu backend chưa trả 2 loại log này, ẩn section hoặc mock)

BỐ CỤC:
1. Header: ← Quay lại (về /admin) · "Workflow #<id>".
2. Thẻ tổng quan: goal · badge status · created/updated · archived (nếu có).
3. Tabs:
   a) "Tổng quan": giống Workflow Detail user, thêm mục "Task plan gốc"
      (hiển thị dạng JSON đọc được, format khối, không phải raw dump).
   b) "Execution log": bảng theo thời gian —
      cột: thời gian · task_id · attempt# · connector_name · http_status ·
      raw_error_code · duration_ms · kết quả (SUCCESS/FAILED).
      Highlight lỗi đỏ; row expand → hiện standard_result JSON nếu cần.
   c) "Phê duyệt": bảng — task_id · decided_by · decision
      (APPROVED/REJECTED) · comment · decided_at.
4. Hành động admin: nút [ Hủy workflow ] (chỉ khi RUNNING/PENDING/
   WAITING_APPROVAL) → confirm dialog → gọi cancel.
```
<!-- ================= PROMPT KẾT THÚC ================= -->

---

## 4. Backend cần bổ sung để UI chạy đúng

| Cần | Cho màn hình | Trạng thái |
|---|---|---|
| `GET /workflows` (list + filter + pagination) | Home "Workflow gần đây" + Admin Dashboard | **Chưa có** — cần thêm `src/api/routes.py` |
| `GET /workflow/{id}/status` gộp `execution_logs` + `approval_decisions` (hoặc endpoint riêng) | Detail / Audit (recovery history) | `get_workflow()` hiện chỉ trả workflow + tasks |
| `POST /workflow/{id}/approve` + `/reject` | HITL Modal | Tuần 3 (`tasks/P118-003-hoanganh.md`) |
| WebSocket `/ws/{workflow_id}` | Timeline realtime | Tuần 3 |
| `POST /auth/register` + `POST /auth/login` + `GET /auth/me` | Login / Register + bảo vệ route | **Đã có** (`src/api/auth_routes.py`) — frontend bind ở `lib/api.ts` + `lib/auth.tsx` |
| CORS | Frontend gọi API | Đã mở trong `src/main.py` |

> Lưu ý auth: mọi route nghiệp vụ `/api/v1` đã thêm `Depends(get_current_user)` —
> frontend gắn header `Authorization: Bearer <token>` cho mọi request. Vai trò
> `admin` được tạo bằng `scripts/create_admin.py` (không đăng ký qua UI).



*Nguồn contract: `shared_contracts.md` · `src/common/enums.py` · `src/db/orm_models.py` · `docs/gate1/wireframe.md` (UI flow) · `docs/gate1/PRD.md` (US/FR)*
