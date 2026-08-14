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

Gate 2 mở rộng dùng đúng 9 tool nghiệp vụ:

| Tool                           | Mô tả                                                                     |
| ------------------------------ | --------------------------------------------------------------------------- |
| `search_properties`          | Tìm và gợi ý bất động sản phù hợp; không tạo giao dịch         |
| `schedule_property_viewing`  | Đặt lịch tham quan dự án người dùng đã chọn                      |
| `register_property_interest` | Đăng ký nhu cầu tư vấn cho dự án qua account contact đã xác minh |
| `create_maintenance_request` | Tạo yêu cầu bảo trì cho căn hộ đã liên kết                       |
| `schedule_move`              | Đăng ký lịch chuyển nhà, thang máy và hỗ trợ bốc dỡ             |
| `register_resident`          | Đăng ký cư dân mới                                                    |
| `register_vehicle`           | Đăng ký phương tiện                                                   |
| `book_parking`               | Đặt chỗ đậu xe                                                         |
| `pay_fee`                    | Thanh toán phí                                                            |

- Không đổi tên.
- Compensation tool (`cancel_resident`, `refund_payment`...) không xuất hiện trong `TaskPlan` chính — chỉ được hệ thống nội bộ gọi khi rollback.

---

## 4. Internal Tool Contracts

Đây là contract nội bộ ổn định của P-118, không phải bản sao của API thật. Các field là **canonical internal fields** — Real Connector có thể map chúng sang tên field khác của API thật.

| Tool                           | Required internal input                                                                       | Internal output                                                                                                                               |
| ------------------------------ | --------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `search_properties`          | `transaction_type`, `property_type`, `residential_area`, `max_price`                  | `properties`, `result_count`                                                                                                              |
| `schedule_property_viewing`  | `project_id`, `viewing_date`, `viewing_time`                                            | `viewing_id`, `project_id`, `project_name`, `viewing_date`, `viewing_time`, `viewing_status`, `contact_name`, `contact_phone` |
| `register_property_interest` | `project_id`, `interest_type`, `preferred_contact_time`, `consent`                    | `interest_id`, `project_id`, `project_name`, `interest_status`, `contact_channel`                                                   |
| `create_maintenance_request` | `issue_type`, `description`, `location`, `preferred_date`, `preferred_time`         | `maintenance_id`, `maintenance_status`, `appointment_date`, `appointment_time`                                                        |
| `schedule_move`              | `move_date`, `move_time`, `needs_elevator`, `needs_loading_support`, `move_vehicle` | `move_request_id`, `move_status`, `move_date`, `move_time`, `elevator_slot`                                                         |
| `register_resident`          | `full_name`, `apartment_code`, `residential_area`                                       | `resident_id`                                                                                                                               |
| `register_vehicle`           | `resident_id`, `plate_number`, `vehicle_type`                                           | `vehicle_id`                                                                                                                                |
| `book_parking`               | `vehicle_id`, `booking_date`, `parking_zone`                                            | `booking_id`, `parking_zone`, `booking_date`, `amount`, `currency`                                                                  |
| `pay_fee`                    | `booking_id`, `amount`, `currency`                                                      | `payment_id`, `payment_status`                                                                                                            |

### Kiểu dữ liệu và định dạng

