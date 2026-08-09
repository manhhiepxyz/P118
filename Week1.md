# Kế hoạch Tuần 1 — Phí Hoàng Anh (Dịch vụ, dữ liệu và giao diện)

> Tham chiếu: `shared_contracts.md` (quy ước bắt buộc), `team-plan.md` (mục 3 — Hoàng Anh).
> Nền tảng repo: skeleton FastAPI + LangGraph có sẵn (`src/`, `docker-compose.yml`, `requirements.txt`).

---

## 1. Vai trò và mục tiêu tuần 1

| Vai trò | Dịch vụ giả lập, dữ liệu và giao diện |
|---|---|
| Việc chính | Xây **mock API**; **PostgreSQL**; lưu **trạng thái workflow**; (tuần sau: giao diện người dùng, giao diện xác nhận, triển khai cloud, Live URL) |
| Kết quả cuối tuần 1 | Dịch vụ giả lập hoạt động; dữ liệu được lưu vào PostgreSQL và đọc lại được; đóng góp vào demo nội bộ cuối tuần |

**Tiêu chí hoàn thành tuần 1 (phần của bạn):**
- [ ] Có ít nhất **3 mock API skeleton** (Resident, Vehicle, Parking) — khuyến khích cả Payment
- [ ] Request/response của 4 dịch vụ theo đúng `shared_contracts.md` (field, enum, format)
- [ ] Mỗi API có ít nhất 1 case thành công (trả đúng ID dạng `RES-001`, `VEH-001`, `BOOK-001`, `PAY-001`)
- [ ] Parking có lỗi mẫu `NO_AVAILABILITY` (Zone A đầy)
- [ ] PostgreSQL có bảng `workflow`, `task`, `execution` + migration/script khởi tạo
- [ ] Lưu được 1 workflow + 1 task result vào PostgreSQL và đọc lại đúng
- [ ] Không còn mâu thuẫn tên field giữa 3 phần (dùng đúng contract)
- [ ] Mọi thay đổi đưa lên GitHub qua pull request

**Chưa làm tuần này:** frontend, HITL UI, WebSocket, deploy hoàn chỉnh, xử lý nhiều lỗi phức tạp.

---

## 2. Yêu cầu đề tài & quyết định kịch bản demo

> **Quyết định nhóm (đã chốt):** giữ **kịch bản đỗ xe** theo `shared_contracts.md` — coi là bài toán tương đương với kịch bản trong đề tài. ⚠️ Cần xác nhận lại với giảng viên (xem mục 2.4).

### 2.1 Ánh xạ kịch bản đề → kịch bản đỗ xe

| Bước trong đề tài | Dịch vụ P-118 | Tool | Bước tốn phí |
|---|---|---|---|
| Chuyển tới căn hộ (thuê/mua BĐS) | Resident service | `register_resident` | Không |
| Đặt xe / phương tiện (dịch vụ gọi xe) | Vehicle service | `register_vehicle` | Không |
| Đặt chỗ có phí (gói khám y tế) | Parking service | `book_parking` | ✅ Có |
| Kích hoạt ví / thanh toán | Payment service | `pay_fee` | Sau khi duyệt |

→ Cấu trúc orchestration mà đề cần (planning, dependency, truyền dữ liệu, HITL, compensation) **hoàn toàn tương đương** giữa 2 kịch bản. Điểm đạt của đề nằm ở cơ chế điều phối, không nằm ở tên dịch vụ.

### 2.2 Yêu cầu đề → tuần thực hiện

| Yêu cầu đề | Loại | Tuần |
|---|---|---|
| Deploy được, đăng nhập, ≥ 2 vai trò (khách/admin) | Must-have | Tuần 4–5 |
| Agent lập plan nhiều bước, gọi 2–3 service nối tiếp | Must-have | Tuần 2–3 |
| Truyền dữ liệu giữa các bước | Must-have | Tuần 2 |
| HITL duyệt bước tốn phí trước khi thực thi | Must-have | Tuần 4 |
| Hiển thị tiến trình real-time từng bước | Must-have | Tuần 4–5 |
| Xử lý lỗi cơ bản (phát hiện, dừng luồng, thông báo) | Must-have | Tuần 3 |
| Compensation / rollback khi 1 bước thất bại | Nice-to-have | Tuần 5 |
| Chạy song song bước độc lập | Nice-to-have | Tuần 3–5 |
| Tạm dừng / resume từ checkpoint | Nice-to-have | Tuần 5 |
| Gợi ý điều chỉnh plan khi ràng buộc đổi | Nice-to-have | Tuần 3 |
| Cảnh báo vượt giới hạn chi phí | Nice-to-have | Tuần 4–5 |

