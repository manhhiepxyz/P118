# Báo cáo thực hiện — Mock API P-118 (Tuần 1)

> Tài liệu này mô tả các việc đã làm trong phiên làm việc: dựng skeleton mock API cho 4 dịch vụ giả lập theo `shared_contracts.md`, kèm test và kết quả xác minh.
> Phiên bản mock: **v0.4.0** (envelope response + `?fail=` injection + fix DB layer + 3 mock provider độc lập theo system design).
> Ngày cập nhật: 2026-08-08 · Thực hiện bởi: AI hỗ trợ (theo vai trò Hoàng Anh — Dịch vụ, dữ liệu và giao diện)

---

## Lịch sử phiên bản

| Phiên bản | Ngày | Thay đổi chính |
|---|---|---|
| v0.1.0 | 2026-08-02 | Skeleton 4 router, response trần (object nghiệp vụ), lỗi trong `HTTPException.detail = {code, message}` |
| **v0.2.0** | **2026-08-04** | **Response envelope đồng nhất `{success, data, error_code, message, retryable}` cho cả success lẫn error. Bổ sung `?fail=<CODE>` cho mọi endpoint POST để giả lập lỗi mà không cần fill state thật.** |
| **v0.3.0** | **2026-08-08** | **Fix DB layer: (1) migration giờ chạy đúng `schema.sql` + `seed.sql` — trước đây `schema.sql` không bao giờ chạy, bảng workflow không được tạo; (2) sửa type mismatch UUID/string của FK `workflow_id` trong `orm_models.py`; (3) sửa `result.error_message` thay vì `result.message`; (4) bind đúng cột DATE cho `booking_date` (asyncpg DataError); (5) fix indent lỗi trong `archive_workflow()`; (6) fix file stale `src/services/mock/resident_service.py` + thêm test.** |
| **v0.4.0** | **2026-08-08** | **Tách 3 mock provider độc lập theo cấu trúc system design: `src/services/mock/{resident,transport,payment}.py` — 3 FastAPI app, 3 cổng (8001/8002/8003) khớp 3 Connector của Mạnh Hiệp đã viết. Mỗi provider có store riêng (độc lập, faithful design), bỏ cross-check giữa provider (HUB orchestrate truyền dữ liệu). Giữ nguyên `src/mock/` (single app + cross-check + 22 test cũ).** |

---

## 1. Tóm tắt

Đã hoàn thành **mock API** cho 4 dịch vụ theo đúng internal tool contract trong `shared_contracts.md`:

| Tool | Mock service | Kết quả |
|---|---|---|
| `register_resident` | Resident | `resident_id` |
| `register_vehicle` | Vehicle | `vehicle_id` |
| `book_parking` | Parking | `booking_id`, `parking_zone`, `booking_date`, `amount`, `currency` |
| `pay_fee` | Payment | `payment_id`, `payment_status` |

**Trạng thái (v0.4.0):** ✅ Chạy được, **129/129 test pass** (mock API single-app + 3 provider độc lập + DB layer + ResidentService trên PG thật), `ruff check` + `ruff format` sạch, happy path + failure injection đã kiểm thử qua HTTP thật trên cả 3 cổng.

---

## 2. Các file đã tạo

### 2.1 Code mock service — `src/mock/`

| File | Mô tả |
|---|---|
| [src/mock/__init__.py](src/mock/__init__.py) | Ghi chú mục đích mock service (chỉ phát triển nội bộ) |
| [src/mock/main.py](src/mock/main.py) | FastAPI app riêng cho mock, mount 4 routers, bật CORS, `/health` |
| [src/mock/errors.py](src/mock/errors.py) | Hàm chuẩn hóa lỗi → HTTP status + body `{code, message}` |
| [src/mock/ids.py](src/mock/ids.py) | Sinh ID tăng dần: `RES-001`, `VEH-001`, `BOOK-001`, `PAY-001` |
| [src/mock/schemas.py](src/mock/schemas.py) | Pydantic request/response theo contract (enum `StrEnum`, snake_case) |
| [src/mock/store.py](src/mock/store.py) | Store in-memory dùng chung cho 4 service, có `reset()` cho test |
| [src/mock/routers/residents.py](src/mock/routers/residents.py) | POST/GET `/api/residents` |
| [src/mock/routers/vehicles.py](src/mock/routers/vehicles.py) | POST/GET `/api/vehicles` |
| [src/mock/routers/parking.py](src/mock/routers/parking.py) | POST/GET `/api/parking/bookings` |
| [src/mock/routers/payments.py](src/mock/routers/payments.py) | POST/GET `/api/payments`, hỗ trợ `?fail=` |
| [src/mock/errors.py](src/mock/errors.py) | Custom `MockApiError`, exception handler trả envelope; `inject_failure(code)` map `?fail=` → lỗi |
| [src/mock/schemas.py](src/mock/schemas.py) | Thêm `ApiEnvelope` (`{success, data, error_code, message, retryable}`) dùng chung cho success và error |

