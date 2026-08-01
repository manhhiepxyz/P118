# P-118 UI Wireframe & Flow

**Đề tài:** AI Agent orchestrate đa dịch vụ hoàn thành tác vụ liên hoàn (đặt nhà → xe → dịch vụ)

**Mã đề tài:** PTNT-02 — STT 158

**Nhóm:** P-118 · **Gate 1 — 02/08/2026**

---

## 1. Design Goal

Người dùng giao một mục tiêu bằng ngôn ngữ tự nhiên. Agent tự lập kế hoạch, thực hiện chuỗi dịch vụ, cho người dùng theo dõi từng bước, xử lý khi có lỗi, hỏi xác nhận trước khi thao tác tốn tiền, và trả kết quả cuối cùng.

UI phải trả lời được 6 câu hỏi của người dùng:

1. Mình đã yêu cầu Agent làm gì?
2. Agent đang thực hiện những bước nào?
3. Bước nào xong, bước nào đang chạy, bước nào thất bại?
4. Agent đang làm gì khi một service gặp lỗi?
5. Khi nào mình cần xác nhận?
6. Kết quả cuối cùng là gì?

**Customer journey MVP:** `Register Resident → Register Vehicle → Book Parking → Pay Fee`

---

## 2. Product UI Flow Overview

```
+---------------------+
|   Home / Goal Input |
+---------------------+
    |             |
    | submit goal | click recent workflow
    ↓             ↓
+---------------------+        +---------------------+
|  Workflow Timeline  |        | Workflow Detail /   |
| (realtime updates)  |        |      Result         |
+---------------------+        +---------------------+
    |         |
    | requires | view result
    | approval ↓
    |    +---------------------+
    |    | Workflow Detail /   |
    |    |      Result         |
    |    +---------------------+
    ↓
+---------------------+
|    HITL Modal       |  ← overlay, không phải page riêng
|  (Final MVP only)   |
+---------------------+
    |           |
    | approve   | reject
    ↓           ↓
+---------------------+
|  Workflow Timeline  |  ← quay lại, workflow tiếp tục hoặc xử lý
+---------------------+
```

> **Ghi chú:** HITL Modal là overlay trên Workflow Timeline — không phải màn hình riêng. Recent Workflows nằm ở Home, không phải ở Timeline.

---

## 3. Screen 1 — Home / Goal Input

### Purpose

Cho người dùng mô tả mục tiêu bằng ngôn ngữ tự nhiên và xem lại các workflow gần đây. Người dùng có thể yêu cầu toàn bộ chuỗi dịch vụ hoặc chỉ một phần tùy theo nhu cầu hiện tại — hệ thống tạo task list phù hợp với goal và dữ liệu đã có.

### Wireframe

```
+----------------------------------------------------------+
|  P-118 — Trợ lý dịch vụ cư dân                          |
+----------------------------------------------------------+
|                                                          |
|                                                          |
|   Bạn muốn làm gì hôm nay?                              |
|                                                          |
|   +----------------------------------------------------+ |
|   | Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư  | |
|   | dân, xe, chỗ đậu xe và thanh toán phí giúp tôi.  | |
|   |                                                    | |
|   |                                                    | |
|   +----------------------------------------------------+ |
|                                                          |
|                       [ Bắt đầu ]                        |
|                                                          |
|                                                          |
+----------------------------------------------------------+
|  Workflow gần đây                                        |
+----------------------------------------------------------+
|  ✅  Đăng ký cư dân + xe + chỗ đậu...      30/07 10:20 |
|  🔄  Đăng ký cư dân + xe + chỗ đậu...      31/07 11:30 |
|  ❌  Đặt chỗ đậu xe — Zone A...             31/07 09:05 |
|  ↩   Đăng ký cư dân + thanh toán...        29/07 14:00 |
+----------------------------------------------------------+
```

### Main Interactions

| Hành động | Kết quả |
|---|---|
| Nhập goal → nhấn **Bắt đầu** | Tạo workflow mới → chuyển sang Workflow Timeline |
| Nhấn vào workflow gần đây | Chuyển sang Workflow Detail của workflow đó |

---

## 4. Screen 2 — Workflow Timeline

### Purpose

Màn hình chính của sản phẩm. Hiển thị tiến trình theo thời gian thực — từng bước Agent đang làm, kết quả từng bước, và trạng thái phục hồi nếu có lỗi.

### Wireframe — Happy Path

