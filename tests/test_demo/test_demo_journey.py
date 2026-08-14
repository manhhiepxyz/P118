"""Test hành trình demo đầy đủ — user đã chọn case này (AskUserQuestion).

Luồng: đặt lịch tham quan → đặt xe tham quan → đăng ký tư vấn.
Xác minh dữ liệu TỒN TẠI qua CẢ HAI lớp (2 lớp dữ liệu độc lập — xem dưới):

  PHẦN 1 — Mock API (monolith src.mock.main, in-memory store):
      resident → tour → shuttle → consultation. Verify envelope + guard trùng.
  PHẦN 2 — Service layer DB (PostgreSQL test DB):
      cùng hành trình qua ResidentService / TourService / ShuttleService /
      ConsultationService, rồi get() từng bản ghi → xác minh dữ liệu đã PERSIST.

LƯU Ý kiến trúc: mock API là in-memory (src.mock.store.Store), service layer
đọc/ghi PostgreSQL — HAI kho dữ liệu riêng. Demo test chạy cả hai để chứng minh
nghiệp vụ nhất quán trên từng tầng, không đọc chéo giữa hai tầng.

Yêu cầu TEST_DATABASE_URL (skip local / FAIL CI). Chạy:
    TEST_DATABASE_URL=... pytest tests/test_demo/ -v
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.db.migrations import run_migrations
from src.mock.main import app
from src.mock.store import store
from src.services.mock.consultation_service import (
    ConsultationAlreadyExistsError,
    ConsultationService,
)
from src.services.mock.resident_service import ResidentAlreadyExistsError, ResidentService
from src.services.mock.shuttle_service import (
    ShuttleAlreadyBookedError,
    ShuttleService,
)
from src.services.mock.tour_service import TourAlreadyBookedError, TourService
from tests._dbcheck import require_test_database_url

AREA = "Vinhomes Ocean Park"
TOUR_DATE = "2026-08-25"
SLOT = "MORNING"


@pytest_asyncio.fixture(scope="session")
async def demo_pool() -> asyncpg.Pool:
    test_url = require_test_database_url()
    pool = await asyncpg.create_pool(dsn=test_url, min_size=1, max_size=5)
    await run_migrations(pool)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_demo_tables(demo_pool: asyncpg.Pool) -> None:
    store.reset()  # reset in-memory mock store trước mỗi test
    yield
    async with demo_pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE TABLE consultations, shuttle_bookings, tour_bookings, "
            "tour_capacity, residents RESTART IDENTITY CASCADE"
        )


# ---------------------------------------------------------------------------
# PHẦN 1 — Hành trình qua Mock API (in-memory)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_journey_mock_api() -> None:
    """Đặt lịch → đặt xe → tư vấn qua mock API, verify envelope + guard trùng."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Bước 0: cư dân
        resident = await ac.post(
            "/api/residents",
            json={"full_name": "Lâm Thành Bảo", "apartment_code": "A1201", "residential_area": AREA},
        )
        assert resident.status_code == 201
        resident_id = resident.json()["data"]["resident_id"]

        # Bước 1: đặt lịch tham quan dự án căn hộ
        tour = await ac.post(
            "/api/tours/bookings",
            json={"residential_area": AREA, "tour_date": TOUR_DATE, "tour_slot": SLOT, "resident_id": resident_id},
        )
        assert tour.status_code == 201
        tour_data = tour.json()["data"]
        assert tour_data["tour_id"].startswith("TOUR-")
        assert tour_data["residential_area"] == AREA
        assert tour_data["tour_date"] == TOUR_DATE
        assert tour_data["tour_slot"] == SLOT
        tour_id = tour_data["tour_id"]

        # Bước 2: đặt xe để đi tham quan căn hộ
        shuttle = await ac.post(
            "/api/shuttles/bookings",
            json={"tour_id": tour_id, "tour_date": TOUR_DATE, "passenger_count": 4},
        )
        assert shuttle.status_code == 201
        shuttle_data = shuttle.json()["data"]
        assert shuttle_data["shuttle_id"].startswith("SHUTTLE-")
        assert shuttle_data["tour_id"] == tour_id
        assert shuttle_data["passenger_count"] == 4

        # Bước 3: đăng ký tư vấn (mua — đầu tư)
        consultation = await ac.post(
            "/api/consultations",
            json={"consultation_type": "BUY", "buy_sub_type": "INVEST", "resident_id": resident_id},
        )
        assert consultation.status_code == 201
        consultation_data = consultation.json()["data"]
        assert consultation_data["consultation_id"].startswith("CONS-")
        assert consultation_data["consultation_type"] == "BUY"
        assert consultation_data["buy_sub_type"] == "INVEST"

        # Guard: hành trình không thể trùng lặp
        dup_tour = await ac.post(
            "/api/tours/bookings",
            json={"residential_area": AREA, "tour_date": TOUR_DATE, "tour_slot": SLOT, "resident_id": resident_id},
        )
        assert dup_tour.status_code == 409
        assert dup_tour.json()["error_code"] == "TOUR_ALREADY_BOOKED"

        dup_shuttle = await ac.post(
            "/api/shuttles/bookings",
            json={"tour_id": tour_id, "tour_date": TOUR_DATE, "passenger_count": 4},
        )
        assert dup_shuttle.status_code == 409
        assert dup_shuttle.json()["error_code"] == "SHUTTLE_ALREADY_BOOKED"

        dup_consultation = await ac.post(
            "/api/consultations",
            json={"consultation_type": "BUY", "buy_sub_type": "RESIDE", "resident_id": resident_id},
        )
        assert dup_consultation.status_code == 409
        assert dup_consultation.json()["error_code"] == "CONSULTATION_ALREADY_EXISTS"


