# Shared Contracts — P-118

> Tài liệu quy ước nội bộ dùng chung cho toàn nhóm và các AI hỗ trợ phát triển.
> Mọi thay đổi phải được cập nhật tại đây trước khi merge.

---

## 1. Vai trò của Shared Contract

Shared contract là chuẩn nội bộ ổn định của P-118. Mọi thành phần trong hệ thống đều giao tiếp qua contract này, không phụ thuộc trực tiếp vào API bên ngoài.

```
Planner
→ TaskPlan nội bộ
→ Validator
→ Policy Engine
→ Executor
→ Connector
→ Mock API hoặc Real API
```

**Policy Engine** kiểm tra mỗi action trước khi Executor thực thi:
- `AUTO_ALLOWED` → Executor được phép thực thi ngay.
- `REQUIRES_APPROVAL` → workflow chuyển sang `WAITING_APPROVAL`, chờ HITL.
- `DENIED` → action không được thực thi.

> Policy không gọi API trực tiếp. Connector không thực hiện Policy.

**Connector** là ranh giới duy nhất giữa hệ thống nội bộ và API bên ngoài. Connector chịu trách nhiệm chuyển đổi giữa contract nội bộ của P-118 và request/response riêng của từng API bên ngoài.

**Ví dụ mapping:**

```json
// P-118 nội bộ
{ "resident_id": "RES-001", "plate_number": "51A-12345" }

// API thật có thể dùng
{ "customerCode": "C123", "licensePlate": "51A12345", "propertyId": "AREA-01" }
```

Connector thực hiện mapping nhưng luôn trả kết quả về chuẩn nội bộ `StandardResult`.

---

## 2. Nguyên tắc bắt buộc

- Tất cả field nội bộ dùng `snake_case`.
- Không tự đổi tên tool, field, trạng thái hoặc error code.
- Không thêm tool mới nếu chưa được nhóm duyệt.
- `TaskPlan` chỉ dùng tool trong allowlist.
- `TaskPlan` không chứa URL, token hoặc chi tiết xác thực.
- Connector là nơi xử lý endpoint, authentication, request mapping, response mapping và error mapping.
- Executor và Planner không phụ thuộc trực tiếp vào schema riêng của API bên ngoài.
- Mọi Connector phải trả về `StandardResult`.
- Nếu API ngoài không thể ánh xạ vào contract hiện tại, phải báo xung đột và đề xuất cập nhật contract — không tự sửa trong code.
- Mọi thay đổi contract phải được cập nhật trong file này trước khi merge.

---

## 3. Tool Allowlist

Chỉ dùng đúng 4 tool nghiệp vụ chính:

| Tool | Mô tả |
|---|---|
| `register_resident` | Đăng ký cư dân mới |
| `register_vehicle` | Đăng ký phương tiện |
| `book_parking` | Đặt chỗ đậu xe |
| `pay_fee` | Thanh toán phí |

- Không đổi tên.
- Compensation tool (`cancel_resident`, `refund_payment`...) không xuất hiện trong `TaskPlan` chính — chỉ được hệ thống nội bộ gọi khi rollback.

---

## 4. Internal Tool Contracts

Đây là contract nội bộ ổn định của P-118, không phải bản sao của API thật. Các field là **canonical internal fields** — Real Connector có thể map chúng sang tên field khác của API thật.

| Tool | Required internal input | Internal output |
|---|---|---|
| `register_resident` | `full_name`, `apartment_code`, `residential_area` | `resident_id` |
| `register_vehicle` | `resident_id`, `plate_number`, `vehicle_type` | `vehicle_id` |
| `book_parking` | `vehicle_id`, `booking_date`, `parking_zone` | `booking_id`, `parking_zone`, `booking_date`, `amount`, `currency` |
| `pay_fee` | `booking_id`, `amount`, `currency` | `payment_id`, `payment_status` |

### Kiểu dữ liệu và định dạng

