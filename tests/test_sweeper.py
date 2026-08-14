"""tests/test_sweeper.py
P-118 — Zombie sweep (Phase B) — deterministic unit tests.

Không cần PostgreSQL: monkeypatch `build_repository` + `release_on_failure` +
các hàm đọc/write bằng fake. Mục tiêu khoá:

  - payment approval AWAITING quá TTL → expire (CANCELLED) + release.
  - workflow RUNNING/PENDING quá TTL và không có process sống → FAILED + release.
  - workflow đang sống trong `_DEMO_JOBS` (live) KHÔNG bị sweep.
  - sweep tắt khi `zombie_sweep_enabled=false`.
"""

from __future__ import annotations

from src.orchestration import sweeper


class _FakePool:
    """Fake asyncpg.Pool — mô phỏng hai câu SELECT của sweeper."""

    def __init__(self, *, approvals: list[str] | None = None, zombies: list[str] | None = None) -> None:
        self.approvals = approvals or []  # workflow_id đang AWAITING quá hạn
        self.zombies = zombies or []  # workflow_id RUNNING/PENDING mồ côi
        self.closed = False
        self.writes: list[tuple[str, str]] = []  # (workflow_id, status)

    def acquire(self):
        """Trả async context manager — giống `async with pool.acquire()` của
        asyncpg (acquire là coroutine nhưng dùng được làm context manager)."""
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch(self, sql: str, *params) -> list[dict]:
        if "payment_approvals" in sql:
            return [{"workflow_id": wf} for wf in self.approvals]
        if "FROM workflows" in sql:
            return [{"workflow_id": wf} for wf in self.zombies]
        return []

    async def close(self) -> None:
        self.closed = True


class _FakeRepository:
    """Fake repository trả task list; không có booking để release."""

    def __init__(self) -> None:
        self._pool = None
        self.task_rows: list[dict] = []
        self.task_writes: list[tuple[str, str]] = []
        self.workflow_writes: list[tuple[str, str]] = []

    async def list_tasks(self, workflow_id: str) -> list[dict]:
        return self.task_rows

    async def update_task_status(self, workflow_id: str, task_id: str, status) -> None:
        self.task_writes.append((workflow_id, task_id, status.value))

    async def update_workflow_status(self, workflow_id: str, status) -> None:
        self.workflow_writes.append((workflow_id, status.value))


async def _noop_release(workflow_id: str) -> dict:
    return {"workflow_id": workflow_id, "released": False}


def _install(monkeypatch, *, approvals=None, zombies=None, enabled=True):
    """Cài fake pool + repository + tắt release thật."""
    pool = _FakePool(approvals=approvals, zombies=zombies)
    repo = _FakeRepository()

    async def _build_repository(*, migrate: bool = True):
        repo._pool = pool
        return repo

    monkeypatch.setattr(sweeper, "build_repository", _build_repository)
    monkeypatch.setattr(sweeper, "release_on_failure", _noop_release)

    class _Settings:
        zombie_sweep_enabled = enabled
        payment_approval_ttl_hours = 24
        zombie_running_ttl_hours = 0.5

    monkeypatch.setattr(sweeper, "get_settings", lambda: _Settings())
    return pool, repo


def test_sweep_expires_stale_payment_approvals(monkeypatch) -> None:
    pool, repo = _install(monkeypatch, approvals=["wf-approval-1", "wf-approval-2"])

    summary = _run(monkeypatch)

    assert summary["expired_approvals"] == ["wf-approval-1", "wf-approval-2"]
    # CANCELLED + release cho từng approval hết hạn.
    assert repo.workflow_writes == [
        ("wf-approval-1", "CANCELLED"),
        ("wf-approval-2", "CANCELLED"),
    ]


def test_sweep_flags_zombie_running_as_failed(monkeypatch) -> None:
    pool, repo = _install(monkeypatch, zombies=["wf-zombie-1"])

    summary = _run(monkeypatch)

    assert summary["swept_workflows"] == ["wf-zombie-1"]
    assert repo.workflow_writes == [("wf-zombie-1", "FAILED")]


def test_sweep_skips_live_workflows(monkeypatch) -> None:
    """Workflow đang có process sống (trong _DEMO_JOBS) KHÔNG bị sweep."""
    pool, repo = _install(monkeypatch, zombies=["wf-live", "wf-dead"])

    summary = _run(monkeypatch, live_ids={"wf-live"})

    assert summary["swept_workflows"] == ["wf-dead"]
    assert repo.workflow_writes == [("wf-dead", "FAILED")]


def test_sweep_disabled_when_config_off(monkeypatch) -> None:
    pool, repo = _install(monkeypatch, approvals=["wf-a"], zombies=["wf-z"], enabled=False)

    summary = _run(monkeypatch)

    assert summary["disabled"] is True
    assert summary["expired_approvals"] == []
    assert summary["swept_workflows"] == []
    assert repo.workflow_writes == []


def _run(monkeypatch, live_ids=None):
    import asyncio

    return asyncio.run(sweeper.sweep_zombie_workflows(live_ids=live_ids))
