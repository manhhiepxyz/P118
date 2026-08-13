"""Domain booking đỗ xe + thanh toán, PostgreSQL là nguồn sự thật DUY NHẤT.

Vì sao cần file này:

Transport provider và Payment provider chạy trong HAI container khác nhau, mỗi
container tự tạo `Store()` RAM riêng. Payment vì thế không thể nhìn thấy booking
do Transport tạo — nó buộc phải tin `booking_id` + `amount` do caller đưa vào,
tức là không kiểm được gì cả. Không có cách nào vá bằng shared in-memory store:
hai process không dùng chung bộ nhớ.

PostgreSQL là điểm chung duy nhất hai container đã cùng trỏ tới (`DATABASE_URL`
trong docker-compose.yml giống nhau).

Chống double-charge có hai lớp, cả hai đều là constraint của database chứ không
phải logic "SELECT rồi INSERT" ở tầng ứng dụng:

  1. `uq_payments_idempotency_key` — cùng một retry (cùng workflow + task) chỉ
     tạo được một row, kể cả khi row đầu còn PENDING.
  2. `uq_payments_paid_booking`    — một booking chỉ có tối đa một payment PAID,
     kể cả khi hai request đến từ hai đường khác nhau.

Toàn bộ đọc-kiểm-ghi nằm trong MỘT transaction với `SELECT ... FOR UPDATE` trên
booking, nên hai request đồng thời không thể cùng đi qua vòng kiểm.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Bảng giá theo zone. Đây là báo giá AUTHORITATIVE: chỉ provider được quyết
# định số tiền, người dùng và LLM không bao giờ tự khai.
ZONE_PRICES: dict[str, int] = {"ZONE_A": 150_000, "ZONE_B": 100_000}
CURRENCY = "VND"


class BookingError(Exception):
    """Lỗi nghiệp vụ có mã ổn định để provider map sang HTTP status.

    Message chỉ mô tả loại lỗi. Không bao giờ chứa payload, connection string
    hay giá trị người dùng nhập.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Booking:
    booking_id: str
    vehicle_id: str
    parking_zone: str
    booking_date: str
    amount: int
    currency: str

    def as_output(self) -> dict[str, Any]:
        """Đúng 5 canonical output field của `book_parking`."""
        return {
            "booking_id": self.booking_id,
            "parking_zone": self.parking_zone,
            "booking_date": self.booking_date,
            "amount": self.amount,
            "currency": self.currency,
        }


@dataclass(frozen=True)
class Payment:
    payment_id: str
    booking_id: str
    amount: int
    currency: str
    payment_status: str

    def as_output(self) -> dict[str, Any]:
        return {"payment_id": self.payment_id, "payment_status": self.payment_status}


def _row_to_booking(row: asyncpg.Record) -> Booking:
    booking_date = row["booking_date"]
    return Booking(
        booking_id=row["booking_id"],
        vehicle_id=row["vehicle_id"],
        parking_zone=row["parking_zone"],
        booking_date=booking_date.isoformat() if isinstance(booking_date, date) else str(booking_date),
        amount=row["amount"],
        currency=row["currency"],
    )


# Sequence tương ứng cho từng loại ID. Tạo trong schema_migrations.sql.
_SEQUENCES = {
    "RES": "seq_resident_id",
    "VEH": "seq_vehicle_id",
    "BOOK": "seq_booking_id",
    "PAY": "seq_payment_id",
}


async def _next_id(conn: asyncpg.Connection, prefix: str) -> str:
    """Sinh ID kế tiếp bằng sequence của PostgreSQL.

    KHÔNG dùng `SELECT max(id) + 1`: nhiều transaction đồng thời đọc cùng một
    giá trị rồi cùng INSERT, gây va chạm PRIMARY KEY. Lỗi đó còn bị map nhầm
    thành BOOKING_ALREADY_EXISTS, khiến người dùng nhận thông báo hoàn toàn sai
    về nguyên nhân. Sequence là bộ đếm nguyên tử nằm ngoài transaction nên
    không bao giờ trả trùng, kể cả khi transaction sau đó rollback.
    """
    value = await conn.fetchval(f"SELECT nextval('{_SEQUENCES[prefix]}')")
    return f"{prefix}-{value:03d}"