### 2.3 Công nghệ đề gợi ý → hướng dùng trong P-118

| Đề gợi ý | Hướng dự kiến P-118 | Khi nào |
|---|---|---|
| LLM + LangGraph, checkpointing | ✅ Dùng | Tuần 2–3 |
| Saga orchestration tự viết | ✅ Dùng | Tuần 2 |
| RabbitMQ / Redis Streams | ⚠️ Dùng (chọn Redis Streams trước, đổi được sau) — thuộc tầng Executor, **không thuộc mock API** | Tuần 2+ |
| FastAPI | ✅ Dùng | Đã có sẵn |
| PostgreSQL: saga state, step log, compensation log | ⚠️ Đề xuất đổi tên/ánh xạ 3 bảng `workflow/task/execution` cho khớp | Tuần 4 |
| OpenAPI specs 4–5 service | ✅ Swagger `/docs` mặc định của FastAPI | Tuần 1 |
| React + WebSocket timeline | ⚠️ Dùng (chưa làm tuần này) | Tuần 4–5 |
| Docker Compose cloud | ⚠️ Dùng | Tuần 5 |

> Các dòng ⚠️ là **đề xuất, cần nhóm chốt** — không phải quyết định chính thức.

### 2.4 Cần chốt với giảng viên

- [ ] Kịch bản đỗ xe có được tính là "bài toán tương đương" với kịch bản trong đề (BĐS – xe – y tế – ví) không?
- [ ] Nếu không: nhóm sẽ đổi contract theo đúng kịch bản đề **trong ngày 1–2 tuần 1**, trước khi viết code — đổi sau sẽ tốn gấp 3.

### 2.5 Hệ quả cho mock API (giữ kịch bản đỗ xe)

- Xây 4 mock theo contract hiện tại: `residents`, `vehicles`, `parking`, `payments` — như mục 5 bên dưới.
- `book_parking` là **bước tốn phí** → mock hỗ trợ HITL: tạo booking trả `payment_status = PENDING`, có endpoint/webhook xác nhận → `PAID` (làm ở tuần 4; enum `PENDING/PAID/FAILED/REFUNDED` đã có sẵn trong contract, **không cần đổi contract**).
- Queue (Redis Streams/RabbitMQ) thuộc tầng Executor (Mạnh Hiệp), **không thuộc mock API** — mock chỉ là HTTP thuần.

---

## 3. Mốc tích hợp chung trong tuần

| Thời điểm | Việc |
|---|---|
| Cuối ngày 1 | Chốt toàn bộ schema và contract (cả nhóm) |
| Cuối ngày 3 | Mỗi người demo module riêng |
| Ngày 4 | Ghép Executor → Connector → Resident API |
| Ngày 5 | Lưu `resident_id` và trạng thái task vào PostgreSQL |
| Cuối tuần | Demo kỹ thuật nội bộ + chốt việc tuần 2 |

---

## 4. Chi tiết từng ngày — công việc và các bước

### Ngày 1 — Chốt contract chung (cả nhóm) + chuẩn bị nền

**Mục tiêu:** Thống nhất contract trước khi viết code.

**Công việc & các bước:**
1. Cùng nhóm chốt: 4 tool (`register_resident`, `register_vehicle`, `book_parking`, `pay_fee`), input/output, `TaskPlan`, `StandardResult`, task status, error code (xem mục 3–8 `shared_contracts.md`).
2. Chốt quy tắc branch và pull request (mục 16 `shared_contracts.md` — thay đổi contract phải cập nhật file trước khi merge).
3. Kiểm tra nền repo:
   - Bật các dependency DB trong [requirements.txt](requirements.txt): bỏ comment `sqlalchemy`, `psycopg2-binary`, `alembic`.
   - Xác nhận chạy được app hiện tại: `uvicorn src.main:app` → mở Swagger tại `/docs`.
4. Thống nhất **đường dẫn mock API** theo mục 12 `shared_contracts.md` (8 endpoint).
5. Tạo nhánh làm việc riêng từ `main`, ví dụ: `feature/hoanganh-week1-mock-api`.

**Đầu ra ngày 1:** Contract đã chốt, repo chạy được, nhánh riêng đã tạo.

---

### Ngày 2 — Mock API skeleton: Resident, Vehicle (case thành công)

**Mục tiêu:** 2 API đầu tiên hoạt động, trả đúng ID, có Swagger.

