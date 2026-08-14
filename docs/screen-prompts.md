# P-118 — Prompt tạo màn hình cho từng luồng tính năng

> Mục đích: bộ prompt copy-paste để tạo màn hình UI thể hiện các luồng tính năng của P-118.
> Nguồn dữ liệu gốc: `shared_contracts.md` (tool allowlist, internal contract, status, error code),
> `docs/gate1/wireframe.md` (3 màn hình core + HITL modal), `src/api/routes.py` + `src/services/mock/*` (runtime thật).
> Mọi màn hình nên bám sát dữ liệu thật này để demo không bị lệch với backend.

---

## 0. Bối cảnh chung — dán đầu mỗi prompt

Đây là khối context dùng chung. Dán khối này vào đầu mọi prompt để AI hiểu sản phẩm trước khi thiết kế:

```text
Bạn đang thiết kế UI cho P-118 — "AI Agent orchestrate đa dịch vụ hoàn thành tác vụ liên hoàn".

Core value: người dùng nhập mục tiêu bằng tiếng Việt tự nhiên → Agent tự lập TaskPlan →
validate → thực thi nhiều service theo đúng thứ tự phụ thuộc → truyền dữ liệu giữa các bước
→ xử lý lỗi → trả kết quả.

9 tool nghiệp vụ (allowlist), tên không được đổi:
- search_properties (read-only, chỉ trả gợi ý)
- schedule_property_viewing (đặt lịch tham quan dự án)
- register_property_interest (đăng ký nhận tư vấn)
- create_maintenance_request (yêu cầu bảo trì)
- schedule_move (đăng ký chuyển nhà)
- register_resident → register_vehicle → book_parking → pay_fee (chuỗi cư dân, có dependency)

Workflow status: PLANNING · PLANNED · VALIDATING · VALIDATED · EXECUTING · RUNNING ·
WAITING_APPROVAL · RECOVERING · SUCCESS · FAILED · CANCELLED · ROLLED_BACK
Task status: PENDING · READY · RUNNING · WAITING_APPROVAL · SUCCESS · FAILED · SKIPPED · CANCELLED
Error code tiêu biểu: NO_AVAILABILITY (hết chỗ → replan), MISSING_INFORMATION (cần hỏi thêm),
INVALID_INPUT, RESIDENT_ALREADY_EXISTS, VEHICLE_ALREADY_EXISTS, PAYMENT_FAILED.

Ngôn ngữ UI: tiếng Việt. Giao diện sạch, hiển thị trạng thái trực quan (icon + màu).
KHÔNG hiển thị raw JSON, system prompt, LangGraph internals, database record, HTTP payload.
Giao diện màn hình thiết kế ở dạng wireframe ASCII kèm ghi chú tương tác và nội dung hiển thị.
```

---

## 1. Luồng chuỗi cư dân (Hero Journey) — 4 màn hình

**Luồng:** `register_resident → register_vehicle → book_parking → pay_fee`

- Dependency tuyến tính: `resident_id` (T1) → T2, `vehicle_id` (T2) → T3, `booking_id + amount + currency` (T3) → T4.
- User không nhập lại dữ liệu đã có từ bước trước (data propagation).
- Happy path = 4 bước đều SUCCESS → workflow SUCCESS.
- Khi `book_parking` ở Zone A trả `NO_AVAILABILITY` → workflow RECOVERING → Agent replan → thử Zone B → SUCCESS → workflow tiếp tục (Hero Recovery Scenario).

