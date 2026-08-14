"""Dependency injection cho workflow + auth API.

Owner: Hoàng Anh
File: src/api/deps.py

Các dependency:
  - `get_runtime`: trả (ValidatedExecutionBoundary, repository) đã dựng trong
    lifespan và lưu vào `app.state.runtime`. Route gọi boundary để execute,
    gọi repository để đọc/ghi state — không tự tạo Executor/Connector.
  - `get_planner`: build `Planner(get_llm())` lazily (chỉ khi route /workflow/start
    gọi theo goal), cache vào `app.state.planner` để không tạo lại ChatOpenAI
    mỗi request. LLMConfigurationError truyền ra ngoài — route bắt → 503.
  - `get_user_repository`: trả `repository.users` (auth) từ app.state.runtime —
    KHÔNG đổi tuple (boundary, repository) để không phá 16 test workflow cũ.
  - `get_current_user`: giải mã Bearer token → tra user → 401 nếu không hợp lệ.
  - `require_roles(*roles)`: factory chặn 403 nếu role không nằm trong danh sách.

Test override qua `app.dependency_overrides` (chạy bằng httpx.ASGITransport
không fire lifespan → app.state.runtime None).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.api.auth import decode_access_token

bearer_scheme = HTTPBearer(auto_error=True)  # thiếu/sai header → 401 + WWW-Authenticate


async def get_runtime(request: Request) -> tuple[Any, Any]:
    """Lấy (boundary, repository) từ app.state; 503 nếu chưa khởi tạo.

    Test chạy qua httpx.ASGITransport không fire lifespan → app.state.runtime
    None → dependency này ném 503; test override bằng fake qua
    dependency_overrides.
    """
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime chưa được khởi tạo.")
    return runtime


def get_planner(request: Request) -> Any:
    """Build Planner(get_llm()) lazily và cache vào app.state.

    Raises:
        LLMConfigurationError: không có API key — route bắt và trả 503.
    """
    planner = getattr(request.app.state, "planner", None)
    if planner is None:
        from src.agents.planner import Planner
        from src.services.llm import get_llm, structured_output_method

        # Cơ chế structured output theo provider: DeepSeek phải dùng json_mode,
        # các provider khác giữ function_calling.
        planner = Planner(get_llm(), structured_output_method=structured_output_method())
        request.app.state.planner = planner
    return planner


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------


async def get_user_repository(request: Request) -> Any:
    """Trả `repository.users` (auth) từ app.state.runtime; 503 nếu chưa sẵn.

    Không đổi tuple (boundary, repository) — đọc `repository.users` xuyên qua
    nó để 16 test workflow cũ (override get_runtime bằng fake) không phá.
    """
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime chưa được khởi tạo.")
    _, repository = runtime
    users = getattr(repository, "users", None)
    if users is None:
        raise HTTPException(status_code=503, detail="UserRepository chưa được khởi tạo.")
    return users


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    users: Any = Depends(get_user_repository),
) -> dict:
    """Giải mã Bearer token → tra user → trả user dict.

    Raises:
        HTTPException 401: token thiếu/không hợp lệ/hết hạn, hoặc user
            không tồn tại / đã bị vô hiệu hoá (archived_at).
    """
    payload = decode_access_token(credentials.credentials)
    user = await users.get_user_by_id(payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="Người dùng không tồn tại hoặc đã bị vô hiệu hoá.")
    return user


def require_roles(*roles: str):
    """Factory trả dependency chặn 403 nếu user.role không trong `roles`.

    Ví dụ: `user: dict = Depends(require_roles("admin"))` — dùng cho endpoint
    quản trị sau Demo Day (user management, HITL review).
    """

    async def _checker(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Bạn không có quyền thực hiện thao tác này.")
        return user

    return _checker