**Công việc & các bước:**
1. Tạo cấu trúc mock service trong repo (gợi ý):
   ```
   src/mock/__init__.py
   src/mock/main.py        # FastAPI app mock
   src/mock/schemas.py     # Pydantic request/response theo contract
   src/mock/store.py       # Lưu trữ tạm in-memory (dict) — thay PostgreSQL sau
   src/mock/routers/
     __init__.py
     residents.py
     vehicles.py
     parking.py
     payments.py
   ```
2. Viết `schemas.py` — đúng field trong mục 4 `shared_contracts.md`:
   - `RegisterResidentRequest`: `full_name`, `apartment_code`, `residential_area`
   - `RegisterVehicleRequest`: `resident_id`, `plate_number`, `vehicle_type` (`car`|`motorcycle`)
   - Response: `resident_id`, `vehicle_id`
   - Tất cả field dùng `snake_case`.
3. Viết `residents.py`:
   - `POST /api/residents` → 201, body `{"resident_id": "RES-001"}` (sinh ID theo thứ tự tăng dần)
   - `GET /api/residents/{resident_id}` → 200; nếu không có → 404 `RESIDENT_NOT_FOUND`
   - Trùng dữ liệu (cùng `apartment_code`) → 409 `RESIDENT_ALREADY_EXISTS`
4. Viết `vehicles.py`:
   - `POST /api/vehicles` → 201, body `{"vehicle_id": "VEH-001"}`
   - Kiểm tra `resident_id` tồn tại trước → nếu không → 404 `RESIDENT_NOT_FOUND`
   - Trùng biển số → 409 `VEHICLE_ALREADY_EXISTS`
   - `GET /api/vehicles/{vehicle_id}` → 200 / 404 `VEHICLE_NOT_FOUND`
5. Input thiếu hoặc sai định dạng → 400 (chuẩn hóa thành `INVALID_INPUT`/`MISSING_INFORMATION` ở tầng Connector, mock chỉ trả đúng HTTP status theo mục 13).
6. Kiểm tra: chạy mock app, gọi thử bằng Swagger `/docs` hoặc `httpx`.

**Đầu ra ngày 2:** Resident + Vehicle skeleton hoạt động, trả đúng ID, có Swagger.

---

### Ngày 3 — Mock API: Parking (kèm lỗi NO_AVAILABILITY) + Payment

**Mục tiêu:** Đủ 3–4 API; có lỗi mẫu Parking để demo cuối ngày 3.

**Công việc & các bước:**
1. Viết `parking.py`:
   - `POST /api/parking/bookings` input: `vehicle_id`, `booking_date` (YYYY-MM-DD), `parking_zone` (`ZONE_A`|`ZONE_B`)
   - Output 201: `booking_id`, `parking_zone`, `booking_date`, `amount`, `currency` (`amount` là số nguyên, `currency` = `VND`)
   - Kiểm tra `vehicle_id` tồn tại → nếu không → 404 `VEHICLE_NOT_FOUND`
   - **Lỗi mẫu:** `ZONE_A` có giới hạn sức chứa (ví dụ 3 chỗ). Khi đã đặt đủ số chỗ cho cùng ngày → **409, body thể hiện `NO_AVAILABILITY`** với message rõ ràng (xem ví dụ mục 6 `shared_contracts.md`). `ZONE_B` luôn còn chỗ.
   - Đặt trùng `vehicle_id` + ngày → 409 `BOOKING_NOT_FOUND` không dùng — thay bằng mã phù hợp theo mục 8 (ví dụ trùng booking → 409).
   - `GET /api/parking/bookings/{booking_id}` → 200 / 404 `BOOKING_NOT_FOUND`
2. Viết `payments.py`:
   - `POST /api/payments` input: `booking_id`, `amount`, `currency`
   - Kiểm tra booking tồn tại → 404 `BOOKING_NOT_FOUND`
   - Output 201: `payment_id` (PAY-001), `payment_status` = `PAID` (mục 4 — không dùng `SUCCESS` cho payment_status)
   - `GET /api/payments/{payment_id}` → 200 / 404 `PAYMENT_NOT_FOUND`
3. Viết `main.py` mock: mount 4 routers, bật CORS (để sau này UI gọi được).
4. Thử 4 kịch bản: resident → vehicle → booking (thành công) → payment; và booking ZONE_A đến khi đầy để thấy `NO_AVAILABILITY`.
5. Demo module riêng cuối ngày 3 cho nhóm.

**Đầu ra ngày 3:** 4 mock API hoạt động, có lỗi mẫu Parking, demo được.

---

