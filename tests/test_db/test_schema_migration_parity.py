"""Database mới và database cũ đã migrate phải có cùng shape.

Hai đường tạo schema tồn tại song song:

  - `src/db/schema.sql` — dựng database MỚI từ đầu.
  - `src/db/schema_migrations.sql` — nâng cấp database ĐANG CHẠY.

Không có gì buộc chúng khớp nhau. Khi lệch, hậu quả chỉ xuất hiện trên môi
trường đã chạy lâu: dev mới `docker compose up` thì mọi thứ xanh, còn staging
thiếu cột và vỡ ở đúng câu INSERT. Test này áp cả hai đường lên hai database
sạch rồi so sánh cột — nếu lệch, nó fail ngay trên máy dev.

Migration còn phải: chạy hai lần vẫn thành công, và không DROP/TRUNCATE.
"""

from __future__ import annotations

import re
from pathlib import Path

import asyncpg
import pytest

from tests._dbcheck import UnsafeTestDatabaseError, is_ci, resolve_test_database_url

REPO_ROOT = Path(__file__).parents[2]
SCHEMA_SQL = REPO_ROOT / "src" / "db" / "schema.sql"
MIGRATION_SQL = REPO_ROOT / "src" / "db" / "schema_migrations.sql"

# Dùng chung đường phân giải với các test PostgreSQL khác. Tự đọc `os.environ`
# ở đây nghĩa là bỏ qua `.env`, và test sẽ skip ngay cả khi dev đã cấu hình
# đúng theo tài liệu — một test skip âm thầm không bảo vệ được gì.
#
# Phân giải trong try/except vì `resolve_test_database_url()` RAISE khi DSN trỏ
# sai database. Để nó bay ra ở module level sẽ làm hỏng cả bước collection và
# dừng toàn bộ suite — một lỗi cấu hình nên báo rõ ràng ở từng test, không nên
# che mất mọi kết quả khác.
try:
    TEST_DSN: str | None = resolve_test_database_url()
    _DSN_ERROR: Exception | None = None
except UnsafeTestDatabaseError as exc:
    TEST_DSN, _DSN_ERROR = None, exc

pytestmark = pytest.mark.skipif(
    not TEST_DSN and _DSN_ERROR is None and not is_ci(),
    reason="cần TEST_DATABASE_URL trỏ tới PostgreSQL thật",
)


@pytest.fixture(autouse=True)
def _fail_fast_on_unsafe_dsn():
    """DSN nguy hiểm → fail từng test, không skip và không abort collection."""
    if _DSN_ERROR is not None:
        pytest.fail(str(_DSN_ERROR), pytrace=False)


