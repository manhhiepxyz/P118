# P-118 — Agent Workspace Redesign

Tài liệu thiết kế và đối chiếu với implementation Gate 2 hiện tại. Mockup HTML
trong cùng thư mục vẫn là bản tĩnh; giao diện chạy thật là React app trong `frontend/` (route `/demo` đã bị xoá).

Mọi phần tử giao diện dưới đây đều được gắn nhãn:

- **[CÓ SẴN]** — backend hiện tại đã trả dữ liệu này.
- **[UI]** — trình bày thuần giao diện, suy ra từ dữ liệu đã có.
- **[CẦN BACKEND]** — chưa có, phải làm thêm mới hiển thị được.

Nguyên tắc bao trùm: không bịa hoạt động backend, không bịa ETA, không bịa
realtime. Thà thiếu một con số còn hơn hiện một con số sai.

---

## 1. Chẩn đoán UX màn hình hiện tại

| # | Vấn đề | Hệ quả |
|---|---|---|
| 1 | Khung hội thoại chiếm ~70% màn hình, composer ghim đáy | Người dùng đọc P-118 là chatbot, chờ mình gõ tiếp thay vì để agent tự chạy |
| 2 | Quick action là "prompt chip" | Bấm chip xong lại phải mô tả lại yêu cầu — hai lần nói cùng một việc |
| 3 | Không thấy công việc đang chạy | Agent làm 3–4 bước thật nhưng người dùng chỉ thấy một dấu "..." |
| 4 | Lập kế hoạch / thực thi / chờ duyệt trộn chung một dòng chat | Không phân biệt được "đang chạy" với "đang chờ tôi" |
| 5 | Không có khái niệm nhiều việc song song | Mỗi lần chỉ theo dõi được một workflow; xong là trôi mất |
| 6 | Trạng thái chỉ phân biệt bằng màu badge | Người mù màu mất thông tin trạng thái |
| 7 | Dịch vụ chỉ dành cho cư dân bị **ẩn hoàn toàn** với tài khoản khách | Khách không biết dịch vụ tồn tại, cũng không biết cần làm gì để mở |
| 8 | Khoảng trắng lớn ở giữa màn hình | Trên máy chiếu laptop, phần lớn diện tích không mang thông tin |

Ba lỗi rò rỉ thuật ngữ **nằm ở backend, không phải ở UI** — cần sửa cùng lượt:

`_STAGE_MESSAGES` trong `src/api/routes.py` đang đưa nguyên văn *"LLM đang phân
tích"*, *"Agent đã tạo TaskPlan"*, *"Validator đang kiểm tra dependency,
allowlist"*, *"Executor đang gọi các dịch vụ"* vào `events[].message` — tức là
đúng thứ đề bài cấm hiển thị, và UI hiện đang hiển thị chúng.

---

## 2. Kiến trúc thông tin

```
P-118 Workspace
├── Tổng quan            ← mặc định
│   ├── Cần bạn xử lý        (chỉ hiện khi có việc)
│   ├── Đang thực hiện
│   ├── Bắt đầu nhanh        (mục tiêu, không phải prompt)
│   ├── Vừa hoàn thành
│   └── Hoạt động gần đây    (tóm tắt, không phải transcript)
├── Đang thực hiện       ← lọc
├── Chờ bạn xử lý        ← lọc, có số đếm
├── Đã hoàn thành        ← lọc
├── Dịch vụ              ← 7 năng lực + trạng thái khoá
└── Hồ sơ cư dân         ← trạng thái liên kết căn hộ
```

Chi tiết một workflow là **trang riêng**, không phải panel trượt: nó có URL
riêng, chia sẻ được, và mở lại sau restart vẫn đúng.

**Phân cấp ưu tiên trên Tổng quan** (từ trên xuống): việc chờ tôi → việc đang
chạy → cách giao việc mới → việc đã xong → nhật ký.

---

## 3. Đặc tả bố cục

### 3.1 Khung chung

