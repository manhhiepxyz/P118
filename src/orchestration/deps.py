"""Factory dựng runtime cho execution boundary.

Owner: Mạnh Hiệp (Executor layer)
File: src/orchestration/deps.py

Mục đích:
  - Một nơi DUY NHẤT dựng 3 Connector thật + PostgreSQLWorkflowStateRepository
    để smoke test / API / demo dùng chung.
  - Tránh mỗi nơi tự hardcode base_url và database_url.

Cổng mặc định khớp docker-compose.yml:
  ResidentConnector  → http://localhost:8001
  TransportConnector → http://localhost:8002
  PaymentConnector   → http://localhost:8003
"""

from __future__ import annotations

from typing import Any

import asyncpg

from src.config import get_settings
from src.connectors.payment import PaymentConnector
from src.connectors.resident import ResidentConnector
from src.connectors.transport import TransportConnector
from src.db.migrations import run_migrations
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.executor.executor import Executor
from src.orchestration.boundary import ValidatedExecutionBoundary


def build_connectors(
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
) -> list[Any]:
    """Dựng 3 Connector thật trỏ tới Mock Provider.

    Args:
        resident_url : Base URL Resident service (mặc định cổng 8001)
        transport_url: Base URL Transport service (mặc định cổng 8002)
        payment_url  : Base URL Payment service (mặc định cổng 8003)

    Returns:
        List 3 Connector: [ResidentConnector, TransportConnector, PaymentConnector]
    """
    return [
        ResidentConnector(base_url=resident_url),
        TransportConnector(base_url=transport_url),
        PaymentConnector(base_url=payment_url),
    ]


async def build_repository() -> PostgreSQLWorkflowStateRepository:
    """Dựng PostgreSQLWorkflowStateRepository từ DATABASE_URL config.

    Tạo pool từ settings.database_url rồi chạy migration idempotent trước khi
    trả repository. PostgreSQL mới từ Docker vì vậy luôn có schema cần thiết.
    """
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url)
    try:
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
) -> tuple[ValidatedExecutionBoundary, PostgreSQLWorkflowStateRepository]:
    """Dựng boundary tương thích trực tiếp với Planner graph.

    Repository được trả kèm để caller quản lý vòng đời pool khi cần. Boundary
    chỉ trả tuple chuẩn của ``Executor.execute``; không tạo wrapper result mới.
    """
    connectors, repository = await build_runtime(resident_url, transport_url, payment_url)
    executor = Executor(connectors, repository)
    return ValidatedExecutionBoundary(executor), repository


async def build_runtime(
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
) -> tuple[list[Any], PostgreSQLWorkflowStateRepository]:
    """Dựng toàn bộ runtime: connectors + repository.

    Returns:
        (connectors, repository) để dựng Executor hoặc test từng tầng.
    """
    connectors = build_connectors(resident_url, transport_url, payment_url)
    repository = await build_repository()
    return connectors, repository