```
+----------------------------------------------------------+
|  ←  Quay lại        Workflow #wf_abc123      [Làm mới]  |
+----------------------------------------------------------+
|  Mục tiêu:                                               |
|  "Đăng ký cư dân, xe, chỗ đậu xe và thanh toán phí."   |
|                                                          |
|  Trạng thái: 🔄 RUNNING     Bắt đầu: 31/07 11:30        |
+----------------------------------------------------------+
|                                                          |
|  ✅  T1 — Đăng ký cư dân                                |
|      Resident ID: R001 · Hoàn thành lúc 11:30:05        |
|         |                                                |
|         ↓  resident_id → T2                             |
|         |                                                |
|  ✅  T2 — Đăng ký phương tiện                           |
|      Vehicle ID: V001 · Hoàn thành lúc 11:30:12         |
|         |                                                |
|         ↓  vehicle_id → T3                              |
|         |                                                |
|  🔄  T3 — Đặt chỗ đậu xe                               |
|      Zone A · Đang thực hiện...                          |
|         |                                                |
|  ⏳  T4 — Thanh toán phí                                |
|      Đang chờ T3 hoàn thành...                          |
|                                                          |
+----------------------------------------------------------+
```

### Wireframe — Recovery State (Hero Scenario)

Khi `book_parking(Zone A)` trả về `NO_AVAILABILITY`, Timeline hiển thị:

```
+----------------------------------------------------------+
|  ←  Quay lại        Workflow #wf_abc123      [Làm mới]  |
+----------------------------------------------------------+
|  Mục tiêu:                                               |
|  "Đăng ký cư dân, xe, chỗ đậu xe và thanh toán phí."   |
|                                                          |
|  Trạng thái: 🔁 RECOVERING     Bắt đầu: 31/07 11:30     |
+----------------------------------------------------------+
|                                                          |
|  ✅  T1 — Đăng ký cư dân                                |
|      Resident ID: R001 · Hoàn thành lúc 11:30:05        |
|         |                                                |
|  ✅  T2 — Đăng ký phương tiện                           |
|      Vehicle ID: V001 · Hoàn thành lúc 11:30:12         |
|         |                                                |
|  ❌  T3 — Đặt chỗ đậu xe · Zone A                      |
|      ⚠  Zone A không còn chỗ trống                     |
|         |                                                |
|  🔁  Agent đang tìm phương án thay thế...               |
|         |                                                |
|  🔄  T3 — Đặt chỗ đậu xe · Zone B                      |
|      Đang thực hiện...                                   |
|         |                                                |
|  ⏳  T4 — Thanh toán phí                                |
|      Đang chờ...                                         |
|                                                          |
+----------------------------------------------------------+
|  ℹ  Agent tự điều chỉnh kế hoạch khi gặp lỗi.          |
|     Không cần thao tác thêm từ bạn.                      |
+----------------------------------------------------------+
```

### Workflow States — Header Badge

| Trạng thái | Hiển thị |
|---|---|
| `RUNNING` | 🔄 RUNNING |
| `RECOVERING` | 🔁 RECOVERING |
| `AWAITING_APPROVAL` | ⏸ CHỜ XÁC NHẬN |
| `COMPLETED` | ✅ COMPLETED |
| `FAILED` | ❌ FAILED |
| `ROLLED_BACK` | ↩ ROLLED BACK |

---

### HITL Modal — Final MVP

HITL là modal/overlay trên Workflow Timeline. Thuộc **Demo Day Final MVP**, được thiết kế ở đây để clarify UX.

**Trigger:** Policy Engine phân loại action là `REQUIRES_APPROVAL`. Ví dụ Demo Day: `pay_fee >= 300,000 VND` → `REQUIRES_APPROVAL` (đây là hardcoded demo rule, không phải định nghĩa tổng quát của HITL).

Workflow chuyển sang `AWAITING_APPROVAL` và modal xuất hiện:

```
+----------------------------------------------------------+
|  [Workflow Timeline — mờ phía sau]                       |
|                                                          |
|     +------------------------------------------------+   |
|     |  ⏸  XÁC NHẬN HÀNH ĐỘNG                       |   |
|     +------------------------------------------------+   |
|     |                                                |   |
|     |  Agent muốn thực hiện:                        |   |
|     |  Thanh toán phí dịch vụ                       |   |
|     |                                                |   |
|     |  Số tiền:   800,000 VND                       |   |
|     |  Mô tả:     Phí đăng ký dịch vụ tháng 7/2026  |   |
|     |  Dịch vụ:   Payment Service                   |   |
|     |                                                |   |
|     |  ⚠  Hành động này sẽ phát sinh giao dịch      |   |
|     |     tài chính. Agent cần xác nhận của bạn     |   |
|     |     trước khi thực hiện.                      |   |
|     |                                                |   |
|     |   [ Từ chối ]              [ ✓ Duyệt ]        |   |
|     |                                                |   |
|     +------------------------------------------------+   |
|                                                          |
+----------------------------------------------------------+
```