| Field | Type / Format | Ghi chú |
|---|---|---|
| `full_name` | string | Không rỗng |
| `apartment_code` | string | Ví dụ: A1201 |
| `residential_area` | string | Tên khu đô thị giả lập |
| `resident_id` | string | Ví dụ: RES-001 |
| `plate_number` | string | Chuỗi biển số |
| `vehicle_type` | enum string | `car`, `motorcycle` |
| `vehicle_id` | string | Ví dụ: VEH-001 |
| `booking_date` | string, YYYY-MM-DD | Ngày đặt chỗ |
| `parking_zone` | enum string | `ZONE_A`, `ZONE_B` |
| `booking_id` | string | Ví dụ: BOOK-001 |
| `amount` | integer | Số tiền nguyên, không âm |
| `currency` | enum string | MVP chỉ dùng `VND` |
| `payment_id` | string | Ví dụ: PAY-001 |
| `payment_status` | enum string | `PENDING`, `PAID`, `FAILED`, `REFUNDED` |

**Quy ước thời gian:**
- Timestamp lưu theo ISO 8601.
- Ưu tiên UTC trong database.
- UI có thể chuyển sang múi giờ người dùng.

---

### register_resident

```json
// Input
{
  "full_name": "Lâm Thành Bảo",
  "apartment_code": "A1201",
  "residential_area": "Vinhomes Ocean Park"
}

// Output (trong StandardResult.data)
{
  "resident_id": "RES-001"
}
```

### register_vehicle

```json
// Input
{
  "resident_id": "RES-001",
  "plate_number": "51A-12345",
  "vehicle_type": "car"
}

// Output
{
  "vehicle_id": "VEH-001"
}
```

### book_parking

```json
// Input
{
  "vehicle_id": "VEH-001",
  "booking_date": "2026-08-10",
  "parking_zone": "ZONE_A"
}

// Output
{
  "booking_id": "BOOK-001",
  "parking_zone": "ZONE_A",
  "booking_date": "2026-08-10",
  "amount": 150000,
  "currency": "VND"
}
```

### pay_fee

```json
// Input
{
  "booking_id": "BOOK-001",
  "amount": 150000,
  "currency": "VND"
}

// Output
{
  "payment_id": "PAY-001",
  "payment_status": "PAID"
}
```

**Giá trị hợp lệ của `payment_status`:**

| Giá trị | Ý nghĩa |
|---|---|
| `PENDING` | Đang xử lý |
| `PAID` | Thanh toán thành công |
| `FAILED` | Thanh toán thất bại |
| `REFUNDED` | Đã hoàn tiền |

- `payment_status` là trạng thái nghiệp vụ thanh toán, khác với task/workflow status.
- Không dùng `SUCCESS` cho `payment_status` — `SUCCESS` chỉ dùng cho task/workflow status.
- Khi `pay_fee` thành công, output mặc định là `PAID`.

---

## 5. TaskPlan Contract

### Cấu trúc

```json
{
  "goal": "Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư dân, đăng ký xe biển số 51A-12345, đặt chỗ tại ZONE_A ngày 2026-08-10 và thanh toán phí.",
  "tasks": [
    {
      "task_id": "T1",
      "tool": "register_resident",
      "depends_on": [],
      "input": {
        "full_name": "Lâm Thành Bảo",
        "apartment_code": "A1201",
        "residential_area": "Vinhomes Ocean Park"
      }
    },
    {
      "task_id": "T2",
      "tool": "register_vehicle",
      "depends_on": ["T1"],
      "input": {
        "resident_id": { "from_task": "T1", "field": "resident_id" },
        "plate_number": "51A-12345",
        "vehicle_type": "car"
      }
    },
    {
      "task_id": "T3",
      "tool": "book_parking",
      "depends_on": ["T2"],
      "input": {
        "vehicle_id": { "from_task": "T2", "field": "vehicle_id" },
        "booking_date": "2026-08-10",
        "parking_zone": "ZONE_A"
      }
    },
    {
      "task_id": "T4",
      "tool": "pay_fee",
      "depends_on": ["T3"],
      "input": {
        "booking_id": { "from_task": "T3", "field": "booking_id" },
        "amount": { "from_task": "T3", "field": "amount" },
        "currency": { "from_task": "T3", "field": "currency" }
      }
    }
  ]
}
```

### Quy tắc

- `task_id` phải duy nhất.
- `tool` phải thuộc allowlist.
- `depends_on` chỉ tham chiếu `task_id` tồn tại.
- Không có dependency cycle.
- Không chứa endpoint, URL, token, header hoặc credential.
- Dữ liệu từ task trước tham chiếu bằng `{ "from_task": "TX", "field": "field_name" }`.

> `TaskPlan` mô tả ý định nghiệp vụ nội bộ, không mô tả chi tiết cách gọi API bên ngoài.

