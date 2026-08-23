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
from datetime import date, datetime, time, timedelta
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
            # SAVEPOINT quanh INSERT: một `UniqueViolation` làm HỎNG cả
            # transaction, và mọi câu lệnh sau đó — kể cả câu tra cứu để xử lý
            # đúng lỗi ấy — đều bị từ chối với InFailedSQLTransactionError.
            # Transaction lồng của asyncpg chính là savepoint, nên chỉ phần
            # INSERT bị cuộn lại.
            async with conn.transaction():
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
                # Đăng ký lại CHÍNH xe của mình là thao tác BẤT BIẾN: trả về xe
                # đã có, không báo trùng.
                #
                # Trước đây mọi lần đăng ký lại đều hỏng, kể cả khi người đăng
                # ký là chủ cũ. Điều đó làm hỏng mọi đường CHẠY LẠI một kế
                # hoạch: khi người dùng trả lời câu hỏi bổ sung ("đổi sang Khu
                # B"), backend lập lại kế hoạch từ đầu và chạy lại
                # `register_vehicle` cho biển số vừa đăng ký thành công ở lượt
                # trước — bước ấy hỏng, `book_parking` phụ thuộc vào nó hỏng
                # theo, và người dùng không bao giờ đổi được khu.
                #
                # Đo được: workflow cha `register_vehicle=SUCCESS`, workflow con
                # `register_vehicle=FAILED book_parking=FAILED`.
                #
                # Biển số của NGƯỜI KHÁC thì vẫn xung đột — đó là xung đột thật,
                # và trả về xe của người khác là rò rỉ dữ liệu.
                existing = await conn.fetchrow(
                    """
                    SELECT vehicle_id, resident_id, plate_number, vehicle_type
                    FROM vehicles WHERE plate_number = $1
                    """,
                    plate_number,
                )
                if existing is not None and existing["resident_id"] == resident_id:
                    return Vehicle(
                        vehicle_id=existing["vehicle_id"],
                        resident_id=existing["resident_id"],
                        plate_number=existing["plate_number"],
                        vehicle_type=existing["vehicle_type"],
                    )
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
    availability_already_approved: bool = False,
) -> Booking:
    """Tạo booking trong một transaction; tuỳ biên gọi có kiểm capacity.

    `SELECT ... FOR UPDATE` trên row capacity giữ khoá tới hết transaction, nên
    hai request đồng thời cho cùng zone/ngày không thể cùng đọc được số chỗ còn
    trống rồi cùng ghi.

    ``availability_already_approved`` chỉ dành cho HTTP mock provider được gọi
    SAU hàng đợi ``/review``. Ở luồng đó đơn vị đã ký quyết định còn chỗ; đọc
    thêm capacity seed trong main DB sẽ tạo người quyết định thứ hai có thể phủ
    quyết chính chữ ký ấy. Repository mặc định vẫn kiểm capacity để giữ primitive
    đặt chỗ an toàn cho caller không đi qua hàng đợi duyệt.
    """
    if parking_zone not in ZONE_PRICES:
        raise BookingError("INVALID_INPUT", "Unsupported parking zone")

    booking_day = date.fromisoformat(booking_date)

    async with pool.acquire() as conn, conn.transaction():
        vehicle = await conn.fetchrow("SELECT vehicle_id FROM vehicles WHERE vehicle_id = $1", vehicle_id)
        if vehicle is None:
            raise BookingError("VEHICLE_NOT_FOUND", "Vehicle not found")

        if not availability_already_approved:
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
                "SELECT COUNT(*) FROM parking_bookings"
                " WHERE parking_zone = $1 AND booking_date = $2 AND status = 'ACTIVE'",
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


@dataclass(frozen=True)
class ZoneChange:
    """Kết quả một lần đổi khu: chỗ đỗ sau khi đổi, và phần tiền đã hoàn lại.

    `refunded` là 0 ở mọi trường hợp trừ đúng một: khách ĐÃ trả tiền và khu mới
    RẺ HƠN. Nó không bao giờ âm — thu thêm là một quyết định của khách, không
    phải hệ quả của một lệnh đổi khu.
    """

    booking: Booking
    refunded: int = 0