```text
Thiết kế 4 màn hình cho LUỒNG CHUỖI CƯ DÂN (Hero Journey):
register_resident → register_vehicle → book_parking → pay_fee.

1. MÀN HÌNH GOAL INPUT: textarea nhập mục tiêu tiếng Việt + nút bắt đầu.
   Placeholder gợi ý: "Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư dân, xe, chỗ đậu xe và thanh toán phí giúp tôi."
   Có mục "Workflow gần đây" (nhấn vào → xem chi tiết workflow cũ).

2. MÀN HÌNH WORKFLOW TIMELINE: theo dõi từng bước Agent đang làm.
   - Header: badge trạng thái workflow (🔄 RUNNING / ✅ SUCCESS / 🔁 RECOVERING...) + goal + thời gian bắt đầu.
   - Danh sách task theo thứ tự dependency, mỗi task hiển thị: icon trạng thái, tên tiếng Việt
     (Đăng ký cư dân / Đăng ký phương tiện / Đặt chỗ đỗ xe / Thanh toán phí), kết quả khi xong.
   - Hiển thị mũi tên truyền dữ liệu giữa các bước: resident_id → register_vehicle,
     vehicle_id → book_parking, booking_id → pay_fee.
   - Trạng thái task: ⏳ PENDING (chờ bước trước) · 🔄 RUNNING · ✅ SUCCESS · ❌ FAILED.

3. MÀN HÌNH RECOVERY STATE (Hero): khi book_parking Zone A → NO_AVAILABILITY.
   - T3 hiển thị ❌ "Khu vực đỗ xe đã hết chỗ" kèm lý do.
   - Dòng trạng thái workflow chuyển 🔁 RECOVERING với thông báo "Agent đang tìm phương án thay thế...".
   - T3 chạy lại với Zone B → 🔄 → ✅ SUCCESS.
   - T1, T2 giữ nguyên ✅, KHÔNG chạy lại.
   - Có ghi chú: "Agent tự điều chỉnh kế hoạch khi gặp lỗi. Không cần thao tác thêm từ bạn."

4. MÀN HÌNH WORKFLOW DETAIL / RESULT (sau khi xong): tổng kết.
   - Badge ✅ SUCCESS + thời gian bắt đầu/kết thúc.
   - Từng task + kết quả (Resident ID, Vehicle ID, Booking ID + phí, Payment ID + trạng thái PAID).
   - Recovery history nếu có: "Lần 1: Zone A ❌ · Lần 2: Zone B ✅".
   - Tổng kết: "Hoàn thành: 4/4 tác vụ" + số lần phục hồi.

Dùng dữ liệu hiển thị mẫu (tiếng Việt) hợp lý theo contract:
resident_id: RES-001 · vehicle_id: VEH-001 · booking_id: BOOK-001 · payment_id: PAY-001 · payment_status: PAID
```

---

## 2. Luồng tìm nhà / dự án — 3 màn hình

**Luồng:** `search_properties` → user chọn `project_id` → `schedule_property_viewing` và/hoặc `register_property_interest`.

- `search_properties` là **read-only**: chỉ trả gợi ý. Agent không tự chọn căn, không đặt cọc, ký hợp đồng hay hoàn tất thuê/mua.
- `schedule_property_viewing` đặt lịch **ở cấp dự án** (`project_id`), không phải căn cụ thể.
- Không tự nối `search → schedule`: user phải chọn `project_id` từ danh sách dự án trước.
- Hai task `viewing` và `interest` độc lập → có thể chạy song song.
- Danh mục dự án (đóng, dùng tên hiển thị — user không nhập `PRJ-*`): Vinhomes Sài Gòn Park (PRJ-001) · Vinhomes Global Gate Hạ Long (PRJ-002) · Vinhomes Hải Vân Bay (PRJ-003) · Vinhomes Pearl Bay (PRJ-004) · Vinhomes Green Paradise (PRJ-005) · Vinhomes Golden City (PRJ-006) · Vinhomes Ocean Park (PRJ-007).
- User thuộc persona **prospect** (account contact đã VERIFIED, resident chưa linked).

