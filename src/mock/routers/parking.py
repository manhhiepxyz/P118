"""Mock Parking service — tool `book_parking`.

POST /api/parking/bookings   → 201, envelope ``{success, data: {booking_id, ...}, ...}``
GET  /api/parking/bookings/{id} → 200, hoặc 404
?fail=<CODE>  → giả lập lỗi tuỳ chọn.

Quy tắc mock:
- ZONE_A sức chứa 3/ngày. Khi hết chỗ cho một ngày → 409 NO_AVAILABILITY.
- ZONE_B luôn còn chỗ.
- Giá theo zone; currency luôn VND.
"""

from fastapi import APIRouter

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, not_found
from src.mock.ids import make_generator
from src.mock.store import store

router = APIRouter(prefix="/api/parking/bookings", tags=["parking"])

new_booking_id = make_generator("BOOK")

ZONE_A_CAPACITY = 3
ZONE_PRICES = {schemas.ParkingZone.ZONE_A: 150_000, schemas.ParkingZone.ZONE_B: 100_000}


@router.post("", status_code=201, summary="Đặt chỗ đậu xe")
def book_parking(
    payload: schemas.BookParkingRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    if payload.vehicle_id not in store.vehicles:
        raise not_found("VEHICLE_NOT_FOUND", f"Vehicle {payload.vehicle_id} not found")

    booking_date = payload.booking_date.isoformat()

    if any(
        b["vehicle_id"] == payload.vehicle_id and b["booking_date"] == booking_date for b in store.bookings.values()
    ):
        raise conflict(
            "BOOKING_ALREADY_EXISTS",
            f"Vehicle {payload.vehicle_id} already booked for {booking_date}",
        )

    load_key = (payload.parking_zone.value, booking_date)
    current_load = store.parking_load.get(load_key, 0)
    if payload.parking_zone == schemas.ParkingZone.ZONE_A and current_load >= ZONE_A_CAPACITY:
        raise conflict(
            "NO_AVAILABILITY",
            f"Parking Zone A ({schemas.ParkingZone.ZONE_A.value}) is full on {booking_date}",
        )

    booking_id = new_booking_id()
    amount = ZONE_PRICES[payload.parking_zone]
    with store._lock:
        store.bookings[booking_id] = {
            "booking_id": booking_id,
            "vehicle_id": payload.vehicle_id,
            "parking_zone": payload.parking_zone.value,
            "booking_date": booking_date,
            "amount": amount,
            "currency": schemas.Currency.VND.value,
        }
        store.parking_load[load_key] = current_load + 1

    return schemas.ApiEnvelope(
        success=True,
        data={
            "booking_id": booking_id,
            "parking_zone": payload.parking_zone.value,
            "booking_date": booking_date,
            "amount": amount,
            "currency": schemas.Currency.VND.value,
        },
        message="Created",
    )


@router.get("/{booking_id}", summary="Tra cứu đặt chỗ")
def get_booking(booking_id: str) -> schemas.ApiEnvelope:
    booking = store.bookings.get(booking_id)
    if booking is None:
        raise not_found("BOOKING_NOT_FOUND", f"Booking {booking_id} not found")
    return schemas.ApiEnvelope(
        success=True,
        data={
            "booking_id": booking["booking_id"],
            "parking_zone": booking["parking_zone"],
            "booking_date": booking["booking_date"],
            "amount": booking["amount"],
            "currency": booking["currency"],
        },
        message="Found",
    )