### Ngày 4 — Thiết kế PostgreSQL schema + migration + nối với Executor/Connector

**Mục tiêu:** Database chạy được; hỗ trợ Mạnh Hiệp ghép Executor → Connector → Resident API.

**Công việc & các bước:**
1. Thiết kế 3 bảng (mục 3 `team-plan.md`: bảng workflow, task, execution):
   - `workflow`: `id` (PK), `goal`, `status`, `created_at`, `updated_at`
   - `task`: `id` (PK), `workflow_id` (FK), `task_id` (T1…), `tool`, `status`, `depends_on` (jsonb), `input` (jsonb), `output` (jsonb), `error_code`, `created_at`, `updated_at`
   - `execution`: `id` (PK), `workflow_id` (FK), `task_id`, `started_at`, `finished_at`, `status`, `result` (jsonb)
   - Lưu trạng thái dùng đúng enum trong mục 7 `shared_contracts.md` (`PENDING`, `READY`, `RUNNING`, `WAITING_APPROVAL`, `SUCCESS`, `FAILED`, `SKIPPED`, `CANCELLED`).
   - Thời gian theo ISO 8601 / UTC (mục 4).
2. Bật dependency DB trong `requirements.txt`.
3. Tạo **migration hoặc script khởi tạo** database (chọn 1):
   - Cách nhẹ: file SQL `scripts/init_db.sql` chạy bằng `psql`.
   - Cách đầy đủ: Alembic (`alembic init` + 1 migration đầu tiên).
4. Thêm service **postgres** vào [docker-compose.yml](docker-compose.yml) (image `postgres:16`, expose port, volume lưu dữ liệu) — hoặc cấu hình `.env` trỏ DB local.
5. Nối mock API với DB (tùy chọn nếu đủ thời gian): thay `store.py` in-memory bằng SQLAlchemy repository cho Resident.
6. Hỗ trợ Mạnh Hiệp ngày 4: đảm bảo `POST /api/residents` trả đúng format để Connector đọc được `resident_id`.

**Đầu ra ngày 4:** Database chạy được, có migration, Executor gọi được Resident API.

---

### Ngày 5 — Lưu workflow + task result vào PostgreSQL và đọc lại

**Mục tiêu:** Đạt demo cuối tuần theo `team-plan.md` mục 4.

**Công việc & các bước:**
1. Viết repository/CRUD cho `workflow` và `task` (SQLAlchemy hoặc SQL thuần):
   - Tạo workflow, cập nhật status
   - Tạo task, lưu `input`, `output`, `error_code`, cập nhật `status`
   - Đọc lại theo `workflow_id`
2. Thực hiện demo tối thiểu (mục 4 `team-plan.md`):
   ```
   Khởi tạo TaskPlan mẫu
   → Executor chọn register_resident
   → Connector gọi Resident API qua HTTP
   → API trả resident_id
   → lưu task SUCCESS và resident_id vào PostgreSQL
   → đọc lại đúng trạng thái
   ```
3. Viết 1–2 test đơn giản: tạo + đọc lại workflow/task (thư mục `tests/`).
4. Chạy lần lượt: `pytest`, `ruff check .`.
5. Demo nội bộ cuối tuần: trình bày luồng trên, chỉ rõ đâu là mock API, đâu là PostgreSQL.

**Đầu ra ngày 5:** Demo tối thiểu chạy được; nếu hoàn thành sớm thì nối thêm `Resident → Vehicle`.

---

## 5. Mock API — code đã có sẵn (xem `src/mock/`)

> Skeleton 4 mock service đã được dựng trong repo: `src/mock/` (routers + schemas + store in-memory) kèm test ở `tests/test_mock/`.

### Cấu trúc code

```
src/mock/
  __init__.py     # ghi chú mục đích mock service
  main.py         # FastAPI app mock, mount 4 routers + CORS
  errors.py       # chuẩn hóa lỗi → HTTP status + body (code/message)
  ids.py          # sinh ID tuần tự: RES-001, VEH-001, BOOK-001, PAY-001
  schemas.py      # Pydantic request/response theo contract (enum, snake_case)
  store.py        # lưu in-memory dùng chung cho 4 service (có reset() cho test)
  routers/
    residents.py  # register_resident
    vehicles.py   # register_vehicle
    parking.py    # book_parking
    payments.py   # pay_fee
tests/test_mock/  # 16 test: happy path + 404/409/NO_AVAILABILITY
```

### Cách chạy

