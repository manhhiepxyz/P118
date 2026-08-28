"""Tests cho auth API — register / login / me + bảo vệ route.

Dùng `app.dependency_overrides` override `get_user_repository` bằng
FakeUserRepository. `get_current_user` KHÔNG override — test chạy đường thật
decode token → tra user → authorize (chứng minh cả chuỗi auth hoạt động).

Lưu ý ASGITransport không fire lifespan → `app.state.runtime` None. Với route
cần `get_runtime` (workflow) phải override nó bằng fake TRƯỚC để 503 không
che mất 401 (dependency-order gotcha).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import Depends

from src.api.deps import get_current_user, get_planner, get_runtime, get_user_repository
from src.main import app

from .fakes import FakeExecutionBoundary, FakePlanner, FakeRepository, FakeUserRepository


@pytest.fixture
def auth_env():
    """Override get_user_repository bằng fake; trả FakeUserRepository."""
    users = FakeUserRepository()
    app.dependency_overrides[get_user_repository] = lambda: users
    with patch("src.api.auth_routes.OtpRepository.verify_otp", return_value=True):
        yield users
    app.dependency_overrides.clear()


@pytest.fixture
def workflow_runtime_env():
    """Override get_runtime/get_planner bằng fake để gọi /workflow/start.

    KHÔNG override get_current_user — muốn chứng minh token thật pass qua
    route được bảo vệ.
    """
    repo = FakeRepository()
    boundary = FakeExecutionBoundary()
    planner = FakePlanner()
    app.dependency_overrides[get_runtime] = lambda: (boundary, repo)
    app.dependency_overrides[get_planner] = lambda: planner
    yield boundary, repo, planner
    app.dependency_overrides.clear()


async def _register(client, username="nguyen.van.a", password="matkhau123", email=None):
    if email is None:
        email = f"{username.strip().replace(' ', '')}@example.com".lower()
    return await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password, "email": email, "otp_code": "123456"},
    )


async def _login(client, username="nguyen.van.a", password="matkhau123"):
    return await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_creates_a_customer_account(client, auth_env):
    res = await _register(client)
    assert res.status_code == 201
    data = res.json()
    # Role canonical sau Phase B. `resident` không còn là một role: nó là
    # trạng thái liên kết căn hộ, sống ở bảng user_resident_links.
    assert data["role"] == "customer"
    assert data["username"] == "nguyen.van.a"
    # Không lộ password_hash.
    assert "password_hash" not in data
    # Không trả token — client tự login sau.
    assert "access_token" not in data
    # Username chuẩn hoá lowercase.
    res2 = await _register(client, username="  ADMIN.X  ")
    assert res2.status_code == 201
    assert res2.json()["username"] == "admin.x"


@pytest.mark.asyncio
async def test_register_duplicate_409(client, auth_env):
    await _register(client)
    res = await _register(client)
    assert res.status_code == 409
    assert "tồn tại" in res.json()["detail"]


@pytest.mark.asyncio
async def test_register_invalid_payload_422(client, auth_env):
    # Password quá ngắn.
    res = await client.post("/api/v1/auth/register", json={"username": "abc", "password": "short"})
    assert res.status_code == 422
    # Thiếu username.
    res = await client.post(
        "/api/v1/auth/register", json={"password": "matkhau123", "email": "abc@abc.com", "otp_code": "123456"}
    )
    assert res.status_code == 422
    # Extra field (extra="forbid").
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "abc",
            "password": "matkhau123",
            "email": "abc@abc.com",
            "otp_code": "123456",
            "role": "admin",
        },
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_returns_token(client, auth_env):
    await _register(client)
    res = await _login(client)
    assert res.status_code == 200
    data = res.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["expires_in"] > 0
    assert data["user"]["username"] == "nguyen.van.a"
    assert data["user"]["role"] == "customer"


@pytest.mark.asyncio
async def test_login_wrong_password_401(client, auth_env):
    await _register(client)
    res = await _login(client, password="sai-mat-khau")
    assert res.status_code == 401
    assert "không đúng" in res.json()["detail"]


@pytest.mark.asyncio
async def test_login_unknown_user_401(client, auth_env):
    res = await _login(client, username="khong-ton-tai")
    assert res.status_code == 401
    # Message giống hệt ca sai password — chống username enumeration.
    wrong_pw = await _login(client, password="sai-mat-khau")
    assert res.json()["detail"] == wrong_pw.json()["detail"]


@pytest.mark.asyncio
async def test_login_missing_fields_422(client, auth_env):
    res = await client.post("/api/v1/auth/login", json={"username": "abc"})
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_with_valid_token(client, auth_env):
    await _register(client)
    token = (await _login(client)).json()["access_token"]
    res = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["username"] == "nguyen.van.a"


@pytest.mark.asyncio
async def test_me_without_token_401(client, auth_env):
    res = await client.get("/api/v1/auth/me")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_me_with_garbage_token_401(client, auth_env):
    res = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Bảo vệ route workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_user_deleted_401(client, auth_env):
    """User bị archive giữa chừng → 401 khi dùng token cũ."""
    await _register(client)
    token = (await _login(client)).json()["access_token"]
    # Xoá user khỏi fake → get_current_user thấy None → 401.
    auth_env.clear()
    res = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# require_roles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_roles_allows_admin_blocks_resident(client, auth_env):
    """require_roles qua route thật (FastAPI resolve Depends) — 403 vs pass.

    Định nghĩa route admin-test tạm trên app, resolve require_roles('admin')
    với get_current_user override để điều khiển role của user.
    """
    from fastapi import APIRouter

    from src.api.deps import require_roles as require_roles_dep

    test_router = APIRouter()

    @test_router.get("/admin-test")
    async def _admin_only(user: dict = Depends(require_roles_dep("admin"))):
        return {"role": user["role"]}

    app.include_router(test_router)

    # resident → 403
    app.dependency_overrides[get_current_user] = lambda: {"id": "x", "username": "u", "role": "customer"}
    try:
        res = await client.get("/admin-test")
        assert res.status_code == 403
    finally:
        app.dependency_overrides.clear()

    # admin → pass
    app.dependency_overrides[get_current_user] = lambda: {"id": "x", "username": "u", "role": "admin"}
    try:
        res = await client.get("/admin-test")
        assert res.status_code == 200
        assert res.json()["role"] == "admin"
    finally:
        app.dependency_overrides.clear()

    # Dọn route tạm.
    for route in app.routes:
        if getattr(route, "path", None) == "/admin-test":
            app.routes.remove(route)
            break


# ---------------------------------------------------------------------------
# API workflow canonical — thay cho `/workflow/start` đã bị xoá ở Phase C.
# ---------------------------------------------------------------------------


@pytest.mark.anonymous
@pytest.mark.asyncio
async def test_starting_a_workflow_without_a_token_is_401(client):
    """Cổng vào của Agent phải từ chối người chưa đăng nhập."""
    res = await client.post("/api/v1/workflows/demo/start", json={"goal": "Đăng ký cư dân"})

    assert res.status_code == 401


@pytest.mark.anonymous
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/v1/workflow/start"),
        ("get", "/api/v1/workflow/wf-1/status"),
        ("post", "/api/v1/workflow/wf-1/execute"),
        ("get", "/api/v1/workflows?page=1&limit=10"),
    ],
)
async def test_the_legacy_workflow_api_no_longer_exists(client, method, path):
    """Bộ API legacy phải biến mất hẳn, không chỉ ngừng được gọi.

    Bốn route này có `Depends(get_current_user)` nên trông như đã được bảo vệ,
    nhưng không kiểm chủ sở hữu: `/status` đọc được workflow của bất kỳ ai và
    `GET /workflows` liệt kê toàn hệ thống. Giữ lại là giữ một đường vòng qua
    toàn bộ guard Phase B.
    """
    kwargs = {"json": {}} if method == "post" else {}
    res = await getattr(client, method)(path, **kwargs)

    assert res.status_code in {404, 405}, res.text
