# Ownership Verification — Trạng thái thực tế (Week 1)

## Tổng quan

Mock Ownership Provider cung cấp khả năng xác minh: người gửi yêu cầu có phải chủ
sở hữu hợp pháp của căn hộ hay không.

> **Ownership verification KHÔNG phải một Agent tool.**
> Đây là mối quan tâm của tầng Auth/VerificationGuard, chạy **trước** khi Agent
> Workflow bắt đầu. TaskPlan chỉ có đúng 4 tool nghiệp vụ:
> `register_resident`, `register_vehicle`, `book_parking`, `pay_fee`.
> Không có "tool thứ 5".

Planner **không** sinh task verification (không có task `T0`); Executor **không**
orchestrate verification như một task trong TaskPlan.

---

## Trạng thái hiện tại (Week 1) — những gì THỰC SỰ tồn tại trong code

### Đã có

1. **Mock Ownership Provider độc lập**
   - `src/services/mock/apartment_ownership.py` — FastAPI app riêng
     (`apartment_ownership_app`), port 8004, store in-memory riêng.
   - `src/mock/routers/apartment_owners.py` — cùng nghiệp vụ, mount trong single
     app `src/mock/main.py`.
   - `src/services/mock/ownership_service.py` — `ApartmentOwnershipService`,
     bản chạy trên PostgreSQL (bảng `apartment_owners`).
   - Endpoint duy nhất: `POST /api/apartment-owners/verify-ownership`.

2. **Các trạng thái provider hỗ trợ hôm nay** — đúng 3:

   | Kết quả | HTTP | Điều kiện |
   |---|---|---|
   | `VERIFIED` (`verified: true`) | 200 | `owner_name` khớp `full_name` |
   | `OWNERSHIP_NOT_FOUND` | 404 | không có record cho `(apartment_code, residential_area)` |
   | `OWNERSHIP_MISMATCH` | 403 | có record nhưng tên không khớp |

### CHƯA có

- **KHÔNG có `VerificationGuard`.** Không tồn tại module, class hay middleware
  nào tên như vậy trong repo. Đây là hạng mục Week 2.
- **KHÔNG có trạng thái `PENDING` / `REJECTED`.** Không có code và không có test
  cho hai trạng thái này ở luồng ownership. Chúng là **thiết kế dự kiến (Week
  2)**, chưa implement.
- **KHÔNG có ai gọi provider tự động.** Provider chỉ chạy khi có client gọi
  endpoint trực tiếp. Không có caller nào trong `src/executor/`, `src/planner/`
  hay các router nghiệp vụ khác.

### `register_resident` hôm nay làm gì — và KHÔNG làm gì

`register_resident` chạy **hoàn toàn độc lập** với ownership verification.

**Có làm:**
- `src/mock/routers/residents.py` và `src/services/mock/resident.py`
  (in-memory): quét store, nếu đã tồn tại resident cùng
  `(apartment_code, residential_area)` → `409 RESIDENT_ALREADY_EXISTS`; nếu
  không → sinh `resident_id` (`RES-001`, …) và ghi vào store, trả `201`.
- `src/services/mock/resident_service.py` (PostgreSQL): `INSERT` thẳng rồi bắt
  `asyncpg.UniqueViolationError` (an toàn với request đồng thời); constraint
  `apt_area` → `ResidentAlreadyExistsError` (409). Không log `full_name` (PII).
- Hỗ trợ `?fail=<CODE>` để inject lỗi.

**KHÔNG làm:**
- KHÔNG gọi `ApartmentOwnershipService.verify()`.
- KHÔNG gọi `POST /api/apartment-owners/verify-ownership`.
- KHÔNG đọc bảng/store `apartment_owners`.
- KHÔNG bao giờ trả `OWNERSHIP_MISMATCH` hay `OWNERSHIP_NOT_FOUND`.

Hệ quả: hôm nay một người **không phải chủ sở hữu vẫn đăng ký resident thành
công** miễn là căn hộ đó chưa có ai đăng ký. Đây là hành vi đã biết và có chủ
đích theo nguyên tắc hub thuần (`ResidentService` chỉ biết về resident) — việc
chặn sẽ do VerificationGuard đảm nhiệm ở Week 2, chạy TRƯỚC workflow. Test
`test_register_resident_independent` khoá đúng hành vi này.

