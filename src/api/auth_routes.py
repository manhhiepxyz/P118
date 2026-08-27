"""
src/api/auth_routes.py
P-118 — Auth endpoints: register / login / me

Owner: Hoàng Anh

Mounted ở /api/v1/auth (prefix đặt trên router). Body JSON — KHÔNG dùng
OAuth2PasswordRequestForm vì cần python-multipart chưa cài. Đăng ký mặc định
role='customer'; admin tạo bằng scripts/create_admin.py.

`customer` là VAI TRÒ TÀI KHOẢN, không phải quyền cư dân. Quyền cư dân nằm ở
bảng `user_resident_links` và chỉ mở khi verification_status='VERIFIED'.

Test: override `get_user_repository` bằng FakeUserRepository qua
`app.dependency_overrides` (ASGITransport không fire lifespan).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, BackgroundTasks

from src.api.auth import create_access_token, hash_password, verify_password
from src.api.deps import get_current_user, get_user_repository
from src.api.schemas import (
    LoginRequest,
    RegisterRequest,
    SendOtpRequest,
    TokenResponse,
    UserResponse,
)
from src.config import get_settings
from src.db.resident_link_repository import get_link_status, get_verified_identity
from src.db.user_repository import UserAlreadyExistsError
from src.db.otp_repository import OtpRepository
from src.services.email_service import send_otp_email
from src.orchestration.runtime_provider import acquire_repository

router = APIRouter(prefix="/auth", tags=["auth"])

_USERNAME_OR_PASSWORD = "Tên đăng nhập hoặc mật khẩu không đúng."


def _clean(value: str | None) -> str | None:
    """Strip optional string field; None và chuỗi rỗng đều → None (không ghi)."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _to_user_response(user: dict) -> UserResponse:
    """Dict user (có thể kèm password_hash) → UserResponse (bỏ hash)."""
    dob = user.get("date_of_birth")
    return UserResponse(
        id=str(user["id"]),
        username=user["username"],
        email=user.get("email"),
        role=user["role"],
        created_at=user["created_at"],
        full_name=user.get("full_name"),
        phone=user.get("phone"),
        address=user.get("address"),
        date_of_birth=dob.isoformat() if hasattr(dob, "isoformat") else dob,
        gender=user.get("gender"),
        cccd_last4=user.get("cccd_last4"),
        avatar_url=user.get("avatar_url"),
    )


@router.post("/send-registration-otp", status_code=200)
async def send_registration_otp(
    req: SendOtpRequest,
    background_tasks: BackgroundTasks,
    users: Any = Depends(get_user_repository),
) -> dict:
    """Gửi OTP xác nhận email trước khi đăng ký."""
    username = req.username.strip().lower()
    user_by_username = await users.get_user_by_username(username)
    if user_by_username is not None:
        raise HTTPException(status_code=409, detail="Tên đăng nhập đã tồn tại.")

    email = req.email.strip().lower()
    user_by_email = await users.get_user_by_email(email)
    if user_by_email is not None:
        raise HTTPException(status_code=409, detail="Email này đã được sử dụng.")

    otp_code = str(secrets.choice(range(100000, 999999)))
    
    repository = await acquire_repository()
    pool = repository._pool
    try:
        from src.db.otp_repository import CooldownError
        otp_repo = OtpRepository(pool)
        await otp_repo.save_otp(email, otp_code)
    except CooldownError as e:
        raise HTTPException(status_code=429, detail=str(e))
    finally:
        await pool.close()

    # Gửi email qua background để không block request
    background_tasks.add_task(send_otp_email, email, otp_code)
    return {"message": "OTP đã được gửi đến email của bạn."}


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    req: RegisterRequest,
    users: Any = Depends(get_user_repository),
) -> UserResponse:
    """Đăng ký tài khoản mới — luôn tạo role='customer'. Không trả token.

    Yêu cầu mã OTP hợp lệ được gửi tới email.
    """
    username = req.username.strip().lower()
    password_hash = hash_password(req.password)
    email = req.email.strip().lower()

    # Kiểm tra OTP trước
    repository = await acquire_repository()
    pool = repository._pool
    try:
        otp_repo = OtpRepository(pool)
        is_valid = await otp_repo.verify_otp(email, req.otp_code)
        if not is_valid:
            raise HTTPException(status_code=400, detail="Mã OTP không hợp lệ hoặc đã hết hạn.")
    finally:
        await pool.close()

    try:
        from src.db.user_repository import EmailAlreadyExistsError
        user = await users.create_user(
            username=username,
            password_hash=password_hash,
            role="customer",
            email=email,
            full_name=_clean(req.full_name),
            phone=_clean(req.phone),
            address=_clean(req.address),
            date_of_birth=_clean(req.date_of_birth),
            gender=_clean(req.gender),
            cccd_last4=_clean(req.cccd_last4),
        )
    except UserAlreadyExistsError:
        raise HTTPException(status_code=409, detail="Tên đăng nhập đã tồn tại.") from None
    except EmailAlreadyExistsError:
        raise HTTPException(status_code=409, detail="Email này đã được sử dụng.") from None

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
    """Thông tin user hiện tại + trạng thái liên kết căn hộ.

    UI cần biết tài khoản đã liên kết chưa để hiển thị dịch vụ cư dân là mở hay
    khoá. Dữ liệu đọc từ `user_resident_links` + `residents`, không từ body và
    không từ token — token chỉ nói người dùng là AI, không nói họ ở căn nào.
    """
    base = _to_user_response(user)

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        identity = await get_verified_identity(pool, user["id"])
        status = await get_link_status(pool, user["id"])
    finally:
        await pool.close()

    if identity is not None:
        return base.model_copy(
            update={
                "resident_verification_status": "VERIFIED",
                "apartment_code": identity.apartment_code,
                "residential_area": identity.residential_area,
            }
        )

    # PENDING/REJECTED được trả đúng tên để UI nói được "hồ sơ đang chờ duyệt"
    # thay vì "bạn chưa liên kết" — nhưng CẢ HAI đều không mở quyền, và không
    # kèm căn hộ nào.
    return base.model_copy(
        update={"resident_verification_status": status.value if status is not None else "NOT_LINKED"}
    )