```text
Thiết kế 3 màn hình cho LUỒNG TÌM NHÀ / DỰ ÁN:
search_properties → (user chọn dự án) → schedule_property_viewing và/hoặc register_property_interest.

1. MÀN HÌNH TÌM BẤT ĐỘNG SẢN (kết quả gợi ý):
   - Hiển thị tiêu chí user nhập: transaction_type (rent/buy), property_type (apartment/room),
     residential_area, max_price (VND).
   - Danh sách gợi ý, mỗi căn chỉ hiển thị: tiêu đề, giá (định dạng VND), số phòng ngủ,
     khu vực, liên hệ. Ví dụ: "Căn hộ 2 phòng ngủ gần công viên · 18.000.000 VND".
   - Ghi chú rõ: kết quả CHỈ là gợi ý — Agent không tự đặt cọc/thuê/mua.
   - Nút/CTA dẫn user đến việc CHỌN một dự án (không tự chọn giúp user).

2. MÀN HÌNH CHỌN DỰ ÁN (người dùng chọn project_id bằng tên):
   - Hiển thị danh sách 7 dự án bằng TÊN tiếng Việt (user không thấy mã PRJ-*):
     Vinhomes Sài Gòn Park · Vinhomes Global Gate Hạ Long · Vinhomes Hải Vân Bay ·
     Vinhomes Pearl Bay · Vinhomes Green Paradise · Vinhomes Golden City · Vinhomes Ocean Park.
   - User chọn 1 dự án → mở 2 hành động độc lập.

3. MÀN HÌNH HÀNH ĐỘNG DỰ ÁN (2 khối song song):
   a) Đặt lịch tham quan: chọn ngày (YYYY-MM-DD, không quá khứ, 08:00–17:30) + giờ.
      Kết quả hiển thị: viewing_id (VIEW-001), dự án, thời gian, contact_name, contact_phone,
      viewing_status: SCHEDULED.
   b) Đăng ký nhận tư vấn: chọn interest_type (buy/rent/consultation), preferred_contact_time
      (morning/afternoon/evening), checkbox đồng ý nhận liên hệ (consent = true là bắt buộc).
      Kết quả hiển thị: interest_id (INT-001), dự án, interest_status: RECEIVED,
      contact_channel: VERIFIED_ACCOUNT_CONTACT.
   - Trạng thái mỗi khối: ⏳/🔄/✅ độc lập vì 2 task chạy song song.
   - Lưu ý: phone/email KHÔNG nằm trong form — hệ thống lấy từ contact đã xác minh.
```

---

## 3. Luồng bảo trì & chuyển nhà — 2 màn hình

**Luồng:** `create_maintenance_request` và `schedule_move` (2 task độc lập, chạy song song).

- Chỉ chạy khi account đã có resident-property mapping **VERIFIED** (persona resident).
- TaskPlan không chứa resident_id/apartment_id — hệ thống lấy quan hệ căn hộ từ account đã xác minh.
- Không tự tạo `pay_fee` trong MVP.
- Khung giờ hợp lệ: bảo trì 08:00–18:00 · chuyển nhà 07:00–20:00.

```text
Thiết kế 2 màn hình cho LUỒNG BẢO TRÌ & CHUYỂN NHÀ (persona: cư dân đã VERIFIED):

1. MÀN HÌNH YÊU CẦU BẢO TRÌ (create_maintenance_request):
   - Form: hạng mục (issue_type), mô tả, vị trí trong căn hộ, ngày ưu tiên + giờ ưu tiên
     (08:00–18:00, ngày không quá khứ).
   - KHÔNG yêu cầu nhập căn hộ/mã cư dân — hệ thống lấy từ account đã xác minh.
   - Kết quả hiển thị: maintenance_id (MAINT-...), maintenance_status: SCHEDULED,
     appointment_date + appointment_time, hạng mục, vị trí.

2. MÀN HÌNH ĐĂNG KÝ CHUYỂN NHÀ (schedule_move):
   - Form: ngày + giờ chuyển (07:00–20:00), cần thang máy (needs_elevator), cần hỗ trợ bốc dỡ
     (needs_loading_support), phương tiện vận chuyển (move_vehicle).
   - Kết quả hiển thị: move_request_id (MOVE-...), move_status: SCHEDULED, ngày/giờ,
     elevator_slot (khung thang máy được xếp hoặc "NOT_REQUIRED"), phương tiện.

Cả 2 màn hình: sau khi gửi, cho phép chạy SONG SONG 2 yêu cầu và hiển thị trạng thái từng
bước (⏳/🔄/✅). Header nhắc ngữ cảnh: "Tài khoản đã liên kết căn hộ A1201" để minh hoạ
verify mapping.
```

---

## 4. Luồng Partial Goal — 2 màn hình

**Luồng:** user đã có `resident_id` + `vehicle_id` trong existing context → chỉ chạy `book_parking → pay_fee` (hoặc chỉ `pay_fee` nếu đã có `booking_id`).

- Existing context nạp trước khi lập plan: `property_id`, `project_id`, `resident_id`, `vehicle_id`, `booking_id`.
- Task đã hoàn thành hoặc dữ liệu đã tồn tại KHÔNG được tạo lại.
- Nếu thiếu dữ liệu bắt buộc → Agent hỏi thêm, không tự đoán ID.
- Nếu yêu cầu không thuộc service được hỗ trợ → `MISSING_INFORMATION` với `supported_goal` chưa rõ.