---

## 6. StandardResult Contract

Mọi Connector phải trả về đúng cấu trúc này:

```json
// Thành công
{
  "success": true,
  "data": {
    "resident_id": "RES-001"
  },
  "error_code": null,
  "message": "Resident registered successfully",
  "retryable": false
}

// Thất bại — NO_AVAILABILITY
{
  "success": false,
  "data": null,
  "error_code": "NO_AVAILABILITY",
  "message": "Parking Zone A (ZONE_A) is full",
  "retryable": false
}
```

| Field | Ý nghĩa |
|---|---|
| `success` | Tác vụ thành công hay thất bại |
| `data` | Output nghiệp vụ theo internal contract |
| `error_code` | Mã lỗi chuẩn hóa của P-118 |
| `message` | Thông báo dễ đọc cho log hoặc UI |
| `retryable` | Lỗi có thể thử lại hay không |

> Response gốc của API ngoài không được truyền thẳng vào Executor. Connector phải chuẩn hóa trước.

---

## 7. Workflow và Task Status

### Workflow status

| Status | Ý nghĩa |
|---|---|
| `PENDING` | Chưa bắt đầu |
| `RUNNING` | Đang thực thi |
| `WAITING_APPROVAL` | Đang chờ user xác nhận |
| `SUCCESS` | Hoàn thành |
| `FAILED` | Thất bại không phục hồi được |
| `CANCELLED` | Đã hủy |

### Task status

| Status | Ý nghĩa |
|---|---|
| `PENDING` | Chưa sẵn sàng (dependency chưa xong) |
| `READY` | Dependency đã xong, chờ chạy |
| `RUNNING` | Đang thực thi |
| `WAITING_APPROVAL` | Đang chờ user xác nhận |
| `SUCCESS` | Hoàn thành |
| `FAILED` | Thất bại |
| `SKIPPED` | Bỏ qua |
| `CANCELLED` | Đã hủy |

**Không dùng:** `DONE`, `COMPLETED`, `PROCESSING`, `ERROR` — trừ khi được cập nhật vào contract này.

> Nếu API thật là bất đồng bộ hoặc cần phê duyệt ngoài hệ thống, có thể cần bổ sung trạng thái mới, nhưng phải thông qua thay đổi contract chính thức.

---

## 8. Error Codes

### Input
| Code | Ý nghĩa |
|---|---|
| `MISSING_INFORMATION` | Thiếu thông tin bắt buộc |
| `INVALID_INPUT` | Dữ liệu đầu vào không hợp lệ |

### Business data
| Code | Ý nghĩa |
|---|---|
| `RESIDENT_NOT_FOUND` | Không tìm thấy cư dân |
| `RESIDENT_ALREADY_EXISTS` | Cư dân đã tồn tại |
| `VEHICLE_NOT_FOUND` | Không tìm thấy phương tiện |
| `VEHICLE_ALREADY_EXISTS` | Phương tiện đã đăng ký |
| `BOOKING_NOT_FOUND` | Không tìm thấy đặt chỗ |
| `PAYMENT_NOT_FOUND` | Không tìm thấy giao dịch |

### Booking and payment
| Code | Ý nghĩa |
|---|---|
| `NO_AVAILABILITY` | Không còn chỗ trống |
| `PAYMENT_FAILED` | Thanh toán thất bại |

### Service
| Code | Ý nghĩa |
|---|---|
| `SERVICE_TIMEOUT` | Dịch vụ không phản hồi đúng hạn |
| `SERVICE_UNAVAILABLE` | Dịch vụ không khả dụng |
| `INTERNAL_SERVICE_ERROR` | Lỗi nội bộ của dịch vụ |
| `UNKNOWN_EXTERNAL_ERROR` | Connector nhận lỗi từ API ngoài nhưng chưa thể ánh xạ sang mã lỗi chuẩn |

**Quy tắc xử lý `UNKNOWN_EXTERNAL_ERROR`:**
- Connector phải lưu hoặc log mã lỗi gốc của API ngoài để debug.
- Executor chỉ nhận mã chuẩn `UNKNOWN_EXTERNAL_ERROR`.
- Mặc định `retryable: false`, trừ khi Connector có đủ thông tin xác định lỗi tạm thời.
- Không truyền nguyên response nhạy cảm hoặc credential vào log.

