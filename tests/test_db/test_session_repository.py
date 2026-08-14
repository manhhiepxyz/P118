"""tests/test_db/test_session_repository.py
P-118 — Integration test: session_repository

Chạy thật với PostgreSQL test DB. Xác nhận `sessions` table (migration) hoạt
động: create + get, first-start-wins (ON CONFLICT DO NOTHING), prospect null
resident.
"""

from __future__ import annotations

import asyncpg
import pytest

from src.db.session_repository import create_session, get_session


@pytest.mark.asyncio
async def test_create_session_then_get(db_pool: asyncpg.Pool) -> None:
    await create_session(
        db_pool,
        session_id="sess-001",
        account_state="resident",
        resident_id="RES-001",
    )

    row = await get_session(db_pool, "sess-001")
    assert row is not None
    assert row["session_id"] == "sess-001"
    assert row["account_state"] == "resident"
    assert row["resident_id"] == "RES-001"


@pytest.mark.asyncio
async def test_prospect_session_has_null_resident(db_pool: asyncpg.Pool) -> None:
    await create_session(
        db_pool,
        session_id="sess-prospect",
        account_state="prospect",
        resident_id=None,
    )

    row = await get_session(db_pool, "sess-prospect")
    assert row is not None
    assert row["account_state"] == "prospect"
    assert row["resident_id"] is None


@pytest.mark.asyncio
async def test_first_start_wins_on_conflict(db_pool: asyncpg.Pool) -> None:
    """ON CONFLICT DO NOTHING — persona đầu tiên giữ nguyên, không ghi đè."""
    await create_session(
        db_pool,
        session_id="sess-lock",
        account_state="prospect",
        resident_id=None,
    )
    # Lần gọi thứ hai cùng session_id với persona khác → KHÔNG đổi quyền.
    await create_session(
        db_pool,
        session_id="sess-lock",
        account_state="resident",
        resident_id="RES-001",
    )

    row = await get_session(db_pool, "sess-lock")
    assert row["account_state"] == "prospect"
    assert row["resident_id"] is None


@pytest.mark.asyncio
async def test_get_session_missing_returns_none(db_pool: asyncpg.Pool) -> None:
    assert await get_session(db_pool, "sess-does-not-exist") is None
