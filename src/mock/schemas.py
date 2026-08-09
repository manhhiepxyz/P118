"""Pydantic schemas cho mock API — theo internal tool contract (mục 4 `shared_contracts.md`).

Field dùng snake_case, enum giới hạn đúng giá trị trong contract.

Từ v0.2.0: mọi endpoint trả **envelope** dạng gần `StandardResult` (mục 6
`shared_contracts.md`): ``{success, data, error_code, message, retryable}``.
Request model giữ nguyên; response model cũ được thay bằng ``ApiEnvelope``.
"""

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class VehicleType(StrEnum):
    CAR = "car"
    MOTORCYCLE = "motorcycle"


class ParkingZone(StrEnum):
    ZONE_A = "ZONE_A"
    ZONE_B = "ZONE_B"


class Currency(StrEnum):
    VND = "VND"


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


# ---- register_resident ----
class RegisterResidentRequest(BaseModel):
    full_name: str = Field(..., min_length=1, description="Tên cư dân, không rỗng")
    apartment_code: str = Field(..., min_length=1, description="Ví dụ: A1201")
    residential_area: str = Field(..., min_length=1, description="Tên khu đô thị giả lập")


# ---- register_vehicle ----
class RegisterVehicleRequest(BaseModel):
    resident_id: str = Field(..., min_length=1)
    plate_number: str = Field(..., min_length=1, description="Chuỗi biển số")
    vehicle_type: VehicleType


# ---- book_parking ----
class BookParkingRequest(BaseModel):
    vehicle_id: str = Field(..., min_length=1)
    booking_date: date = Field(..., description="Ngày đặt chỗ, định dạng YYYY-MM-DD")
    parking_zone: ParkingZone


# ---- pay_fee ----
class PayFeeRequest(BaseModel):
    booking_id: str = Field(..., min_length=1)
    amount: int = Field(..., ge=0, description="Số tiền nguyên, không âm")
    currency: Currency


# ---- verify_ownership ----
class VerifyOwnershipRequest(BaseModel):
    full_name: str = Field(..., min_length=1, description="Tên người yêu cầu xác minh")
    apartment_code: str = Field(..., min_length=1, description="Mã căn hộ cần xác minh quyền sở hữu")
    residential_area: str = Field(..., min_length=1, description="Tên khu đô thị")


# ---- API Envelope (mục 6 shared_contracts.md) ----
class ApiEnvelope(BaseModel):
    """Envelope response dạng gần ``StandardResult`` cho mọi endpoint mock."""

    success: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None
    retryable: bool = False