### Planning and policy
| Code | Ý nghĩa |
|---|---|
| `INVALID_TASK_PLAN` | TaskPlan không hợp lệ |
| `UNKNOWN_TOOL` | Tool không trong allowlist |
| `DEPENDENCY_ERROR` | Lỗi thứ tự phụ thuộc |
| `APPROVAL_REQUIRED` | Cần người dùng xác nhận |
| `ACTION_DENIED` | Hành động bị từ chối bởi Policy |

### Hành vi xử lý lỗi

| Error code | Hành vi |
|---|---|
| `NO_AVAILABILITY` | Tìm phương án thay thế (replan) |
| `SERVICE_TIMEOUT` | Retry nếu `retryable: true` |
| `SERVICE_UNAVAILABLE` | Retry hoặc tạm dừng workflow |
| `MISSING_INFORMATION` | Hỏi người dùng bổ sung |
| `APPROVAL_REQUIRED` | Pause workflow, chờ user |
| `ACTION_DENIED` | Không thực hiện, thông báo user |
| `INVALID_TASK_PLAN` | Từ chối plan, yêu cầu lập kế hoạch lại |
| `UNKNOWN_EXTERNAL_ERROR` | Dừng tác vụ, ghi log đã làm sạch và yêu cầu kiểm tra mapping |

> Connector chịu trách nhiệm map mã lỗi riêng của API ngoài sang error code chuẩn này.

---

## 9. Data Propagation

```
register_resident.resident_id  →  register_vehicle.resident_id
register_vehicle.vehicle_id    →  book_parking.vehicle_id
book_parking.booking_id        →  pay_fee.booking_id
book_parking.amount            →  pay_fee.amount
book_parking.currency          →  pay_fee.currency
```

**Quy tắc:**

- Output lấy từ `StandardResult.data`.
- Task sau chỉ chạy khi tất cả dependency có status `SUCCESS`.
- User không nhập lại dữ liệu đã có từ bước trước.
- Task đã `SUCCESS` không chạy lại khi replan.
- Connector không được thay đổi ý nghĩa nghiệp vụ của dữ liệu nội bộ.

### Existing Context cho Partial Goals

Người dùng không phải lúc nào cũng chạy đủ 4 tác vụ. Với yêu cầu một phần, hệ thống có thể dùng dữ liệu đã tồn tại trong hồ sơ nội bộ hoặc database.

**Quy tắc:**
- Nếu đã có `resident_id` → không chạy lại `register_resident`.
- Nếu đã có `vehicle_id` → không chạy lại `register_vehicle`.
- Nếu đã có `booking_id` → có thể chỉ chạy `pay_fee`.
- Existing context được nạp trước khi lập hoặc thực thi TaskPlan.
- Planner có thể nhận các giá trị đã có: `resident_id`, `vehicle_id`, `booking_id`.
- Task đã hoàn thành hoặc dữ liệu đã tồn tại không được tạo lại nếu không cần thiết.
- Gate 2 chưa cần thêm tool tra cứu mới vào allowlist.
- Nếu dữ liệu cần thiết chưa tồn tại, Agent phải hỏi người dùng hoặc thêm tác vụ hợp lệ để tạo dữ liệu đó.
- Không được tự đoán ID.

**Ví dụ:**

```
User goal: "Đặt chỗ cho xe của tôi ngày mai."

Existing context:
{
  "resident_id": "RES-001",
  "vehicle_id": "VEH-001"
}

TaskPlan: chỉ gồm book_parking.

Nếu người dùng yêu cầu đặt chỗ và thanh toán,
TaskPlan gồm book_parking → pay_fee.

Không thêm register_resident hoặc register_vehicle.
```

---

## 10. Connector Contract

Mỗi Connector chịu trách nhiệm:

1. Nhận input theo internal tool contract.
2. Map field nội bộ sang request của API đích.
3. Thêm endpoint, authentication, headers và timeout.
4. Gọi API bằng HTTP hoặc giao thức phù hợp.
5. Nhận response gốc.
6. Map response về internal output.
7. Map lỗi về error_code chuẩn.
8. Trả `StandardResult`.
9. Không làm thay Planner, Policy hoặc Executor.

**Pseudo-flow:**

```
book_parking internal input
→ TransportConnector.execute("book_parking", input_data)
→ POST /api/parking/bookings
→ Mock API raw JSON response
→ TransportConnector parse và validate
→ StandardResult object
→ Executor
```

