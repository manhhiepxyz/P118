"""Tests cho startup repository và migration lifecycle."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.orchestration import deps


class _Pool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_build_repository_runs_migrations_before_returning(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _Pool()
    events: list[tuple[str, object]] = []

    async def _create_pool(url: str) -> _Pool:
        events.append(("pool", url))
        return pool

    async def _run_migrations(received_pool: _Pool) -> None:
        events.append(("migration", received_pool))

    monkeypatch.setattr(deps, "get_settings", lambda: SimpleNamespace(database_url="postgresql://test-db"))
    monkeypatch.setattr(deps.asyncpg, "create_pool", _create_pool)
    monkeypatch.setattr(deps, "run_migrations", _run_migrations)

    repository = await deps.build_repository()

    assert events == [("pool", "postgresql://test-db"), ("migration", pool)]
    assert repository._pool is pool  # noqa: SLF001 - xác nhận factory truyền đúng pool
    assert pool.closed is False


@pytest.mark.asyncio
async def test_build_repository_closes_pool_when_migration_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _Pool()

    async def _create_pool(url: str) -> _Pool:
        return pool

    async def _run_migrations(received_pool: _Pool) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(deps, "get_settings", lambda: SimpleNamespace(database_url="postgresql://test-db"))
    monkeypatch.setattr(deps.asyncpg, "create_pool", _create_pool)
    monkeypatch.setattr(deps, "run_migrations", _run_migrations)

    with pytest.raises(RuntimeError, match="migration failed"):
        await deps.build_repository()

    assert pool.closed is True
