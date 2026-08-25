"""Harness E2E phải fail-fast — không được nuốt lỗi setup.

Lượt trước, helper `sql()` trả chuỗi rỗng khi câu lệnh hỏng. Script chạy tiếp
với một user không có liên kết cư dân, rồi quy `VALIDATION_ERROR` cho model
trong khi lỗi nằm ở setup. Mất một lượt để lần ra.

Harness sống trong scratchpad của phiên nên không import được từ đây; các test
dưới đây kiểm chính những bất biến mà harness dựa vào, trên PostgreSQL thật.
"""

from __future__ import annotations

import os
import subprocess
import uuid

from tests._dbcheck import require_test_database_url


def _psql(query: str) -> subprocess.CompletedProcess:
    require_test_database_url()  # skip khi chưa cấu hình, fail trong CI
    # Nối thẳng qua `TEST_DATABASE_URL`, KHÔNG `docker exec` vào tên container
    # cố định của dev stack (`p118_postgres`) — CI dùng Postgres ephemeral qua
    # `services:`, container đó không tồn tại và mọi test ở đây fail ngay từ
    # "Initialize containers". Bài test này kiểm HÀNH VI của cờ `psql`
    # (`-q`/`ON_ERROR_STOP`), không kiểm gì riêng của dev stack — nối trực
    # tiếp chạy đúng ở cả hai nơi.
    dsn = os.environ["TEST_DATABASE_URL"]
    return subprocess.run(
        ["psql", dsn, "-q", "-v", "ON_ERROR_STOP=1", "-tAc", query],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_broken_statement_reports_a_nonzero_exit_code():
    """`ON_ERROR_STOP=1` là thứ khiến harness raise được thay vì đi tiếp."""
    result = _psql("SELECT * FROM bang_khong_ton_tai_e2e")

    assert result.returncode != 0, "SQL hỏng mà exit code = 0 → harness sẽ nuốt lỗi"
    assert result.stdout.strip() == "", "SQL hỏng không được trả dữ liệu"


def test_a_quiet_select_returns_only_rows_without_a_command_tag():
    """Thiếu `-q`, psql in kèm `UPDATE 1` và harness đếm sai số row."""
    result = _psql("SELECT 1")

    assert result.returncode == 0
    assert [line for line in result.stdout.strip().split("\n") if line] == ["1"]


def test_an_insert_without_a_matching_row_returns_nothing():
    """`RETURNING` không có row = setup thất bại, dù exit code vẫn 0."""
    missing = f"KHONG-CO-{uuid.uuid4().hex[:8]}"
    result = _psql(f"UPDATE residents SET full_name = full_name WHERE resident_id = '{missing}' RETURNING resident_id")

    assert result.returncode == 0
    assert result.stdout.strip() == "", "phải rỗng để harness phát hiện seed hỏng"
