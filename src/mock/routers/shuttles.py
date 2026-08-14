"""Mock Shuttle service (monolith) — tool `book_shuttle`.

POST /api/shuttles/bookings      → 201, envelope ``{success, data: {shuttle_id, ...}, ...}``
GET  /api/shuttles/bookings/{id} → 200, hoặc 404
?fail=<CODE>  — giả lập lỗi tuỳ chọn.

Dùng store singleton (src.mock.store.store) — CÓ cross-check tour_id (cùng app,
giống payments.py check booking_id). Sức chứa: 30 khách/ngày.
"""

from fastapi import APIRouter

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, not_found
from src.mock.ids import make_generator
from src.mock.store import store

router = APIRouter(prefix="/api/shuttles/bookings", tags=["shuttles"])

new_shuttle_id = make_generator("SHUTTLE")

SHUTTLE_DAILY_CAPACITY = 30


@router.post("", status_code=201, summary="Đặt xe tham quan")
def book_shuttle(
    payload: schemas.BookShuttleRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    # Cross-check (cùng app): lịch tham quan phải tồn tại.
    if payload.tour_id not in store.tour_bookings:
        raise not_found("TOUR_NOT_FOUND", f"Tour {payload.tour_id} not found")

    tour_date = payload.tour_date.isoformat()

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


@router.get("/{shuttle_id}", summary="Tra cứu xe tham quan")
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
