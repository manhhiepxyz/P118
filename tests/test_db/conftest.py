"""
tests/test_db/conftest.py
P-118 — Fixtures cho test PostgreSQL repository

Yêu cầu:
- PostgreSQL đang chạy (local hoặc Docker)
- Biến môi trường TEST_DATABASE_URL đã set trong .env
  Ví dụ: postgresql://p118:p118pass@localhost:5432/p118_test_db

Chạy test:
    pytest tests/test_db/ -v
    # hoặc chỉ test repository:
    pytest tests/test_db/test_repository.py -v
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrations import create_test_db
from tests._dbcheck import require_test_database_url


@pytest_asyncio.fixture(scope="session")
async def db_pool() -> asyncpg.Pool:
    """
    Tạo pool kết nối tới test DB và chạy migration.
    scope="session": dùng chung pool cho cả test session → nhanh hơn.
    """
    # Thiếu TEST_DATABASE_URL: skip khi chạy local, FAIL khi chạy CI.
    # Skip âm thầm trong CI khiến toàn bộ tầng PostgreSQL không được kiểm
    # mà suite vẫn báo xanh.
    test_url = require_test_database_url()

    pool = await create_test_db(test_url)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(db_pool: asyncpg.Pool) -> None:
    """
    Xóa dữ liệu test sau mỗi test case — giữ schema nguyên.
    Thứ tự xóa theo FK dependency (con trước, cha sau).
    """
    yield
    # Chờ tác vụ NỀN xong TRƯỚC khi dọn bảng.
    #
    # `request_fresh_answer` và đường chạy workflow đều `create_task` rồi trả về
    # ngay, nên chúng còn sống sau khi test đã xong — và còn cầm khoá dòng.
    # `TRUNCATE` lấy khoá theo thứ tự liệt kê, tác vụ nền lấy theo thứ tự
    # nghiệp vụ của nó; hai thứ tự ngược nhau là công thức của một deadlock:
    #
    #     asyncpg.exceptions.DeadlockDetectedError: deadlock detected
    #     Process A waits for AccessExclusiveLock ...; blocked by B.
    #     Process B waits for RowShareLock ...; blocked by A.
    #
    # PostgreSQL giết một bên, và bên thua đổi theo tải máy — nên nó hiện ra
    # như "một test khác nhau mỗi lượt, ~1/3 số lượt". Không phải flaky: dọn
    # bảng dưới chân một việc đang chạy thì đó là lỗi của phép dọn.
    from src.api.routes import drain_demo_tasks

    await drain_demo_tasks()

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE TABLE
                -- Chứng từ báo giá. Sót lại thì `bao_gia_dang_song` của test
                -- sau đọc phải báo giá của test trước — và vì vân tay tính từ
                -- input, hai test dùng cùng một yêu cầu mẫu sẽ trùng vân tay.
                service_quotes,
                -- Ai nhân danh đơn vị nào. Dọn cùng `users` chứ không dựa vào
                -- CASCADE: một mapping sót lại cho phép tài khoản của test sau
                -- thấy hàng đợi của test trước, và test ấy xanh vì lý do sai.
                service_provider_accounts,
                -- Hàng đợi duyệt của MỌI dịch vụ. Sau khi gộp hai hàng đợi,
                -- `viewing_approvals` chỉ còn là khung nhìn trên bảng này —
                -- dọn khung nhìn thì không dọn được gì, và test sau đọc phải
                -- dữ liệu của test trước.
                service_approvals,
                approval_decisions,
                execution_logs,
                llm_usage,
                payment_approvals,
                sessions,
                workflow_repair_hints,
                workflow_tasks,
                workflows,
                consultations,
                shuttle_bookings,
                tour_capacity,
                tour_bookings,
                payments,
                parking_capacity,
                parking_bookings,
                vehicles,
                residents,
                verification_records,
                users
            RESTART IDENTITY CASCADE
            """
        )


# ---------------------------------------------------------------------------
# Phase B — client mang danh tính thật, dùng chung cho các test auth/IDOR.
#
# Đặt ở conftest thay vì import chéo giữa hai file test: import một fixture
# từ module test khác khiến `client` bị định nghĩa lại và ruff báo F811, còn
# thứ tự nạp module thì quyết định fixture nào thắng.
# ---------------------------------------------------------------------------

from httpx import ASGITransport, AsyncClient  # noqa: E402

from src.main import app  # noqa: E402
from src.orchestration.runtime_provider import (  # noqa: E402
    clear_repository_provider,
    set_repository_provider,
)