| Lựa chọn | Kết quả |
|---|---|
| **Duyệt** | Agent thực thi `pay_fee` → workflow tiếp tục |
| **Từ chối** | Workflow xử lý theo quy tắc của hệ thống (hoàn tác nếu cần) |

---

## 5. Screen 3 — Workflow Detail / Result

### Purpose

Xem lại chi tiết một workflow đã hoàn thành hoặc đang chạy — bao gồm kết quả từng bước, quá trình phục hồi nếu có, và tổng kết cuối cùng.

### Wireframe — Workflow COMPLETED

```
+----------------------------------------------------------+
|  ←  Quay lại        Workflow Detail #wf_abc123           |
+----------------------------------------------------------+
|  Mục tiêu:                                               |
|  "Đăng ký cư dân, xe, chỗ đậu xe và thanh toán phí."   |
|                                                          |
|  Bắt đầu: 31/07/2026 11:30     ✅ COMPLETED             |
|  Kết thúc: 31/07/2026 11:31                             |
+----------------------------------------------------------+
|                                                          |
|  ✅  T1 — Đăng ký cư dân                                |
|      Resident ID: R001                                   |
|                                                          |
+----------------------------------------------------------+
|                                                          |
|  ✅  T2 — Đăng ký phương tiện                           |
|      Vehicle ID: V001                                    |
|      (Dùng Resident ID từ T1: R001)                      |
|                                                          |
+----------------------------------------------------------+
|                                                          |
|  ✅  T3 — Đặt chỗ đậu xe                               |
|                                                          |
|      Lần 1:  Zone A  ❌  Không còn chỗ trống            |
|              → Agent tự replan                           |
|      Lần 2:  Zone B  ✅  Booking ID: PKG-045            |
|                                                          |
+----------------------------------------------------------+
|                                                          |
|  ✅  T4 — Thanh toán phí                                |
|      Số tiền: 800,000 VND · Mã GD: PAY-88712            |
|                                                          |
+----------------------------------------------------------+
|  KẾT QUẢ CUỐI CÙNG                                      |
|                                                          |
|  Hoàn thành: 4/4 tác vụ                                 |
|  Phục hồi:   1 lần (Zone A → Zone B)                    |
|                                                          |
|  ✅  "Workflow hoàn thành thành công."                   |
+----------------------------------------------------------+
```

### Wireframe — Workflow ROLLED BACK

```
+----------------------------------------------------------+
|  ←  Quay lại        Workflow Detail #wf_xyz789           |
+----------------------------------------------------------+
|  Mục tiêu:                                               |
|  "Đăng ký cư dân, xe, chỗ đậu xe và thanh toán phí."   |
|                                                          |
|  Bắt đầu: 31/07/2026 14:00     ↩ ROLLED BACK            |
|  Kết thúc: 31/07/2026 14:02                             |
+----------------------------------------------------------+
|                                                          |
|  ✅ → ↩  T1 — Đăng ký cư dân                           |
|          Đã hoàn tác: Hủy đăng ký cư dân               |
|                                                          |
+----------------------------------------------------------+
|                                                          |
|  ✅ → ↩  T2 — Đăng ký phương tiện                      |
|          Đã hoàn tác: Hủy đăng ký xe                    |
|                                                          |
+----------------------------------------------------------+
|                                                          |
|  ❌  T3 — Đặt chỗ đậu xe                               |
|      Lần 1: Zone A — Không còn chỗ trống                |
|      Lần 2: Zone B — Không còn chỗ trống                |
|      → Không thể phục hồi                               |
|                                                          |
+----------------------------------------------------------+
|  KẾT QUẢ CUỐI CÙNG                                      |
|                                                          |
|  ↩  Workflow đã hoàn tác các bước đã thực hiện.         |
|     Không có khoản phí nào bị tính.                     |
+----------------------------------------------------------+
```

### Main Information

| Thông tin | Mô tả |
|---|---|
| Mục tiêu | Goal người dùng đã nhập |
| Thời gian | Bắt đầu / kết thúc |
| Trạng thái tổng | Badge trạng thái workflow |
| Kết quả từng bước | ID, confirmation number |
| Recovery history | Số lần thử và kết quả mỗi lần |
| Tổng kết | Số task hoàn thành / tổng, số lần phục hồi |

**Không hiển thị:** raw JSON, system prompt, LangGraph internals, database records, HTTP payload.

