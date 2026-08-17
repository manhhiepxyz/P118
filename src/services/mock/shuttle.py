"""Mock Shuttle provider — FastAPI app độc lập (tool `book_shuttle`).

Port: 8009.

Theo cấu trúc system design (`01-high-level-architecture.md`): mock provider
độc lập theo domain, implement đúng contract riêng. Đây là service đặt xe
(shuttle) đưa khách đi tham quan dự án căn hộ, đi theo chuỗi
schedule_property_viewing → book_shuttle (đã có contract chính thức).

Quy tắc mock:
- Sức chứa xe theo ngày: tối đa 30 khách/ngày (SHUTTLE_DAILY_CAPACITY).
  Vượt → 409 NO_AVAILABILITY.
- Một lịch tham quan (viewing_id) chỉ đặt được 1 xe → 409 SHUTTLE_ALREADY_BOOKED.
- Đặt xe "xử lý" ~30 giây (SHUTTLE_BOOKING_DELAY_SECONDS) trước khi thành công
  — giả lập thời gian điều phối xe. Test monkeypatch về 0 để chạy nhanh.
- Xác nhận xe gồm 4 thông tin tài xế deterministic từ shuttle_id + số khách:
  driver_name / license_plate / vehicle_type / pickup_time (provider chéo không
  biết viewing_time nên tự lấy giờ đón từ roster cố định của mình).

KHÁC với src/mock/ (single app, có cross-check): provider này KHÔNG check
`viewing_id` tồn tại trong Tour provider — đó là dữ liệu của provider khác
(hub thuần, giống payment.py không check booking_id). HUB orchestrate truyền
`viewing_id` + `tour_date` đã verify vào input.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, install_error_handler, not_found
from src.mock.ids import make_generator
from src.mock.store import Store

shuttle_app = FastAPI(
    title="P-118 Shuttle Mock Provider",
    description="Dịch vụ giả lập đặt xe tham quan — tool book_shuttle.",
    version="0.1.0",
)

shuttle_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(shuttle_app)

# Store riêng của provider này — KHÔNG dùng singleton src.mock.store.store.
store = Store()

new_shuttle_id = make_generator("SHUTTLE")

# Sức chứa xe tham quan tối đa mỗi ngày (giả lập).
SHUTTLE_DAILY_CAPACITY = 30

# Đặt xe "xử lý" ~30 giây trước khi đặt thành công. Test monkeypatch về 0.
SHUTTLE_BOOKING_DELAY_SECONDS = 30

# Roster tài xế deterministic: xe nào về tay tài xế nào được xoay vòng theo
# shuttle_id, không phụ thuộc LLM. `driver_name` là TÊN RÚT GỌN cho màn demo —
# không phải PII thật, không để đâu khác ngoài booking được tạo.
_DRIVER_ROSTER = [
    ("Anh Tuấn", "29A-456.78"),
    ("Anh Hùng", "30A-123.45"),
    ("Anh Minh", "43A-789.01"),
    ("Anh Đức", "51B-234.56"),
]

# Giờ đón cố định theo roster — provider chéo KHÔNG biết `viewing_time` của
# Tour nên không bắt nó "đoán" đúng giờ tham quan; giờ này là giá trị mock.
_PICKUP_TIMES = ["07:30", "08:00", "09:15", "10:45", "13:00", "14:30", "15:45"]


def _shuttle_details(shuttle_id: str, passenger_count: int) -> dict[str, str]:
    """4 thông tin tài xế deterministic từ shuttle_id + số khách.

    `shuttle_id` có dạng `SHUTTLE-001` — phần số làm index xoay vòng roster.
    `vehicle_type` theo bậc số khách: ≤7 → 7 chỗ, ≤16 → 16 chỗ, còn lại → 30 chỗ.
    """
    try:
        seq = int(shuttle_id.rsplit("-", 1)[-1])
    except ValueError:
        seq = 1
    driver_name, license_plate = _DRIVER_ROSTER[(seq - 1) % len(_DRIVER_ROSTER)]
    if passenger_count <= 7:
        vehicle_type = "Ô tô 7 chỗ"
    elif passenger_count <= 16:
        vehicle_type = "Ô tô 16 chỗ"
    else:
        vehicle_type = "Ô tô 30 chỗ"
    pickup_time = _PICKUP_TIMES[(seq - 1) % len(_PICKUP_TIMES)]
    return {
        "driver_name": driver_name,
        "license_plate": license_plate,
        "vehicle_type": vehicle_type,
        "pickup_time": pickup_time,
    }


@shuttle_app.post("/api/shuttles/bookings", status_code=201, summary="Đặt xe tham quan")
async def book_shuttle(
    payload: schemas.BookShuttleRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    tour_date = payload.tour_date.isoformat()

    # KHÔNG check viewing_id tồn tại — Tour là provider khác (cross-provider).
    # HUB orchestrate truyền viewing_id đã verify vào input của task này.
    current_load = store.shuttle_load.get(tour_date, 0)
    if current_load + payload.passenger_count > SHUTTLE_DAILY_CAPACITY:
        raise conflict(
            "NO_AVAILABILITY",
            f"Shuttle capacity exceeded on {tour_date} ({SHUTTLE_DAILY_CAPACITY} passengers/day)",
        )

    if any(b["viewing_id"] == payload.viewing_id for b in store.shuttle_bookings.values()):
        raise conflict(
            "SHUTTLE_ALREADY_BOOKED",
            f"Shuttle already booked for viewing {payload.viewing_id}",
        )

    # Giả lập thời gian điều phối xe: ~30 giây rồi mới đặt thành công. Đặt SAU
    # validation (hết chỗ/đặt trùng trả lỗi NGAY) và TRƯỚC khi ghi store (không
    # có lịch nào được giữ trong khoảng chờ này).
    await asyncio.sleep(SHUTTLE_BOOKING_DELAY_SECONDS)

    shuttle_id = new_shuttle_id()
    driver = _shuttle_details(shuttle_id, payload.passenger_count)
    with store._lock:
        store.shuttle_bookings[shuttle_id] = {
            "shuttle_id": shuttle_id,
            "viewing_id": payload.viewing_id,
            "tour_date": tour_date,
            "passenger_count": payload.passenger_count,
            **driver,
        }
        store.shuttle_load[tour_date] = current_load + payload.passenger_count

    return schemas.ApiEnvelope(
        success=True,
        data={
            "shuttle_id": shuttle_id,
            "viewing_id": payload.viewing_id,
            "tour_date": tour_date,
            "passenger_count": payload.passenger_count,
            **driver,
        },
        message="Created",
    )


@shuttle_app.get("/api/shuttles/bookings/{shuttle_id}", summary="Tra cứu xe tham quan")
def get_shuttle(shuttle_id: str) -> schemas.ApiEnvelope:
    shuttle = store.shuttle_bookings.get(shuttle_id)
    if shuttle is None:
        raise not_found("SHUTTLE_NOT_FOUND", f"Shuttle {shuttle_id} not found")
    return schemas.ApiEnvelope(
        success=True,
        data={
            "shuttle_id": shuttle["shuttle_id"],
            "viewing_id": shuttle["viewing_id"],
            "tour_date": shuttle["tour_date"],
            "passenger_count": shuttle["passenger_count"],
            "driver_name": shuttle["driver_name"],
            "license_plate": shuttle["license_plate"],
            "vehicle_type": shuttle["vehicle_type"],
            "pickup_time": shuttle["pickup_time"],
        },
        message="Found",
    )


@shuttle_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "shuttle"}