---

## Kế hoạch Week 2

```
User → Auth/VerificationGuard              ← CHƯA implement
         → Mock Ownership Provider          ← đã có (Week 1)
            → VERIFIED             → cho Agent Workflow chạy
            → OWNERSHIP_NOT_FOUND  → từ chối        (đã có)
            → OWNERSHIP_MISMATCH   → từ chối        (đã có)
            → PENDING              → chờ            (DỰ KIẾN, chưa có code)
            → REJECTED             → từ chối        (DỰ KIẾN, chưa có code)

[chỉ khi VERIFIED]
User prompt → Planner → Validator → Executor
  → T1: register_resident → T2: register_vehicle → T3: book_parking → T4: pay_fee
```

VerificationGuard sẽ gọi provider **TRƯỚC** khi Agent Workflow chạy. Contract của
Agent không đổi: verification không bao giờ là một task trong TaskPlan, ở cả
Week 1 lẫn Week 2.

---

## Thành phần

### Database layer

**Bảng `apartment_owners`** (`src/db/schema.sql`):
```sql
CREATE TABLE apartment_owners (
    apartment_code   VARCHAR(50)  NOT NULL,
    residential_area VARCHAR(100) NOT NULL,
    owner_name       VARCHAR(200) NOT NULL,
    id_number        VARCHAR(20),
    verified_at      TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (apartment_code, residential_area)
);
```

Seed (`src/db/seed.sql`, và `DEFAULT_APARTMENT_OWNERS` trong `src/mock/store.py`)
gồm các căn A1201, B2305, C1801, D0502, E1101.

### Service layer

**`ApartmentOwnershipService`** (`src/services/mock/ownership_service.py`)
- `verify(full_name, apartment_code, residential_area)`.
- Raise `OwnershipNotFoundError` (404) / `OwnershipMismatchError` (403).
- Trả về `{"verified": True, "apartment_code", "residential_area"}` — **không có
  `owner_name`**.

**`ResidentService`** (`src/services/mock/resident_service.py`)
- `register()` chỉ INSERT + bắt `UniqueViolationError`. **Không** gọi ownership.

### Error handling

`OWNERSHIP_NOT_FOUND` / `OWNERSHIP_MISMATCH` là **mã của provider (string)**,
**không** phải thành viên của `ErrorCode` trong `src/common/enums.py` — chúng
không tồn tại ở đó. Lý do: verification chạy trước workflow nên không đi qua
`StandardResult`.

Chúng được định nghĩa/dùng ở:
- `src/mock/errors.py` — helper `not_found()` / `forbidden()` và mapping trong
  `inject_failure()` (`?fail=OWNERSHIP_MISMATCH` → 403,
  `?fail=OWNERSHIP_NOT_FOUND` → 404).
- `src/services/mock/ownership_service.py` — `OwnershipNotFoundError`,
  `OwnershipMismatchError` (cùng kế thừa `OwnershipNotVerifiedError`).

---

## PII: `owner_name` không bao giờ rời khỏi provider

`owner_name` là dữ liệu cá nhân. Quy tắc:

- `owner_name` **chỉ tồn tại nội bộ** (store in-memory / cột DB) để so khớp.
- **Không** endpoint nào trả `owner_name` ra response.
- **Không** log `owner_name` hay `full_name` ở bất kỳ đâu — log chỉ dùng
  `apartment_code`, `residential_area`, `resident_id`.
- Endpoint tra cứu chủ sở hữu `GET /api/apartment-owners/{apartment_code}/{residential_area}`
  **đã bị xoá** khỏi cả single app và provider độc lập: nó trả thẳng `owner_name`
  mà không có nhu cầu nghiệp vụ nào (không caller nào trong repo dùng).
- Regression test `test_verify_ownership_response_has_no_owner_name` khoá điều
  này.

---

## API

### Verify ownership — thành công

```bash
POST /api/apartment-owners/verify-ownership
{
  "full_name": "Lâm Thành Bảo",
  "apartment_code": "A1201",
  "residential_area": "Vinhomes Ocean Park"
}

→ 200 OK
{
  "success": true,
  "data": {
    "verified": true,
    "apartment_code": "A1201",
    "residential_area": "Vinhomes Ocean Park"
  },
  "message": "Ownership verified"
}
```