### 2.2 Test — `tests/test_mock/`

| File | Số test | Nội dung |
|---|---|---|
| [tests/test_mock/conftest.py](tests/test_mock/conftest.py) | — | Fixture autouse `reset_store` để cô lập dữ liệu giữa các test |
| [tests/test_mock/test_residents.py](tests/test_mock/test_residents.py) | 4 | Happy path, 409 trùng căn hộ, 404, **?fail=SERVICE_UNAVAILABLE** |
| [tests/test_mock/test_vehicles.py](tests/test_mock/test_vehicles.py) | 5 | Happy path, 404 resident, 409 trùng biển số, 404, **?fail=SERVICE_UNAVAILABLE** |
| [tests/test_mock/test_parking.py](tests/test_mock/test_parking.py) | 7 | Happy path, 404, **NO_AVAILABILITY** (capacity + ?fail=), trùng xe+ngày, 404, **?fail=SERVICE_UNAVAILABLE** |
| [tests/test_mock/test_payments.py](tests/test_mock/test_payments.py) | 6 | Happy path, 404, amount mismatch, 404, **?fail=PAYMENT_FAILED**, **?fail=SERVICE_UNAVAILABLE** |

### 2.3 File sửa đổi

| File | Thay đổi |
|---|---|
| [Makefile](Makefile) | Thêm target `run-mock` (cổng 8001) và `test-mock` |
| [Week1.md](Week1.md) | Thêm **mục 5** — mô tả code mock API, cách chạy, cập nhật checklist; đánh số lại các mục |

### 2.4 Thay đổi v0.2.0 — Response envelope + Failure injection

**Response envelope (cả success và error):**

Mọi endpoint (POST/GET) đều trả JSON envelope thống nhất, không phụ thuộc HTTP status:

```json
// 2xx — success
{
  "success": true,
  "data": { "resident_id": "RES-001" },
  "error_code": null,
  "message": "Created",
  "retryable": false
}

// 4xx/5xx — error (body ở root, KHÔNG bọc trong "detail")
{
  "success": false,
  "data": null,
  "error_code": "NO_AVAILABILITY",
  "message": "Parking Zone A (ZONE_A) is full on 2026-08-10",
  "retryable": false
}
```

Cơ chế:
- `src/mock/schemas.py::ApiEnvelope` — Pydantic model chung.
- `src/mock/errors.py::MockApiError` — custom exception, thay thế `HTTPException`.
- `install_error_handler(app)` — đăng ký exception handler trong `src/mock/main.py`, đảm bảo response lỗi cũng ở body gốc (không bị FastAPI bọc vào `{"detail": {...}}`).

> **Quyết định kiến trúc:** Chọn custom exception thay vì `HTTPException(detail=...)` để envelope đồng nhất giữa 2xx và 4xx/5xx. Connector parse body root trực tiếp → dễ map sang `StandardResult` ở Mạnh Hiệp.

**Failure injection — `?fail=<CODE>`:**

Mọi endpoint POST đều chấp nhận query param `?fail=<CODE>` để giả lập lỗi mà không cần fill state thật (xe, slot, tiền...).

