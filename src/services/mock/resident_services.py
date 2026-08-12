"""Mock provider cho yêu cầu bảo trì và lịch chuyển nhà của cư dân."""

from __future__ import annotations

from threading import RLock

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import install_error_handler
from src.mock.ids import make_generator

resident_services_app = FastAPI(
    title="P-118 Resident Services Mock Provider",
    description="Dịch vụ giả lập bảo trì và chuyển nhà.",
    version="0.1.0",
)
resident_services_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_error_handler(resident_services_app)

_maintenance_requests: dict[str, dict] = {}
_move_requests: dict[str, dict] = {}
_lock = RLock()
_new_maintenance_id = make_generator("MAINT")
_new_move_id = make_generator("MOVE")


@resident_services_app.post(
    "/api/resident-services/maintenance",
    status_code=201,
    summary="Tạo yêu cầu bảo trì",
)
def create_maintenance_request(payload: schemas.CreateMaintenanceRequest) -> schemas.ApiEnvelope:
    maintenance_id = _new_maintenance_id()
    result = {
        "maintenance_id": maintenance_id,
        "maintenance_status": "SCHEDULED",
        "appointment_date": payload.preferred_date.isoformat(),
        "appointment_time": payload.preferred_time,
    }
    with _lock:
        _maintenance_requests[maintenance_id] = result
    return schemas.ApiEnvelope(success=True, data=result, message="Maintenance request scheduled")


@resident_services_app.post(
    "/api/resident-services/moves",
    status_code=201,
    summary="Đặt lịch chuyển nhà",
)
def schedule_move(payload: schemas.ScheduleMoveRequest) -> schemas.ApiEnvelope:
    move_request_id = _new_move_id()
    result = {
        "move_request_id": move_request_id,
        "move_status": "SCHEDULED",
        "move_date": payload.move_date.isoformat(),
        "move_time": payload.move_time,
        "elevator_slot": payload.move_time if payload.needs_elevator else "NOT_REQUIRED",
    }
    with _lock:
        _move_requests[move_request_id] = result
    return schemas.ApiEnvelope(success=True, data=result, message="Move scheduled")


@resident_services_app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "resident-services"}
