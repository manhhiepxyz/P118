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
from src.mock.business_contacts import contact_for_project
from src.mock.errors import conflict, inject_failure, install_error_handler, not_found
from src.mock.ids import make_generator
from src.mock.projects import UnknownProjectError, get_project
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
# Id của lịch xem nhà mang prefix riêng. Dùng lại "TOUR-" sẽ khiến tên nội bộ
# rò ra ngoài qua GIÁ TRỊ id, dù tên field đã canonical.
new_viewing_id = make_generator("VIEW")


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
            # Endpoint legacy `book_tour` vẫn nhận `resident_id` và chống trùng
            # theo nó. Nó bị xoá nhầm khi bỏ `resident_id` khỏi endpoint canonical
            # (contract public không có field này) — hai endpoint dùng chung store
            # nên một lần sửa nhầm chỗ làm hỏng cái còn lại.
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


# =====================================================================
# Endpoint canonical — tool `schedule_property_viewing`
#
# Tái sử dụng nguyên implementation phía trên (sức chứa slot, chống đặt trùng,
# sinh id) nhưng nói bằng ngôn ngữ contract public. Khác biệt thật sự:
#
#   - Vào bằng `project_id`, không phải `residential_area` chuỗi tự do. Tên khu
#     tra từ danh mục dự án, nên LLM không thể gõ sai thành 404.
#   - `viewing_time` HH:MM được LƯU NGUYÊN VĂN. `tour_slot` chỉ còn là khoá gom
#     nhóm để đếm chỗ; nó không còn là nguồn sự thật về giờ hẹn.
# =====================================================================


@tour_app.post("/api/property/viewings", status_code=201, summary="Đặt lịch xem nhà")
def schedule_property_viewing(
    payload: schemas.SchedulePropertyViewingRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    try:
        project = get_project(payload.project_id)
    except UnknownProjectError as exc:
        raise not_found("PROJECT_NOT_FOUND", str(exc)) from exc

    slot = schemas.viewing_time_to_slot(payload.viewing_time)
    slot_key = (project.residential_area, slot.value)
    if slot_key not in store.tour_slots:
        raise not_found(
            "VIEWING_SLOT_NOT_FOUND",
            f"Dự án {project.project_id} không mở lịch xem vào khung {payload.viewing_time}",
        )

    viewing_date = payload.viewing_date.isoformat()

    # Chống trùng theo (ngày, GIỜ) — không phải (ngày, buổi). Đặt 09:00 rồi vẫn
    # phải đặt được 11:00 cùng buổi sáng; gom về buổi sẽ chặn nhầm lịch hợp lệ.
    #
    # Khoá gồm CẢ `project_id`. Thiếu nó, một lịch xem PRJ-001 lúc 09:30 sẽ chặn
    # luôn PRJ-002 lúc 09:30 — hai dự án khác nhau, hai đoàn khác nhau, không có
    # lý do nào xung đột.
    #
    # Không khoá theo người đặt: contract canonical không nhận `resident_id`, và
    # thông tin liên hệ do provider giữ chứ không đi qua TaskPlan. Đây cũng là
    # điểm khác endpoint legacy `book_tour` (khoá theo resident_id + buổi) — hai
    # endpoint dùng chung store nên khác biệt này phải là chủ ý, không phải tình cờ.
    if any(
        t.get("project_id") == project.project_id
        and t.get("tour_date") == viewing_date
        and t.get("viewing_time") == payload.viewing_time
        for t in store.tour_bookings.values()
    ):
        raise conflict(
            "VIEWING_ALREADY_BOOKED",
            f"Đã có lịch xem {viewing_date} {payload.viewing_time}",
        )

    load_key = (project.residential_area, viewing_date, slot.value)
    current_load = store.tour_load.get(load_key, 0)
    if current_load >= store.tour_slots[slot_key]:
        raise conflict(
            "NO_AVAILABILITY",
            f"Khung {payload.viewing_time} ngày {viewing_date} của {project.project_name} đã kín chỗ",
        )

    viewing_id = new_viewing_id()
    contact = contact_for_project(project.project_id)
    with store._lock:
        store.tour_bookings[viewing_id] = {
            "tour_id": viewing_id,
            "residential_area": project.residential_area,
            "project_id": project.project_id,
            "tour_date": viewing_date,
            "tour_slot": slot.value,
            "viewing_time": payload.viewing_time,
            "viewing_status": "SCHEDULED",
        }
        store.tour_load[load_key] = current_load + 1

    return schemas.ApiEnvelope(
        success=True,
        data={
            "viewing_id": viewing_id,
            "project_id": project.project_id,
            "project_name": project.project_name,
            "viewing_date": viewing_date,
            "viewing_time": payload.viewing_time,
            "viewing_status": "SCHEDULED",
            # Đầu mối tư vấn CỦA DỰ ÁN, không phải người đặt lịch. Prospect chưa
            # có hồ sơ vẫn nhận được người để liên hệ.
            "contact_name": contact.contact_name,
            "contact_phone": contact.contact_phone,
        },
        message="Created",
    )


@tour_app.get("/api/property/viewings/{viewing_id}", summary="Tra cứu lịch xem nhà")
def get_property_viewing(viewing_id: str) -> schemas.ApiEnvelope:
    viewing = store.tour_bookings.get(viewing_id)
    if viewing is None:
        raise not_found("VIEWING_NOT_FOUND", f"Không tìm thấy lịch xem {viewing_id}")

    project_id = viewing.get("project_id")
    project_name = get_project(project_id).project_name if project_id else None
    contact = contact_for_project(project_id)
    return schemas.ApiEnvelope(
        success=True,
        data={
            "viewing_id": viewing["tour_id"],
            "project_id": project_id,
            "project_name": project_name,
            "viewing_date": viewing["tour_date"],
            "viewing_time": viewing.get("viewing_time"),
            "viewing_status": viewing.get("viewing_status", "SCHEDULED"),
            "contact_name": contact.contact_name,
            "contact_phone": contact.contact_phone,
        },
        message="Found",
    )