@pytest_asyncio.fixture
async def client(db_pool, monkeypatch):
    """Client in-process nói chuyện với PostgreSQL test thật.

    `app.state.runtime` chỉ được dựng trong lifespan, mà ASGITransport không
    chạy lifespan — nên mọi endpoint auth trả 503. Ở đây gắn thẳng repository
    thật (không phải fake) để test đi qua đúng đường SQL mà production đi.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    repository = PostgreSQLWorkflowStateRepository(db_pool)

    class _SharedPool:
        def __init__(self, pool):
            self._inner = pool

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def close(self):
            return None

    repository._pool = _SharedPool(db_pool)  # noqa: SLF001 - test sở hữu pool

    async def _provide():
        return repository

    # ĐÚNG MỘT chỗ override. Trước đây mỗi module import `build_repository` vào
    # namespace riêng nên test phải vá từng module — và bốn lần đã quên một
    # module, khiến route đó lặng lẽ đọc `p118_db` thật trong khi phần còn lại
    # chạy trên `p118_test_db`.
    set_repository_provider(_provide)

    app.state.runtime = (None, repository)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        clear_repository_provider()
        app.state.runtime = None


async def _register_and_login(client: AsyncClient, username: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "MatKhauRatDai123!"},
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "MatKhauRatDai123!"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


# ---------------------------------------------------------------------------
# Danh tính ĐƠN VỊ CUNG CẤP — dùng chung cho mọi test đi qua cổng duyệt.
#
# Từ khi cổng duyệt kiểm quyền sở hữu (fail-closed), một tài khoản `provider`
# KHÔNG được gắn đơn vị nào thì thấy hàng đợi rỗng và không quyết định được gì.
# Đó là hành vi đúng — nhưng nó khiến mọi test dựng provider bằng tay lặng lẽ
# đổi nghĩa: chúng vẫn xanh ở phần 401/403, và đỏ ở phần "provider làm được".
#
# Nên chỉ có MỘT chỗ dựng danh tính ấy. Lặp lại đoạn INSERT này ở từng file
# test là cách nhanh nhất để lần sau đổi luật phải sửa mười chỗ và quên hai.
# ---------------------------------------------------------------------------


async def dang_nhap_don_vi(
    client: AsyncClient,
    db_pool: asyncpg.Pool,
    username: str,
    *,
    don_vi: tuple[str, ...] = ("BQL-PARK", "FIX-01", "MOV-01", "BQL-SHUTTLE", "BQL-SALES"),
) -> tuple[str, str]:
    """Tài khoản `provider` đã được gắn đơn vị. Trả `(token, user_id)`.

    Mặc định gắn TẤT CẢ đơn vị mặc định, vì phần lớn test chỉ cần "một người
    duyệt hợp lệ" chứ không kiểm ranh giới sở hữu. Test nào kiểm ranh giới thì
    truyền `don_vi` hẹp lại — và khi ấy nó nói rõ mình đang kiểm cái gì.

    Token cấp SAU khi role đã đổi: `require_roles` đọc vai từ JWT, nên một token
    phát trước lúc promote sẽ mang vai cũ và test đo nhầm thứ nó tuyên bố.
    """
    await _register_and_login(client, username)
    await db_pool.execute("UPDATE users SET role = 'provider' WHERE username = $1", username)
    token = await _register_and_login(client, username)
    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    for provider_id in don_vi:
        await db_pool.execute(
            "INSERT INTO service_provider_accounts (user_id, service_provider_id) "
            "VALUES ($1, $2) ON CONFLICT DO NOTHING",
            user_id,
            provider_id,
        )
    return token, str(user_id)


# ---------------------------------------------------------------------------
# Ma trận "duyệt → chạy tiếp → hoàn tất" — connector gián điệp dùng chung.
#
# Đặt ở conftest thay vì import chéo giữa hai file test: import một fixture từ
# module test khác rồi lại nhận nó làm tham số khiến nó bị định nghĩa lại và
# ruff báo F811 — đúng lý do `client` cũng nằm ở đây.
# ---------------------------------------------------------------------------


@pytest.fixture
def matrix_spy(db_pool, monkeypatch):
    """MỘT connector cho mọi tool, tiêm vào cả BA lối ra provider.

    Ba lối, không một: `build_connectors` cho Executor, `TourConnector` cho
    materialize lịch tham quan, `PaymentConnector` cho đường trả tiền. Bỏ sót
    một lối nghĩa là test đi ra HTTP thật và hỏng theo kiểu khó đọc.
    """
    from tests.matrix.domain_spy import DomainSpyConnector

    connector = DomainSpyConnector(pool=db_pool)
    monkeypatch.setattr("src.orchestration.demo_service.build_connectors", lambda **_: [connector])
    monkeypatch.setattr("src.orchestration.demo_service.TourConnector", lambda **_: connector)
    monkeypatch.setattr("src.orchestration.demo_service.PaymentConnector", lambda **_: connector)
    return connector