| Field                      | Type / Format      | Ghi chú                                                               |
| -------------------------- | ------------------ | ---------------------------------------------------------------------- |
| `transaction_type`       | enum string        | `rent`, `buy`                                                      |
| `property_type`          | enum string        | `apartment`, `room`                                                |
| `max_price`              | integer            | Ngân sách tối đa, đơn vị VND, lớn hơn 0                       |
| `property_id`            | string             | Ví dụ: PROP-001                                                      |
| `project_id`             | string             | Mã dự án từ danh sách provider, ví dụ: PRJ-001                  |
| `project_name`           | string             | Tên dự án do provider trả về                                      |
| `properties`             | list[object]       | Danh sách gợi ý đã lọc                                           |
| `result_count`           | integer            | Số kết quả tìm thấy                                               |
| `price`                  | integer            | Giá thuê/tham khảo do provider trả về, đơn vị theo`currency` |
| `bedrooms`               | integer            | Số phòng ngủ                                                        |
| `viewing_date`           | string, YYYY-MM-DD | Ngày xem nhà                                                         |
| `viewing_time`           | string, HH:MM      | Giờ xem nhà                                                          |
| `viewing_id`             | string             | Ví dụ: VIEW-001                                                      |
| `viewing_status`         | enum string        | MVP dùng`SCHEDULED`                                                 |
| `interest_type`          | enum string        | `buy`, `rent`, `consultation`                                    |
| `preferred_contact_time` | enum string        | `morning`, `afternoon`, `evening`                                |
| `consent`                | boolean            | Phải là`true`, không được Planner tự suy diễn                |
| `interest_status`        | enum string        | MVP dùng`RECEIVED`                                                  |
| `contact_name`           | string             | Tên liên hệ nghiệp vụ do provider trả về                        |
| `contact_phone`          | string             | Số liên hệ nghiệp vụ do provider trả về                         |
| `full_name`              | string             | Không rỗng                                                           |
| `apartment_code`         | string             | Ví dụ: A1201                                                         |
| `residential_area`       | string             | Tên khu đô thị giả lập                                           |
| `resident_id`            | string             | Ví dụ: RES-001                                                       |
| `plate_number`           | string             | Chuỗi biển số                                                       |
| `vehicle_type`           | enum string        | `car`, `motorcycle`                                                |
| `vehicle_id`             | string             | Ví dụ: VEH-001                                                       |
| `booking_date`           | string, YYYY-MM-DD | Ngày đặt chỗ                                                       |
| `parking_zone`           | enum string        | `ZONE_A`, `ZONE_B`                                                 |
| `booking_id`             | string             | Ví dụ: BOOK-001                                                      |
| `amount`                 | integer            | Số tiền nguyên, không âm                                          |
| `currency`               | enum string        | MVP chỉ dùng`VND`                                                  |
| `payment_id`             | string             | Ví dụ: PAY-001                                                       |
| `payment_status`         | enum string        | `PENDING`, `PAID`, `FAILED`, `REFUNDED`                        |

**Quy ước thời gian:**

- Timestamp lưu theo ISO 8601.
- Ưu tiên UTC trong database.
- UI có thể chuyển sang múi giờ người dùng.

### search_properties

```json
// Input
{
  "transaction_type": "rent",
  "property_type": "apartment",
  "residential_area": "Vinhomes Ocean Park",
  "max_price": 20000000
}

// Output (rút gọn)
{
  "properties": [
    {
      "property_id": "PROP-001",
      "title": "Căn hộ 2 phòng ngủ gần công viên",
      "price": 18000000,
      "currency": "VND"
    }
  ],
  "result_count": 1
}
```

`search_properties` là read-only. Kết quả chỉ là gợi ý; Agent không tự chọn
căn, giữ căn, đặt cọc, ký hợp đồng hoặc hoàn tất thuê/mua.

Mỗi phần tử trong `properties` chỉ gồm các field canonical:
`property_id`, `title`, `transaction_type`, `property_type`,
`residential_area`, `price`, `currency`, `bedrooms`, `contact_name`,
`contact_phone`. Connector phải loại bỏ mọi field lồng nhau khác từ provider.

### schedule_property_viewing

```json
// Input
{
  "project_id": "PRJ-001",
  "viewing_date": "2026-08-15",
  "viewing_time": "10:00"
}

// Output
{
  "viewing_id": "VIEW-001",
  "project_id": "PRJ-001",
  "project_name": "Vinhomes Sài Gòn Park",
  "viewing_date": "2026-08-15",
  "viewing_time": "10:00",
  "viewing_status": "SCHEDULED",
  "contact_name": "Minh Anh - Tư vấn",
  "contact_phone": "0900000001"
}
```

`schedule_property_viewing` đặt lịch ở cấp dự án, không phải một căn hộ cụ thể.
Không tự nối `search_properties → schedule_property_viewing`: người dùng phải
chọn `project_id` từ danh sách dự án trước khi đặt lịch.

`project_id` là khóa nội bộ. UI/chat chỉ hiển thị và nhận `project_name`;
API boundary đối chiếu tên với danh mục dự án đóng rồi đưa ID tin cậy vào
`existing_context`. Không yêu cầu người dùng biết hoặc nhập mã `PRJ-*`.

### register_property_interest

```json
// Input
{
  "project_id": "PRJ-001",
  "interest_type": "consultation",
  "preferred_contact_time": "afternoon",
  "consent": true
}

// Output
{
  "interest_id": "INT-001",
  "project_id": "PRJ-001",
  "project_name": "Vinhomes Sài Gòn Park",
  "interest_status": "RECEIVED",
  "contact_channel": "VERIFIED_ACCOUNT_CONTACT"
}
```

