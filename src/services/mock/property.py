"""Mock Property provider cho tìm nhà và đặt lịch xem.

Provider chỉ hỗ trợ discovery/contact. Nó không giữ căn, đặt cọc, ký hợp đồng
hay thực hiện giao dịch thuê/mua.
"""

from __future__ import annotations

from threading import RLock

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.common.projects import PROJECTS
from src.mock import schemas
from src.mock.errors import conflict, install_error_handler, not_found
from src.mock.ids import make_generator

property_app = FastAPI(
    title="P-118 Property Mock Provider",
    description="Dịch vụ giả lập tìm bất động sản và đặt lịch xem.",
    version="0.1.0",
)
property_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_error_handler(property_app)

PROPERTIES = (
    {
        "property_id": "PROP-001",
        "title": "Căn hộ 2 phòng ngủ gần công viên",
        "transaction_type": "rent",
        "property_type": "apartment",
        "residential_area": "Vinhomes Ocean Park",
        "price": 18_000_000,
        "currency": "VND",
        "bedrooms": 2,
        "contact_name": "Minh Anh - Tư vấn",
        "contact_phone": "0900000001",
    },
    {
        "property_id": "PROP-002",
        "title": "Căn hộ studio đầy đủ nội thất",
        "transaction_type": "rent",
        "property_type": "apartment",
        "residential_area": "Vinhomes Ocean Park",
        "price": 11_000_000,
        "currency": "VND",
        "bedrooms": 1,
        "contact_name": "Thu Hà - Tư vấn",
        "contact_phone": "0900000002",
    },
    {
        "property_id": "PROP-003",
        "title": "Căn hộ 3 phòng ngủ để mua",
        "transaction_type": "buy",
        "property_type": "apartment",
        "residential_area": "Vinhomes Smart City",
        "price": 5_200_000_000,
        "currency": "VND",
        "bedrooms": 3,
        "contact_name": "Quang Huy - Tư vấn",
        "contact_phone": "0900000003",
    },
    {
        "property_id": "PROP-004",
        "title": "Phòng cho thuê dài hạn",
        "transaction_type": "rent",
        "property_type": "room",
        "residential_area": "Vinhomes Smart City",
        "price": 6_500_000,
        "currency": "VND",
        "bedrooms": 1,
        "contact_name": "Ngọc Linh - Tư vấn",
        "contact_phone": "0900000004",
    },
)

_viewings: dict[str, dict] = {}
_interests: dict[str, dict] = {}
_lock = RLock()
_new_viewing_id = make_generator("VIEW")
_new_interest_id = make_generator("INT")


@property_app.post("/api/properties/search", summary="Tìm bất động sản phù hợp")
def search_properties(payload: schemas.SearchPropertiesRequest) -> schemas.ApiEnvelope:
    area = payload.residential_area.casefold()
    matches = [
        dict(item)
        for item in PROPERTIES
        if item["transaction_type"] == payload.transaction_type.value
        and item["property_type"] == payload.property_type.value
        and item["residential_area"].casefold() == area
        and item["price"] <= payload.max_price
    ]
    return schemas.ApiEnvelope(
        success=True,
        data={"properties": matches, "result_count": len(matches)},
        message="Search completed",
    )


@property_app.post("/api/projects/viewings", status_code=201, summary="Đặt lịch tham quan dự án")
def schedule_property_viewing(payload: schemas.SchedulePropertyViewingRequest) -> schemas.ApiEnvelope:
    project = next((item for item in PROJECTS if item["project_id"] == payload.project_id), None)
    if project is None:
        raise not_found("INVALID_INPUT", "Project not found")

    viewing_date = payload.viewing_date.isoformat()
    if any(
        item["project_id"] == payload.project_id
        and item["viewing_date"] == viewing_date
        and item["viewing_time"] == payload.viewing_time
        for item in _viewings.values()
    ):
        raise conflict("NO_AVAILABILITY", "Viewing slot is unavailable")

    viewing_id = _new_viewing_id()
    result = {
        "viewing_id": viewing_id,
        "project_id": payload.project_id,
        "project_name": project["project_name"],
        "viewing_date": viewing_date,
        "viewing_time": payload.viewing_time,
        "viewing_status": "SCHEDULED",
        "contact_name": "Trung tâm tư vấn dự án",
        "contact_phone": "1900232389",
    }
    with _lock:
        _viewings[viewing_id] = {
            **result,
            "request_contact": payload.model_dump(
                include={"full_name", "phone", "email", "note"},
                exclude_none=True,
            ),
        }

    return schemas.ApiEnvelope(success=True, data=result, message="Viewing scheduled")


@property_app.post("/api/projects/interests", status_code=201, summary="Đăng ký tư vấn dự án")
def register_property_interest(payload: schemas.RegisterPropertyInterestRequest) -> schemas.ApiEnvelope:
    project = next((item for item in PROJECTS if item["project_id"] == payload.project_id), None)
    if project is None:
        raise not_found("INVALID_INPUT", "Project not found")

    interest_id = _new_interest_id()
    result = {
        "interest_id": interest_id,
        "project_id": payload.project_id,
        "project_name": project["project_name"],
        "interest_status": "RECEIVED",
        # Provider/account directory chịu trách nhiệm contact; TaskPlan không
        # mang phone/email PII của user.
        "contact_channel": "VERIFIED_ACCOUNT_CONTACT",
    }
    with _lock:
        _interests[interest_id] = {
            **result,
            "request_contact": payload.model_dump(
                include={"full_name", "phone", "email", "note"},
                exclude_none=True,
            ),
        }
    return schemas.ApiEnvelope(success=True, data=result, message="Interest registered")


@property_app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "property"}
