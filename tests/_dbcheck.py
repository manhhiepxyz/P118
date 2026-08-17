"""Guard dùng chung cho các test cần PostgreSQL thật.

Hai rủi ro khác nhau, và module này chặn cả hai.

**Skip âm thầm.** Nếu `TEST_DATABASE_URL` không được set, các test PostgreSQL
tự skip và suite vẫn báo xanh — toàn bộ tầng database không được kiểm mà không
ai biết. Chấp nhận được khi dev chạy local, KHÔNG chấp nhận được trong CI.

**Xoá nhầm database.** `tests/test_db/conftest.py` và
`tests/test_integration/conftest.py` chạy `TRUNCATE TABLE ... RESTART IDENTITY
CASCADE` sau mỗi test. Trước đây helper này nhận DSN từ môi trường rồi trả
thẳng cho fixture, nên một biến trỏ nhầm sang `p118_db` là đủ để một lần chạy
`pytest` xoá sạch dữ liệu phát triển. Không có bước xác nhận nào ở giữa, và
người chạy test không có lý do gì để nghi ngờ.

Vì vậy DSN phải qua kiểm tra fail-closed: chỉ đúng một tên database được chấp
nhận, mọi giá trị khác — kể cả `postgres`, DSN thiếu tên database, hay DSN gõ
sai cú pháp — đều bị từ chối TRƯỚC khi asyncpg mở kết nối.

Quy tắc:
  - Local (không có biến CI): thiếu `TEST_DATABASE_URL` → skip, kèm hướng dẫn.
  - CI (`CI=true`): thiếu `TEST_DATABASE_URL` → FAIL ngay, không skip.
  - Trỏ sai database → FAIL ở cả hai môi trường. Đây không phải "chưa cấu hình"
    mà là cấu hình nguy hiểm; skip nó đi sẽ giấu mất chính thứ cần báo động.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).parents[1]

# Tên database DUY NHẤT được phép cho test. Đây là danh sách trắng, không phải
# danh sách đen: thêm một tên mới phải là hành động có chủ ý và review được.
ALLOWED_TEST_DATABASE = "p118_test_db"

_MISSING_MESSAGE = (
    "TEST_DATABASE_URL chưa được set — không thể chạy test PostgreSQL. "
    "Set biến này trong .env (local) hoặc trong job env (CI)."
)

# Message KHÔNG chứa DSN, username, password hay host. Lỗi cấu hình database
# hay được dán vào issue và CI log; nhắc lại DSN ở đây là phát tán credential
# tới đúng những nơi lưu lâu nhất. Tên database đúng là hằng số công khai nên
# nêu ra được, và nó đủ để người đọc tự sửa.
_WRONG_DATABASE_MESSAGE = f"TEST_DATABASE_URL phải trỏ tới database {ALLOWED_TEST_DATABASE}."


class UnsafeTestDatabaseError(RuntimeError):
    """DSN test không trỏ tới database được phép.

    Message cố định, không nội suy bất cứ phần nào của DSN vào.
    """

    def __init__(self) -> None:
        super().__init__(_WRONG_DATABASE_MESSAGE)


def is_ci() -> bool:
    """True nếu đang chạy trong CI.

    GitHub Actions luôn set ``CI=true``; các CI khác cũng theo quy ước này.
    """
    return os.environ.get("CI", "").lower() in {"1", "true", "yes"}


def _database_name(dsn: str) -> str | None:
    """Tên database trong DSN, hoặc None nếu không xác định được.

    Phân giải bằng `urlsplit` chứ không so khớp chuỗi con trên toàn URL. Kiểm
    kiểu `"p118_test_db" in dsn` sẽ chấp nhận `postgresql://.../p118_db?opt=p118_test_db`
    và cả `.../p118_test_db_backup` — hai thứ đều không phải database ta muốn
    ghi vào, và cả hai đều lọt.
    """
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return None

    # Thiếu scheme nghĩa là chuỗi không phải DSN; đừng đoán ý người viết.
    if not parts.scheme:
        return None

    name = parts.path.lstrip("/")
    # Path rỗng (không có tên database) hoặc còn dấu "/" nghĩa là DSN không chỉ
    # đúng một database — từ chối thay vì lấy đại phần đầu.
    if not name or "/" in name:
        return None
    return name


def _require_safe(dsn: str) -> str:
    """Trả `dsn` nếu nó trỏ đúng database test, ngược lại raise.

    Áp cho MỌI nguồn. Chỉ kiểm nhánh `os.environ` là bỏ ngỏ đúng nhánh nguy
    hiểm hơn: `.env` nằm trên đĩa, được copy từ máy này sang máy khác, và
    thường là nơi DSN phát triển bị dán nhầm vào.
    """
    if _database_name(dsn) != ALLOWED_TEST_DATABASE:
        raise UnsafeTestDatabaseError
    return dsn


def _from_env_file() -> str | None:
    """Đọc `TEST_DATABASE_URL` từ `.env` ở gốc repo.

    Docstring của module này vẫn bảo "set biến trong .env (local)", nhưng không
    có gì nạp `.env` vào `os.environ` cho pytest: `src/config.py` đọc file qua
    pydantic-settings, và điều đó không đụng tới process env. Nên đường local
    được ghi trong tài liệu chưa bao giờ chạy — dev làm đúng hướng dẫn vẫn thấy
    toàn bộ test PostgreSQL bị skip, và suite vẫn xanh.

    Chỉ lấy đúng một khoá. KHÔNG nạp cả file vào `os.environ`: `.env` chứa API
    key thật, và đổ chúng vào môi trường của test là mở rộng phạm vi rò rỉ mà
    không ai yêu cầu.
    """
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "TEST_DATABASE_URL":
            return value.strip().strip("'\"") or None
    return None


def resolve_test_database_url() -> str | None:
    """DSN test đã được kiểm an toàn, từ process env hoặc `.env`.

    Process env thắng: nó là thứ người chạy vừa gõ, và phải ghi đè được file
    cấu hình cũ trên đĩa.

    Raises:
        UnsafeTestDatabaseError: nguồn thắng trỏ tới database khác
            `p118_test_db`. KHÔNG âm thầm rơi sang nguồn còn lại — làm vậy sẽ
            biến một DSN nguy hiểm thành một lần chạy có vẻ bình thường.
    """
    from_env = os.environ.get("TEST_DATABASE_URL")
    if from_env:
        return _require_safe(from_env)

    from_file = _from_env_file()
    if from_file:
        return _require_safe(from_file)

    return None


async def require_running_app_database() -> None:
    """Skip (local) hoặc FAIL (CI) nếu database của ỨNG DỤNG chưa chạy.

    Khác `require_test_database_url`: hàm kia canh `TEST_DATABASE_URL` —
    `p118_test_db`, nơi test được phép TRUNCATE. Hàm này canh `DATABASE_URL` —
    `p118_db` của stack thật, thứ mà một vài test buộc phải chạm vì chúng gọi
    `lifespan()` thật thay vì tiêm provider giả.

    Vì sao cần: khi stack tắt, những test ấy đổ với
    `InvalidAuthorizationSpecificationError: role "user" does not exist` —
    thông báo của asyncpg về DSN mặc định, không nói gì về việc phải bật
    Docker. Repo vừa clone về là đỏ, và người đọc không có manh mối nào.

    KHÔNG bao giờ ghi gì vào database này, kể cả khi kết nối thành công. Chỉ mở
    rồi đóng.
    """
    import asyncpg

    from src.config import get_settings

    dsn = get_settings().database_url
    try:
        connection = await asyncpg.connect(dsn, timeout=3)
    except Exception:  # noqa: BLE001 - mọi lỗi kết nối đều là "stack chưa sẵn sàng"
        # Message KHÔNG chứa DSN: xem ghi chú ở `_WRONG_DATABASE_MESSAGE`.
        message = (
            "Database của ứng dụng chưa chạy — test này gọi lifespan() thật. "
            "Bật stack bằng `sh scripts/stack_up.sh` rồi chạy lại."
        )
        if is_ci():
            pytest.fail(f"{message} Trong CI, test này không được phép skip.", pytrace=False)
        pytest.skip(message)
    else:
        await connection.close()


def require_test_database_url() -> str:
    """Trả về `TEST_DATABASE_URL` đã kiểm an toàn.

    Thiếu biến: FAIL trong CI, skip khi chạy local.
    Trỏ sai database: FAIL ở mọi môi trường.
    """
    try:
        test_url = resolve_test_database_url()
    except UnsafeTestDatabaseError as exc:
        pytest.fail(str(exc), pytrace=False)

    if test_url:
        return test_url

    if is_ci():
        pytest.fail(f"{_MISSING_MESSAGE} Trong CI, test PostgreSQL không được phép skip.")
    pytest.skip(_MISSING_MESSAGE)
