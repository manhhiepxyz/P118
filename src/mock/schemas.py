"""Pydantic schemas cho mock API — theo internal tool contract (mục 4 `shared_contracts.md`).

Field dùng snake_case, enum giới hạn đúng giá trị trong contract.

Từ v0.2.0: mọi endpoint trả **envelope** dạng gần `StandardResult` (mục 6
`shared_contracts.md`): ``{success, data, error_code, message, retryable}``.
Request model giữ nguyên; response model cũ được thay bằng ``ApiEnvelope``.

Từ v0.5.0: thêm 3 dịch vụ demo — đặt lịch tham quan dự án (`book_tour`),
đặt xe tham quan (`book_shuttle`), đăng ký tư vấn (`register_consultation`).
"""

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


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


# ---- API Envelope (mục 6 shared_contracts.md) ----
class ApiEnvelope(BaseModel):
    """Envelope response dạng gần ``StandardResult`` cho mọi endpoint mock."""

    success: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None
    retryable: bool = False


# =====================================================================
# Contract canonical — schedule_property_viewing / register_property_interest
#
# Hai tool này TÁI SỬ DỤNG implementation của `book_tour` / `register_consultation`
# (sức chứa slot, chống trùng, sinh id) nhưng nói bằng ngôn ngữ contract public.
# `tour_id`/`tour_date`/`tour_slot` là chi tiết nội bộ và không được lộ ra ngoài.
# =====================================================================


def viewing_time_to_slot(viewing_time: str) -> TourSlot:
    """Quy giờ HH:MM về khung sức chứa MORNING/AFTERNOON.

    Đây CHỈ là khoá gom nhóm để đếm chỗ. Giờ người dùng chọn vẫn được lưu
    nguyên văn ở `viewing_time` — quy về hai buổi rồi vứt phút giờ đi là làm
    mất dữ liệu họ đã nhập, và lịch trả về sẽ sai so với lịch họ đặt.
    """
    hour = int(viewing_time.split(":", 1)[0])
    return TourSlot.MORNING if hour < 12 else TourSlot.AFTERNOON


class SchedulePropertyViewingRequest(BaseModel):
    project_id: str = Field(..., min_length=1, description="Mã dự án, ví dụ PRJ-001")
    viewing_date: date = Field(..., description="Ngày xem nhà, YYYY-MM-DD")
    viewing_time: str = Field(
        ...,
        pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$",
        description="Giờ xem nhà, HH:MM 24h",
    )
    resident_id: str | None = Field(default=None, description="ID cư dân (NULL = khách)")


class InterestType(StrEnum):
    BUY = "buy"
    RENT = "rent"
    CONSULTATION = "consultation"


class PreferredContactTime(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class RegisterPropertyInterestRequest(BaseModel):
    project_id: str = Field(..., min_length=1, description="Mã dự án quan tâm")
    interest_type: InterestType = Field(..., description="buy | rent | consultation")
    preferred_contact_time: PreferredContactTime = Field(..., description="Khung giờ muốn được liên hệ")
    consent: bool = Field(..., description="Đồng ý được liên hệ — phải là true")
    resident_id: str | None = Field(default=None, description="ID cư dân (tùy chọn)")

    @model_validator(mode="after")
    def _consent_must_be_granted(self) -> "RegisterPropertyInterestRequest":
        """`consent` phải là literal true.

        Pydantic coerce "false"/0/"" thành bool nên chỉ khai báo `bool` là chưa
        đủ: đăng ký nhận liên hệ mà không có đồng ý rõ ràng là vấn đề về dữ
        liệu cá nhân, không phải chi tiết validation. Từ chối tại schema để
        không nhánh gọi nào bỏ sót được.
        """
        if self.consent is not True:
            raise ValueError("consent phải là true — không đăng ký khi người dùng chưa đồng ý.")
        return self
