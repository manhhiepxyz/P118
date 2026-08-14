"""Mock Shuttle provider — FastAPI app độc lập (tool `book_shuttle`).

Port: 8006.

Theo cấu trúc system design (`01-high-level-architecture.md`): mock provider
độc lập theo domain, implement đúng contract riêng. Đây là service đặt xe
(shuttle) đưa khách đi tham quan dự án căn hộ (demo sau Gate 2) — KHÔNG nằm
trong chuỗi register_resident → pay_fee hiện tại.

Quy tắc mock:
- Sức chứa xe theo ngày: tối đa 30 khách/ngày (SHUTTLE_DAILY_CAPACITY).
  Vượt → 409 NO_AVAILABILITY.
- Một lịch tham quan (tour_id) chỉ đặt được 1 xe → 409 SHUTTLE_ALREADY_BOOKED.

KHÁC với src/mock/ (single app, có cross-check): provider này KHÔNG check
`tour_id` tồn tại trong Tour provider — đó là dữ liệu của provider khác
(hub thuần, giống payment.py không check booking_id). HUB orchestrate truyền
`tour_id` + `tour_date` đã verify vào input.
"""

from __future__ import annotations

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


@shuttle_app.post("/api/shuttles/bookings", status_code=201, summary="Đặt xe tham quan")
def book_shuttle(
    payload: schemas.BookShuttleRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    tour_date = payload.tour_date.isoformat()

    # KHÔNG check tour_id tồn tại — Tour là provider khác (cross-provider).
    # HUB orchestrate truyền tour_id đã verify vào input của task này.
    current_load = store.shuttle_load.get(tour_date, 0)
    if current_load + payload.passenger_count > SHUTTLE_DAILY_CAPACITY:
        raise conflict(
            "NO_AVAILABILITY",
            f"Shuttle capacity exceeded on {tour_date} ({SHUTTLE_DAILY_CAPACITY} passengers/day)",
        )

    if any(b["tour_id"] == payload.tour_id for b in store.shuttle_bookings.values()):
        raise conflict(
            "SHUTTLE_ALREADY_BOOKED",
            f"Shuttle already booked for tour {payload.tour_id}",
        )

    shuttle_id = new_shuttle_id()
    with store._lock:
        store.shuttle_bookings[shuttle_id] = {
            "shuttle_id": shuttle_id,
            "tour_id": payload.tour_id,
            "tour_date": tour_date,
            "passenger_count": payload.passenger_count,
        }
        store.shuttle_load[tour_date] = current_load + payload.passenger_count

    return schemas.ApiEnvelope(
        success=True,
        data={
            "shuttle_id": shuttle_id,
            "tour_id": payload.tour_id,
            "tour_date": tour_date,
            "passenger_count": payload.passenger_count,
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
            "tour_id": shuttle["tour_id"],
            "tour_date": shuttle["tour_date"],
            "passenger_count": shuttle["passenger_count"],
        },
        message="Found",
    )


@shuttle_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "shuttle"}
