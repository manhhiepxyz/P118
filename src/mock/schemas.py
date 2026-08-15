"""Pydantic schemas cho mock API — theo internal tool contract (mục 4 `shared_contracts.md`).

Field dùng snake_case, enum giới hạn đúng giá trị trong contract.

Từ v0.2.0: mọi endpoint trả **envelope** dạng gần `StandardResult` (mục 6
`shared_contracts.md`): ``{success, data, error_code, message, retryable}``.
Request model giữ nguyên; response model cũ được thay bằng ``ApiEnvelope``.
"""

import re
from datetime import date, time
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _reject_past(value: date) -> date:
    if value < date.today():
        raise ValueError("date must not be in the past")
    return value


# Đúng dạng `HH:MM`, hai chữ số mỗi bên.
#
# `time.fromisoformat` rộng hơn thế: nó nhận cả "0800" và "08:00:00". Rộng hơn
# nghe có vẻ tử tế, nhưng nó khiến provider và `TaskPlanValidator` — vốn dùng
# đúng regex này — bất đồng về việc thế nào là hợp lệ. Hai tầng nói hai kiểu là
# chỗ một giá trị lọt qua tầng này rồi chết ở tầng kia, hoặc tệ hơn, ngược lại.
_HH_MM = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def _check_business_time(value: str, opens_at: time, closes_at: time) -> str:
    if _HH_MM.fullmatch(value or "") is None:
        raise ValueError("time must be HH:MM")
    parsed = time.fromisoformat(value)
    if not opens_at <= parsed <= closes_at:
        raise ValueError("time is outside service hours")
    return value


def _validate_optional_email(value: str | None) -> str | None:
    if value is not None and (value.count("@") != 1 or "." not in value.rsplit("@", 1)[1]):
        raise ValueError("email không đúng định dạng cơ bản")
    return value


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


class PropertyTransactionType(StrEnum):
    RENT = "rent"
    BUY = "buy"


class PropertyType(StrEnum):
    APARTMENT = "apartment"
    ROOM = "room"


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

    _booking_not_past = field_validator("booking_date")(_reject_past)


# ---- pay_fee ----
class PayFeeRequest(BaseModel):
    booking_id: str = Field(..., min_length=1)
    amount: int = Field(..., gt=0, description="Số tiền nguyên, lớn hơn 0")
    currency: Currency


# ---- search_properties ----
class SearchPropertiesRequest(BaseModel):
    transaction_type: PropertyTransactionType
    property_type: PropertyType
    residential_area: str = Field(..., min_length=1)
    max_price: int = Field(..., gt=0, description="Ngân sách tối đa, đơn vị VND")


# ---- schedule_property_viewing ----
class SchedulePropertyViewingRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    viewing_date: date
    viewing_time: str = Field(..., pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    full_name: str | None = Field(default=None, min_length=2, max_length=100)
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9 ]{9,15}$")
    email: str | None = Field(default=None, max_length=254)
    note: str | None = Field(default=None, max_length=500)

    _viewing_not_past = field_validator("viewing_date")(_reject_past)
    _valid_viewing_email = field_validator("email")(_validate_optional_email)

    @field_validator("viewing_time")
    @classmethod
    def viewing_during_business_hours(cls, value: str) -> str:
        return _check_business_time(value, time(8, 0), time(17, 30))


class RegisterPropertyInterestRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    interest_type: Literal["buy", "rent", "consultation"]
    preferred_contact_time: str
    consent: Literal[True]
    full_name: str | None = Field(default=None, min_length=2, max_length=100)
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9 ]{9,15}$")
    email: str | None = Field(default=None, max_length=254)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("preferred_contact_time")
    @classmethod
    def contact_during_business_hours(cls, value: str) -> str:
        """Giờ liên hệ phải là HH:MM trong khung 08:00–18:00.

        Provider tự kiểm chứ không dựa vào Validator: không phải request nào
        cũng đi qua Agent, và một endpoint chỉ an toàn khi nó tự an toàn.
        """
        return _check_business_time(value, time(8, 0), time(18, 0))

    _valid_interest_email = field_validator("email")(_validate_optional_email)