```
┌──────────┬──────────────────────────────────────────────────┐
│ 232px    │  Nội dung, tối đa 1100px, canh trái              │
│ sidebar  │                                                  │
│          │  ┌── Topbar 56px: breadcrumb · ô giao việc ──┐   │
│          │  ├────────────────────────────────────────────┤  │
│          │  │  Vùng cuộn                                 │  │
└──────────┴──────────────────────────────────────────────────┘
```

Ô "Giao việc cho P-118…" nằm ở **topbar**, cao 36px — luôn với tới được nhưng
không còn là nhân vật chính. Đây là thay đổi quan trọng nhất so với bản cũ.

### 3.2 View 1 — Workspace Home

```
┌─────────────────────────────────────────────────────────────┐
│ Chào Thành Bảo · ✓ Đã liên kết căn hộ A1201                 │  greeting
├─────────────────────────────────────────────────────────────┤
│ ▲ CẦN BẠN XỬ LÝ (1)                                         │  band nổi
│ ┌─────────────────────────────────────────────────────────┐ │  amber, viền trái 3px
│ │ ⏸ Đăng ký xe và chỗ đậu                                 │ │
│ │   Đã giữ chỗ đậu xe tại Khu A.                          │ │
│ │   Phí cần thanh toán: 150.000 VND                       │ │
│ │   [Xác nhận thanh toán]  [Từ chối]      Xem chi tiết →   │ │
│ └─────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│ ĐANG THỰC HIỆN (1)                          Xem tất cả →    │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ ◐ Đăng ký chuyển nhà        Bước 3/6                    │ │
│ │   Đang đăng ký nhu cầu thang máy                        │ │
│ │   ▓▓▓▓▓▓▓▓▓▓░░░░░░░░  2 xong · 1 đang chạy              │ │
│ └─────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│ BẮT ĐẦU NHANH                                               │
│ ┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐          │
│ │Chuyển  ││Xe và   ││Báo hỏng││Tham    ││Nhận    │          │
│ │nhà     ││chỗ đậu ││cần sửa ││quan    ││tư vấn  │          │
│ └────────┘└────────┘└────────┘└────────┘└────────┘          │
├─────────────────────────────────────────────────────────────┤
│ VỪA HOÀN THÀNH                              Xem tất cả →    │
│ ✓ Báo hỏng điều hoà · Đã tiếp nhận, hẹn 09:00              │
├─────────────────────────────────────────────────────────────┤
│ HOẠT ĐỘNG GẦN ĐÂY                                           │
│ · Đã giữ chỗ đậu xe Khu A                                   │
│ · Đã đăng ký phương tiện 51A-12345                          │
└─────────────────────────────────────────────────────────────┘
```

Với tài khoản **chưa liên kết căn hộ**, khối "Bắt đầu nhanh" đổi thành:

```
┌────────┐┌────────┐┌─ 🔒 ───┐┌─ 🔒 ───┐┌─ 🔒 ───┐
│Tham    ││Nhận    ││Chuyển  ││Xe và   ││Báo hỏng│
│quan    ││tư vấn  ││nhà     ││chỗ đậu ││cần sửa │
└────────┘└────────┘└────────┘└────────┘└────────┘
     ↑ dùng được          ↑ khoá, bấm vào mở panel giải thích
```

Thẻ khoá **vẫn hiện**, làm mờ + icon khoá + nhãn chữ "Cần liên kết căn hộ".
Ẩn hẳn như bản cũ khiến người dùng không biết dịch vụ tồn tại. Không dựa vào
màu: có cả icon lẫn chữ.

**Empty state** (chưa có việc nào): bỏ hai khối "Cần bạn xử lý" và "Đang thực
hiện", đẩy "Bắt đầu nhanh" lên đầu kèm một dòng giải thích P-118 làm được gì.

### 3.3 View 2 — Workflow "Đăng ký chuyển nhà"