```text
Thiết kế 2 màn hình cho LUỒNG PARTIAL GOAL (user đã có sẵn dữ liệu):

1. MÀN HÌNH XÁC NHẬN KẾ HOẠCH RÚT GỌN:
   - User gõ: "Đặt chỗ cho xe của tôi ngày mai." — Agent KHÔNG thêm register_resident/register_vehicle.
   - Hiển thị dữ liệu đã có sẵn (badge/thẻ): resident_id: RES-001 · vehicle_id: VEH-001 (read-only,
     người dùng nhìn thấy để hiểu vì sao plan ngắn).
   - TaskPlan đề xuất CHỈ gồm: T1 book_parking → T2 pay_fee.
   - Cho phép user duyệt plan hoặc sửa.

2. MÀN HÌNH HỎI BỔ SUNG THÔNG TIN (MISSING_INFORMATION):
   - Khi goal thiếu dữ liệu bắt buộc, Agent hiển thị câu hỏi và liệt kê các field đang chờ.
   - Mỗi field đi kèm hướng dẫn định dạng (ví dụ: booking_date "chọn ngày hợp lệ, không ở quá khứ",
     parking_zone "ZONE_A hoặc ZONE_B", plate_number "ví dụ 59A-12345").
   - Hỗ trợ 2 kiểu trả lời: nhập vào ô form theo field, HOẶC trả lời bằng câu tự nhiên.
   - Trường hợp agent không hiểu yêu cầu → hiển thị gợi ý các dịch vụ được hỗ trợ để user chọn lại.
```

---

## 5. Luồng lỗi / từ chối nghiệp vụ — 1 màn hình

**Màn hình này hiển thị các trạng thái FAILED cụ thể mà runtime P-118 thật trả về** (message mẫu lấy từ `src/api/routes.py`):

- `RESIDENT_ALREADY_EXISTS` → "Căn hộ A1201 đã có hồ sơ cư dân. Hãy sử dụng tài khoản cư dân đã liên kết."
- `VEHICLE_ALREADY_EXISTS` → "Biển số 59A-12345 đã được đăng ký. Hãy sử dụng phương tiện đã liên kết hoặc kiểm tra lại biển số."
- `NO_AVAILABILITY` (book_parking) → "Khu vực đỗ xe đã hết chỗ cho ngày 2026-08-15. Hãy chọn ngày hoặc khu vực khác."
- `NO_AVAILABILITY` (viewing) → "Khung giờ tham quan 2026-08-15 10:00 không còn trống. Hãy chọn thời gian khác."
- `BOOKING_ALREADY_EXISTS` → "Phương tiện này đã có chỗ đỗ trong ngày được chọn."
- `INVALID_INPUT` → "Thông tin của bước "..." chưa hợp lệ. Hãy kiểm tra lại dữ liệu đã nhập."
- `DEPENDENCY_ERROR` → "Bước "..." chưa được thực hiện vì bước trước đó không thành công."
- Lỗi chưa rõ → fallback chung + trạng thái `retryable` (có thể thử lại hay không).

```text
Thiết kế 1 MÀN HÌNH TRẠNG THÁI LỖI / TỪ CHỐI NGHIỆP VỤ:
- Hiển thị task FAILED với mã lỗi dạng dễ hiểu (icon ❌ + màu), không lộ raw error code/thông báo kỹ thuật.
- Kèm thông báo tiếng Việt theo từng mã lỗi (dùng message mẫu bên dưới), gợi ý hành động khắc phục
  cho user (chọn ngày khác, kiểm tra biển số, dùng tài khoản đã liên kết...).
- Nếu lỗi retryable=true → hiển thị nút "Thử lại".
- Nếu lỗi không phục hồi được → workflow FAILED, hiển thị tổng kết dừng an toàn + các bước đã xong giữ nguyên.

Message mẫu:
- RESIDENT_ALREADY_EXISTS → "Căn hộ A1201 đã có hồ sơ cư dân. Hãy sử dụng tài khoản cư dân đã liên kết."
- VEHICLE_ALREADY_EXISTS → "Biển số 59A-12345 đã được đăng ký. Hãy sử dụng phương tiện đã liên kết hoặc kiểm tra lại biển số."
- NO_AVAILABILITY (parking) → "Khu vực đỗ xe đã hết chỗ cho ngày 2026-08-15. Hãy chọn ngày hoặc khu vực khác."
- BOOKING_ALREADY_EXISTS → "Phương tiện này đã có chỗ đỗ trong ngày được chọn."
```

