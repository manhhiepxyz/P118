"""Chốt chặn database của harness E2E. Chạy được mà KHÔNG cần Docker.

Harness `tests/e2e/system_docker.py` ghi thật và gọi `docker compose
--force-recreate`. Trỏ nhầm vào `p118_db` nghĩa là ghi đè database demo, và
không ai biết cho tới lúc mở demo.

Ở lượt nghiệm thu trước, chuyện đó xảy ra BA lần: harness khôi phục stack bằng
`docker compose up -d --force-recreate` mà không mang theo `POSTGRES_DB`, nên
container lặng lẽ quay về `p118_db` giữa lượt chạy.

File này khoá cả hai hướng: tên đích, và việc giữ được đích ấy qua mọi lệnh
compose và mọi lần khởi động lại.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))

from db_guard import (  # noqa: E402
    ALLOWED_DATABASE,
    UnsafeDatabaseError,
    assert_ready_on_e2e_database,
    compose_env,
    require_e2e_database,
)


def test_the_only_accepted_name_is_the_e2e_database():
    assert require_e2e_database(ALLOWED_DATABASE) == ALLOWED_DATABASE
    # Khoảng trắng thừa quanh biến môi trường là tai nạn gõ phím, không phải
    # một đích khác — cắt là đúng, và cắt XONG mới so bằng.
    assert require_e2e_database(f"  {ALLOWED_DATABASE}  ") == ALLOWED_DATABASE


@pytest.mark.parametrize(
    "value",
    [
        "p118_db",
        "postgres",
        "",
        "   ",
        # Tên GẦN GIỐNG. Mọi kiểm tra bằng `startswith` đều nhận cái này.
        "p118_e2e_db_backup",
        "p118_e2e_db2",
        "P118_E2E_DB",
        # Thứ trông như tên nhưng mang theo cả một kết nối.
        "postgresql://p118:matkhau@postgres:5432/p118_e2e_db",
        "p118_e2e_db?options=-c",
        "p118_e2e_db;DROP TABLE users",
        "public.p118_e2e_db",
    ],
    ids=[
        "demo-db",
        "postgres",
        "rỗng",
        "khoảng-trắng",
        "hậu-tố",
        "số-đuôi",
        "hoa",
        "dsn",
        "query-string",
        "sql",
        "có-schema",
    ],
)
def test_everything_else_is_refused(value):
    with pytest.raises(UnsafeDatabaseError):
        require_e2e_database(value)


def test_a_missing_variable_is_refused_not_defaulted(monkeypatch):
    """Không fallback. Fallback im lặng luôn tìm được đường tới môi trường sai."""
    monkeypatch.delenv("P118_DB", raising=False)
    with pytest.raises(UnsafeDatabaseError):
        require_e2e_database()


def test_the_error_never_carries_a_dsn(monkeypatch):
    """Lỗi cấu hình hay bị dán nguyên văn vào issue và CI log."""
    dsn = "postgresql://p118:matkhau@postgres:5432/p118_e2e_db"
    with pytest.raises(UnsafeDatabaseError) as loi:
        require_e2e_database(dsn)
    text = str(loi.value)
    assert "matkhau" not in text
    assert "postgresql://" not in text
    assert "postgres:5432" not in text


def test_every_compose_command_carries_the_database(monkeypatch):
    """Compose nội suy `${POSTGRES_DB}` từ môi trường của tiến trình GỌI."""
    monkeypatch.setenv("P118_DB", ALLOWED_DATABASE)
    assert compose_env()["POSTGRES_DB"] == ALLOWED_DATABASE
    # Kể cả khi môi trường nền đang mang tên sai.
    assert compose_env({"POSTGRES_DB": "p118_db"})["POSTGRES_DB"] == ALLOWED_DATABASE


def test_compose_env_refuses_when_the_target_is_unsafe(monkeypatch):
    monkeypatch.setenv("P118_DB", "p118_db")
    with pytest.raises(UnsafeDatabaseError):
        compose_env()


def _ready(name: str, status: str = "ready") -> dict:
    return {
        "status": status,
        "checks": [
            {"name": "llm_config", "ok": True, "detail": "provider=deepseek"},
            {"name": "database", "ok": True, "detail": f"kết nối được · database={name}"},
        ],
    }


def test_a_restart_that_lands_on_another_database_stops_the_run():
    """`/ready` xanh KHÔNG có nghĩa là đúng database.

    Đây là lỗ hổng thật của lượt trước: harness chỉ chờ `/ready` trả 200 rồi
    chạy tiếp, trong khi container vừa quay về `p118_db`.
    """
    assert_ready_on_e2e_database(_ready(ALLOWED_DATABASE))

    for sai in ("p118_db", "postgres", "p118_e2e_db_backup"):
        with pytest.raises(UnsafeDatabaseError):
            assert_ready_on_e2e_database(_ready(sai))


def test_a_backend_that_is_not_ready_also_stops_the_run():
    with pytest.raises(UnsafeDatabaseError):
        assert_ready_on_e2e_database(_ready(ALLOWED_DATABASE, status="degraded"))


def test_the_check_is_an_exact_match_not_a_substring():
    """`ALLOWED in detail` nhận cả `p118_e2e_db_backup` — chỉ so BẰNG mới đúng.

    Đây là test giữ cho phép so sánh không bị viết lại thành `in` khi ai đó
    "đơn giản hoá" nó sau này.
    """
    assert ALLOWED_DATABASE in "database=p118_e2e_db_backup"
    with pytest.raises(UnsafeDatabaseError):
        assert_ready_on_e2e_database(_ready("p118_e2e_db_backup"))


def test_the_harness_module_has_no_default_target(monkeypatch):
    """Import harness mà thiếu `P118_DB` phải NỔ, không im lặng chọn giúp."""
    monkeypatch.delenv("P118_DB", raising=False)
    for module in [m for m in list(sys.modules) if m.endswith("system_docker")]:
        del sys.modules[module]
    with pytest.raises(UnsafeDatabaseError):
        import system_docker  # noqa: F401