# ĐÃ XOÁ: hai route nộp/xem yêu cầu liên kết căn hộ theo đường cũ.
#
#     POST /auth/resident-link-requests
#     GET  /auth/resident-link-requests/me
#
# Đóng một đầu thôi thì không đủ: hồ sơ vẫn nộp được vào một hàng đợi không còn
# ai duyệt, và người dùng chờ một quyết định không bao giờ tới. Cả hai đầu —
# chỗ nộp và chỗ duyệt — cùng đóng.
#
# Đường canonical: `POST /verification-records` (kèm ảnh chứng minh) và
# `GET /verification-records/my`. Xem ghi chú dài ở `admin_routes.py`.

users_router = APIRouter(prefix="/users", tags=["users"])

# Avatar + ảnh giấy tờ dùng chung một root; avatar con riêng để dễ dọn khi
# user thay ảnh (xóa avatar cũ nếu nằm trong thư mục avatar của họ).
AVATAR_ROOT = Path("./data/uploads/avatars")
_ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_AVATAR_BYTES = 5 * 1024 * 1024


@users_router.patch("/me", response_model=UserResponse, summary="Cập nhật hồ sơ cá nhân")
async def update_my_profile(
    full_name: str | None = Form(default=None, max_length=200),
    phone: str | None = Form(default=None, max_length=20),
    address: str | None = Form(default=None, max_length=255),
    date_of_birth: str | None = Form(default=None, description="YYYY-MM-DD"),
    gender: str | None = Form(default=None, max_length=10),
    cccd_last4: str | None = Form(default=None, min_length=4, max_length=4),
    avatar: UploadFile | None = File(default=None),
    user: dict = Depends(get_current_user),
    users: Any = Depends(get_user_repository),
) -> UserResponse:
    """Cập nhật thông tin tự khai. Multipart: fields optional + ảnh avatar.

    Avatar lưu vào `./data/uploads/avatars/{user_id}/`, filename `uuid4.jpg`.
    Không bao giờ dùng filename gốc của client (chống path traversal). Avatar
    cũ của chính user bị xoá khi thay cái mới.
    """
    avatar_url = None
    if avatar is not None and avatar.filename:
        data = await avatar.read()
        if avatar.content_type not in _ALLOWED_AVATAR_TYPES:
            raise HTTPException(status_code=422, detail="Avatar phải là ảnh JPEG, PNG hoặc WEBP.")
        if len(data) > _MAX_AVATAR_BYTES:
            raise HTTPException(status_code=422, detail="Avatar vượt quá 5MB.")
        if data:
            avatar_url = _save_avatar(user["id"], avatar.content_type, data)

    updated = await users.update_profile(
        user["id"],
        full_name=_clean(full_name),
        phone=_clean(phone),
        address=_clean(address),
        date_of_birth=_clean(date_of_birth),
        gender=_clean(gender),
        cccd_last4=_clean(cccd_last4),
        avatar_url=avatar_url,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Tài khoản không tồn tại.")
    return _to_user_response(updated)


def _save_avatar(user_id: str, content_type: str, data: bytes) -> str:
    """Lưu avatar của user; xoá avatar cũ của họ; trả URL công khai."""
    import re

    safe_user = re.sub(r"[^0-9a-fA-F-]", "", str(user_id)) or "anon"
    avatar_dir = AVATAR_ROOT / safe_user
    avatar_dir.mkdir(parents=True, exist_ok=True)

    # Xoá ảnh cũ (nếu có) để không tích tụ file khi user đổi avatar nhiều lần.
    for old in avatar_dir.iterdir():
        old.unlink(missing_ok=True)

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
    filename = f"{uuid4().hex}{ext}"
    (avatar_dir / filename).write_bytes(data)
    return f"/uploads/avatars/{safe_user}/{filename}"