| Endpoint | Ví dụ | HTTP | `error_code` | `retryable` |
|---|---|---|---|---|
| POST `/api/residents` | `?fail=RESIDENT_ALREADY_EXISTS` | 409 | `RESIDENT_ALREADY_EXISTS` | false |
| POST `/api/residents` | `?fail=SERVICE_UNAVAILABLE` | 503 | `SERVICE_UNAVAILABLE` | **true** |
| POST `/api/vehicles` | `?fail=VEHICLE_ALREADY_EXISTS` | 409 | `VEHICLE_ALREADY_EXISTS` | false |
| POST `/api/parking/bookings` | `?fail=NO_AVAILABILITY` | 409 | `NO_AVAILABILITY` | false |
| POST `/api/parking/bookings` | `?fail=SERVICE_TIMEOUT` | 504 | `SERVICE_TIMEOUT` | **true** |
| POST `/api/payments` | `?fail=PAYMENT_FAILED` | 409 | `PAYMENT_FAILED` | false |
| POST `/api/payments` | `?fail=INSUFFICIENT_BALANCE` | 409 | `INSUFFICIENT_BALANCE` | false |
| (mọi POST) | `?fail=INTERNAL_SERVICE_ERROR` | 500 | `INTERNAL_SERVICE_ERROR` | false |

Map đầy đủ ở `src/mock/errors.py::inject_failure()`. Fallback cho code không nhận dạng → `500 UNKNOWN_EXTERNAL_ERROR`.

---

## 2.5 Thay đổi v0.4.0 — 3 mock provider độc lập (theo system design)

Theo cấu trúc `01-high-level-architecture.md` (mock providers tách theo domain: `mocks/resident/`, `mocks/transport/`, `mocks/payment/`), bổ sung **3 FastAPI app độc lập** tại `src/services/mock/`:

| App | File | Cổng | Connector (Mạnh Hiệp) |
|---|---|---|---|
| Resident | `src/services/mock/resident.py` | **8001** | `ResidentConnector` (base_url 8001) |
| Transport | `src/services/mock/transport.py` | **8002** | `TransportConnector` (8002 — vehicle + parking) |
| Payment | `src/services/mock/payment.py` | **8003** | `PaymentConnector` (8003) |

**Điểm mấu chốt:** các Connector của Mạnh Hiệp đã được viết sẵn với base_url 8001/8002/8003 — 3 app này khớp đúng, **không cần sửa connector nào**.

**Nguyên tắc provider độc lập (faithful design):**

- Mỗi app có **store in-memory riêng** (`store = Store()` module-level), KHÔNG dùng singleton `src.mock.store.store`. Provider không biết dữ liệu provider khác — HUB orchestrate nối chuỗi và truyền `resident_id` / `vehicle_id` / `booking_id` vào input task sau.
- **Giữ check trong-provider:** resident check trùng căn hộ; transport check trùng plate, trùng xe+ngày, capacity ZONE_A, và `book_parking` vẫn check `vehicle_id` (vehicle + parking là **cùng** Transport provider).
- **Bỏ check cross-provider:** `register_vehicle` không check `resident_id` (của Resident provider); `pay_fee` không check `booking_id`/amount (của Transport provider).

**Hai bản mock song song:**

| Bản | Vị trí | Cổng | Đặc điểm |
|---|---|---|---|
| Single app (có cross-check) | `src/mock/main.py` | 8001 | Demo chuỗi nội bộ, 22 test cũ giữ nguyên |
| 3 provider độc lập | `src/services/mock/*.py` | 8001/8002/8003 | Khớp 3 Connector, mỗi provider Swagger riêng |

> `run-mock` (8001) và `run-mock-resident` (8001) đều bind cổng 8001 — **2 lựa chọn thay thế, không chạy đồng thời** (đã ghi chú trong Makefile).

---

## 3. Thiết kế chi tiết

### 3.1 Cấu trúc dữ liệu lưu trong store

```python
Store:
  residents: {RES-001: {resident_id, full_name, apartment_code, residential_area}}
  vehicles:  {VEH-001: {vehicle_id, resident_id, plate_number, vehicle_type}}
  bookings:  {BOOK-001: {booking_id, vehicle_id, parking_zone, booking_date, amount, currency}}
  payments:  {PAY-001: {payment_id, booking_id, amount, currency, payment_status}}
  parking_load: {(zone, booking_date): so_chỗ_đã_đặt}
```

