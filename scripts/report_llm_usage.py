"""In bảng token/cost LLM từ `llm_usage`.

Owner: Mạnh Hiệp (Planner layer)
File: scripts/report_llm_usage.py

Chạy:
    python -m scripts.report_llm_usage

Kết nối DB qua DATABASE_URL trong .env. Chỉ đọc llm_usage, không ghi.

CẢNH BÁO: cost là ƯỚC TÍNH (bảng giá trong src/monitoring/llm_pricing.py chưa
được owner finance xác nhận). Output gắn cờ `[PLACEHOLDER]` cho tới khi xác
nhận. Không PII: bảng không lưu goal.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

from src.monitoring.llm_pricing import IS_PLACEHOLDER, cost_summary


async def fetch_usage_rows(pool: asyncpg.Pool, limit: int = 10_000) -> list[dict]:
    """Đọc row llm_usage gần nhất — chỉ số, không có prompt/response/goal."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT workflow_id, run_id, stage, model,
                   prompt_tokens, completion_tokens, total_tokens, latency_ms,
                   created_at
            FROM llm_usage
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(row) for row in rows]


async def main() -> None:
    database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost/p118")
    pool = await asyncpg.create_pool(database_url)
    if pool is None:
        raise RuntimeError("Could not create DB pool")

    try:
        rows = await fetch_usage_rows(pool)
        summary = cost_summary(rows)

        print("=" * 70)
        print("LLM Usage Report")
        if IS_PLACEHOLDER:
            print("[PLACEHOLDER] Giá chưa được finance xác nhận — cost là ước tính.")
        print("=" * 70)
        print(f"Total LLM calls   : {len(rows)}")
        print(f"Total tokens      : {summary['total_tokens']}")
        print(f"Total cost (VND)  : {summary['total_cost_vnd']:,}")
        print("-" * 70)

        for model, bucket in sorted(summary["per_model"].items()):
            print(f"Model: {model}")
            print(f"  prompt      : {bucket['prompt_tokens']:,}")
            print(f"  completion  : {bucket['completion_tokens']:,}")
            print(f"  cost (VND)  : {bucket['cost_vnd']:,}")
            print()

        if not rows:
            print("Không có dữ liệu llm_usage. Chạy một workflow demo trước.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
