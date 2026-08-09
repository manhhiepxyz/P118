"""Mock Transport provider — FastAPI app độc lập (tools `register_vehicle` + `book_parking`).

Port: 8002 — khớp `TransportConnector` (src/connectors/transport.py).

Theo cấu trúc system design (`01-high-level-architecture.md`): mock provider
độc lập theo domain, implement đúng contract riêng. Transport là MỘT provider
xử lý cả vehicle lẫn parking (Object Group Transport), nên `book_parking` vẫn
check `vehicle_id` tồn tại trong store nội bộ.

KHÁC với src/mock/ (single app, có cross-check resident): `register_vehicle`
KHÔNG check `resident_id` — đó là dữ liệu của Resident provider, HUB orchestrate
truyền vào input. Payment/booking amount check cũng không thuộc provider này.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, install_error_handler, not_found
from src.mock.ids import make_generator
from src.mock.store import Store

transport_app = FastAPI(
    title="P-118 Transport Mock Provider",
    description="Dịch vụ giả lập Transport — tools register_vehicle, book_parking.",
    version="0.1.0",
)

transport_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(transport_app)

# Store riêng của provider này — KHÔNG dùng singleton src.mock.store.store.
store = Store()

new_vehicle_id = make_generator("VEH")
new_booking_id = make_generator("BOOK")

ZONE_A_CAPACITY = 3
ZONE_PRICES = {schemas.ParkingZone.ZONE_A: 150_000, schemas.ParkingZone.ZONE_B: 100_000}


@transport_app.post("/api/vehicles", status_code=201, summary="Đăng ký phương tiện")
def register_vehicle(
    payload: schemas.RegisterVehicleRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    # KHÔNG check resident_id — Resident là provider khác (cross-provider).
    # HUB orchestrate truyền resident_id vào input của task này.
    if any(v["plate_number"] == payload.plate_number for v in store.vehicles.values()):
        raise conflict(
            "VEHICLE_ALREADY_EXISTS",
            f"Vehicle with plate {payload.plate_number} already exists",
        )

    vehicle_id = new_vehicle_id()
    with store._lock:
        store.vehicles[vehicle_id] = {
            "vehicle_id": vehicle_id,
            "resident_id": payload.resident_id,
            "plate_number": payload.plate_number,
            "vehicle_type": payload.vehicle_type.value,
        }
    return schemas.ApiEnvelope(
        success=True,
        data={"vehicle_id": vehicle_id},
        message="Created",
    )


@transport_app.get("/api/vehicles/{vehicle_id}", summary="Tra cứu phương tiện")
def get_vehicle(vehicle_id: str) -> schemas.ApiEnvelope:
    vehicle = store.vehicles.get(vehicle_id)
    if vehicle is None:
        raise not_found("VEHICLE_NOT_FOUND", f"Vehicle {vehicle_id} not found")
    return schemas.ApiEnvelope(
        success=True,
        data={"vehicle_id": vehicle["vehicle_id"]},
        message="Found",
    )


@transport_app.post("/api/parking/bookings", status_code=201, summary="Đặt chỗ đậu xe")
def book_parking(
    payload: schemas.BookParkingRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    # Vehicle và Parking là CÙNG provider (Transport) → giữ check này.
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


@transport_app.get("/api/parking/bookings/{booking_id}", summary="Tra cứu đặt chỗ")
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


@transport_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "transport"}