async def change_booking_zone(pool: asyncpg.Pool, *, booking_id: str, parking_zone: str) -> ZoneChange:
    """Chuyển một chỗ đỗ ĐÃ GIỮ sang khu khác — MỘT thao tác, một transaction.

    Không phải "huỷ rồi đặt lại". Hai lời gọi tách rời để lại một khoảng trống
    giữa chúng, và trong khoảng ấy chỗ ở khu mới có thể bị người khác lấy —
    khách vào với một chỗ trong tay, ra tay trắng. Tệ hơn cả không cho đổi.

    `uq_bookings_vehicle_date` cấm một xe giữ hai chỗ cùng ngày, nên
    huỷ-rồi-đặt BẮT BUỘC có khoảng trống ấy; không vá được bằng thứ tự.

    Khoá theo đúng kỷ luật của `create_booking`: `SELECT ... FOR UPDATE` trên
    dòng capacity của khu MỚI, đếm chỗ đã dùng trong cùng transaction. Khu mới
    hết chỗ thì `BookingError` bay ra, transaction rollback, và chỗ cũ còn
    nguyên cả zone lẫn giá.

    `amount` do SERVER tính lại theo `ZONE_PRICES` — không nhận từ caller, cùng
    lý do với lúc đặt mới: giá là dữ liệu của bên bán.

    `booking_id` KHÔNG đổi. Đó là lợi thế lớn nhất so với huỷ-rồi-đặt: thẻ chờ
    thanh toán, hoá đơn và mọi tham chiếu của khách vẫn trỏ đúng chỗ.

    ĐÃ TRẢ TIỀN rồi mới đổi sang khu rẻ hơn thì phần trả thừa quay lại NGAY
    TRONG transaction này. Hoàn CHÊNH LỆCH, không hoàn toàn bộ rồi thu lại:
    hoàn toàn bộ để chỗ đỗ rơi vào trạng thái chưa thanh toán giữa hai lần động
    vào tiền, và khách bị trừ hai lần cho một lần đổi khu. Hoàn toàn bộ chỉ
    đúng cho yêu cầu HUỶ, và yêu cầu ấy đi đường khác.

    Cùng transaction vì hai lệnh tách rời để lại một khoảng mà
    `parking_bookings.amount` đã là giá mới còn `payments` vẫn giữ giá cũ — ai
    đọc vào khoảng ấy cũng thấy khách đang thừa tiền mà không có bản ghi nào
    giải thích. Khu mới hết chỗ thì rollback cuốn theo cả lệnh hoàn, nên không
    ai được hoàn tiền cho một lần đổi khu chưa từng xảy ra.

    Chiều ngược lại KHÔNG tự thu thêm: tiền đi ra khỏi túi khách phải qua cổng
    duyệt thanh toán như mọi khoản khác. Chỗ đỗ vẫn mang giá mới, và phần còn
    thiếu là việc của tầng trên.
    """
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow("SELECT * FROM parking_bookings WHERE booking_id = $1 FOR UPDATE", booking_id)
        if row is None:
            raise BookingError("BOOKING_NOT_FOUND", "Booking not found")
        if parking_zone not in ZONE_PRICES:
            raise BookingError("INVALID_INPUT", "Unknown parking zone")

        # Đổi về đúng khu đang giữ: không phải lỗi, và cũng không được đi tiếp
        # qua phần kiểm capacity — chính chỗ này đang chiếm một suất của khu đó,
        # nên một khu sức chứa 1 sẽ tự báo hết chỗ với chủ của nó.
        if row["parking_zone"] == parking_zone:
            return ZoneChange(booking=_row_to_booking(row))

        booking_day = row["booking_date"]
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
            "SELECT COUNT(*) FROM parking_bookings WHERE parking_zone = $1 AND booking_date = $2 AND status = 'ACTIVE'",
            parking_zone,
            booking_day,
        )
        if capacity_row is not None and booked >= capacity_row["capacity"]:
            raise BookingError("NO_AVAILABILITY", "Parking zone is full for that date")

        # Capacity khu CŨ được trả lại bằng chính lệnh này: sức chứa còn trống
        # tính bằng `COUNT(*)` trên `parking_bookings`, nên dòng rời khỏi khu cũ
        # là khu cũ trống thêm một suất. Không có bộ đếm riêng để lệch.
        updated = await conn.fetchrow(
            """
            UPDATE parking_bookings
               SET parking_zone = $2, amount = $3, updated_at = NOW()
             WHERE booking_id = $1
            RETURNING *
            """,
            booking_id,
            parking_zone,
            ZONE_PRICES[parking_zone],
        )
        return ZoneChange(
            booking=_row_to_booking(updated),
            refunded=await _settle_difference(conn, booking_id, ZONE_PRICES[parking_zone]),
        )


