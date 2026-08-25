"""Composition root: repository chỉ đến từ một chỗ, và fail-closed khi thiếu.

Trước đây mỗi module `import build_repository` vào namespace riêng rồi tự gọi.
Nó đọc thẳng `settings.database_url`, nên test phải patch từng module một —
và bốn lần đã quên một module, khiến route đó lặng lẽ đọc `p118_db` thật trong
khi phần còn lại của suite chạy trên `p118_test_db`. Không có gì báo động.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._dbcheck import require_running_app_database

from src.orchestration import runtime_provider
from src.orchestration.runtime_provider import (
    RepositoryNotConfiguredError,
    acquire_repository,
    clear_repository_provider,
    set_repository_provider,
)

REPO_ROOT = Path(__file__).parents[1]

# Chỉ hai nơi được phép dựng repository trực tiếp: chính factory, và
# composition root nối nó vào vòng đời app.
ALLOWED_DIRECT_BUILDERS = {"src/orchestration/deps.py", "src/main.py"}


@pytest.mark.asyncio
async def test_acquiring_without_a_provider_fails_instead_of_opening_a_connection():
    """Thiếu cấu hình phải nổ, KHÔNG rơi về DATABASE_URL.

    Fallback im lặng chính là thứ đã đưa test tới database phát triển: nó luôn
    tìm được một đường đi tới môi trường sai.
    """
    original = runtime_provider._provider  # noqa: SLF001 - test khôi phục nguyên trạng
    clear_repository_provider()
    try:
        with pytest.raises(RepositoryNotConfiguredError):
            await acquire_repository()
    finally:
        runtime_provider._provider = original  # noqa: SLF001


@pytest.mark.asyncio
async def test_the_error_never_names_a_database_or_a_dsn():
    """Lỗi cấu hình hay bị dán vào issue và CI log — nơi credential sống lâu nhất."""
    message = str(RepositoryNotConfiguredError())

    for leaked in ("postgresql://", "p118_db", "p118_test_db", "password", "@localhost", "5432"):
        assert leaked not in message, f"message rò {leaked!r}"


@pytest.mark.asyncio
async def test_overriding_one_provider_is_enough_for_every_module():
    """Một chỗ override phải phủ routes/auth/admin/orchestration.

    Đây là điều kiện khiến bug cũ không tái diễn: nếu còn module nào tự dựng
    repository, nó sẽ KHÔNG thấy provider này và test dưới đây sẽ lộ ra.
    """
    sentinel = object()

    async def _provide():
        return sentinel

    original = runtime_provider._provider  # noqa: SLF001
    set_repository_provider(_provide)
    try:
        from src.api import admin_routes, auth_routes, routes
        from src.orchestration import compensation, demo_service, sweeper

        for module in (routes, auth_routes, admin_routes, demo_service, sweeper, compensation):
            assert await module.acquire_repository() is sentinel, f"{module.__name__} dùng nguồn khác"
    finally:
        runtime_provider._provider = original  # noqa: SLF001


def test_no_module_builds_its_own_repository_behind_the_scenes():
    """Đối chiếu tĩnh: chỉ factory và composition root được gọi `build_repository`.

    Chạy được không cần database, nên nó chặn hồi quy ngay khi ai đó thêm lại
    một `build_repository()` trong handler — thời điểm dễ bỏ sót nhất.
    """
    offenders: list[str] = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in ALLOWED_DIRECT_BUILDERS:
            continue
        # Bỏ docstring và comment: tên hàm xuất hiện trong lời giải thích là
        # bình thường, chỉ LỜI GỌI mới là vi phạm.
        code = re.sub(r'"""(?:.|\n)*?"""', "", path.read_text(encoding="utf-8"))
        code = re.sub(r"#[^\n]*", "", code)
        if re.search(r"\bbuild_repository\s*\(", code):
            offenders.append(relative)

    assert not offenders, f"module tự dựng repository, bỏ qua composition root: {sorted(offenders)}"


def test_the_shared_pool_refuses_to_be_closed_by_a_request_helper():
    """Pool app-lifetime: `close()` phải là no-op.

    Các helper request-scope đóng pool trong `finally` — đúng khi pool là của
    riêng chúng. Với pool dùng chung, cú đóng đầu tiên sẽ giết kết nối của mọi
    request sau, và lỗi chỉ xuất hiện từ request thứ hai trở đi.
    """

    class _Inner:
        closed = False

        async def close(self) -> None:
            _Inner.closed = True

    shared = runtime_provider.SharedPool(_Inner())

    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(shared.close())

    assert _Inner.closed is False


@pytest.mark.asyncio
async def test_the_application_lifespan_actually_installs_a_working_provider():
    """Chạy thật lifespan và khẳng định provider dùng được.

    Bug đã xảy ra: một helper bị chèn giữa `@asynccontextmanager` và
    `lifespan`, nên decorator dính nhầm vào helper. App vẫn khởi động, mọi test
    in-process vẫn xanh — nhưng MỌI request chạm database đều 500 với
    `_AsyncGeneratorContextManager can't be used in 'await' expression`.

    Không test nào bắt được vì test override provider trước khi chạy. Chỉ
    browser E2E thấy. Test này đóng khoảng trống đó: nó gọi lifespan thật rồi
    dùng chính provider mà lifespan cài.
    """
    import inspect

    from src.main import app, lifespan

    # Kiểm tra decorator chạy TRƯỚC guard database, và không cần database.
    # Chính bug lịch sử nói ở trên bị bắt bởi đúng dòng này — nên nó phải chạy
    # cả khi stack tắt, nếu không thì lá chắn duy nhất chống lần lặp lại lại
    # biến mất đúng lúc dev chưa bật Docker.
    assert inspect.isasyncgenfunction(lifespan.__wrapped__), (  # noqa: SLF001
        "lifespan phải là async generator được bọc bởi @asynccontextmanager"
    )

    # Phần còn lại mở pool thật tới `p118_db`, nên cần stack đang chạy.
    await require_running_app_database()

    original = runtime_provider._provider  # noqa: SLF001
    try:
        async with lifespan(app):
            repository = await acquire_repository()
            assert repository is not None
            # Pool dùng chung: helper request-scope đóng nó phải là no-op.
            await repository._pool.close()  # noqa: SLF001
            assert await acquire_repository() is repository
    finally:
        runtime_provider._provider = original  # noqa: SLF001


def test_no_private_helper_is_accidentally_mounted_as_a_route():
    """Helper chèn nhầm giữa decorator và handler sẽ thành route công khai.

    Đã xảy ra hai lần: một lần với `@asynccontextmanager` + `lifespan`, một lần
    với `@router.get` + `get_demo_workflow_status`. Cả hai lần app vẫn khởi
    động và test in-process vẫn xanh — chỉ request thật mới lộ ra.

    Tên bắt đầu bằng `_` là quy ước "không phải API công khai", nên nó xuất hiện
    trong bảng route đồng nghĩa với một lần chèn sai chỗ.
    """
    from src.main import app

    mounted = sorted(
        name for name in (getattr(route, "name", "") for route in app.router.routes) if name.startswith("_")
    )

    assert not mounted, f"helper riêng bị mount thành route: {mounted}"
