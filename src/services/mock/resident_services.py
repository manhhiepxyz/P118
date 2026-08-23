"""Mock provider cho yêu cầu bảo trì và lịch chuyển nhà của cư dân."""

from __future__ import annotations

from threading import RLock

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import install_error_handler, not_found
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


def _huy(kho: dict[str, dict], ma: str, o_trang_thai: str, ten: str) -> schemas.ApiEnvelope:
    """Đánh dấu một yêu cầu là đã huỷ. Idempotent: gọi lần hai vẫn 200.

    ĐÁNH DẤU chứ không xoá, cùng lý do với chỗ đỗ xe và lịch tham quan: xoá thì
    lần gọi thứ hai không phân biệt được "đã huỷ rồi" với "chưa bao giờ tồn
    tại", và cả hai đều thành 200 — tức xác nhận một việc chưa từng xảy ra cho
    một mã bịa ra.
    """
    with _lock:
        row = kho.get(ma)
        if row is None:
            raise not_found(f"{ten}_NOT_FOUND", f"Không tìm thấy yêu cầu {ma}")
        row[o_trang_thai] = "CANCELLED"
        ket_qua = dict(row)
    return schemas.ApiEnvelope(success=True, data=ket_qua, message="Cancelled")


@resident_services_app.post(
    "/api/resident-services/maintenance/{maintenance_id}/cancel",
    summary="Huỷ yêu cầu bảo trì",
)
def cancel_maintenance(maintenance_id: str) -> schemas.ApiEnvelope:
    return _huy(_maintenance_requests, maintenance_id, "maintenance_status", "MAINTENANCE")


@resident_services_app.post(
    "/api/resident-services/moves/{move_request_id}/cancel",
    summary="Huỷ lịch chuyển nhà",
)
def cancel_move(move_request_id: str) -> schemas.ApiEnvelope:
    return _huy(_move_requests, move_request_id, "move_status", "MOVE")


@resident_services_app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "resident-services"}