async def _settle_difference(conn: asyncpg.Connection, booking_id: str, phai_tra: int) -> int:
    """Trả lại phần khách đã trả thừa. Trả 0 khi không có gì để trả.

    "Còn giữ" = tổng đã trả trừ tổng đã hoàn. Trừ đi phần đã hoàn là thứ khiến
    hàm này gọi lại được: đổi khu hai lần liên tiếp chỉ hoàn đúng một lần.

    Không có dòng `PAID` nào thì cũng không có gì để hoàn — kể cả khi khu mới rẻ
    hơn. Chỗ đỗ chưa trả tiền thì tầng trên chỉ cần ghim lại thẻ theo giá mới.

    Phần hoàn được ghi thành một dòng `REFUNDED` RIÊNG chứ không sửa dòng `PAID`:
    dòng `PAID` là bản ghi khách đã trả bao nhiêu và lúc nào, và viết đè lên nó
    là làm mất một sự kiện có thật. `uq_payments_paid_booking` là partial index
    chỉ trên `PAID`, nên nhiều dòng `REFUNDED` không đụng ràng buộc nào.
    """
    rows = await conn.fetch(
        "SELECT amount, currency, payment_status FROM payments WHERE booking_id = $1 FOR UPDATE", booking_id
    )
    da_tra = [r for r in rows if r["payment_status"] == "PAID"]
    if not da_tra:
        return 0
    con_giu = sum(r["amount"] for r in da_tra) - sum(r["amount"] for r in rows if r["payment_status"] == "REFUNDED")
    chenh = con_giu - phai_tra
    if chenh < 0:
        # Khu mới ĐẮT HƠN phần khách đã trả. Fail-closed cho tới khi có đường
        # thu thêm: đi tiếp ở đây nghĩa là khách giữ một chỗ 150.000 sau khi trả
        # 100.000, và không bản ghi nào nói ra điều đó.
        #
        # Không tự thu: tiền đi RA khỏi túi khách là quyết định của khách, và nó
        # phải qua cổng duyệt thanh toán như mọi khoản khác. `BookingError` làm
        # rollback cuốn theo cả lệnh đổi khu, nên chỗ cũ và khoản đã trả còn
        # nguyên — khách chọn khu khác, hoặc liên hệ hỗ trợ.
        raise BookingError(
            "PAYMENT_TOP_UP_REQUIRED",
            "Khu này có phí cao hơn khoản bạn đã thanh toán. Bạn chọn khu khác, "
            "hoặc liên hệ bộ phận hỗ trợ để bù phần chênh lệch giúp mình nhé.",
        )
    if chenh == 0:
        return 0
    await conn.execute(
        """
        INSERT INTO payments (payment_id, booking_id, amount, currency, payment_status)
        VALUES ($1, $2, $3, $4, 'REFUNDED')
        """,
        await _next_id(conn, "PAY"),
        booking_id,
        chenh,
        da_tra[0]["currency"],
    )
    return chenh