Response là **trạng thái tối thiểu** — không kèm `owner_name`.

### Verify ownership — sai chủ sở hữu

```bash
POST /api/apartment-owners/verify-ownership
{"full_name": "Nguyễn Văn A", "apartment_code": "A1201", "residential_area": "Vinhomes Ocean Park"}

→ 403 Forbidden
{
  "success": false,
  "error_code": "OWNERSHIP_MISMATCH",
  "message": "Requester is not the owner of apartment A1201 in Vinhomes Ocean Park"
}
```

### Verify ownership — căn hộ không có trong ownership records

```bash
→ 404 { "success": false, "error_code": "OWNERSHIP_NOT_FOUND", ... }
```

### Register resident — KHÔNG verify ownership

```bash
POST /api/residents
{"full_name": "Nguyễn Văn A", "apartment_code": "A1201", "residential_area": "Vinhomes Ocean Park"}

→ 201 Created            ← dù "Nguyễn Văn A" KHÔNG phải chủ sở hữu A1201
{"success": true, "data": {"resident_id": "RES-001"}}
```

Đăng ký lần hai cho cùng căn hộ → `409 RESIDENT_ALREADY_EXISTS`. Đây là lỗi duy
nhất mà `register_resident` sinh ra ở nhánh nghiệp vụ.

---

## Tests

`tests/test_mock/test_ownership_verification.py` — 8 test:

| Test | Khoá hành vi |
|---|---|
| `test_verify_ownership_success` | 200, `verified: true`, payload tối thiểu |
| `test_verify_ownership_apartment_not_found` | 404 `OWNERSHIP_NOT_FOUND` |
| `test_verify_ownership_wrong_area` | 404 `OWNERSHIP_NOT_FOUND` |
| `test_verify_ownership_wrong_owner_name` | 403 `OWNERSHIP_MISMATCH` |
| `test_register_resident_independent` | register_resident KHÔNG verify ownership |
| `test_register_resident_duplicate` | 409 `RESIDENT_ALREADY_EXISTS` |
| `test_verify_ownership_response_has_no_owner_name` | regression PII |
| `test_get_apartment_owner_endpoint_removed` | endpoint tra cứu đã bị xoá |

Kết quả `tests/test_mock/`: **46 passed, 4 skipped** (các test skip cần
PostgreSQL đang chạy).

Hợp đồng "đúng 4 tool" được khoá bằng regression test `EXPECTED_TOOLS` trong
`tests/unit/test_validator.py`.

**Không** có test nào cho `PENDING`, `REJECTED`, hay VerificationGuard — vì các
thành phần đó chưa tồn tại.

---

## Security

### Đã có
- Provider trả trạng thái tối thiểu; `owner_name` không rời khỏi provider.
- Không log PII (`full_name`, `owner_name`).
- Error envelope thống nhất, message không leak tên chủ sở hữu.

### Chưa có / Week 2 trở đi
- VerificationGuard chặn workflow khi chưa VERIFIED — **chưa có**, nên hôm nay
  ownership chưa thực sự chặn được đăng ký gian lận.
- Trạng thái `PENDING` / `REJECTED` — chưa có.
- Authentication/authorization layer, audit log, rate limiting cho endpoint
  verify, mã hoá `id_number` — chưa có.

---

## Design decisions

1. **Không phải Agent tool.** Verification không bao giờ xuất hiện trong
   TaskPlan. TaskPlan giữ đúng 4 tool ở cả Week 1 và Week 2.
2. **Hub thuần.** `ResidentService` chỉ biết về resident, không biết về
   ownership. Provider ownership độc lập, không tạo resident hay vehicle.
3. **Bảng riêng.** `apartment_owners` tách khỏi `residents` cho rõ nghiệp vụ.
4. **Không pre-check ở register.** `register_resident` INSERT thẳng rồi bắt
   `UniqueViolationError` — an toàn dưới tải đồng thời.
5. **Minimal response.** Verify trả trạng thái, không trả dữ liệu chủ sở hữu.