async def _apply(dsn: str, dbname: str, sql_files: list[Path]) -> list[tuple[str, str, str]]:
    """Tạo database sạch, áp lần lượt các file SQL, trả (bảng, cột, kiểu)."""
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()

    target = re.sub(r"/[^/?]+(\?|$)", f"/{dbname}\\1", dsn)
    conn = await asyncpg.connect(target)
    try:
        for path in sql_files:
            await conn.execute(path.read_text(encoding="utf-8"))
        rows = await conn.fetch(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, column_name
            """
        )
        return [(r["table_name"], r["column_name"], r["data_type"]) for r in rows]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_redeploying_over_an_existing_database_gives_the_same_shape():
    """Cài mới và nâng cấp phải ra cùng một shape.

    Đường triển khai (`src/db/migrations.py`) luôn áp `schema.sql` rồi
    `schema_migrations.sql`. `schema.sql` KHÔNG nhằm đủ một mình — nó tạo bảng,
    migration thêm cột về sau. Nên "so schema.sql với schema.sql + migration"
    là một invariant sai, và so cả hai file với chính cả hai file là tautology:
    hai vế giống nhau kể cả khi hai file mâu thuẫn nhau.

    Phép so có ý nghĩa là: chạy đường triển khai trên database RỖNG, so với
    chạy nó trên database ĐÃ CÓ (redeploy). Lệch nhau nghĩa là `schema.sql` và
    migration khai cùng một cột theo hai kiểu khác nhau, hoặc migration không
    idempotent — cả hai chỉ lộ ra trên môi trường đã chạy lâu.
    """
    fresh = await _apply(TEST_DSN, "p118_parity_fresh", [SCHEMA_SQL, MIGRATION_SQL])
    redeployed = await _apply(
        TEST_DSN,
        "p118_parity_migrated",
        [SCHEMA_SQL, MIGRATION_SQL, SCHEMA_SQL, MIGRATION_SQL],
    )

    only_fresh = sorted(set(fresh) - set(redeployed))
    only_redeployed = sorted(set(redeployed) - set(fresh))

    assert not only_fresh and not only_redeployed, (
        f"shape lệch giữa cài mới và redeploy — chỉ ở fresh: {only_fresh}; chỉ ở redeploy: {only_redeployed}"
    )


def test_schema_and_migration_never_declare_the_same_column_differently():
    """Cột khai ở cả hai file phải cùng kiểu.

    Chạy được không cần PostgreSQL, nên nó vẫn bảo vệ khi dev chưa dựng
    database — đúng lúc dễ bỏ sót nhất. `schema.sql` thắng vì chạy trước, nên
    một `VARCHAR(5)` trong migration đối lại `VARCHAR(20)` trong schema sẽ im
    lặng không có tác dụng, và giới hạn ta tưởng đang cưỡng chế thì không tồn tại.
    """
    migration = MIGRATION_SQL.read_text(encoding="utf-8")
    schema = SCHEMA_SQL.read_text(encoding="utf-8")

    migration_cols = {
        m.group(1).lower(): m.group(2).upper().rstrip(",")
        for m in re.finditer(r"ADD COLUMN IF NOT EXISTS\s+([a-z_]+)\s+([A-Za-z]+(?:\(\d+\))?)", migration)
    }
    schema_cols = {
        m.group(1).lower(): m.group(2).upper()
        for m in re.finditer(r"^\s{4}([a-z_]+)\s+([A-Z]+(?:\(\d+\))?)", schema, re.M)
    }

    conflicts = {
        name: (schema_cols[name], kind)
        for name, kind in migration_cols.items()
        if name in schema_cols and schema_cols[name] != kind
    }

    assert not conflicts, f"cột khai khác kiểu giữa schema.sql và migration: {conflicts}"


@pytest.mark.asyncio
async def test_migration_is_idempotent_and_keeps_existing_rows():
    """Chạy lại migration không được lỗi, và không được đụng dữ liệu cũ."""
    dbname = "p118_parity_idempotent"
    await _apply(TEST_DSN, dbname, [SCHEMA_SQL])

    target = re.sub(r"/[^/?]+(\?|$)", f"/{dbname}\\1", TEST_DSN)
    conn = await asyncpg.connect(target)
    try:
        await conn.execute(
            "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
            "VALUES ('R-PARITY', 'Nguyễn Văn A', 'A-0101', 'Vinhomes Ocean Park') "
            "ON CONFLICT DO NOTHING"
        )
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        await conn.execute(sql)
        await conn.execute(sql)  # lần hai phải im lặng thành công

        survived = await conn.fetchval("SELECT full_name FROM residents WHERE resident_id = 'R-PARITY'")
        assert survived == "Nguyễn Văn A", "migration làm mất dữ liệu đã có"
    finally:
        await conn.close()


def test_migration_never_drops_or_truncates():
    """Migration chỉ được thêm. DROP/TRUNCATE trên bảng nghiệp vụ là mất dữ liệu.

    `DROP INDEX`/`DROP CONSTRAINT` được phép: chúng không xoá hàng nào.
    """
    sql = MIGRATION_SQL.read_text(encoding="utf-8")
    stripped = re.sub(r"--[^\n]*", "", sql)

    banned = re.findall(r"\b(DROP\s+TABLE|DROP\s+COLUMN|DROP\s+DATABASE|TRUNCATE)\b", stripped, re.I)

    assert not banned, f"migration chứa lệnh phá dữ liệu: {sorted({b.upper() for b in banned})}"