`register_property_interest` cũng đăng ký nhu cầu ở cấp dự án. Phone/email
không đi qua TaskPlan. Provider hoặc account directory lấy thông
tin liên hệ đã xác minh. `register_property_interest` và
`schedule_property_viewing` không phụ thuộc output của nhau nên có thể nằm
trong cùng một wave song song của DAG.

### create_maintenance_request / schedule_move

Hai tool này chỉ chạy khi account đã có resident-property mapping `VERIFIED`.
TaskPlan không chứa resident ID, apartment ID, thông tin liên hệ hay giấy tờ;
Provider lấy quan hệ căn hộ từ account đã xác minh. Hai tác vụ độc lập, có thể
chạy song song và không tự tạo `pay_fee` trong MVP.

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

| Giá trị    | Ý nghĩa                |
| ------------ | ------------------------ |
| `PENDING`  | Đang xử lý            |
| `PAID`     | Thanh toán thành công |
| `FAILED`   | Thanh toán thất bại   |
| `REFUNDED` | Đã hoàn tiền         |

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

| Field          | Ý nghĩa                                 |
| -------------- | ----------------------------------------- |
| `success`    | Tác vụ thành công hay thất bại      |
| `data`       | Output nghiệp vụ theo internal contract |
| `error_code` | Mã lỗi chuẩn hóa của P-118           |
| `message`    | Thông báo dễ đọc cho log hoặc UI    |
| `retryable`  | Lỗi có thể thử lại hay không        |

> Response gốc của API ngoài không được truyền thẳng vào Executor. Connector phải chuẩn hóa trước.

---

## 7. Workflow và Task Status

### Workflow status

| Status               | Ý nghĩa                            |
| -------------------- | ------------------------------------ |
| `PENDING`          | Chưa bắt đầu                     |
| `RUNNING`          | Đang thực thi                      |
| `WAITING_APPROVAL` | Đang chờ user xác nhận           |
| `SUCCESS`          | Hoàn thành                         |
| `FAILED`           | Thất bại không phục hồi được |
| `CANCELLED`        | Đã hủy                            |

### Task status

| Status               | Ý nghĩa                                |
| -------------------- | ---------------------------------------- |
| `PENDING`          | Chưa sẵn sàng (dependency chưa xong) |
| `READY`            | Dependency đã xong, chờ chạy         |
| `RUNNING`          | Đang thực thi                          |
| `WAITING_APPROVAL` | Đang chờ user xác nhận               |
| `SUCCESS`          | Hoàn thành                             |
| `FAILED`           | Thất bại                               |
| `SKIPPED`          | Bỏ qua                                  |
| `CANCELLED`        | Đã hủy                                |

**Không dùng:** `DONE`, `COMPLETED`, `PROCESSING`, `ERROR` — trừ khi được cập nhật vào contract này.

> Nếu API thật là bất đồng bộ hoặc cần phê duyệt ngoài hệ thống, có thể cần bổ sung trạng thái mới, nhưng phải thông qua thay đổi contract chính thức.

---

## 8. Error Codes

### Input

| Code                    | Ý nghĩa                            |
| ----------------------- | ------------------------------------ |
| `MISSING_INFORMATION` | Thiếu thông tin bắt buộc         |
| `INVALID_INPUT`       | Dữ liệu đầu vào không hợp lệ |

### Business data

| Code                        | Ý nghĩa                        |
| --------------------------- | -------------------------------- |
| `RESIDENT_NOT_FOUND`      | Không tìm thấy cư dân       |
| `RESIDENT_ALREADY_EXISTS` | Cư dân đã tồn tại          |
| `VEHICLE_NOT_FOUND`       | Không tìm thấy phương tiện |
| `VEHICLE_ALREADY_EXISTS`  | Phương tiện đã đăng ký   |
| `BOOKING_NOT_FOUND`       | Không tìm thấy đặt chỗ     |
| `PAYMENT_NOT_FOUND`       | Không tìm thấy giao dịch     |

### Booking and payment

| Code                | Ý nghĩa               |
| ------------------- | ----------------------- |
| `NO_AVAILABILITY` | Không còn chỗ trống |
| `PAYMENT_FAILED`  | Thanh toán thất bại  |

### Service