# ---- resident services: maintenance / moving ----
class CreateMaintenanceRequest(BaseModel):
    issue_type: Literal["air_conditioning", "electrical", "plumbing", "other"]
    description: str = Field(..., min_length=1, max_length=500)
    location: str = Field(..., min_length=1, max_length=100)
    preferred_date: date
    preferred_time: str = Field(..., pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    _maintenance_not_past = field_validator("preferred_date")(_reject_past)

    @field_validator("preferred_time")
    @classmethod
    def maintenance_during_business_hours(cls, value: str) -> str:
        return _check_business_time(value, time(8, 0), time(18, 0))


class ScheduleMoveRequest(BaseModel):
    move_date: date
    move_time: str = Field(..., pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    needs_elevator: bool
    needs_loading_support: bool
    move_vehicle: Literal["none", "van", "truck"]

    _move_not_past = field_validator("move_date")(_reject_past)

    @field_validator("move_time")
    @classmethod
    def move_during_business_hours(cls, value: str) -> str:
        return _check_business_time(value, time(7, 0), time(20, 0))


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


# =====================================================================
# Dịch vụ tham quan/tư vấn — implementation nội bộ.
#
# `book_tour`/`book_shuttle`/`register_consultation` KHÔNG phải tool public.
# Chúng là phần chạy bên dưới của `schedule_property_viewing` và
# `register_property_interest`; schema giữ lại vì mock provider và test của
# chúng vẫn dùng.
# =====================================================================


# ---- book_tour (đặt lịch tham quan dự án căn hộ) ----
class TourSlot(StrEnum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"


class BookTourRequest(BaseModel):
    residential_area: str = Field(..., min_length=1, description="Tên khu đô thị dự án căn hộ")
    tour_date: date = Field(..., description="Ngày tham quan, định dạng YYYY-MM-DD")
    tour_slot: TourSlot = Field(..., description="Khung giờ tham quan: MORNING hoặc AFTERNOON")
    resident_id: str | None = Field(default=None, description="ID cư dân (tùy chọn; NULL = khách tham quan)")


# ---- book_shuttle (đặt xe tham quan căn hộ) ----
class BookShuttleRequest(BaseModel):
    tour_id: str = Field(..., min_length=1, description="Mã đặt lịch tham quan")
    tour_date: date = Field(..., description="Ngày tham quan, định dạng YYYY-MM-DD")
    passenger_count: int = Field(..., ge=1, le=30, description="Số người đi xe (1–30)")


# ---- register_consultation (đăng ký tư vấn) ----
class ConsultationType(StrEnum):
    BUY = "BUY"  # tư vấn mua
    RENT = "RENT"  # tư vấn thuê


class BuySubType(StrEnum):
    RESIDE = "RESIDE"  # mua để ở
    BUSINESS = "BUSINESS"  # mua để kinh doanh
    INVEST = "INVEST"  # mua để đầu tư


class RegisterConsultationRequest(BaseModel):
    consultation_type: ConsultationType = Field(..., description="Loại tư vấn: BUY (mua) hoặc RENT (thuê)")
    buy_sub_type: BuySubType | None = Field(
        default=None,
        description="Phân loại tư vấn mua — bắt buộc khi consultation_type=BUY",
    )
    resident_id: str | None = Field(default=None, description="ID cư dân (tùy chọn)")

    @model_validator(mode="after")
    def _require_buy_sub_type(self) -> "RegisterConsultationRequest":
        """Bắt buộc `buy_sub_type` khi tư vấn mua.

        Tư vấn thuê (RENT) không có phân loại con. Vi phạm → 422 INVALID_INPUT
        qua validation handler chuẩn của mock API.
        """
        if self.consultation_type == ConsultationType.BUY and self.buy_sub_type is None:
            raise ValueError("buy_sub_type is required when consultation_type is BUY")
        return self


def viewing_time_to_slot(viewing_time: str) -> TourSlot:
    """Quy giờ HH:MM về khung sức chứa MORNING/AFTERNOON.

    Đây CHỈ là khoá gom nhóm để đếm chỗ. Giờ người dùng chọn vẫn được lưu
    nguyên văn ở `viewing_time` — quy về hai buổi rồi vứt phút giờ đi là làm
    mất dữ liệu họ đã nhập, và lịch trả về sẽ sai so với lịch họ đặt.
    """
    hour = int(viewing_time.split(":", 1)[0])
    return TourSlot.MORNING if hour < 12 else TourSlot.AFTERNOON


class InterestType(StrEnum):
    BUY = "buy"
    RENT = "rent"
    CONSULTATION = "consultation"


class PreferredContactTime(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
