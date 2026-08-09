"""Mock Vehicle service — tool `register_vehicle`.

POST /api/vehicles           → 201, envelope ``{success, data: {vehicle_id}, ...}``
GET  /api/vehicles/{id}     → 200, hoặc 404
?fail=<CODE>  — giả lập lỗi tuỳ chọn.
"""

from fastapi import APIRouter

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, not_found
from src.mock.ids import make_generator
from src.mock.store import store

router = APIRouter(prefix="/api/vehicles", tags=["vehicles"])

new_vehicle_id = make_generator("VEH")


@router.post("", status_code=201, summary="Đăng ký phương tiện")
def register_vehicle(
    payload: schemas.RegisterVehicleRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    if payload.resident_id not in store.residents:
        raise not_found("RESIDENT_NOT_FOUND", f"Resident {payload.resident_id} not found")

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


@router.get("/{vehicle_id}", summary="Tra cứu phương tiện")
def get_vehicle(vehicle_id: str) -> schemas.ApiEnvelope:
    vehicle = store.vehicles.get(vehicle_id)
    if vehicle is None:
        raise not_found("VEHICLE_NOT_FOUND", f"Vehicle {vehicle_id} not found")
    return schemas.ApiEnvelope(
        success=True,
        data={"vehicle_id": vehicle["vehicle_id"]},
        message="Found",
    )