---

## 6. UI Flow

### Happy Path

```
Home
  → Nhập goal
  → [ Bắt đầu ]
  → Workflow Timeline
      T1 RUNNING → T1 COMPLETED
      T2 RUNNING → T2 COMPLETED
      T3 RUNNING → T3 COMPLETED
      T4 RUNNING → T4 COMPLETED
  → Status: ✅ COMPLETED
  → Xem Workflow Detail
```

### Recovery Path

```
Workflow Timeline
  → T3 FAILED (Zone A — NO_AVAILABILITY)
  → Status: 🔁 RECOVERING
  → Agent replan
  → T3 RUNNING (Zone B)
  → T3 COMPLETED (Zone B)
  → T4 RUNNING → T4 COMPLETED
  → Status: ✅ COMPLETED
```

### HITL Path — Final MVP

```
Workflow Timeline
  → T4 requires approval (800,000 VND)
  → Status: ⏸ AWAITING_APPROVAL
  → HITL Modal xuất hiện
      → [ Từ chối ] → workflow xử lý
      → [ Duyệt ]   → T4 thực thi
                    → T4 COMPLETED
                    → Status: ✅ COMPLETED
```

### History Path

```
Home
  → Recent Workflows
  → Chọn workflow bất kỳ
  → Workflow Detail
```

---

## 7. Status Mapping

### Flow Status

| Status | Icon | Ý nghĩa |
|---|---|---|
| `RUNNING` | 🔄 | Agent đang thực hiện workflow |
| `RECOVERING` | 🔁 | Agent đang xử lý lỗi và tìm phương án |
| `AWAITING_APPROVAL` | ⏸ | Chờ người dùng xác nhận hành động |
| `COMPLETED` | ✅ | Tất cả tác vụ hoàn thành thành công |
| `FAILED` | ❌ | Workflow thất bại, không thể tiếp tục |
| `ROLLED_BACK` | ↩ | Các tác vụ đã thực hiện đã được hoàn tác |

### Task / Step Status

| Status | Icon | Ý nghĩa |
|---|---|---|
| `PENDING` | ⏳ | Chưa đến lượt thực hiện |
| `RUNNING` | 🔄 | Đang thực hiện |
| `COMPLETED` | ✅ | Thành công |
| `FAILED` | ❌ | Thất bại |
| `AWAITING_APPROVAL` | ⏸ | Chờ người dùng xác nhận |
| `COMPENSATED` | ↩ | Đã được hoàn tác sau rollback |

> **Lưu ý:** `RECOVERING` là trạng thái của **workflow**, không phải của từng step. Khi workflow đang ở `RECOVERING`, Timeline hiển thị activity indicator "🔁 Agent đang tìm phương án thay thế..." — đây là UX message, không phải `step.status`.

---

## 8. Gate 2 UI vs Demo Day UI

### Gate 2 Working MVP — ~17/08/2026

UI tối thiểu đủ để chứng minh workflow chạy thật và có Live URL.

**Phải có:**
- Goal input (Screen 1)
- Workflow status + task list (Screen 2, không cần realtime)
- Basic result display (Screen 3)
- Live URL

**Chấp nhận:**
- Polling / manual refresh thay vì WebSocket
- UI tối giản, chưa cần styled
- Không cần HITL Modal

### Demo Day Final MVP — 03-05/09/2026

**Bổ sung thêm:**
- React Timeline cập nhật realtime (WebSocket)
- HITL Modal (approve/reject)
- Recovery visualization (lần 1 failed → replan → lần 2 success)
- Compensation visualization (T1 → ↩, T2 → ↩)
- Workflow Detail đầy đủ
- UI polished

---

## 9. Extension

Các tính năng dưới đây **không** thuộc wireframe Gate 1 và không nằm trong Demo Day Final MVP:

**Admin Dashboard** *(Extension)* — có thể bổ sung sau Demo Day:
- Xem toàn bộ workflow của mọi người dùng
- Filter theo trạng thái
- Audit history
- Manual intervention

**Healthcare scenario** *(Extension)* — Scenario 2 sau Demo Day, sử dụng lại toàn bộ UI hiện tại.

---

## 10. Design Reference

| Artifact | Trạng thái |
|---|---|
| Wireframe Gate 1 | File Markdown này (`docs/gate1/wireframe.md`) |
| Figma | Không bắt buộc ở Gate 1 |
| UI Implementation | React — bắt đầu từ Phase 5 (sau Gate 2) |
| Architecture liên quan | `docs/architecture_diagram.md` |

---

*Gate 1 — 02/08/2026 — P-118 Build Phase*
