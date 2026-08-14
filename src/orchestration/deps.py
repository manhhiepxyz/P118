"""Factory dựng runtime cho execution boundary.

Owner: Mạnh Hiệp (Executor layer)
File: src/orchestration/deps.py

Mục đích:
  - Một nơi DUY NHẤT dựng các Connector thật + PostgreSQLWorkflowStateRepository
    để smoke test / API / demo dùng chung.
  - Tránh mỗi nơi tự hardcode base_url và database_url.

Cổng mặc định khớp docker-compose.yml:
  ResidentConnector  → http://localhost:8001
  TransportConnector → http://localhost:8002
  PaymentConnector   → http://localhost:8003
  PropertyConnector  → http://localhost:8005
  ResidentServicesConnector → http://localhost:8006
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from src.common.enums import ErrorCode, TaskStatus
from src.config import get_settings
from src.connectors.payment import PaymentConnector
from src.connectors.property import PropertyConnector
from src.connectors.resident import ResidentConnector
from src.connectors.resident_services import ResidentServicesConnector
from src.connectors.transport import TransportConnector
from src.db.migrations import run_migrations
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.executor.executor import Executor
from src.orchestration.boundary import ValidatedExecutionBoundary


def build_connectors(
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
    property_url: str = "http://localhost:8005",
    resident_services_url: str = "http://localhost:8006",
    contact_profile: dict[str, Any] | None = None,
) -> list[Any]:
    """Dựng các Connector thật trỏ tới Mock Provider.

    Args:
        resident_url : Base URL Resident service (mặc định cổng 8001)
        transport_url: Base URL Transport service (mặc định cổng 8002)
        payment_url  : Base URL Payment service (mặc định cổng 8003)

    Returns:
        List Connector cho năm provider nghiệp vụ.
    """
    return [
        ResidentConnector(base_url=resident_url),
        TransportConnector(base_url=transport_url),
        PaymentConnector(base_url=payment_url),
        PropertyConnector(base_url=property_url, contact_profile=contact_profile),
        ResidentServicesConnector(base_url=resident_services_url),
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
    )
    executor = Executor(connectors, repository, on_progress=on_task_progress, on_failure=on_failure)
    return ValidatedExecutionBoundary(executor), repository


async def build_runtime(
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
    property_url: str = "http://localhost:8005",
    resident_services_url: str = "http://localhost:8006",
    contact_profile: dict[str, Any] | None = None,
) -> tuple[list[Any], PostgreSQLWorkflowStateRepository]:
    """Dựng toàn bộ runtime: connectors + repository.

    Returns:
        (connectors, repository) để dựng Executor hoặc test từng tầng.
    """
    connectors = build_connectors(
        resident_url,
        transport_url,
        payment_url,
        property_url,
        resident_services_url,
        contact_profile,
    )
    repository = await build_repository()
    return connectors, repository