> `TransportConnector` route dựa trên `tool_name`: `register_vehicle` → `POST /api/vehicles`, `book_parking` → `POST /api/parking/bookings`. Không tạo thêm `ParkingConnector`.

---

## 11. Mock API và Real API

| | Mock API | Real API |
|---|---|---|
| Xây dựng bởi | Nhóm P-118 | Bên thứ ba |
| Endpoint | Nội bộ dự án | Riêng của provider |
| Field | Theo internal contract | Theo schema của provider |
| Auth | Không cần hoặc đơn giản | Token, OAuth, API key... |
| Mục đích | Phát triển, Gate 2, Demo Day | Production |

> **Endpoint của mock service không phải là contract bất biến của hệ thống. Contract bất biến tương đối là internal tool contract, TaskPlan và StandardResult.**

---

## 11b. Demo Services (đặt lịch tham quan / đặt xe / tư vấn) — Gate 3

> Thêm 14/08/2026 bởi Hoàng Anh. 3 dịch vụ demo **chưa nối vào AI workflow engine**
> (Planner/Executor/Connector). KHÔNG thêm vào Tool Allowlist §3 — chỉ mock dữ liệu
> + DB để demo. Khi tích hợp sau phải qua §17 Change Control.

### 11b.1 Tool & nội dung hợp đồng

| Tool (định danh demo) | Mô tả | Required input | Output |
|---|---|---|---|
| `book_tour` | Đặt lịch tham quan dự án căn hộ | `residential_area`, `tour_date`, `tour_slot` | `tour_id` |
| `book_shuttle` | Đặt xe tham quan căn hộ | `tour_id`, `tour_date`, `passenger_count` | `shuttle_id` |
| `register_consultation` | Đăng ký tư vấn (mua/thuê) | `consultation_type`, `buy_sub_type?` | `consultation_id` |

- `tour_slot`: enum `MORNING` | `AFTERNOON`.
- `consultation_type`: `BUY` (tư vấn mua) | `RENT` (tư vấn thuê).
- `buy_sub_type` (chỉ khi `BUY`, bắt buộc): `RESIDE` (ở) | `BUSINESS` (kinh doanh) | `INVEST` (đầu tư).
- `passenger_count`: integer 1–30; sức chứa xe tham quan 30 khách/ngày.
- `resident_id` là tuỳ chọn trên cả 3 tool — NULL = khách (chưa là cư dân).
- Sức chứa slot tham quan (cấu hình `tour_slot_config`, seed mặc định 3/slot):
  mỗi cư dân 1 đặt lịch/slot; sức chứa slot là guard chính, khách cũng đếm.

### 11b.2 Endpoint (Mock API)

| Method | Path | Tool | 201 data |
|---|---|---|---|
| `POST` | `/api/tours/bookings` | `book_tour` | `{tour_id, residential_area, tour_date, tour_slot}` |
| `GET` | `/api/tours/bookings/{tour_id}` | — | `{tour_id, residential_area, tour_date, tour_slot}` |
| `POST` | `/api/shuttles/bookings` | `book_shuttle` | `{shuttle_id, tour_id, tour_date, passenger_count}` |
| `GET` | `/api/shuttles/bookings/{shuttle_id}` | — | `{shuttle_id, tour_id, tour_date, passenger_count}` |
| `POST` | `/api/consultations` | `register_consultation` | `{consultation_id, consultation_type, buy_sub_type}` |
| `GET` | `/api/consultations/{consultation_id}` | — | `{consultation_id, consultation_type, buy_sub_type}` |

### 11b.3 Error codes (mock-string space)

> Các mã này **không** nằm trong enum `ErrorCode` của `src/common/enums.py`
> (sở hữu Mạnh Hiệp). Định nghĩa riêng trong mock layer + map theo bảng dưới.
> `NO_AVAILABILITY` (đã có trong contract) dùng cho mọi "hết chỗ" thật.

