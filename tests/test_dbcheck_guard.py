"""Guard chọn database test phải fail-closed.

Fixture database chạy `TRUNCATE TABLE ... RESTART IDENTITY CASCADE` sau mỗi
test. Nếu `TEST_DATABASE_URL` trỏ nhầm sang `p118_db`, một lần `pytest` xoá sạch
dữ liệu phát triển — không có bước xác nhận nào, và người chạy test không có lý
do gì để nghi ngờ. Vì vậy nhánh TỪ CHỐI mới là nhánh quan trọng, và nó phải
được kiểm kỹ hơn nhánh chấp nhận.
"""

from __future__ import annotations

import pytest

from tests import _dbcheck
from tests._dbcheck import (
    ALLOWED_TEST_DATABASE,
    UnsafeTestDatabaseError,
    resolve_test_database_url,
)

SAFE_DSN = f"postgresql://p118:p118pass@localhost:5433/{ALLOWED_TEST_DATABASE}"

# Canary: giá trị này KHÔNG bao giờ được xuất hiện trong bất kỳ message nào.
# Đặt nó vào cả username lẫn password để một lần nội suy DSN là lộ ngay.
CREDENTIAL_CANARY = "sup3rsecr3t-canary"  # secret-fixture
DEV_DSN_WITH_CREDENTIAL = f"postgresql://admin:{CREDENTIAL_CANARY}@db.internal:5432/p118_db"


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch, tmp_path):
    """Cô lập khỏi môi trường thật.

    Không có fixture này, kết quả phụ thuộc `.env` của máy đang chạy và
    `TEST_DATABASE_URL` mà dev tình cờ export — test sẽ xanh hoặc đỏ vì lý do
    không liên quan đến code.
    """
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(_dbcheck, "REPO_ROOT", tmp_path)
    return tmp_path


