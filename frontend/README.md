# P-118 Frontend — React (Vite)

## Node runtime

`vite` 8 chạy trên `rolldown`, và `rolldown` import `styleText` từ `node:util`
— API chỉ có từ Node 20.12. Trên Node 18 lệnh `npm run build` chết ngay ở bước
nạp module với `SyntaxError`, và thông điệp không nói gì về phiên bản Node.

Khai `>=20.19` ở `package.json#engines` và `.nvmrc`. Hai chỗ vì chúng phục vụ
hai người đọc: `engines` cho `npm` và cho người rà soát; `.nvmrc` cho `nvm use`
của lập trình viên. CI (`.github/workflows/ci.yml`) đã dùng Node 20 — cùng
major, nên khai báo này không đổi CI, nó chỉ làm máy cá nhân khớp với CI.

`20.19` chứ không phải `20.12`: `styleText` xuất hiện từ 20.12, nhưng 20.19 là
phiên bản ĐÃ CHẠY THỬ ở đây (`tsc` + `oxlint` + `vite build` đều sạch). Khai
một mốc chưa ai chạy là hứa hộ một thứ chưa kiểm.

```
nvm use          # đọc .nvmrc
npm ci
npm run build
```

Giao diện người dùng cho **P-118 — AI Agent điều phối đa dịch vụ cư dân**.
Theo `docs/ui-design-prompts.md` (Design System + các prompt màn hình).

## Stack

- **React 18** + TypeScript + **Vite**
- **Tailwind CSS v4** (`@tailwindcss/vite`, design tokens trong `src/index.css`)
- **lucide-react** icons
- **react-router-dom** v7 (routing)
- **@xyflow/react** (React Flow v12) — canvas builder workflow kéo-thả

## Cài đặt & chạy

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Trong dev, Vite proxy `/api` → `http://localhost:8000` (backend FastAPI).
Đổi target qua env `VITE_API_PROXY_TARGET` nếu backend chạy cổng khác.

## Build

```bash
npm run build        # tsc -b && vite build → dist/
npm run preview      # xem bản build
```

## Cấu trúc

```
frontend/
├── src/
│   ├── components/
│   │   ├── AppLayout.tsx# header+sidebar, StatusBadge, Timeline, HitlModal, Bits
│   │   └── builder/     # ToolPalette, BuilderCanvas, ToolNode, BuilderInspector, GeneratedGoalModal
│   ├── lib/
│   │   ├── client.ts    # ★ Facade dữ liệu — UI chỉ import từ đây
│   │   ├── mockData.ts  # Bộ dữ liệu mock bám sát contract thật
│   │   ├── api.ts       # API client (proxy /api/v1 → backend) — chưa nối
│   │   ├── status.ts    # status mapping, tool labels, format tiền/ngày VN
│   │   ├── types.ts     # types bind contract thật (WorkflowStatus/TaskStatus)
│   │   ├── toolRegistry.ts # ★ Schema từng tool (input/output) — nguồn cho builder
│   │   ├── builder.ts   # ★ Logic builder: DraftStep/Edge, buildPlan, generateGoal, validate
│   │   └── usePolling.ts# polling 2.5s (Gate 2; WebSocket thay ở Demo Day)
│   └── pages/           # Dashboard, BuilderPage, WorkflowsPage, WorkflowPage (timeline), DetailPage, ApprovalsPage
├── vite.config.ts       # react + tailwind plugin + /api proxy
└── index.html
```

## Trình tạo workflow kéo-thả (`/builder`)

Thay vì gõ prompt, người dùng ráp chuỗi dịch vụ liên hoàn bằng kéo-thả:

- **ToolPalette** (trái): 4 dịch vụ (`register_resident`, `register_vehicle`, `book_parking`, `pay_fee`) — kéo vào canvas hoặc bấm **+**.
- **BuilderCanvas** (giữa, React Flow): node + nối dây theo field (handle id `stepId::field`), drag node để sắp xếp, tự chặn cycle/self-loop.
- **BuilderInspector** (phải): điền literal hoặc "Lấy từ task khác" (InputRef). Với `pay_fee`, 3 field `booking_id/amount/currency` **chỉ** lấy từ task `book_parking` (trust boundary).
- **Xem goal sinh ra**: câu Vietnamese được tự sinh từ các bước (theo thứ tự topo) — sửa được trước khi tạo.
- **Tạo workflow & chạy**: dựng `TaskPlan` (`T<n>-<tool>`, `depends_on`, `InputRef`) → `startPlan(goal, tasks)` → về `/workflow/:id` (timeline + HITL).

Logic thuần (không React) nằm ở `src/lib/builder.ts` + `src/lib/toolRegistry.ts`.
Input/output field khớp `shared_contracts.md` §4 + `src/agents/validator.py`.

## Chế độ MOCK — nối backend sau

UI hiện chạy với **mock data**, chưa gọi backend. Cơ chế ở `src/lib/client.ts`:

- `USE_MOCK = (import.meta.env.VITE_USE_MOCK ?? 'true') !== 'false'`
- **Mặc định `true`** → tất cả hàm trong `client.ts` trả dữ liệu từ `mockData.ts`
  (có độ trễ giả lập để skeleton hiển thị đúng).
- Khi backend sẵn sàng, chạy với `VITE_USE_MOCK=false` (hoặc sửa flag) → tự chuyển
  sang `api.ts`, gọi thật qua proxy `/api/v1`. **UI không cần sửa gì.**
- `mockData.ts` bám sát contract (`shared_contracts.md` + `src/common/enums.py`):
  5 workflow mẫu phủ hết trạng thái WAITING_APPROVAL / SUCCESS / RUNNING /
  FAILED / CANCELLED, kèm chuỗi task `register_resident → register_vehicle →
  book_parking → pay_fee` (input truyền nhau qua InputRef).
- Mock HITL: bấm Duyệt/Từ chối cập nhật trạng thái **trong bộ nhớ** (trang
  Chờ duyệt và workflow tương ứng sẽ phản ánh ngay, kể cả khi reload SPA).
- Trang Chờ duyệt và header hiển thị badge **MOCK DATA** để không nhầm với dữ liệu thật.

## Trạng thái tích hợp backend

UI đã viết theo contract thật (`shared_contracts.md` + `src/common/enums.py`).
Happy path **đã nối backend** — bật bằng `VITE_USE_MOCK=false` (xem `.env`):

| Endpoint UI gọi | Backend hiện tại |
|---|---|
| `POST /api/v1/auth/register` / `login` / `me` | ✅ Có |
| `POST /api/v1/workflow/start` (prompt → LLM Planner) | ✅ Có |
| `POST /api/v1/workflow/start` (plan — builder, body `{goal, tasks}`) | ✅ Có |
| `GET /api/v1/workflow/{id}/status` | ✅ Có |
| `GET /api/v1/workflows` | ✅ Có |
| `POST /api/v1/workflow/{id}/execute` | ✅ Có |
| `POST /api/v1/workflow/{id}/approve` / `reject` / `cancel` | ⏳ **Tuần 3** — endpoint chưa có; trong chế độ real, các nút HITL báo lỗi rõ ràng (backend không bao giờ vào `WAITING_APPROVAL` nên happy path không chạm tới) |

Với `VITE_USE_MOCK=false`, nếu backend chưa khởi động hoặc route lỗi, UI hiển
thị skeleton/loading và thông báo lỗi kết nối — không crash. Chi tiết:
`docs/ui-design-prompts.md` §4.

## Lưu ý

- **Trạng thái dùng đúng contract**, không dùng tên cũ của wireframe Gate 1
  (`COMPLETED` → `SUCCESS`, `AWAITING_APPROVAL` → `WAITING_APPROVAL`, ...).
- Tiền hiển thị định dạng VN (`150.000 VND`), ngày theo `vi-VN`.
- Không hiển thị raw JSON / prompt / database record trong UI.
