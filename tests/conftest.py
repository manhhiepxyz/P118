import os
from unittest.mock import AsyncMock

# Tắt rate limiter trong test suite để các test API không bị 429 do share bucket.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
# Tắt zombie sweep: list endpoints test không có PostgreSQL thật, sweep sẽ mở
# pool vào database thật và làm hỏng cô lập. Lazy sweep vẫn được test riêng
# trong tests/test_sweeper.py bằng cách bật flag lên.
os.environ.setdefault("ZOMBIE_SWEEP_ENABLED", "false")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.orchestration.runtime_provider import clear_repository_provider, set_repository_provider


@pytest_asyncio.fixture
async def client(request):
    """Async HTTP client, mặc định MANG SẴN token hợp lệ.

    Sau Phase B, các endpoint workflow đòi xác thực. Phần lớn test ở đây kiểm
    hành vi nghiệp vụ chứ không kiểm auth, nên chúng cần một danh tính hợp lệ
    để đi qua cổng — KHÔNG phải cần tắt cổng đi.

    Vì vậy fixture này phát một token THẬT và để `get_current_user` chạy đầy
    đủ: giải mã token, tra user. Chỉ kho user là giả. Override
    `get_current_user` thay vì làm thế này sẽ vô hiệu hoá luôn phần kiểm token,
    và một hồi quy ở đó sẽ không test nào bắt được.

    Test nào cần trạng thái chưa đăng nhập thì đánh dấu `@pytest.mark.anonymous`.
    """
    from src.api.auth import create_access_token
    from src.api.deps import get_user_repository
    from tests.test_api.fakes import FAKE_USER, FakeUserRepository

    anonymous = request.node.get_closest_marker("anonymous") is not None

    headers = {}
    if not anonymous:
        users = FakeUserRepository()
        users._users[str(FAKE_USER["id"])] = dict(FAKE_USER)  # noqa: SLF001 - test dựng state
        users._by_username[FAKE_USER["username"]] = dict(FAKE_USER)  # noqa: SLF001
        app.dependency_overrides[get_user_repository] = lambda: users
        headers["Authorization"] = f"Bearer {create_access_token(FAKE_USER)}"

    # Guard ownership tra chủ sở hữu qua provider trung tâm, mà các test ở đây
    # chạy trên repository giả. Cấp một repo giả CÓ `get_workflow_owner` trả về
    # chính user đang đăng nhập: guard vẫn chạy đủ đường, chỉ nguồn dữ liệu là
    # giả. Việc chứng minh guard thực sự chặn người khác thuộc về tests/test_db
    # — nơi có PostgreSQL thật và hai tài khoản thật.

    class _StubAcquire:
        """`async with pool.acquire() as conn` — conn cũng là pool rỗng."""

        async def __aenter__(self):
            return _StubPool()

        async def __aexit__(self, *_exc):
            return False

    class _OwnerAwareRepository:
        """Uỷ quyền mọi thứ cho repository thật, chỉ trả lời riêng câu hỏi chủ sở hữu.

        Thay hẳn repository sẽ nuốt luôn đường ghi shell/session mà nhiều test ở
        đây đang kiểm — guard quyền không được phép làm hỏng phần còn lại.
        """

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def get_workflow_owner(self, workflow_id: str):
            return None if anonymous else str(FAKE_USER["id"])

    class _StubPool:
        """Pool KHÔNG kết nối gì cả — trả rỗng cho mọi truy vấn.

        Trước đây các test ở đây đi qua `build_repository()` thật, nghĩa là
        chúng mở kết nối tới DATABASE_URL phát triển. Không ai thấy vì kết quả
        vẫn đúng: database dev có sẵn schema. Giờ chúng chạy trên một pool
        không chạm mạng — test cần dữ liệu thật thuộc về tests/test_db.
        """

        async def close(self) -> None:
            return None

        async def fetchrow(self, *_args, **_kwargs):
            return None

        async def fetchval(self, *_args, **_kwargs):
            return None

        async def fetch(self, *_args, **_kwargs):
            return []

        async def execute(self, *_args, **_kwargs):
            return "OK"

        def acquire(self):
            return _StubAcquire()

    class _StubRepository(_OwnerAwareRepository):
        """Repo cho test API dùng fake — KHÔNG chạm PostgreSQL.

        Nó đại diện cho persistence THÀNH CÔNG: `/start` giờ ghim shell +
        session trước khi trả 202, nên một stub không biết `create_workflow` sẽ
        khiến mọi test ở đây nhận 503 — lỗi của fixture, không phải của route.

        Các thao tác ghi là no-op thành công. Thao tác nào KHÔNG được khai báo
        vẫn raise: test cần dữ liệu thật thuộc về tests/test_db.
        """

        _pool = _StubPool()

        def __init__(self):
            super().__init__(None)

        async def create_shell_and_session(self, **_kwargs) -> None:
            return None

        async def create_workflow(self, workflow_data: dict) -> str:
            return str(workflow_data.get("id") or "00000000-0000-0000-0000-0000000000ff")

        async def save_clarification(self, *_args, **_kwargs) -> None:
            return None

        async def update_workflow_status(self, *_args, **_kwargs) -> None:
            return None

        def __getattr__(self, name):
            raise AttributeError(
                f"Test API không được chạm repository thật qua {name!r}. Test cần database phải nằm ở tests/test_db."
            )

    async def _stub_provider():
        return _StubRepository()

    set_repository_provider(_stub_provider)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as ac:
            yield ac
    finally:
        clear_repository_provider()
        app.dependency_overrides.pop(get_user_repository, None)


@pytest.fixture
def mock_llm():
    """Mock LLM to avoid calling OpenAI during tests.

    Usage in test:
        def test_something(mock_llm):
            # LLM calls will return mock response instead of hitting OpenAI
            ...
    """
    mock = AsyncMock()
    mock.ainvoke.return_value = AsyncMock(content="Mocked LLM response")
    return mock


@pytest.fixture(autouse=True)
def _test_jwt_secret(monkeypatch):
    """Cấp JWT secret dùng-một-lần cho toàn bộ test.

    Production PHẢI fail-closed khi `JWT_SECRET` rỗng — đó là hành vi đúng và
    không được nới. Nhưng test thì không được bắt developer export biến môi
    trường trước khi chạy `pytest`: chạy được hay không sẽ phụ thuộc shell của
    từng người, và CI sẽ đỏ vì lý do không liên quan tới code.

    Secret ở đây được sinh ngẫu nhiên mỗi lần chạy, KHÔNG ghi ra file và không
    bao giờ được in. Test nào muốn kiểm hành vi "secret rỗng" thì tự
    monkeypatch đè lại trong phạm vi của nó.
    """
    import secrets

    from src.config import get_settings

    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