### 3.2 Quy tắc nghiệp vụ trong mock

| Service | Kiểm tra | HTTP + mã lỗi (nằm trong envelope `error_code`) |
|---|---|---|
| Resident | Trùng căn hộ trong cùng khu | 409 `RESIDENT_ALREADY_EXISTS` |
| Vehicle | `resident_id` không tồn tại | 404 `RESIDENT_NOT_FOUND` |
| Vehicle | Trùng biển số | 409 `VEHICLE_ALREADY_EXISTS` |
| Parking | `vehicle_id` không tồn tại | 404 `VEHICLE_NOT_FOUND` |
| Parking | Trùng xe + ngày | 409 `BOOKING_ALREADY_EXISTS` ⚠️ |
| Parking | ZONE_A hết chỗ (sức chứa 3/ngày) | 409 `NO_AVAILABILITY` |
| Payment | `booking_id` không tồn tại | 404 `BOOKING_NOT_FOUND` |
| Payment | Amount không khớp booking | 409 `PAYMENT_AMOUNT_MISMATCH` ⚠️ |

> Toàn bộ response lỗi đều có dạng envelope (xem mục 2.4), `error_code` ở body root chứ không bọc trong `detail`.

> ⚠️ `BOOKING_ALREADY_EXISTS` và `PAYMENT_AMOUNT_MISMATCH` **chưa có trong bảng error code mục 8** `shared_contracts.md`. Cần nhóm cập nhật vào contract trước khi merge (xem mục 6).

### 3.3 Giá tham chiếu (giả lập)

| Zone | Giá/ngày |
|---|---|
| ZONE_A | 150 000 VND |
| ZONE_B | 100 000 VND |

### 3.4 Quyết định kiến trúc

- **Monolith + router tách rời** — không microservice (phù hợp quy mô nhóm, contract đã cô lập biên giới qua Connector).
- **Store in-memory** — chưa dùng PostgreSQL; tuần sau thay bằng SQLAlchemy repository.
- **Queue (Redis Streams/RabbitMQ) thuộc tầng Executor** — mock API chỉ là HTTP thuần.
- **HITL** (`PENDING` → `PAID`) làm tuần 4; enum đã có sẵn trong contract, không cần đổi.
- **Custom exception `MockApiError` + exception handler** (v0.2.0) — thay vì `HTTPException(detail=...)`, để error envelope cùng shape với success envelope, dễ parse ở Connector.

---

## 4. Kết quả xác minh

### 4.1 Kiểm thử tự động

```text
python -m pytest tests/ -q
→ 129 passed   (gồm 22 test mock single-app + 16 test 3 provider độc lập + DB tests)

python -m ruff check src/ tests/
→ All checks passed!
```

### 4.2 Kiểm thử qua HTTP thật (uvicorn cổng 8001)

Happy path đầy đủ `Resident → Vehicle → Booking → Payment` (envelope):

```text
POST /api/residents       → 201 {"success":true, "data":{"resident_id":"RES-001"}, "error_code":null, "message":"Created", "retryable":false}
POST /api/vehicles        → 201 {"success":true, "data":{"vehicle_id":"VEH-001"}, ...}
POST /api/parking/bookings → 201 {"success":true, "data":{"booking_id":"BOOK-001","parking_zone":"ZONE_A","booking_date":"2026-08-10","amount":150000,"currency":"VND"}, ...}
POST /api/payments        → 201 {"success":true, "data":{"payment_id":"PAY-001","payment_status":"PAID"}, ...}
```

Kiểm tra chuỗi phụ thuộc — mỗi bước chặn đúng khi bước trước chưa có (error envelope ở body root):

```text
POST /api/vehicles  với resident chưa tồn tại → 404
  {"success":false, "data":null, "error_code":"RESIDENT_NOT_FOUND", "message":"Resident RES-999 not found", "retryable":false}
POST /api/parking/bookings với vehicle chưa có → 404
  {"success":false, "data":null, "error_code":"VEHICLE_NOT_FOUND", ...}
POST /api/payments  với booking chưa có        → 404
  {"success":false, "data":null, "error_code":"BOOKING_NOT_FOUND", ...}
```

