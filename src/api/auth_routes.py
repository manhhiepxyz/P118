"""
src/api/auth_routes.py
P-118 — Auth endpoints: register / login / me

Owner: Hoàng Anh

Mounted ở /api/v1/auth (prefix đặt trên router). Body JSON — KHÔNG dùng
OAuth2PasswordRequestForm vì cần python-multipart chưa cài. Đăng ký mặc định
role='resident'; admin tạo bằng scripts/create_admin.py.

Test: override `get_user_repository` bằng FakeUserRepository qua
`app.dependency_overrides` (ASGITransport không fire lifespan).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.api.auth import create_access_token, hash_password, verify_password
from src.api.deps import get_current_user, get_user_repository
from src.api.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from src.config import get_settings
from src.db.user_repository import UserAlreadyExistsError

router = APIRouter(prefix="/auth", tags=["auth"])

_USERNAME_OR_PASSWORD = "Tên đăng nhập hoặc mật khẩu không đúng."


def _to_user_response(user: dict) -> UserResponse:
    """Dict user (có thể kèm password_hash) → UserResponse (bỏ hash)."""
    return UserResponse(
        id=str(user["id"]),
        username=user["username"],
        email=user.get("email"),
        role=user["role"],
        created_at=user["created_at"],
    )


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    req: RegisterRequest,
    users: Any = Depends(get_user_repository),
) -> UserResponse:
    """Đăng ký tài khoản mới — luôn tạo role='resident'. Không trả token.

    Trả 409 nếu username đã tồn tại (đồng bộ với nút trùng trong DB).
    """
    username = req.username.strip().lower()
    password_hash = hash_password(req.password)
    email = req.email.strip().lower() if req.email and req.email.strip() else None

    try:
        user = await users.create_user(
            username=username,
            password_hash=password_hash,
            role="resident",
            email=email,
        )
    except UserAlreadyExistsError:
        raise HTTPException(status_code=409, detail="Tên đăng nhập đã tồn tại.") from None

    return _to_user_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    users: Any = Depends(get_user_repository),
) -> TokenResponse:
    """Đăng nhập → access token (24h). Message 401 giống nhau cho sai username
    lẫn sai password — tránh lộ username hợp lệ."""
    username = req.username.strip().lower()
    user = await users.get_user_by_username(username)
    if user is None or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail=_USERNAME_OR_PASSWORD)

    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(user),
        token_type="bearer",
        expires_in=settings.jwt_expire_minutes * 60,
        user=_to_user_response(user),
    )


@router.get("/me", response_model=UserResponse)
async def me(user: dict = Depends(get_current_user)) -> UserResponse:
    """Thông tin user hiện tại (theo Bearer token)."""
    return _to_user_response(user)
