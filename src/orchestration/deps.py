"""Factory dựng runtime cho execution boundary.

Owner: Mạnh Hiệp (Executor layer)
File: src/orchestration/deps.py

Mục đích:
  - Một nơi DUY NHẤT dựng các Connector thật + PostgreSQLWorkflowStateRepository
    để smoke test / API / demo dùng chung.
  - Tránh mỗi nơi tự hardcode base_url và database_url.

Cổng mặc định khớp docker-compose.yml — MỘT tool một owner, một service một cổng:
  ResidentConnector         → 8001  register_resident
  TransportConnector        → 8002  register_vehicle, book_parking
  PaymentConnector          → 8003  pay_fee
  PropertyConnector         → 8008  search_properties
  TourConnector             → 8005  schedule_property_viewing
  ResidentServicesConnector → 8006  create_maintenance_request, schedule_move
  ConsultationConnector     → 8007  register_property_interest
  ShuttleConnector          → 8009  book_shuttle

Trước đây PropertyConnector và TourConnector cùng trỏ 8005 (mock-tour không có
/api/properties/search) còn ResidentServicesConnector trỏ 8006 nơi Docker chạy
shuttle. Cả hai là 404 lúc chạy thật mà test in-process không thấy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from src.common.enums import ErrorCode, TaskStatus
from src.config import get_settings
from src.connectors.consultation import ConsultationConnector
from src.connectors.payment import PaymentConnector
from src.connectors.property import PropertyConnector
from src.connectors.resident import ResidentConnector
from src.connectors.resident_services import ResidentServicesConnector
from src.connectors.shuttle import ShuttleConnector
from src.connectors.tour import TourConnector
from src.connectors.transport import TransportConnector
from src.db.migrations import run_migrations
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.executor.executor import Executor
from src.orchestration.boundary import ValidatedExecutionBoundary


def build_connectors(
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
    property_url: str = "http://localhost:8008",
    resident_services_url: str = "http://localhost:8006",
    tour_url: str = "http://localhost:8005",
    consultation_url: str = "http://localhost:8007",
    shuttle_url: str = "http://localhost:8009",
    contact_profile: dict[str, Any] | None = None,
) -> list[Any]:
    """Dựng các Connector thật trỏ tới Mock Provider.

    Args:
        resident_url : Base URL Resident service (mặc định cổng 8001)
        transport_url: Base URL Transport service (mặc định cổng 8002)
        payment_url  : Base URL Payment service (mặc định cổng 8003)
        shuttle_url  : Base URL Shuttle service (mặc định cổng 8009)

    Returns:
        List Connector cho các provider nghiệp vụ.
    """
    return [
        ResidentConnector(base_url=resident_url),
        TransportConnector(base_url=transport_url),
        PaymentConnector(base_url=payment_url),
        PropertyConnector(base_url=property_url, contact_profile=contact_profile),
        TourConnector(base_url=tour_url),
        ResidentServicesConnector(base_url=resident_services_url),
        ConsultationConnector(base_url=consultation_url),
        ShuttleConnector(base_url=shuttle_url),
    ]


async def build_repository(*, migrate: bool = True) -> PostgreSQLWorkflowStateRepository:
    """Dựng PostgreSQLWorkflowStateRepository từ DATABASE_URL config.

    Tạo pool từ settings.database_url rồi chạy migration idempotent trước khi
    trả repository. PostgreSQL mới từ Docker vì vậy luôn có schema cần thiết.
    """
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url)
    try:
        if migrate:
            await run_migrations(pool)
    except BaseException:
        # Không để pool mở nếu startup/migration thất bại.
        await pool.close()
        raise
    return PostgreSQLWorkflowStateRepository(pool)


async def build_execution_boundary(
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
    property_url: str = "http://localhost:8005",
    resident_services_url: str = "http://localhost:8006",
    shuttle_url: str | None = None,
    contact_profile: dict[str, Any] | None = None,
    on_task_progress: Callable[[str, str, TaskStatus], Awaitable[None]] | None = None,
    on_failure: Callable[[str, str, ErrorCode, str, bool], None] | None = None,
) -> tuple[ValidatedExecutionBoundary, PostgreSQLWorkflowStateRepository]:
    """Dựng boundary tương thích trực tiếp với Planner graph.

    Repository được trả kèm để caller quản lý vòng đời pool khi cần. Boundary
    chỉ trả tuple chuẩn của ``Executor.execute``; không tạo wrapper result mới.
    """
    connectors, repository = await build_runtime(
        resident_url,
        transport_url,
        payment_url,
        property_url,
        resident_services_url,
        contact_profile=contact_profile,
        shuttle_url=shuttle_url,
    )
    executor = Executor(connectors, repository, on_progress=on_task_progress, on_failure=on_failure)
    return ValidatedExecutionBoundary(executor), repository


async def build_runtime(
    resident_url: str | None = None,
    transport_url: str | None = None,
    payment_url: str | None = None,
    property_url: str | None = None,
    resident_services_url: str | None = None,
    tour_url: str | None = None,
    consultation_url: str | None = None,
    shuttle_url: str | None = None,
    contact_profile: dict[str, Any] | None = None,
) -> tuple[list[Any], PostgreSQLWorkflowStateRepository]:
    """Dựng toàn bộ runtime: connectors + repository.

    Returns:
        (connectors, repository) để dựng Executor hoặc test từng tầng.
    """
    # URL mặc định lấy từ Settings, KHÔNG hardcode lại ở đây: trong container
    # phải là service DNS (http://mock-tour:8005), trên máy dev là localhost.
    # Hai nơi cùng giữ hằng số là hai nơi có thể lệch nhau.
    #
    # Truyền theo KEYWORD. Trước đây gọi positional, nên khi signature của
    # `build_connectors` thêm tham số ở giữa, `contact_profile` lặng lẽ trôi
    # vào `tour_url` — sai không lộ ra cho tới khi gọi HTTP thật.
    settings = get_settings()
    connectors = build_connectors(
        resident_url=resident_url or settings.resident_service_url,
        transport_url=transport_url or settings.transport_service_url,
        payment_url=payment_url or settings.payment_service_url,
        property_url=property_url or settings.property_service_url,
        resident_services_url=resident_services_url or settings.resident_services_service_url,
        tour_url=tour_url or settings.tour_service_url,
        consultation_url=consultation_url or settings.consultation_service_url,
        shuttle_url=shuttle_url or settings.shuttle_service_url,
        contact_profile=contact_profile,
    )
    repository = await build_repository()
    return connectors, repository
