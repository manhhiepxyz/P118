"""Mock Tour service (monolith) — tool `book_tour`.

POST /api/tours/bookings      → 201, envelope ``{success, data: {tour_id, ...}, ...}``
GET  /api/tours/bookings/{id} → 200, hoặc 404
?fail=<CODE>  — giả lập lỗi tuỳ chọn.

Dùng store singleton (src.mock.store.store) — CÓ cross-check resident_id (cùng
app) giống residents/vehicles router. Sức chứa slot từ store.tour_slots.
"""

from fastapi import APIRouter

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, not_found
from src.mock.ids import make_generator
from src.mock.store import store

router = APIRouter(prefix="/api/tours/bookings", tags=["tours"])

new_tour_id = make_generator("TOUR")


@router.post("", status_code=201, summary="Đặt lịch tham quan dự án")
def book_tour(
    payload: schemas.BookTourRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    slot_key = (payload.residential_area, payload.tour_slot.value)
    if slot_key not in store.tour_slots:
        raise not_found(
            "TOUR_SLOT_NOT_FOUND",
            f"Tour slot for {payload.residential_area} / {payload.tour_slot.value} not found",
        )

    # Cross-check (cùng app): resident phải tồn tại.
    if payload.resident_id is not None and payload.resident_id not in store.residents:
        raise not_found("RESIDENT_NOT_FOUND", f"Resident {payload.resident_id} not found")

    tour_date = payload.tour_date.isoformat()

    if payload.resident_id is not None and any(
        t["resident_id"] == payload.resident_id
        and t["tour_date"] == tour_date
        and t["tour_slot"] == payload.tour_slot.value
        for t in store.tour_bookings.values()
    ):
        raise conflict(
            "TOUR_ALREADY_BOOKED",
            f"Resident {payload.resident_id} already booked tour for {tour_date} / {payload.tour_slot.value}",
        )

    load_key = (payload.residential_area, tour_date, payload.tour_slot.value)
    current_load = store.tour_load.get(load_key, 0)
    if current_load >= store.tour_slots[slot_key]:
        raise conflict(
            "NO_AVAILABILITY",
            f"Tour slot {payload.residential_area} / {payload.tour_slot.value} is full on {tour_date}",
        )

    tour_id = new_tour_id()
    with store._lock:
        store.tour_bookings[tour_id] = {
            "tour_id": tour_id,
            "resident_id": payload.resident_id,
            "residential_area": payload.residential_area,
            "tour_date": tour_date,
            "tour_slot": payload.tour_slot.value,
        }
        store.tour_load[load_key] = current_load + 1

    return schemas.ApiEnvelope(
        success=True,
        data={
            "tour_id": tour_id,
            "residential_area": payload.residential_area,
            "tour_date": tour_date,
            "tour_slot": payload.tour_slot.value,
        },
        message="Created",
    )


@router.get("/{tour_id}", summary="Tra cứu lịch tham quan")
def get_tour(tour_id: str) -> schemas.ApiEnvelope:
    tour = store.tour_bookings.get(tour_id)
    if tour is None:
        raise not_found("TOUR_NOT_FOUND", f"Tour {tour_id} not found")
    return schemas.ApiEnvelope(
        success=True,
        data={
            "tour_id": tour["tour_id"],
            "residential_area": tour["residential_area"],
            "tour_date": tour["tour_date"],
            "tour_slot": tour["tour_slot"],
        },
        message="Found",
    )