# Huỷ trước MỐC NÀY thì được hoàn tiền; muộn hơn thì vẫn huỷ, không hoàn.
#
# Tính theo 24 GIỜ chứ không theo ngày lịch: "huỷ trước 1 ngày" hiểu theo ngày
# lịch thì huỷ lúc 23:59 hôm trước vẫn được hoàn, và đơn vị mất trọn một suất
# mà không kịp bán lại.
_REFUND_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Cancellation:
    """Kết quả một lần huỷ: đã huỷ chưa, và hoàn lại bao nhiêu.

    `refunded == 0` KHÔNG có nghĩa là huỷ hỏng. Nó có ba nghĩa, và cả ba đều là
    kết cục hợp lệ: chưa trả tiền lần nào, đã hoàn hết từ trước, hoặc huỷ muộn.
    `refund_denied` phân biệt cái thứ ba — đó là thứ khách cần được nói rõ.
    """

    booking_id: str
    refunded: int = 0
    refund_denied: bool = False


async def cancel_parking_booking(pool: asyncpg.Pool, *, booking_id: str, now: datetime) -> Cancellation:
    """Huỷ một chỗ đỗ. Trả suất về kho, và hoàn tiền theo luật 24 giờ.

    Huỷ LUÔN thành công. Chỉ tiền là có điều kiện, và điều kiện ấy do CODE quyết
    theo mốc thời gian — không phải đơn vị quyết từng ca. Nhờ vậy khách biết
    trước kết cục ngay lúc bấm, thay vì chờ một câu trả lời có thể khác nhau.

    `booking_date` chỉ có NGÀY, không có giờ. Mốc 24 giờ vì thế tính từ 00:00
    của ngày đặt: huỷ trước `booking_date - 1 ngày, 00:00` thì được hoàn. Đây là
    một lựa chọn, không phải một sự thật — chỗ đỗ tính theo ngày nên không có
    giờ bắt đầu nào chính xác hơn.

    KHÔNG xoá dòng booking. Với một lần huỷ muộn, dòng `payments` PAID còn
    nguyên và trỏ vào chỗ này; xoá nó thì khoản tiền ấy trỏ vào hư không và
    không ai giải thích được nó là tiền gì. Suất được trả về kho bằng chính cột
    `status`: mọi phép đếm sức chứa đều lọc `ACTIVE`.

    Gọi lại trên một chỗ đã huỷ là im lặng thành công, và KHÔNG hoàn lần hai.
    """
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow("SELECT * FROM parking_bookings WHERE booking_id = $1 FOR UPDATE", booking_id)
        if row is None:
            raise BookingError("BOOKING_NOT_FOUND", "Booking not found")
        if str(row["status"]) == "CANCELLED":
            return Cancellation(booking_id=booking_id)

        han = datetime.combine(row["booking_date"], time.min, tzinfo=now.tzinfo) - _REFUND_WINDOW
        con_kip = now < han

        hoan = 0
        if con_kip:
            # Hoàn TOÀN BỘ phần khách còn giữ, không phải `booking.amount`: nếu
            # họ từng đổi sang khu rẻ hơn thì phần chênh đã quay lại rồi, và
            # hoàn theo giá niêm yết là trả cho họ nhiều hơn số đã thu.
            hoan = await _settle_difference(conn, booking_id, 0)

        await conn.execute(
            "UPDATE parking_bookings SET status = 'CANCELLED', updated_at = NOW() WHERE booking_id = $1",
            booking_id,
        )
        # Khoản đã trả mà KHÔNG được hoàn chỉ có nghĩa khi thật sự còn tiền.
        da_tra = await conn.fetchval(
            "SELECT COUNT(*) FROM payments WHERE booking_id = $1 AND payment_status = 'PAID'", booking_id
        )
        return Cancellation(booking_id=booking_id, refunded=hoan, refund_denied=not con_kip and bool(da_tra))