```
┌─────────────────────────────────────────────────────────────┐
│ ← Tổng quan / Đăng ký chuyển nhà                            │
│ Đăng ký chuyển nhà                     ◐ Đang thực hiện     │
│ "Tôi muốn chuyển nhà ngày 20/12, cần thang máy và xe tải"   │  mục tiêu người dùng
│ Bước hiện tại: Đăng ký nhu cầu thang máy                    │
│ ▓▓▓▓▓▓▓▓▓▓░░░░░░░░  2/6 bước                                │
├──────────────────────────────┬──────────────────────────────┤
│ CÁC BƯỚC                     │ HOẠT ĐỘNG                    │
│                              │                              │
│ ✓ Kiểm tra quyền cư dân      │ · Đang lập kế hoạch thực hiện│
│ ✓ Xác nhận ngày và khung giờ │ · Đã kiểm tra kế hoạch và    │
│ ◐ Đăng ký nhu cầu thang máy  │   dữ liệu bắt buộc           │
│ ○ Đăng ký phương tiện  ᴰᴱ    │ · Đang kiểm tra quyền cư dân │
│ ○ Gửi tới ban quản lý  ᴰᴱ    │ · Đã đăng ký lịch chuyển nhà│
│ ○ Hoàn tất                   │                              │
│                              │ ᴰᴱ = bước đề xuất, chưa      │
│                              │      được hệ thống thực hiện │
└──────────────────────────────┴──────────────────────────────┘
```

Khi cần thêm thông tin, một thẻ chèn **ngay dưới header**, đẩy phần còn lại
xuống — không mở modal, vì người dùng cần thấy ngữ cảnh khi điền.

---

## 4. Danh mục component

| Component | Mô tả | Trạng thái |
|---|---|---|
| `SidebarNav` | 6 mục, số đếm chỉ ở "Chờ bạn xử lý" | [UI] |
| `AccountBadge` | Tên + trạng thái liên kết căn hộ | [CÓ SẴN] `account_state` |
| `TaskInput` | Ô "Giao việc cho P-118…" trên topbar | [CÓ SẴN] `/start` |
| `AttentionBand` | Dải việc chờ người dùng, ưu tiên cao nhất | [UI] |
| `WorkflowRow` | Tiêu đề + bước hiện tại + progress | [CÓ SẴN] `plan[]`,`tasks[]` |
| `StepRail` | Danh sách bước dọc, 4 trạng thái + nhãn "đề xuất" | [CÓ SẴN] + [UI] |
| `ApprovalCard` | Báo giá + 2 nút quyết định | [CÓ SẴN] `payment_quote` |
| `MissingInfoCard` | Form các trường còn thiếu | [CÓ SẴN] `missing_fields` |
| `ActivityLog` | Dòng sự kiện gọn, theo thứ tự | [CÓ SẴN] `events[]` |
| `ResultSummary` | Kết quả nghiệp vụ sau khi xong | [CÓ SẴN] `summary`,`tasks[].details` |
| `FailureCard` | Lý do hỏng + việc nên làm tiếp | [CÓ SẴN] `message` |
| `GoalCard` | Mục tiêu bắt đầu nhanh, có biến thể khoá | [UI] |
| `LockedServiceCard` | Dịch vụ cần liên kết căn hộ | [CÓ SẴN] `account_state` |
| `StatusPill` | Icon + chữ + màu (không chỉ màu) | [UI] |

---

## 5. Trạng thái workflow và cách hiển thị

Ánh xạ **đúng** các status backend đang trả:

| Status backend | Nhóm giao diện | Hiển thị | Icon |
|---|---|---|---|
| `PENDING` | Đang thực hiện | "Đang chuẩn bị" | ◔ |
| `RUNNING` | Đang thực hiện | Tên bước hiện tại | ◐ |
| `NEEDS_INFORMATION` | **Chờ bạn xử lý** | "Cần thêm thông tin" + form | ✎ |
| `WAITING_APPROVAL` | **Chờ bạn xử lý** | Báo giá + 2 nút | ⏸ |
| `SUCCESS` | Đã hoàn thành | Kết quả nghiệp vụ | ✓ |
| `FAILED` | Không thành công | Lý do + việc nên làm | ✕ |
| `PLANNING_ERROR` | Không thành công | "Chưa lập được kế hoạch" | ✕ |
| `VALIDATION_ERROR` | Không thành công | "Kế hoạch chưa hợp lệ" | ✕ |
| `EXECUTION_ERROR` | Không thành công | "Có lỗi khi thực hiện" | ✕ |

Trạng thái từng bước, từ `tasks[].status`:

| | Hiển thị | Dấu hiệu không phải màu |
|---|---|---|
| `SUCCESS` | Đã xong | dấu ✓ |
| `RUNNING` | Đang chạy | spinner + chữ "đang" |
| `PENDING` | Chờ | vòng tròn rỗng |
| `FAILED` | Không thành công | dấu ✕ |
| `NOT_RUN` | Chưa chạy | vòng tròn mờ |

> **Khoảng trống đã biết:** `DemoTaskResult.status` **không có**
> `WAITING_APPROVAL`, trong khi `workflow_tasks` trong database thì có. Nghĩa là
> API hiện không nói được "bước thanh toán đang chờ duyệt" ở mức từng bước — chỉ
> nói được ở mức workflow. **[CẦN BACKEND]** thêm giá trị này vào view model.

---

## 6. Hành vi tương tác

| Hành động | Kết quả | Nguồn |
|---|---|---|
| Gõ mục tiêu + Enter | Tạo workflow, chuyển sang trang chi tiết | [CÓ SẴN] `/start` |
| Bấm thẻ Bắt đầu nhanh | Gửi thẳng mục tiêu, **không** bắt gõ lại | [CÓ SẴN] `/start` |
| Bấm thẻ khoá | Mở panel "cần liên kết căn hộ", không gửi gì | [UI] |
| Điền form thiếu thông tin | Tiếp tục đúng workflow cũ | [CÓ SẴN] `/continue` |
| Xác nhận thanh toán | Chạy tiếp, **không** chạy lại bước trước | [CÓ SẴN] `/payment-decision` |
| Từ chối | Huỷ bước thanh toán, giữ chỗ đã đặt | [CÓ SẴN] `/payment-decision` |
| Mở lại trang sau restart | Đọc lại trạng thái từ database | [CÓ SẴN] `GET {id}` |

**Cập nhật tiến độ:** polling `GET /workflows/demo/{id}`, giãn dần 1s → 5s.
Không có WebSocket. Giao diện **không** được gợi ý là realtime — không dùng chữ
"trực tiếp", không có chấm nhấp nháy kiểu live.

Enter gửi, Shift+Enter xuống dòng, và bỏ qua khi `event.isComposing` để không
cướp phím giữa lúc gõ dấu tiếng Việt.

Nút quyết định bị vô hiệu ngay khi bấm — bấm hai lần là hai lệnh duyệt.

---

## 7. Responsive

| Bề rộng | Bố cục |
|---|---|
| ≥1280px | Sidebar 232px + nội dung; workflow chia 2 cột 1fr/380px |
| 1024–1279px | Sidebar thu còn icon 64px; workflow vẫn 2 cột, cột phải 320px |
| 768–1023px | Sidebar thành thanh trên; workflow xếp 1 cột, Hoạt động xuống dưới |
| <768px | 1 cột; dải "Cần bạn xử lý" ghim đầu; nút quyết định full-width |

Ưu tiên desktop. Mật độ đặt cho màn laptop 1440×900 khi trình chiếu: chữ nền
14px, tiêu đề khối 12px in hoa, hàng workflow cao 64px.

---

## 8. Ghi chú tiếp cận