---

## 6. Luồng HITL — Xác nhận thanh toán (Demo Day Final MVP) — 1 màn hình + 1 modal

**Quy tắc Policy Engine (5 rule hardcoded, deterministic):**
- `action_type = READ_ONLY` → `AUTO_ALLOWED`
- `action_type = PAYMENT AND amount < 300.000 VND` → `AUTO_ALLOWED`
- `action_type = PAYMENT AND amount ≥ 300.000 VND` → `REQUIRES_APPROVAL`
- `action_type = DELETE/CANCEL/DESTRUCTIVE` → `REQUIRES_APPROVAL`
- `risk_level = BLOCKED` → `DENIED`

- `pay_fee` = action_type `PAYMENT`, risk_level `FINANCIAL`. Phí đặt chỗ mẫu: `150.000 VND` (auto) hoặc ví dụ `800.000 VND` (cần duyệt).
- HITL Modal là **overlay trên Workflow Timeline**, không phải màn hình riêng.
- Workflow chuyển `WAITING_APPROVAL` khi có action cần duyệt.

```text
Thiết kế 1 MÀN HÌNH + 1 MODAL cho LUỒNG HITL XÁC NHẬN (Demo Day Final MVP):

1. WORKFLOW TIMELINE ở trạng thái chờ duyệt:
   - Badge workflow chuyển ⏸ WAITING_APPROVAL.
   - Task pay_fee hiển thị ⏸ "Chờ bạn xác nhận..." thay vì tự chạy.
   - Các bước trước đó vẫn ✅, chưa chạy tiếp bước sau.

2. HITL MODAL (overlay, làm mờ timeline phía sau):
   - Tiêu đề: "XÁC NHẬN HÀNH ĐỘNG".
   - Nội dung: Agent muốn "Thanh toán phí đỗ xe" · Số tiền: 800.000 VND ·
     Dịch vụ: Payment Service · mô tả khoản phí.
   - Cảnh báo: "Hành động này sẽ phát sinh giao dịch tài chính. Agent cần xác nhận của bạn trước khi thực hiện."
   - 2 nút: [ Từ chối ] và [ ✓ Duyệt ].
   - Chọn Duyệt → pay_fee chạy → workflow tiếp tục. Chọn Từ chối → xử lý theo quy tắc hệ thống
     (ATOMIC → recovery, có thể hoàn tác các bước đã làm).
   - Lưu ý: payment < 300.000 VND → tự động chạy, KHÔNG hiện modal (AUTO_ALLOWED).
```

---

## 7. Luồng Rollback / Saga Compensation (Demo Day Final MVP) — 1 màn hình

**Luồng:** khi workflow cần hoàn tác (policy denied / không thể phục hồi / user từ chối), hệ thống gọi compensation **theo thứ tự ngược** chỉ với step đã COMPLETED:

- Compensation tools (nội bộ, KHÔNG xuất hiện trong TaskPlan): `cancel_resident`, `cancel_vehicle`, `cancel_parking`, `refund_payment`.
- Step COMPLETED mới có side effect cần undo; step FAILED bỏ qua.
- Kết quả cuối: `ROLLED_BACK` (hoàn tác hết) hoặc `COMPENSATION_FAILED` (hoàn tác một phần, cần can thiệp thủ công).

```text
Thiết kế 1 MÀN HÌNH WORKFLOW ROLLED BACK (Saga Compensation):
- Ví dụ kịch bản: T1 register_resident ✅ → T2 register_vehicle ✅ → T3 book_parking ❌ (Zone A và Zone B
  đều hết chỗ, không thể phục hồi) → hệ thống hoàn tác T2 rồi T1.
- Hiển thị từng bước với trạng thái chuyển đổi:
    T1 ✅ → ↩ COMPENSATED "Đã hoàn tác: hủy đăng ký cư dân"
    T2 ✅ → ↩ COMPENSATED "Đã hoàn tác: hủy đăng ký phương tiện"
    T3 ❌ FAILED "Khu vực đỗ xe hết chỗ (Zone A, Zone B) — không thể phục hồi"
- Trạng thái workflow cuối: ↩ ROLLED_BACK, tổng kết: "Workflow đã hoàn tác các bước đã thực hiện.
  Không có khoản phí nào bị tính."
- Nếu 1 compensation thất bại → badge COMPENSATION_FAILED + cảnh báo cần hỗ trợ thủ công.
- Lưu ý: step FAILED không có mũi tên hoàn tác (không có side effect để undo).
```