```bash
# Chạy mock server (cổng 8001, Swagger tại http://localhost:8001/docs)
make run-mock
# hoặc: uvicorn src.mock.main:app --reload --port 8001

# Chạy test cho mock API
make test-mock
# hoặc: pytest tests/test_mock/ -v
```

### Trạng thái so với checklist tuần 1 (phần mock API)

- [x] Resident API: POST/GET, 404, 409 — trả đúng `resident_id`
- [x] Vehicle API: POST/GET, 404, 409 — trả đúng `vehicle_id`
- [x] Parking API: POST/GET, tính `amount`, lỗi `NO_AVAILABILITY` cho `ZONE_A`
- [x] Payment API: POST/GET, trả `payment_status` = `PAID`
- [x] Swagger `/docs` hiển thị đủ 4 service
- [ ] PostgreSQL có 3 bảng `workflow`, `task`, `execution` (ngày 4–5)
- [ ] Migration/script khởi tạo DB chạy được (ngày 4–5)
- [ ] Lưu + đọc lại được 1 workflow và 1 task result (ngày 5)
- [x] `pytest` và `ruff check .` pass (toàn suite 21 test)
- [ ] PR đã tạo, có ít nhất 1 thành viên khác review

### Ghi chú triển khai

- Mock dùng **store in-memory** (dict) — chưa dùng PostgreSQL; tuần sau thay bằng SQLAlchemy repository.
- `book_parking` là **bước tốn phí**; HITL (`PENDING` → xác nhận → `PAID`) làm ở tuần 4, enum đã có sẵn trong contract.
- Queue (Redis Streams/RabbitMQ) thuộc tầng Executor, **không thuộc mock API**.

---

## 6. Danh sách endpoint mock API (theo mục 12 `shared_contracts.md`)

| Method | Path | Kết quả mong đợi |
|---|---|---|
| POST | `/api/residents` | 201 → `resident_id` |
| POST | `/api/vehicles` | 201 → `vehicle_id` |
| POST | `/api/parking/bookings` | 201 → `booking_id`, `parking_zone`, `booking_date`, `amount`, `currency` |
| POST | `/api/payments` | 201 → `payment_id`, `payment_status` = `PAID` |
| GET | `/api/residents/{resident_id}` | 200 / 404 |
| GET | `/api/vehicles/{vehicle_id}` | 200 / 404 |
| GET | `/api/parking/bookings/{booking_id}` | 200 / 404 |
| GET | `/api/payments/{payment_id}` | 200 / 404 |

**HTTP status** (mục 13): 200, 201, 400 (input sai), 404 (không tìm thấy), 409 (trùng lặp / hết chỗ `NO_AVAILABILITY`), 500, 503.

---

## 7. Lưu ý contract bắt buộc (từ `shared_contracts.md`)

- Field nội bộ dùng `snake_case`; không tự đổi tên tool/field/status/error code.
- `payment_status` dùng `PAID` khi thành công — **không** dùng `SUCCESS`.
- `vehicle_type` chỉ nhận `car`, `motorcycle`; `parking_zone` chỉ nhận `ZONE_A`, `ZONE_B`; `currency` MVP chỉ `VND`.
- `booking_date` định dạng `YYYY-MM-DD`; timestamp ISO 8601/UTC.
- Mock API dùng theo mục 12–13; Real API (tuần sau) có thể khác — không ép API thật giống mock.
- Thay đổi contract phải cập nhật `shared_contracts.md` trước, không sửa trong code (mục 2, 16).

---

## 8. Checklist cuối tuần

- [ ] Resident API: POST/GET, 404, 409 — trả đúng `resident_id`
- [ ] Vehicle API: POST/GET, 404, 409 — trả đúng `vehicle_id`
- [ ] Parking API: POST/GET, tính `amount`, lỗi `NO_AVAILABILITY` cho `ZONE_A`
- [ ] Payment API: POST/GET, trả `payment_status` = `PAID`
- [ ] Swagger `/docs` hiển thị đủ 4 service
- [ ] PostgreSQL có 3 bảng `workflow`, `task`, `execution`
- [ ] Migration/script khởi tạo DB chạy được
- [ ] Lưu + đọc lại được 1 workflow và 1 task result
- [ ] `pytest` và `ruff check .` pass
- [ ] PR đã tạo, có ít nhất 1 thành viên khác review

---

## 9. Nếu xong sớm (ưu tiên theo thứ tự)

1. Nối mock API sang PostgreSQL thay vì in-memory (chuyển `store.py` → SQLAlchemy).
2. Nối luồng `Resident → Vehicle` cho demo.
3. Thêm test cho các case lỗi (409, 404, 400) của 4 API.