Kiểm tra `NO_AVAILABILITY` — 2 cách đều hoạt động:

```text
Cách 1 — fill capacity thật (ZONE_A sức chứa 3, xe thứ 4 cùng ngày):
→ 409 {"success":false, "error_code":"NO_AVAILABILITY", "message":"Parking Zone A (ZONE_A) is full on 2026-08-10"}

Cách 2 — ?fail= injection (không cần fill data, Mạnh Hiệp dùng cho test retry):
POST /api/parking/bookings?fail=NO_AVAILABILITY
→ 409 {"success":false, "data":null, "error_code":"NO_AVAILABILITY", "retryable":false}
```

Kiểm tra retryable error qua injection:

```text
POST /api/residents?fail=SERVICE_UNAVAILABLE
→ 503 {"success":false, "data":null, "error_code":"SERVICE_UNAVAILABLE", "retryable":true}
```

Kiểm tra OpenAPI: đủ **8 endpoint** tại `/openapi.json`, Swagger tại `/docs`.

---

## 5. Cách chạy

```bash
# Chạy mock server single-app (Swagger: http://localhost:8001/docs)
make run-mock
# hoặc: uvicorn src.mock.main:app --reload --port 8001

# Chạy 3 mock provider độc lập (3 terminal) — khớp 3 Connector
make run-mock-resident    # http://localhost:8001/docs
make run-mock-transport   # http://localhost:8002/docs
make run-mock-payment     # http://localhost:8003/docs

# Chạy test mock API
make test-mock
# hoặc: pytest tests/test_mock/ -v
```

> Lưu ý: mock server chạy riêng trên cổng 8001, không đụng app chính (cổng 8000). `run-mock` và `run-mock-resident` cùng bind 8001 — không chạy đồng thời.

---

## 6. Việc cần nhóm chốt / làm tiếp

### Đã chốt ở v0.2.0 (2026-08-04)
- ✅ Response format chọn **envelope đồng nhất** `{success, data, error_code, message, retryable}` cho cả success và error. Khớp mô tả ở `tasks/P118-003-hoanganh.md` mục "Mock API response format".
- ✅ Failure injection qua `?fail=<CODE>` đã hoạt động đầy đủ cho cả 4 service.

### Cần nhóm xác nhận (mang tính chính thức vào contract)
- [ ] Hai mã lỗi `BOOKING_ALREADY_EXISTS`, `PAYMENT_AMOUNT_MISMATCH` đang dùng trong mock — đồng ý đưa vào `shared_contracts.md` mục 8 (`ErrorCode`) chính thức không? (Chủ quản: Mạnh Hiệp — sở hữu `src/common/enums.py`)
- [ ] Kịch bản đỗ xe có được giảng viên chấp nhận là "bài toán tương đương" không? (mục 2.4 Week1.md)

### Chưa làm (thuộc kế hoạch tuần)
- [x] ~~PostgreSQL: 3 bảng `workflow`, `task`, `execution` + migration/script khởi tạo (ngày 4–5 tuần 1)~~ ✅ Hoàn thành v0.3.0 — `schema.sql` + `seed.sql` chạy đúng, có `workflow`, `workflow_task`, `execution_log` (+ `residents`, `vehicles`, `parking_*`, `payments` cho mock service)
- [x] ~~Lưu + đọc lại 1 workflow và 1 task result vào PostgreSQL (ngày 5)~~ ✅ Xác minh end-to-end trong phiên: tạo workflow → save task result (RES-001 trong result_data JSONB) → đọc lại khớp
- [ ] Thay store in-memory bằng SQLAlchemy repository (xong sớm thì làm) — ⚠️ quyết định: `src/services/mock/resident_service.py` đã dùng asyncpg trực tiếp, bỏ SQLAlchemy
- [ ] FastAPI workflow routes (`POST /workflow/start`, `GET /workflow/{id}/status`) — tuần 2
- [ ] HITL cho bước tốn phí (`PENDING` → xác nhận → `PAID`) — tuần 3
- [ ] WebSocket realtime — tuần 3
- [ ] React Timeline UI + HITL Modal — tuần 3
- [ ] Docker Compose, deploy Render/Railway, Evaluation — tuần 4