async def get_booking(pool: asyncpg.Pool, booking_id: str) -> Booking | None:
    """Chỗ đỗ CÒN HIỆU LỰC. Đã huỷ thì trả `None` — nó không còn là chỗ của ai.

    Dòng vẫn nằm trong bảng (xem cột `status`), nhưng mọi đường nghiệp vụ hỏi
    "chỗ này thế nào" đều đang hỏi về một chỗ đang giữ. Trả về một chỗ đã huỷ ở
    đây nghĩa là thẻ thanh toán, báo giá và lệnh đổi khu đều làm việc trên nó.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM parking_bookings WHERE booking_id = $1 AND status = 'ACTIVE'", booking_id
        )
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


async def cancel_booking(pool: asyncpg.Pool, booking_id: str) -> bool:
    """Release capacity: xoá booking CHỈ khi chưa có payment PAID.

    Chính sách (Phase C ranee): booking đã được thanh toán không bao giờ bị xoá
    mặc định — gọi `refund_payment` trước rồi mới `cancel_booking`. FK order:
    payments → parking_bookings, nên xoá booking trước phải dọn payment con.

    Idempotent: lần hai xoá 0 row → trả False. Trả True nếu booking được xoá.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Xoá payment KHÔNG PAID trước (FK payments → parking_bookings).
            # Payment PAID bị chặn ở câu DELETE dưới → booking PAID không chạm.
            await conn.execute(
                """
                DELETE FROM payments
                WHERE booking_id = $1
                  AND payment_status <> 'PAID'
                """,
                booking_id,
            )
            tag = await conn.execute(
                """
                DELETE FROM parking_bookings
                WHERE booking_id = $1
                  AND NOT EXISTS (
                      SELECT 1 FROM payments p
                      WHERE p.booking_id = parking_bookings.booking_id
                        AND p.payment_status = 'PAID'
                  )
                """,
                booking_id,
            )
    return tag.endswith(" 1") or tag.endswith("1")


async def refund_payment(pool: asyncpg.Pool, booking_id: str) -> bool:
    """Flip payment PAID → REFUNDED (mock provider-side). Idempotent.

    `uq_payments_paid_booking` là partial index CHỈ trên payment_status='PAID',
    nên flip sang REFUNDED không đụng constraint: booking có thể được trả lại
    đúng một lần. Trả True nếu có payment PAID được hoàn.
    """
    async with pool.acquire() as conn:
        tag = await conn.execute(
            """
            UPDATE payments
            SET payment_status = 'REFUNDED', updated_at = NOW()
            WHERE booking_id = $1
              AND payment_status = 'PAID'
            """,
            booking_id,
        )
    return tag.endswith(" 1") or tag.endswith("1")


def payment_idempotency_key(workflow_id: str, booking_id: str) -> str:
    """Khoá deterministic: cùng workflow + cùng booking luôn ra cùng một khoá.

    Nhờ vậy retry sau timeout — kể cả từ một process khác sau khi restart —
    vẫn rơi vào đúng row cũ thay vì tạo giao dịch mới.

    Khoá theo BOOKING chứ không theo task_id, vì task_id chỉ có ở tầng
    orchestration còn connector thì không nhận nó: `Connector.execute` chỉ có
    `(tool_name, input_data)`, và thêm tham số vào đó là sửa cả 8 connector cho
    một nhu cầu của đúng một cái.

    `booking_id` nằm sẵn trong input của `pay_fee`, nên connector tự dựng được
    khoá mà không cần trạng thái thay đổi được — quan trọng vì các task trong
    cùng một wave chạy đồng thời.

    Vẫn giữ `workflow_id` trong khoá: bỏ nó đi thì một lần trả tiền MỚI cho
    booding đã hoàn tiền sẽ rơi vào bản ghi REFUNDED cũ và được coi là đã trả.
    """
    return f"wf:{workflow_id}:booking:{booking_id}"