| Code                       | Ý nghĩa                                                                           |
| -------------------------- | ----------------------------------------------------------------------------------- |
| `SERVICE_TIMEOUT`        | Dịch vụ không phản hồi đúng hạn                                             |
| `SERVICE_UNAVAILABLE`    | Dịch vụ không khả dụng                                                         |
| `INTERNAL_SERVICE_ERROR` | Lỗi nội bộ của dịch vụ                                                        |
| `UNKNOWN_EXTERNAL_ERROR` | Connector nhận lỗi từ API ngoài nhưng chưa thể ánh xạ sang mã lỗi chuẩn |

**Quy tắc xử lý `UNKNOWN_EXTERNAL_ERROR`:**

- Connector phải lưu hoặc log mã lỗi gốc của API ngoài để debug.
- Executor chỉ nhận mã chuẩn `UNKNOWN_EXTERNAL_ERROR`.
- Mặc định `retryable: false`, trừ khi Connector có đủ thông tin xác định lỗi tạm thời.
- Không truyền nguyên response nhạy cảm hoặc credential vào log.

### Planning and policy

| Code                  | Ý nghĩa                              |
| --------------------- | -------------------------------------- |
| `INVALID_TASK_PLAN` | TaskPlan không hợp lệ               |
| `UNKNOWN_TOOL`      | Tool không trong allowlist            |
| `DEPENDENCY_ERROR`  | Lỗi thứ tự phụ thuộc              |
| `APPROVAL_REQUIRED` | Cần người dùng xác nhận          |
| `ACTION_DENIED`     | Hành động bị từ chối bởi Policy |

### Hành vi xử lý lỗi

| Error code                 | Hành vi                                                                |
| -------------------------- | ----------------------------------------------------------------------- |
| `NO_AVAILABILITY`        | Tìm phương án thay thế (replan)                                    |
| `SERVICE_TIMEOUT`        | Retry nếu`retryable: true`                                           |
| `SERVICE_UNAVAILABLE`    | Retry hoặc tạm dừng workflow                                         |
| `MISSING_INFORMATION`    | Hỏi người dùng bổ sung                                             |
| `APPROVAL_REQUIRED`      | Pause workflow, chờ user                                               |
| `ACTION_DENIED`          | Không thực hiện, thông báo user                                    |
| `INVALID_TASK_PLAN`      | Từ chối plan, yêu cầu lập kế hoạch lại                          |
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
- Planner có thể nhận các giá trị đã có: `property_id`, `project_id`, `resident_id`, `vehicle_id`, `booking_id`.
- Task đã hoàn thành hoặc dữ liệu đã tồn tại không được tạo lại nếu không cần thiết.
- Tool tìm bất động sản chỉ trả gợi ý; không mở rộng quyền Agent sang giao dịch.
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

|                 | Mock API                       | Real API                  |
| --------------- | ------------------------------ | ------------------------- |
| Xây dựng bởi | Nhóm P-118                    | Bên thứ ba              |
| Endpoint        | Nội bộ dự án               | Riêng của provider      |
| Field           | Theo internal contract         | Theo schema của provider |
| Auth            | Không cần hoặc đơn giản  | Token, OAuth, API key...  |
| Mục đích     | Phát triển, Gate 2, Demo Day | Production                |

> **Endpoint của mock service không phải là contract bất biến của hệ thống. Contract bất biến tương đối là internal tool contract, TaskPlan và StandardResult.**

---

## 12. Mock API Endpoints

> Mock API endpoints — chỉ dùng cho phát triển nội bộ.

| Method   | Path                                   | Mô tả                                          |
| -------- | -------------------------------------- | ------------------------------------------------ |
| `POST` | `/api/properties/search`             | Tìm bất động sản phù hợp                  |
| `POST` | `/api/projects/viewings`             | Đặt lịch tham quan dự án đã chọn         |
| `POST` | `/api/projects/interests`            | Đăng ký nhận tư vấn cho dự án đã chọn |
| `POST` | `/api/residents`                     | Đăng ký cư dân                              |
| `POST` | `/api/vehicles`                      | Đăng ký phương tiện                        |
| `POST` | `/api/parking/bookings`              | Đặt chỗ đậu xe                              |
| `POST` | `/api/payments`                      | Thanh toán phí                                 |
| `GET`  | `/api/residents/{resident_id}`       | Tra cứu cư dân                                |
| `GET`  | `/api/vehicles/{vehicle_id}`         | Tra cứu phương tiện                          |
| `GET`  | `/api/parking/bookings/{booking_id}` | Tra cứu đặt chỗ                              |
| `GET`  | `/api/payments/{payment_id}`         | Tra cứu giao dịch                              |

