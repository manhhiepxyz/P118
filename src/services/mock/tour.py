"""Mock Tour provider — FastAPI app độc lập (tool `book_tour`).

Port: 8005.

Theo cấu trúc system design (`01-high-level-architecture.md`): mock provider
độc lập theo domain, implement đúng contract riêng. Đây là service đặt lịch
tham quan dự án căn hộ (demo sau Gate 2) — KHÔNG phải một phần của chuỗi
register_resident → pay_fee hiện tại.

Quy tắc mock:
- Slot tham quan (residential_area, tour_slot) có sức chứa cố định (seed trong
  src/mock/store.py DEFAULT_TOUR_SLOTS). Hết chỗ một slot → 409 NO_AVAILABILITY.
- Một resident chỉ được đặt 1 lịch (resident_id, tour_date, tour_slot) →
  409 TOUR_ALREADY_BOOKED. `resident_id` NULL = khách tham quan (không bị chặn
  trùng, sức chứa vẫn là guard chính).

KHÁC với src/mock/ (single app, có cross-check): provider này KHÔNG check
`resident_id` tồn tại trong Resident provider — đó là dữ liệu của provider
khác, HUB orchestrate truyền vào input.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, install_error_handler, not_found
from src.mock.ids import make_generator
from src.mock.store import Store

tour_app = FastAPI(
    title="P-118 Tour Mock Provider",
    description="Dịch vụ giả lập đặt lịch tham quan dự án — tool book_tour.",
    version="0.1.0",
)

tour_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(tour_app)

# Store riêng của provider này — KHÔNG dùng singleton src.mock.store.store.
store = Store()

new_tour_id = make_generator("TOUR")


@tour_app.post("/api/tours/bookings", status_code=201, summary="Đặt lịch tham quan dự án")
def book_tour(
    payload: schemas.BookTourRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    # Slot không được offer (khu + khung giờ không nằm trong danh sách seed).
    slot_key = (payload.residential_area, payload.tour_slot.value)
    if slot_key not in store.tour_slots:
        raise not_found(
            "TOUR_SLOT_NOT_FOUND",
            f"Tour slot for {payload.residential_area} / {payload.tour_slot.value} not found",
        )

    tour_date = payload.tour_date.isoformat()

    # Một resident không được đặt trùng (resident_id, tour_date, tour_slot).
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


@tour_app.get("/api/tours/bookings/{tour_id}", summary="Tra cứu lịch tham quan")
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


@tour_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "tour"}