def _write_env_file(root, value: str) -> None:
    (root / ".env").write_text(
        f"OPENAI_API_KEY=sk-not-real-placeholder\nTEST_DATABASE_URL={value}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Chấp nhận — đúng một tên database
# ---------------------------------------------------------------------------


def test_process_env_pointing_at_the_test_database_is_accepted(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", SAFE_DSN)

    assert resolve_test_database_url() == SAFE_DSN


def test_env_file_pointing_at_the_test_database_is_accepted(_no_ambient_env):
    _write_env_file(_no_ambient_env, SAFE_DSN)

    assert resolve_test_database_url() == SAFE_DSN


def test_process_env_wins_over_the_env_file(monkeypatch, _no_ambient_env):
    """Giá trị người chạy vừa gõ phải thắng file cấu hình cũ trên đĩa."""
    other_safe = f"postgresql://other:pw@127.0.0.1:5432/{ALLOWED_TEST_DATABASE}"
    _write_env_file(_no_ambient_env, SAFE_DSN)
    monkeypatch.setenv("TEST_DATABASE_URL", other_safe)

    assert resolve_test_database_url() == other_safe


# ---------------------------------------------------------------------------
# Từ chối — mọi thứ khác
# ---------------------------------------------------------------------------


REJECTED_DSNS = [
    ("database phát triển", "postgresql://p118:p118pass@localhost:5433/p118_db"),
    ("database mặc định của cluster", "postgresql://p118:p118pass@localhost:5433/postgres"),
    ("không có tên database", "postgresql://p118:p118pass@localhost:5433"),
    ("tên database rỗng", "postgresql://p118:p118pass@localhost:5433/"),
    ("DSN gõ sai, không có scheme", "p118:p118pass@localhost:5433/p118_test_db"),
    ("chuỗi vô nghĩa", "khong-phai-dsn"),
    # Bẫy của so khớp chuỗi con: cả hai đều CHỨA "p118_test_db" nhưng không
    # phải database đó.
    ("tên đúng nằm trong query string", "postgresql://p118@localhost/p118_db?application_name=p118_test_db"),
    ("tên đúng là tiền tố của tên khác", "postgresql://p118@localhost/p118_test_db_backup"),
    ("nhiều đoạn path", "postgresql://p118@localhost/p118_db/p118_test_db"),
]


@pytest.mark.parametrize("label,dsn", REJECTED_DSNS, ids=[label for label, _ in REJECTED_DSNS])
def test_unsafe_dsn_from_process_env_is_rejected(monkeypatch, label, dsn):
    monkeypatch.setenv("TEST_DATABASE_URL", dsn)

    with pytest.raises(UnsafeTestDatabaseError):
        resolve_test_database_url()


@pytest.mark.parametrize("label,dsn", REJECTED_DSNS, ids=[label for label, _ in REJECTED_DSNS])
def test_unsafe_dsn_from_the_env_file_is_rejected(_no_ambient_env, label, dsn):
    """Nhánh `.env` phải bị kiểm y hệt nhánh process env.

    Đây mới là nhánh nguy hiểm hơn: `.env` nằm trên đĩa, được copy giữa các máy,
    và là nơi DSN phát triển hay bị dán nhầm vào rồi quên mất.
    """
    _write_env_file(_no_ambient_env, dsn)

    with pytest.raises(UnsafeTestDatabaseError):
        resolve_test_database_url()


def test_an_unsafe_process_env_does_not_silently_fall_back_to_the_env_file(monkeypatch, _no_ambient_env):
    """DSN nguy hiểm phải dừng cuộc chạy, không được lặng lẽ dùng nguồn khác.

    Rơi sang `.env` sẽ biến một cấu hình nguy hiểm thành một lần chạy trông
    hoàn toàn bình thường — và lần sau, khi `.env` không còn, nó xoá thật.
    """
    _write_env_file(_no_ambient_env, SAFE_DSN)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://p118@localhost/p118_db")

    with pytest.raises(UnsafeTestDatabaseError):
        resolve_test_database_url()


# ---------------------------------------------------------------------------
# Message không được rò credential
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["process_env", "env_file"])
def test_the_error_never_leaks_the_dsn_or_any_credential(monkeypatch, _no_ambient_env, source):
    if source == "process_env":
        monkeypatch.setenv("TEST_DATABASE_URL", DEV_DSN_WITH_CREDENTIAL)
    else:
        _write_env_file(_no_ambient_env, DEV_DSN_WITH_CREDENTIAL)

    with pytest.raises(UnsafeTestDatabaseError) as excinfo:
        resolve_test_database_url()

    message = str(excinfo.value)
    for leaked in (CREDENTIAL_CANARY, "admin", "db.internal", "p118_db", DEV_DSN_WITH_CREDENTIAL):
        assert leaked not in message, f"message rò {leaked!r}"
    assert message == f"TEST_DATABASE_URL phải trỏ tới database {ALLOWED_TEST_DATABASE}."


# ---------------------------------------------------------------------------
# Thiếu biến: skip khi local, fail khi CI
# ---------------------------------------------------------------------------


def test_missing_url_skips_when_running_locally(monkeypatch):
    monkeypatch.delenv("CI", raising=False)

    with pytest.raises(pytest.skip.Exception):
        _dbcheck.require_test_database_url()


def test_missing_url_fails_in_ci(monkeypatch):
    monkeypatch.setenv("CI", "true")

    # `pytest.fail` và `pytest.skip` đều ném từ `BaseException`, không phải
    # `Exception` — bắt bằng `Exception` sẽ để chúng bay qua và test đỏ vì lý do
    # sai. Phân biệt hai lớp mới là điều cần khẳng định ở đây.
    with pytest.raises(BaseException) as excinfo:  # noqa: B017, PT011
        _dbcheck.require_test_database_url()

    assert isinstance(excinfo.value, pytest.fail.Exception), "trong CI phải fail"
    assert not isinstance(excinfo.value, pytest.skip.Exception), "trong CI không được phép skip"


def test_an_unsafe_url_fails_even_when_running_locally(monkeypatch):
    """Trỏ sai database không phải "chưa cấu hình" — skip nó là giấu báo động."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://p118@localhost/p118_db")

    with pytest.raises(BaseException) as excinfo:  # noqa: B017, PT011
        _dbcheck.require_test_database_url()

    assert isinstance(excinfo.value, pytest.fail.Exception)
    assert not isinstance(excinfo.value, pytest.skip.Exception)
    assert ALLOWED_TEST_DATABASE in str(excinfo.value)
    assert "p118_db" not in str(excinfo.value).replace(ALLOWED_TEST_DATABASE, "")