> Real Connector có thể gọi endpoint hoàn toàn khác. Không yêu cầu API thật phải dùng các path này.

---

## 13. HTTP Status Guideline (Mock API)

| Status  | Ý nghĩa                          |
| ------- | ---------------------------------- |
| `200` | Thành công                       |
| `201` | Tạo mới thành công             |
| `400` | Input không hợp lệ              |
| `404` | Không tìm thấy                  |
| `409` | Trùng lặp hoặc không còn chỗ |
| `500` | Lỗi nội bộ dịch vụ            |
| `503` | Dịch vụ không khả dụng        |

> Connector không dựa duy nhất vào HTTP status. Phải đọc response body và chuẩn hóa thành `StandardResult`.

---

## 14. Khi nào chỉ thay Connector là đủ

| Trường hợp                           | Chỉ thay Connector?                     |
| --------------------------------------- | ---------------------------------------- |
| API khác tên field, endpoint, auth    | ✅ Có                                   |
| API trả mã lỗi khác                 | ✅ Có                                   |
| API dùng format ngày hoặc ID khác   | ✅ Có                                   |
| API nghiệp vụ tương đương        | ✅ Thường có                          |
| API cần phê duyệt thủ công         | ⚠️ Có thể phải sửa workflow/status |
| API dùng webhook hoặc bất đồng bộ | ⚠️ Có thể phải sửa state/executor  |
| API thanh toán redirect                | ⚠️ Có thể phải sửa UI/HITL         |
| API không hỗ trợ compensation        | ⚠️ Có thể phải sửa policy/recovery |
| API yêu cầu bước nghiệp vụ mới   | ❌ Phải sửa TaskPlan/contract          |

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

| File              | Nội dung                                         | Owner                                           |
| ----------------- | ------------------------------------------------- | ----------------------------------------------- |
| `task_plan.py`  | `TaskPlan`, `Task`, `InputRef`              | Thành Bảo                                     |
| `results.py`    | `StandardResult`                                | Mạnh Hiệp                                     |
| `enums.py`      | `WorkflowStatus`, `TaskStatus`, `ErrorCode` | Mạnh Hiệp                                     |
| `repository.py` | `WorkflowStateRepository` Protocol              | Mạnh Hiệp (interface), Hoàng Anh (implement) |

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

| Contract area                         | Primary owner | Reviewers            |
| ------------------------------------- | ------------- | -------------------- |
| Tool allowlist, TaskPlan              | Người 1     | Người 2, Người 3 |
| StandardResult, task state            | Người 2     | Người 1, Người 3 |
| API request/response, business errors | Người 3     | Người 1, Người 2 |
| Data propagation                      | Người 2     | Người 1, Người 3 |
| Database schema                       | Người 3     | Người 2            |
| Policy decision                       | Người 1     | Người 2, Người 3 |
| Connector mapping                     | Người 2     | Người 3            |

Primary owner đề xuất thay đổi. Các thay đổi ảnh hưởng interface phải được nhóm duyệt.

---

## 16. Change Control

1. Không sửa contract trong code mà không cập nhật file này.
2. Khi đổi field, status hoặc error code, cập nhật: `shared_contracts.md` → schema/model → tests.
3. Thay đổi contract phải được ghi rõ trong pull request.
4. Ít nhất một thành viên khác review trước khi merge.
5. AI không được tự mở rộng contract.
6. Thay đổi endpoint riêng trong Connector không cần sửa core contract, trừ khi làm thay đổi input/output nội bộ hoặc hành vi workflow.

---

## 17. AI Usage Instruction

**English:**

> Read and follow `shared_contracts.md` as the source of truth. Treat tool names, internal fields, TaskPlan, StandardResult, statuses, and normalized error codes as fixed contracts. External API endpoints, authentication, and vendor-specific fields belong inside connectors. Do not assume that a real API must match the mock API. If the requested integration requires a business workflow that cannot be represented by the current contract, stop and report the conflict before generating code.

**Tiếng Việt:**

> Hãy đọc và tuân thủ `shared_contracts.md` như nguồn quy ước chính thức. Xem tên tool, field nội bộ, TaskPlan, StandardResult, trạng thái và mã lỗi chuẩn hóa là contract cố định. Endpoint, xác thực và field riêng của API bên ngoài phải nằm trong Connector. Không giả định API thật phải giống mock API. Nếu tích hợp yêu cầu quy trình nghiệp vụ không thể biểu diễn bằng contract hiện tại, hãy dừng và báo xung đột trước khi sinh code.