| Code | HTTP | Nghĩa |
|---|---|---|
| `TOUR_SLOT_NOT_FOUND` | 404 | Khu + khung giờ không được offer |
| `TOUR_NOT_FOUND` | 404 | Lịch tham quan không tồn tại |
| `TOUR_ALREADY_BOOKED` | 409 | Cư dân đặt trùng (resident_id, tour_date, tour_slot) |
| `SLOT_FULL` | 409 | Chỉ dùng khi inject `?fail=` (mock) — thật dùng `NO_AVAILABILITY` |
| `SHUTTLE_NOT_FOUND` | 404 | Xe tham quan không tồn tại |
| `SHUTTLE_ALREADY_BOOKED` | 409 | Lịch tham quan đã có xe |
| `CONSULTATION_NOT_FOUND` | 404 | Đăng ký tư vấn không tồn tại |
| `CONSULTATION_ALREADY_EXISTS` | 409 | Cư dân đã đăng ký loại tư vấn này |
| `RESIDENT_NOT_FOUND` | 404 | `resident_id` tham chiếu không tồn tại (dùng chung §8) |

### 11b.4 Hai lớp triển khai (deviation có chủ đích)

| Lớp | Cross-check | Ví dụ |
|---|---|---|
| Provider độc lập (8005–8007, docker) | **Không** — hub thuần | `book_shuttle` không verify `tour_id` tồn tại (giống standalone `pay_fee` không verify `booking_id`) |
| Monolith (`src.mock.main:app`, 8010) | **Có** | `book_shuttle` verify `tour_id`; `book_tour`/`register_consultation` verify `resident_id` |

Khác biệt này cố ý giống cách standalone `payment.py` bỏ qua `booking_id` trong khi
monolith `payments.py` check — do mỗi provider độc lập không có dữ liệu của provider khác.

---

## 12. Mock API Endpoints

> Mock API endpoints — chỉ dùng cho phát triển nội bộ.

| Method | Path | Mô tả |
|---|---|---|
| `POST` | `/api/residents` | Đăng ký cư dân |
| `POST` | `/api/vehicles` | Đăng ký phương tiện |
| `POST` | `/api/parking/bookings` | Đặt chỗ đậu xe |
| `POST` | `/api/payments` | Thanh toán phí |
| `POST` | `/api/tours/bookings` | Đặt lịch tham quan dự án (demo §11b) |
| `POST` | `/api/shuttles/bookings` | Đặt xe tham quan (demo §11b) |
| `POST` | `/api/consultations` | Đăng ký tư vấn (demo §11b) |
| `GET` | `/api/residents/{resident_id}` | Tra cứu cư dân |
| `GET` | `/api/vehicles/{vehicle_id}` | Tra cứu phương tiện |
| `GET` | `/api/parking/bookings/{booking_id}` | Tra cứu đặt chỗ |
| `GET` | `/api/payments/{payment_id}` | Tra cứu giao dịch |
| `GET` | `/api/tours/bookings/{tour_id}` | Tra cứu lịch tham quan (demo §11b) |
| `GET` | `/api/shuttles/bookings/{shuttle_id}` | Tra cứu xe tham quan (demo §11b) |
| `GET` | `/api/consultations/{consultation_id}` | Tra cứu đăng ký tư vấn (demo §11b) |

> Real Connector có thể gọi endpoint hoàn toàn khác. Không yêu cầu API thật phải dùng các path này.

---

## 13. HTTP Status Guideline (Mock API)

| Status | Ý nghĩa |
|---|---|
| `200` | Thành công |
| `201` | Tạo mới thành công |
| `400` | Input không hợp lệ |
| `404` | Không tìm thấy |
| `409` | Trùng lặp hoặc không còn chỗ |
| `500` | Lỗi nội bộ dịch vụ |
| `503` | Dịch vụ không khả dụng |

> Connector không dựa duy nhất vào HTTP status. Phải đọc response body và chuẩn hóa thành `StandardResult`.

---

## 14. Khi nào chỉ thay Connector là đủ

| Trường hợp | Chỉ thay Connector? |
|---|---|
| API khác tên field, endpoint, auth | ✅ Có |
| API trả mã lỗi khác | ✅ Có |
| API dùng format ngày hoặc ID khác | ✅ Có |
| API nghiệp vụ tương đương | ✅ Thường có |
| API cần phê duyệt thủ công | ⚠️ Có thể phải sửa workflow/status |
| API dùng webhook hoặc bất đồng bộ | ⚠️ Có thể phải sửa state/executor |
| API thanh toán redirect | ⚠️ Có thể phải sửa UI/HITL |
| API không hỗ trợ compensation | ⚠️ Có thể phải sửa policy/recovery |
| API yêu cầu bước nghiệp vụ mới | ❌ Phải sửa TaskPlan/contract |

