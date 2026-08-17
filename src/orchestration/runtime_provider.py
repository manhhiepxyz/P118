"""Composition root cho repository — một nguồn duy nhất, không fallback.

Vì sao module này tồn tại:

Trước đây mỗi handler, helper và background job tự `from ...deps import
build_repository` rồi gọi nó. Hệ quả không phải chỉ là lặp code:

  - **Test đọc nhầm database phát triển.** `build_repository()` đọc thẳng
    `settings.database_url`. Mỗi module import hàm đó vào namespace RIÊNG, nên
    test phải `monkeypatch` từng module một. Quên một module — và đã quên bốn
    lần — thì route đó lặng lẽ mở kết nối tới `p118_db` thật trong khi phần còn
    lại của test chạy trên `p118_test_db`. Không có gì báo động.
  - **Mỗi request mở một pool mới.** 27 chỗ gọi, mỗi chỗ `create_pool` rồi
    `close`. Đó là bắt tay TCP + xác thực cho từng lần chạm database.

Thiết kế ở đây:

  - Provider được ĐẶT MỘT LẦN lúc app khởi động (`src/main.py` lifespan).
  - Mọi nơi cần repository gọi `acquire_repository()`.
  - Chưa đặt provider → **raise**. KHÔNG rơi về `DATABASE_URL`: fallback chính
    là thứ đã khiến test chạm nhầm database, và một fallback im lặng luôn tìm
    được đường tới môi trường sai.
  - Test override đúng MỘT chỗ này, không phải từng namespace.

Pool trả về là pool app-lifetime, nên `close()` trên nó là no-op: các helper
hiện có đóng pool trong `finally`, và nếu cú đóng đó là thật thì request đầu
tiên sẽ giết kết nối của mọi request sau.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

RepositoryFactory = Callable[[], Awaitable[Any]]

_provider: RepositoryFactory | None = None


class RepositoryNotConfiguredError(RuntimeError):
    """Chưa có composition root nào đặt provider.

    Message KHÔNG chứa DSN, host hay tên database: lỗi cấu hình hay bị dán vào
    issue và CI log, và đó là nơi credential sống lâu nhất.
    """

    def __init__(self) -> None:
        super().__init__(
            "Repository chưa được cấu hình. "
            "Ứng dụng phải gọi set_repository_provider() lúc khởi động, "
            "và test phải override provider đó."
        )


class SharedPool:
    """Pool app-lifetime; `close()` là no-op.

    Các helper request-scope hiện có đều `await pool.close()` trong `finally` —
    đúng khi pool là của riêng chúng, sai khi pool dùng chung. Bọc ở đây thay vì
    sửa 27 khối `finally` giữ cho thay đổi này nhỏ và dễ soát.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def close(self) -> None:
        """Không đóng gì. Vòng đời pool thuộc về lifespan của app."""
        return None

    async def aclose(self) -> None:
        return None


def set_repository_provider(provider: RepositoryFactory) -> None:
    """Đăng ký nguồn repository. Chỉ composition root và test được gọi."""
    global _provider
    _provider = provider


def clear_repository_provider() -> None:
    """Gỡ provider — dùng khi shutdown và khi test dọn dẹp."""
    global _provider
    _provider = None


def has_repository_provider() -> bool:
    return _provider is not None


async def acquire_repository() -> Any:
    """Repository dùng chung của tiến trình.

    Raises:
        RepositoryNotConfiguredError: chưa ai đặt provider. Fail-closed là chủ
            ý — thà một lỗi ồn ào còn hơn một kết nối im lặng tới database sai.
    """
    if _provider is None:
        raise RepositoryNotConfiguredError
    return await _provider()