---

## 8. Màn hình tổng hợp lịch sử workflow (Dashboard cá nhân) — 1 màn hình

- Recent workflows trên Home (màn hình Goal Input) hiển thị: tên tóm tắt, trạng thái, thời gian.
- Bấm vào → mở Workflow Detail của workflow đó (luồng #1, màn hình 4).

```text
Thiết kế 1 MÀN HÌNH DANH SÁCH WORKFLOW GẦN ĐÂY (trên Home / Goal Input):
- Mỗi dòng: icon trạng thái, tóm tắt goal, thời gian, badge kết quả.
- Trạng thái hiển thị: ✅ SUCCESS · 🔄 RUNNING · 🔁 RECOVERING · ⏸ WAITING_APPROVAL ·
  ❌ FAILED · ↩ ROLLED_BACK.
- 4 dòng mẫu:
    ✅  Đăng ký cư dân + xe + chỗ đậu xe + thanh toán phí      30/07 10:20
    🔄  Đăng ký cư dân + xe + chỗ đậu xe + thanh toán phí      31/07 11:30
    ❌  Đặt chỗ đậu xe — Zone A hết chỗ                       31/07 09:05
    ↩  Đăng ký cư dân + xe (đã hoàn tác)                      29/07 14:00
- Tương tác: nhấn vào dòng → mở Workflow Detail tương ứng; dòng RUNNING nhấn vào → mở Timeline đang chạy.
```

---

## 9. Màn hình tổng quan kiến trúc (demo technical screen)

Không phải màn hình user — dùng để minh hoạ trong demo mentor/ràng buộc kỹ thuật. Lấy trực tiếp từ `src/api/routes.py` + `docs/architecture_diagram.md`:

```text
Thiết kế 1 MÀN HÌNH "DƯỚI MUI" (technical demo screen) thể hiện hành trình từ goal → kết quả:
- 5 chip tầng: Planner (LLM) → Validator (deterministic) → Executor → Connector → PostgreSQL.
- Hiển thị lần lượt trạng thái từng tầng khi workflow chạy (PLANNING → VALIDATED → EXECUTING → FINISHED).
- Kèm timeline các sự kiện (message tiếng Việt):
    "LLM đang phân tích mục tiêu và chọn các dịch vụ cần thiết." (PLANNING)
    "Agent đã tạo TaskPlan có cấu trúc." (PLANNED)
    "Validator đang kiểm tra dependency, allowlist và dữ liệu an toàn." (VALIDATING)
    "Kế hoạch đã hợp lệ và được phép chuyển sang thực thi." (VALIDATED)
    "Executor đang gọi các dịch vụ theo đúng thứ tự phụ thuộc." (EXECUTING)
    "Agent đang thực hiện bước "..."." (TASK_RUNNING)
    "Agent đã hoàn thành bước "..."." (TASK_SUCCESS)
    "Workflow đã kết thúc và trạng thái đã được lưu." (FINISHED)
- Mục đích: chứng minh deterministic core (validate → execute) + persistent state, không phải black box.
```

---

## Hướng dẫn sử dụng

- Mỗi prompt ở trên có thể dùng độc lập hoặc ghép nhiều luồng để tạo 1 app demo đầy đủ.
- Luôn dán khối **Bối cảnh chung** (§0) trước prompt của từng luồng để AI có đủ context.
- Nếu tạo màn hình bằng Pencil (.pen): sau khi thiết kế xong từng màn hình, dùng `get_screenshot` để kiểm tra bố cục/typography trước khi chuyển sang màn hình kế tiếp.
- Tham chiếu chính thức:
  - Tool/field/status/error code: [shared_contracts.md](../shared_contracts.md)
  - Wireframe & UI flow: [docs/gate1/wireframe.md](gate1/wireframe.md)
  - Message thật backend: [src/api/routes.py](../src/api/routes.py)
  - Danh mục dự án: [src/common/projects.py](../src/common/projects.py)
  - Kiến trúc: [docs/architecture_diagram.md](architecture_diagram.md)

---

*P-118 — Prompt pack màn hình theo luồng tính năng — 12/08/2026*