> **Kiến trúc giúp cô lập phần lớn khác biệt trong Connector, nhưng không đảm bảo mọi thay đổi nghiệp vụ đều chỉ cần thay Connector.**

---

## 14b. Shared Schema Location

Schema dùng chung được tách thành các file riêng để tránh Git conflict:

```
src/common/
├── task_plan.py      ← TaskPlan, Task, InputRef          [Thành Bảo]
├── results.py        ← StandardResult                    [Mạnh Hiệp]
├── enums.py          ← WorkflowStatus, TaskStatus, ErrorCode  [Mạnh Hiệp]
├── repository.py     ← WorkflowStateRepository Protocol  [Mạnh Hiệp]
└── __init__.py       ← Export chung (cập nhật khi tích hợp)
```

| File | Nội dung | Owner |
|---|---|---|
| `task_plan.py` | `TaskPlan`, `Task`, `InputRef` | Thành Bảo |
| `results.py` | `StandardResult` | Mạnh Hiệp |
| `enums.py` | `WorkflowStatus`, `TaskStatus`, `ErrorCode` | Mạnh Hiệp |
| `repository.py` | `WorkflowStateRepository` Protocol | Mạnh Hiệp (interface), Hoàng Anh (implement) |

**Import chuẩn:**
```python
from src.common import TaskPlan, Task, InputRef
from src.common import StandardResult, WorkflowStatus, TaskStatus, ErrorCode
from src.common import WorkflowStateRepository
```

> Tuần 1 có thể import trực tiếp từ file cụ thể nếu `__init__.py` chưa export đủ. Không định nghĩa lại schema trong `src/agents/`, `src/executor/`, `src/connectors/`, `src/db/` hoặc Mock API.

**Nguyên tắc phát triển song song:**
- Module chỉ phụ thuộc vào interface và contract, không phụ thuộc implementation của module khác.
- Unit test dùng fake hoặc in-memory implementation thay cho module thật.
- Không chờ API, database, Planner hoặc Executor thật để viết unit test.
- Integration test cuối tuần mới thay fake bằng implementation thật.

---

## 14c. WorkflowStateRepository Interface

Định nghĩa trong `src/common/repository.py`. Mạnh Hiệp viết interface, Hoàng Anh implement `PostgreSQLWorkflowStateRepository`:

```python
from typing import Protocol
from src.common.enums import WorkflowStatus, TaskStatus
from src.common.results import StandardResult

class WorkflowStateRepository(Protocol):
    async def create_workflow(self, workflow_data: dict) -> str: ...
    async def update_workflow_status(self, workflow_id: str, status: WorkflowStatus) -> None: ...
    async def create_task(self, workflow_id: str, task_data: dict) -> None: ...
    async def update_task_status(self, workflow_id: str, task_id: str, status: TaskStatus) -> None: ...
    async def save_task_result(self, workflow_id: str, task_id: str, result: StandardResult) -> None: ...
    async def get_workflow(self, workflow_id: str) -> dict: ...
```

**Quy tắc:**
- `create_workflow()` tạo và trả `workflow_id` (UUID string).
- `task_id` lấy từ `TaskPlan` — repository không tạo task ID mới.
- Executor gọi repository sau mỗi task — không viết SQL trực tiếp.
- Repository không quyết định thứ tự task hoặc điều khiển execution flow.
- Mạnh Hiệp dùng `InMemoryWorkflowStateRepository` khi unit test.
- Hoàng Anh implement `PostgreSQLWorkflowStateRepository` tại `src/db/postgres_repository.py`.
- Executor inject implementation qua dependency injection, không biết SQL.

---

## 15. Module Ownership

| Contract area | Primary owner | Reviewers |
|---|---|---|
| Tool allowlist, TaskPlan | Người 1 | Người 2, Người 3 |
| StandardResult, task state | Người 2 | Người 1, Người 3 |
| API request/response, business errors | Người 3 | Người 1, Người 2 |
| Data propagation | Người 2 | Người 1, Người 3 |
| Database schema | Người 3 | Người 2 |
| Policy decision | Người 1 | Người 2, Người 3 |
| Connector mapping | Người 2 | Người 3 |

Primary owner đề xuất thay đổi. Các thay đổi ảnh hưởng interface phải được nhóm duyệt.

---

## 16. Auth (đăng nhập / đăng ký / phân quyền)

