"""Chạy một workflow KHÔNG được mở pool mới và chạy lại migration.

`runtime_provider` ra đời để dẹp đúng chuyện này — docstring của nó ghi:

    "Mỗi request mở một pool mới. 27 chỗ gọi, mỗi chỗ `create_pool` rồi
     `close`. Đó là bắt tay TCP + xác thực cho từng lần chạm database."

Nhưng `build_runtime` vẫn gọi thẳng `build_repository()`, tức vẫn tự
`asyncpg.create_pool()` + `run_migrations()`. Đo được trên log Docker, mỗi lần
người dùng gửi một yêu cầu:

    created workflow 96b8f72b…
    Chạy migration: schema.sql
    Chạy migration: schema_migrations.sql
    Chạy migration: seed.sql
    Tất cả migration đã chạy xong.

21 lần chạy migration trong một phiên. Lifespan đã dựng sẵn một `SharedPool`
dùng chung — đường này đi vòng qua nó.

Cái giá không chỉ là ~0.2s: nó là một kết nối tới database đọc từ
`settings.database_url`, tức đúng đường mà composition root sinh ra để bịt.
"""

from __future__ import annotations

import pytest

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration import deps as orch_deps
from src.orchestration.runtime_provider import clear_repository_provider, set_repository_provider


@pytest.fixture
def dung_chung(db_pool, monkeypatch):
    """Đặt provider dùng chung, và đếm mọi lần ai đó tự dựng repository."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    tu_dung: list[str] = []

    async def _bat(*_args, **_kwargs):
        tu_dung.append("build_repository")
        raise AssertionError("build_runtime tự mở pool mới thay vì dùng pool chung")

    set_repository_provider(lambda: _tra(repository))
    monkeypatch.setattr(orch_deps, "build_repository", _bat)
    yield repository, tu_dung
    clear_repository_provider()


async def _tra(repository):
    return repository


@pytest.mark.asyncio
async def test_the_runtime_reuses_the_process_pool(dung_chung):
    """`build_runtime` phải lấy repository từ composition root."""
    repository, _ = dung_chung

    _connectors, tra_ve = await orch_deps.build_runtime()

    assert tra_ve is repository, "dựng một repository khác thay vì dùng cái đã có"


@pytest.mark.asyncio
async def test_the_execution_boundary_reuses_it_too(dung_chung):
    """Cả dây: build_execution_boundary → build_runtime → repository chung."""
    repository, _ = dung_chung

    _boundary, tra_ve = await orch_deps.build_execution_boundary()

    assert tra_ve is repository


@pytest.mark.asyncio
async def test_migrations_do_not_run_again_for_each_workflow(dung_chung, monkeypatch):
    """Migration là việc của lúc KHỞI ĐỘNG, không phải của mỗi yêu cầu."""
    chay = []
    monkeypatch.setattr(orch_deps, "run_migrations", lambda *_a, **_k: chay.append(1))

    await orch_deps.build_execution_boundary()

    assert chay == [], f"chạy lại migration {len(chay)} lần cho một workflow"
