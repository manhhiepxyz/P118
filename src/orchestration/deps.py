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
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository


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

    Trả pool từ settings.database_url (đã được docker-compose override
    bằng POSTGRES_USER/PASSWORD/DB). Nếu không có DB chạy, lời gọi sẽ
    lỗi connection — smoke test nên báo rõ lỗi này cho user.
    """
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url)
    return PostgreSQLWorkflowStateRepository(pool)


async def build_runtime(
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
) -> tuple[list[Any], PostgreSQLWorkflowStateRepository]:
    """Dựng toàn bộ runtime: connectors + repository.

    Returns:
        (connectors, repository) sẵn sàng truyền vào execute_plan().
    """
    connectors = build_connectors(resident_url, transport_url, payment_url)
    repository = await build_repository()
    return connectors, repository
