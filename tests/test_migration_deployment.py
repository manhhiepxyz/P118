"""Migration phải là một job riêng chạy TRƯỚC mọi service chạm database.

Trước đây schema chỉ được nâng cấp khi ai đó nhớ chạy `psql` bằng tay. Hệ quả
đã xảy ra thật: container khởi động khoẻ mạnh, healthcheck xanh, nhưng
`POST /api/residents` trả 500 vì `seq_resident_id` chưa tồn tại — provider dùng
`database_lifespan`, mà lifespan đó không chạy migration.

Các test ở đây đọc `docker compose config` đã render (không đọc YAML thô), nên
chúng kiểm cấu hình thực sự có hiệu lực chứ không phải chuỗi ký tự trong file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]

# Service nào thực sự đọc/ghi PostgreSQL và vì thế phải chờ migration xong.
DATABASE_BACKED_SERVICES = (
    "backend",
    "mock-resident",
    "mock-transport",
    "mock-payment",
)

MIGRATION_SERVICE = "db-migrate"


def _compose_config() -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker không có sẵn trong môi trường này")
    result = subprocess.run(
        ["docker", "compose", "config"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.fail(f"docker compose config lỗi: {result.stderr[-500:]}")
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(result.stdout)


@pytest.fixture(scope="module")
def compose() -> dict:
    return _compose_config()


def test_migration_job_exists_and_waits_for_a_healthy_database(compose) -> None:
    services = compose["services"]
    assert MIGRATION_SERVICE in services, "thiếu one-shot job migration"

    depends = services[MIGRATION_SERVICE].get("depends_on") or {}
    assert depends.get("postgres", {}).get("condition") == "service_healthy"


@pytest.mark.parametrize("service", DATABASE_BACKED_SERVICES)
def test_database_services_start_only_after_migration_completes(compose, service: str) -> None:
    """`service_completed_successfully` là điểm mấu chốt.

    `service_started` không đủ: job có thể đang chạy dở, hoặc đã thoát với exit
    code khác 0, mà provider vẫn khởi động trên schema cũ.
    """
    depends = compose["services"][service].get("depends_on") or {}

    assert MIGRATION_SERVICE in depends, f"{service} không chờ {MIGRATION_SERVICE}"
    assert depends[MIGRATION_SERVICE]["condition"] == "service_completed_successfully", (
        f"{service} phải chờ migration THÀNH CÔNG, không chỉ chờ nó khởi động"
    )
    assert depends.get("postgres", {}).get("condition") == "service_healthy"


def test_migration_job_runs_the_shared_runner_not_a_second_implementation(compose) -> None:
    """Một nguồn SQL duy nhất — không có runner thứ hai đọc file khác."""
    command = compose["services"][MIGRATION_SERVICE].get("command")
    flat = " ".join(command) if isinstance(command, list) else str(command)

    assert "src.db.migrate_cli" in flat


def test_providers_do_not_migrate_inside_their_lifespan() -> None:
    """Quyền đổi schema không được phát tán ra từng provider.

    Sáu service khởi động song song sẽ chạy cùng một ALTER TABLE từ sáu kết
    nối; và bất kỳ container nào bị chiếm cũng sửa được cấu trúc database.
    """
    lifespan = (REPO_ROOT / "src" / "services" / "mock" / "db_pool.py").read_text(encoding="utf-8")

    assert "run_migrations" not in lifespan
    assert "schema.sql" not in lifespan


def test_migration_runner_never_executes_its_own_sql() -> None:
    """Runner chỉ điều phối; toàn bộ SQL nằm trong file .sql dùng chung.

    Kiểm bằng AST thay vì tìm chuỗi: docstring có quyền NHẮC TỚI
    CREATE/TRUNCATE để giải thích vì sao runner không làm những việc đó.
    """
    import ast

    source = (REPO_ROOT / "src" / "db" / "migrate_cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in docstrings
    ]

    for text in literals:
        upper = text.upper()
        for forbidden in ("CREATE DATABASE", "DROP DATABASE", "TRUNCATE", "DROP SCHEMA", "DELETE FROM"):
            assert forbidden not in upper, f"{forbidden} trong literal: {text[:60]}"

    # Và runner không tự gọi execute: mọi câu lệnh đi qua `run_migrations`.
    called = {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "execute" not in called


def test_migration_runner_masks_the_connection_string() -> None:
    """DATABASE_URL chứa mật khẩu; log Docker/CI thường sống lâu hơn dự kiến."""
    from src.db.migrate_cli import _safe_target

    masked = _safe_target("postgresql://p118:supersecret@postgres:5432/p118_db")

    assert "supersecret" not in masked
    assert "p118:" not in masked
    assert masked == "postgres:5432/p118_db"


@pytest.mark.asyncio
async def test_migration_runner_exits_non_zero_when_the_database_is_unreachable(monkeypatch) -> None:
    """Lỗi migration phải chặn cả cụm, nên exit code khác 0 là bắt buộc."""
    import src.db.migrate_cli as cli

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@127.0.0.1:1/nonexistent")
    monkeypatch.setattr(cli, "_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(cli, "_RETRY_DELAY_SECONDS", 0.0)

    with pytest.raises(RuntimeError) as exc_info:
        await cli.main()

    # Message chỉ nêu loại lỗi, không nêu DSN.
    assert "pw@" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_migration_runner_exits_non_zero_when_sql_fails(monkeypatch) -> None:
    import src.db.migrate_cli as cli

    async def _boom(pool):
        raise RuntimeError("syntax error at or near")

    class _FakePool:
        async def close(self) -> None:
            return None

    async def _fake_connect(url):
        return _FakePool()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@postgres:5432/db")
    monkeypatch.setattr(cli, "_connect_with_retry", _fake_connect)
    monkeypatch.setattr(cli, "run_migrations", _boom)

    assert await cli.main() == 1


@pytest.mark.asyncio
async def test_migration_runner_exits_non_zero_without_a_database_url(monkeypatch) -> None:
    import src.db.migrate_cli as cli

    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert await cli.main() == 2
