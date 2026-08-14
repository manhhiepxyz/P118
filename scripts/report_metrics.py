"""In bảng audit metrics từ execution_logs.

Owner: Thành Bảo (Decision layer)
File: scripts/report_metrics.py

Chạy:
    python -m scripts.report_metrics

Kết nối DB qua DATABASE_URL trong .env. Chỉ đọc execution_logs, không ghi.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.monitoring.execution_metrics import compute_execution_metrics


async def main() -> None:
    database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost/p118")
    pool = await asyncpg.create_pool(database_url)
    if pool is None:
        raise RuntimeError("Could not create DB pool")

    try:
        repository = PostgreSQLWorkflowStateRepository(pool)
        rows = await repository.list_execution_logs(limit=10_000)
        metrics = compute_execution_metrics(rows)

        print("=" * 70)
        print("Execution Audit Metrics")
        print("=" * 70)
        print(f"Total attempts     : {metrics.total_attempts}")
        print(f"Overall success    : {metrics.overall_success_rate:.2%}")
        print(f"Overall retry      : {metrics.overall_retry_rate:.2%}")
        if metrics.avg_duration_ms is not None:
            print(f"Avg duration       : {metrics.avg_duration_ms:.1f} ms")
        else:
            print("Avg duration       : N/A")
        print("-" * 70)

        for name, conn in sorted(metrics.connector_metrics.items()):
            print(f"Connector: {name}")
            print(f"  attempts  : {conn.total_attempts}")
            print(f"  success   : {conn.success_rate:.2%}")
            print(f"  retry     : {conn.retry_rate:.2%}")
            if conn.avg_duration_ms is not None:
                print(f"  avg dur   : {conn.avg_duration_ms:.1f} ms")
            if conn.error_breakdown:
                print("  errors    :")
                for code, count in sorted(conn.error_breakdown.items(), key=lambda x: -x[1]):
                    print(f"    {code}: {count}")
            print()

        if metrics.error_breakdown:
            print("-" * 70)
            print("Global error breakdown")
            for code, count in sorted(metrics.error_breakdown.items(), key=lambda x: -x[1]):
                print(f"  {code}: {count}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