> Thêm 14/08/2026 bởi Hoàng Anh. Extension vào contract API (đã thông báo qua
> PR — mọi thay đổi interface auth phải cập nhật mục này theo §Change Control).

### Vai trò (RBAC)

Hai vai trò, lưu cột `users.role`:

| Role | Tạo bằng | Quyền |
|---|---|---|
| `resident` | `POST /auth/register` (mặc định) | Mọi endpoint nghiệp vụ `/api/v1` |
| `admin` | `scripts/create_admin.py` (manual) | Như resident + (tương lai) endpoint quản trị |

Mọi endpoint nghiệp vụ hiện tại yêu cầu đăng nhập (Bearer token). Không có
endpoint admin-only trong phạm vi hiện tại — `require_roles("admin")` là hook
sẵn cho sau Demo Day.

### Endpoint

| Method | Path | Body | Response | Auth |
|---|---|---|---|---|
| POST | `/api/v1/auth/register` | `{username, password, email?}` | `201 UserResponse` / `409` / `422` | — |
| POST | `/api/v1/auth/login` | `{username, password}` | `200 TokenResponse` / `401` / `422` | — |
| GET | `/api/v1/auth/me` | — | `200 UserResponse` / `401` | Bearer |

**UserResponse:** `{id, username, email, role, created_at}` — KHÔNG chứa
`password_hash`.
**TokenResponse:** `{access_token, token_type:"bearer", expires_in, user}`.

### Bảo mật / quy ước

- Password hash: stdlib `hashlib.scrypt` (salt 16B mỗi user), lưu
  `users.password_hash` dạng `scrypt:N:r:p:salt_b64:hash_b64`. Không lưu
  plaintext; không trả hash qua API.
- Access token: JWT-shaped HS256 (header.payload.signature) tự dựng bằng
  stdlib (`hmac` + `hashlib` + `base64`). Payload: `sub` (user UUID),
  `username`, `role`, `iat`, `exp`. TTL mặc định 24h (`JWT_EXPIRE_MINUTES`);
  không refresh token.
- `JWT_SECRET` bắt buộc trong `.env` (rỗng → tạo token 500). Không commit
  secret thật; `.env.example` chỉ có placeholder.
- Trả về: `401` thiếu/sai/hết hạn token, sai username/password (cùng message
  chống username enumeration); `403` role không đủ (`require_roles`);
  `409` username trùng khi register; `503` runtime/user repo chưa khởi tạo.
- Username chuẩn hoá lowercase ở register/login (không cho `Admin`/`admin`
  cùng tồn tại).

### Bảo vệ route

| Route | Auth |
|---|---|
| `/health` | public |
| `/api/v1/auth/*` | register/login public; `/me` Bearer |
| `/api/v1/chat`, `/api/v1/status` | Bearer (mọi role) |
| `/api/v1/workflow/*` | Bearer (mọi role) |

---

## 17. Change Control

1. Không sửa contract trong code mà không cập nhật file này.
2. Khi đổi field, status hoặc error code, cập nhật: `shared_contracts.md` → schema/model → tests.
3. Thay đổi contract phải được ghi rõ trong pull request.
4. Ít nhất một thành viên khác review trước khi merge.
5. AI không được tự mở rộng contract.
6. Thay đổi endpoint riêng trong Connector không cần sửa core contract, trừ khi làm thay đổi input/output nội bộ hoặc hành vi workflow.

---

## 18. AI Usage Instruction

**English:**

> Read and follow `shared_contracts.md` as the source of truth. Treat tool names, internal fields, TaskPlan, StandardResult, statuses, and normalized error codes as fixed contracts. External API endpoints, authentication, and vendor-specific fields belong inside connectors. Do not assume that a real API must match the mock API. If the requested integration requires a business workflow that cannot be represented by the current contract, stop and report the conflict before generating code.

**Tiếng Việt:**

> Hãy đọc và tuân thủ `shared_contracts.md` như nguồn quy ước chính thức. Xem tên tool, field nội bộ, TaskPlan, StandardResult, trạng thái và mã lỗi chuẩn hóa là contract cố định. Endpoint, xác thực và field riêng của API bên ngoài phải nằm trong Connector. Không giả định API thật phải giống mock API. Nếu tích hợp yêu cầu quy trình nghiệp vụ không thể biểu diễn bằng contract hiện tại, hãy dừng và báo xung đột trước khi sinh code.