def _violated(exc: asyncpg.UniqueViolationError, constraint: str) -> bool:
    """Đúng constraint nào bị vi phạm.

    Map mọi UniqueViolationError về một mã lỗi là nói dối người dùng: va chạm
    PRIMARY KEY và "xe này đã đặt ngày đó" là hai chuyện khác hẳn nhau.
    """
    return getattr(exc, "constraint_name", None) == constraint


@dataclass(frozen=True)
class Resident:
    resident_id: str
    full_name: str
    apartment_code: str
    residential_area: str

    def as_output(self) -> dict[str, Any]:
        """`register_resident` chỉ trả `resident_id`.

        KHÔNG trả full_name: đó là PII, và contract không yêu cầu.
        """
        return {"resident_id": self.resident_id}


# ---------------------------------------------------------------------------
# Resident
#
# LƯU Ý KIẾN TRÚC: đây là năng lực của provider, KHÔNG phải đường để Agent cấp
# quyền cư dân. `ResidentAccessBoundary` chặn `register_resident` khỏi mọi
# TaskPlan. Việc liên kết/xác minh hồ sơ cư dân xảy ra ngoài Agent.
# ---------------------------------------------------------------------------


async def create_resident(
    pool: asyncpg.Pool,
    *,
    full_name: str,
    apartment_code: str,
    residential_area: str,
) -> Resident:
    """Đăng ký cư dân. Không pre-check rồi INSERT: để constraint làm trọng tài."""
    if not full_name.strip() or not apartment_code.strip() or not residential_area.strip():
        raise BookingError("INVALID_INPUT", "Resident fields must not be empty")

    async with pool.acquire() as conn, conn.transaction():
        resident_id = await _next_id(conn, "RES")
        try:
            await conn.execute(
                """
                INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)
                VALUES ($1, $2, $3, $4)
                """,
                resident_id,
                full_name,
                apartment_code,
                residential_area,
            )
        except asyncpg.UniqueViolationError as exc:
            if _violated(exc, "uq_residents_apt_area"):
                raise BookingError("RESIDENT_ALREADY_EXISTS", "Apartment already has a registered resident") from exc
            raise BookingError("INVALID_INPUT", "Resident could not be registered") from exc

        return Resident(
            resident_id=resident_id,
            full_name=full_name,
            apartment_code=apartment_code,
            residential_area=residential_area,
        )


async def get_resident(pool: asyncpg.Pool, resident_id: str) -> Resident | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM residents WHERE resident_id = $1", resident_id)
    if row is None:
        return None
    return Resident(
        resident_id=row["resident_id"],
        full_name=row["full_name"],
        apartment_code=row["apartment_code"],
        residential_area=row["residential_area"],
    )


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    resident_id: str
    plate_number: str
    vehicle_type: str

    def as_output(self) -> dict[str, Any]:
        """`register_vehicle` chỉ trả đúng `vehicle_id` theo contract."""
        return {"vehicle_id": self.vehicle_id}


# ---------------------------------------------------------------------------
# Vehicle
# ---------------------------------------------------------------------------


async def create_vehicle(
    pool: asyncpg.Pool,
    *,
    resident_id: str,
    plate_number: str,
    vehicle_type: str,
) -> Vehicle:
    """Đăng ký xe cho một cư dân ĐÃ tồn tại.

    Không pre-check biển số rồi mới INSERT: hai request đồng thời đều qua được
    pre-check rồi cùng ghi. Để `uq_vehicles_plate` làm trọng tài và bắt
    UniqueViolationError.
    """
    if vehicle_type not in {"car", "motorcycle"}:
        raise BookingError("INVALID_INPUT", "Unsupported vehicle type")
    if not plate_number.strip():
        raise BookingError("INVALID_INPUT", "Plate number must not be empty")

    async with pool.acquire() as conn, conn.transaction():
        resident = await conn.fetchrow("SELECT resident_id FROM residents WHERE resident_id = $1", resident_id)
        if resident is None:
            # Liên kết hồ sơ cư dân xảy ra NGOÀI Agent. Không tự tạo resident ở
            # đây: làm vậy là biến đăng ký xe thành đường giành quyền căn hộ.
            raise BookingError("RESIDENT_NOT_FOUND", "Resident not found")

        vehicle_id = await _next_id(conn, "VEH")
        try:
            await conn.execute(
                """
                INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)
                VALUES ($1, $2, $3, $4)
                """,
                vehicle_id,
                resident_id,
                plate_number,
                vehicle_type,
            )
        except asyncpg.UniqueViolationError as exc:
            if _violated(exc, "uq_vehicles_plate"):
                raise BookingError("VEHICLE_ALREADY_EXISTS", "Plate number is already registered") from exc
            raise BookingError("INVALID_INPUT", "Vehicle could not be registered") from exc

        return Vehicle(
            vehicle_id=vehicle_id,
            resident_id=resident_id,
            plate_number=plate_number,
            vehicle_type=vehicle_type,
        )