# ---------------------------------------------------------------------------
# PHẦN 2 — Hành trình qua Service layer DB (PostgreSQL)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_journey_db_service_layer(demo_pool: asyncpg.Pool) -> None:
    """Cùng hành trình qua service layer — xác minh dữ liệu PERSIST trong Postgres."""
    # Bước 0: cư dân
    resident_id = (
        await ResidentService(demo_pool).register(
            full_name="Lâm Thành Bảo", apartment_code="A1201", residential_area=AREA
        )
    )["resident_id"]

    # Bước 1: đặt lịch tham quan → đọc lại từ DB
    tour = await TourService(demo_pool).book(AREA, TOUR_DATE, SLOT, resident_id=resident_id)
    persisted_tour = await TourService(demo_pool).get(tour["tour_id"])
    assert persisted_tour["tour_id"] == tour["tour_id"]
    assert persisted_tour["residential_area"] == AREA
    assert persisted_tour["resident_id"] == resident_id

    # Bước 2: đặt xe tham quan → đọc lại từ DB
    shuttle = await ShuttleService(demo_pool).book(tour["tour_id"], TOUR_DATE, 4)
    persisted_shuttle = await ShuttleService(demo_pool).get(shuttle["shuttle_id"])
    assert persisted_shuttle["tour_id"] == tour["tour_id"]
    assert persisted_shuttle["passenger_count"] == 4

    # Bước 3: đăng ký tư vấn (mua — đầu tư) → đọc lại từ DB
    consultation = await ConsultationService(demo_pool).register("BUY", buy_sub_type="INVEST", resident_id=resident_id)
    persisted_consultation = await ConsultationService(demo_pool).get(consultation["consultation_id"])
    assert persisted_consultation["consultation_type"] == "BUY"
    assert persisted_consultation["buy_sub_type"] == "INVEST"
    assert persisted_consultation["resident_id"] == resident_id

    # Guard: DB cũng chặn trùng (domain error, không phải 409 HTTP)
    with pytest.raises(TourAlreadyBookedError):
        await TourService(demo_pool).book(AREA, TOUR_DATE, SLOT, resident_id=resident_id)
    with pytest.raises(ShuttleAlreadyBookedError):
        await ShuttleService(demo_pool).book(tour["tour_id"], TOUR_DATE, 4)
    with pytest.raises(ConsultationAlreadyExistsError):
        await ConsultationService(demo_pool).register("BUY", buy_sub_type="RESIDE", resident_id=resident_id)
    with pytest.raises(ResidentAlreadyExistsError):
        await ResidentService(demo_pool).register(full_name="Người 2", apartment_code="A1201", residential_area=AREA)