- Trạng thái **luôn** có icon + nhãn chữ, không chỉ màu.
- Tương phản: chữ chính 16.1:1, chữ phụ 7.4:1, viền ≥3:1 — vượt WCAG AA.
- Focus ring 2px `--brand` + offset 2px, thấy rõ trên mọi nền.
- Dải "Cần bạn xử lý" là `role="region"` có `aria-label`; khi có việc mới thì
  `aria-live="polite"` — thông báo nhưng không cướp focus.
- Step rail là `<ol>`; bước hiện tại có `aria-current="step"`.
- Thẻ khoá dùng `aria-disabled` + mô tả lý do, không dùng `disabled` trần để
  screen reader vẫn đọc được và người dùng vẫn biết dịch vụ tồn tại.
- Số tiền đọc được nguyên vẹn: `<span aria-label="150.000 đồng">`.
- Vùng bấm ≥40×40px.

---

## 9. Mockup

`docs/design/mockup-agent-workspace.html` — tĩnh, không gọi API, dữ liệu minh
hoạ được gắn nhãn ngay trong trang.

---

## 10. Ánh xạ giao diện ↔ backend

### Đã có, dùng được ngay

| Phần tử | Nguồn |
|---|---|
| Trạng thái liên kết căn hộ | `account_state` (`prospect`/`resident`) |
| Khoá dịch vụ cư dân | `ResidentAccessBoundary._RESIDENT_TOOLS` (5 tool) |
| Danh sách bước | `plan[]` → `task_id`, `title`, `description` |
| Trạng thái từng bước | `tasks[]` → `status`, `title`, `message`, `details[]` |
| Tiến độ | đếm `tasks[].status == SUCCESS` / tổng số bước |
| Nhật ký hoạt động | `events[]` → `sequence`, `stage`, `message` |
| Thẻ thiếu thông tin | `status=NEEDS_INFORMATION`, `question`, `missing_fields[]` |
| Thẻ duyệt thanh toán | `status=WAITING_APPROVAL`, `payment_quote{booking_id, amount, currency, description}` |
| Kết quả cuối | `summary`, `tasks[].details[]` |
| Lý do hỏng | `message` theo từng status lỗi |
| Đã lưu vào hệ thống | `persisted` |

### Thuần giao diện

| Phần tử | Suy ra từ |
|---|---|
| Nhóm "Đang thực hiện / Chờ bạn xử lý / Đã hoàn thành" | ánh xạ từ `status` |
| Chữ "Bước 3/6" | đếm `tasks[]`, không phải backend trả |
| Thẻ mục tiêu Bắt đầu nhanh | câu goal dựng sẵn phía client |
| Nhãn "đề xuất" trên bước chưa hỗ trợ | danh sách tĩnh phía client |

### Cần backend làm thêm

| Việc | Vì sao cần | Mức |
|---|---|---|
| Timestamp cho `events[]` | Hiện chỉ có `sequence`. Không có thời gian thật thì nhật ký **không** được hiện giờ | Trung bình |
| Hồ sơ cư dân (căn hộ, xe đã đăng ký) | Mục "Hồ sơ cư dân" trên sidebar chưa có nguồn dữ liệu | Trung bình |
| Bước nghiệp vụ nhỏ hơn cho `schedule_move` | 6 bước đề bài nêu thì **bước 2–5 đều là input/output của MỘT lời gọi `schedule_move`**. Chỉ "Kiểm tra quyền cư dân" và "Hoàn tất" là mốc thật | Thấp |

Đã triển khai sau bản thiết kế đầu: `GET /workflows/demo`, message sự kiện bằng
ngôn ngữ nghiệp vụ và `WAITING_APPROVAL` ở mức task. Endpoint danh sách hiện
vẫn chỉ an toàn cho demo một người vì bảng workflow chưa có owner/account scope.

### Tuyệt đối không hiển thị

ETA (không có ước lượng thật ở đâu cả) · cập nhật realtime (chỉ có polling) ·
tên tool (`pay_fee`, `schedule_move`) · `InputRef` · `PostgreSQL` · tên
exception · mã lỗi provider · bước lập luận của mô hình.