async def get_vehicle(pool: asyncpg.Pool, vehicle_id: str) -> Vehicle | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM vehicles WHERE vehicle_id = $1", vehicle_id)
    if row is None:
        return None
    return Vehicle(
        vehicle_id=row["vehicle_id"],
        resident_id=row["resident_id"],
        plate_number=row["plate_number"],
        vehicle_type=row["vehicle_type"],
    )


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


async def create_booking(
    pool: asyncpg.Pool,
    *,
    vehicle_id: str,
    parking_zone: str,
    booking_date: str,
) -> Booking:
    """Tạo booking trong một transaction có kiểm capacity.

    `SELECT ... FOR UPDATE` trên row capacity giữ khoá tới hết transaction, nên
    hai request đồng thời cho cùng zone/ngày không thể cùng đọc được số chỗ còn
    trống rồi cùng ghi.
    """
    if parking_zone not in ZONE_PRICES:
        raise BookingError("INVALID_INPUT", "Unsupported parking zone")

    booking_day = date.fromisoformat(booking_date)

    async with pool.acquire() as conn, conn.transaction():
        vehicle = await conn.fetchrow("SELECT vehicle_id FROM vehicles WHERE vehicle_id = $1", vehicle_id)
        if vehicle is None:
            raise BookingError("VEHICLE_NOT_FOUND", "Vehicle not found")

        await conn.execute(
            """
            INSERT INTO parking_capacity (parking_zone, booking_date, capacity)
            VALUES ($1::varchar, $2, COALESCE(
                (SELECT capacity FROM zone_capacity_config WHERE parking_zone = $1::varchar), 10))
            ON CONFLICT (parking_zone, booking_date) DO NOTHING
            """,
            parking_zone,
            booking_day,
        )
        capacity_row = await conn.fetchrow(
            "SELECT capacity FROM parking_capacity WHERE parking_zone = $1 AND booking_date = $2 FOR UPDATE",
            parking_zone,
            booking_day,
        )
        booked = await conn.fetchval(
            "SELECT COUNT(*) FROM parking_bookings WHERE parking_zone = $1 AND booking_date = $2",
            parking_zone,
            booking_day,
        )
        if capacity_row is not None and booked >= capacity_row["capacity"]:
            raise BookingError("NO_AVAILABILITY", "Parking zone is full for that date")

        booking_id = await _next_id(conn, "BOOK")
        amount = ZONE_PRICES[parking_zone]
        try:
            await conn.execute(
                """
                INSERT INTO parking_bookings
                    (booking_id, vehicle_id, parking_zone, booking_date, amount, currency)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                booking_id,
                vehicle_id,
                parking_zone,
                booking_day,
                amount,
                CURRENCY,
            )
        except asyncpg.UniqueViolationError as exc:
            if _violated(exc, "uq_bookings_vehicle_date"):
                raise BookingError("BOOKING_ALREADY_EXISTS", "Vehicle already booked for that date") from exc
            raise BookingError("INVALID_INPUT", "Booking could not be created") from exc

        return Booking(
            booking_id=booking_id,
            vehicle_id=vehicle_id,
            parking_zone=parking_zone,
            booking_date=booking_date,
            amount=amount,
            currency=CURRENCY,
        )


async def get_booking(pool: asyncpg.Pool, booking_id: str) -> Booking | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM parking_bookings WHERE booking_id = $1", booking_id)
    return None if row is None else _row_to_booking(row)


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------


async def create_payment(
    pool: asyncpg.Pool,
    *,
    booking_id: str,
    amount: int,
    currency: str,
    idempotency_key: str | None = None,
) -> Payment:
    """Thanh toán một booking. An toàn khi gọi lại với cùng idempotency key.

    Thứ tự kiểm bên trong MỘT transaction đang giữ khoá booking:
      1. booking tồn tại;
      2. amount khớp chính xác booking.amount;
      3. currency khớp booking.currency;
      4. chưa có payment PAID cho booking đó.

    Không bước nào tin dữ liệu caller đưa vào ngoài `booking_id` — số tiền được
    đối chiếu với báo giá authoritative đã lưu lúc tạo booking.
    """
    async with pool.acquire() as conn, conn.transaction():
        # Retry cùng key trả lại đúng payment cũ, không tạo giao dịch thứ hai.
        if idempotency_key is not None:
            existing = await conn.fetchrow("SELECT * FROM payments WHERE idempotency_key = $1", idempotency_key)
            if existing is not None:
                return Payment(
                    payment_id=existing["payment_id"],
                    booking_id=existing["booking_id"],
                    amount=existing["amount"],
                    currency=existing["currency"],
                    payment_status=existing["payment_status"],
                )

        booking_row = await conn.fetchrow("SELECT * FROM parking_bookings WHERE booking_id = $1 FOR UPDATE", booking_id)
        if booking_row is None:
            raise BookingError("BOOKING_NOT_FOUND", "Booking not found")

        booking = _row_to_booking(booking_row)
        if amount != booking.amount:
            raise BookingError("PAYMENT_AMOUNT_MISMATCH", "Amount does not match the booking")
        if currency != booking.currency:
            raise BookingError("PAYMENT_CURRENCY_MISMATCH", "Currency does not match the booking")

        already_paid = await conn.fetchrow(
            "SELECT payment_id FROM payments WHERE booking_id = $1 AND payment_status = 'PAID'",
            booking_id,
        )
        if already_paid is not None:
            raise BookingError("PAYMENT_ALREADY_COMPLETED", "Booking has already been paid")

        payment_id = await _next_id(conn, "PAY")
        try:
            await conn.execute(
                """
                INSERT INTO payments
                    (payment_id, booking_id, amount, currency, payment_status, idempotency_key)
                VALUES ($1, $2, $3, $4, 'PAID', $5)
                """,
                payment_id,
                booking_id,
                amount,
                currency,
                idempotency_key,
            )
        except asyncpg.UniqueViolationError as exc:
            # Hai request đồng thời cùng qua được vòng kiểm: database là trọng
            # tài cuối. Bên thua đọc lại payment của bên thắng.
            winner = await conn.fetchrow(
                "SELECT * FROM payments WHERE booking_id = $1 AND payment_status = 'PAID'",
                booking_id,
            )
            if winner is None:
                raise BookingError("PAYMENT_FAILED", "Payment could not be completed") from exc
            return Payment(
                payment_id=winner["payment_id"],
                booking_id=winner["booking_id"],
                amount=winner["amount"],
                currency=winner["currency"],
                payment_status=winner["payment_status"],
            )

        return Payment(
            payment_id=payment_id,
            booking_id=booking_id,
            amount=amount,
            currency=currency,
            payment_status="PAID",
        )


async def get_payment(pool: asyncpg.Pool, payment_id: str) -> Payment | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM payments WHERE payment_id = $1", payment_id)
    if row is None:
        return None
    return Payment(
        payment_id=row["payment_id"],
        booking_id=row["booking_id"],
        amount=row["amount"],
        currency=row["currency"],
        payment_status=row["payment_status"],
    )


def payment_idempotency_key(workflow_id: str, task_id: str) -> str:
    """Khoá deterministic: cùng workflow + cùng task luôn ra cùng một khoá.

    Nhờ vậy retry sau timeout — kể cả từ một process khác sau khi restart —
    vẫn rơi vào đúng row cũ thay vì tạo giao dịch mới.
    """
    return f"wf:{workflow_id}:task:{task_id}"
