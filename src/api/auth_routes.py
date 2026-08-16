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

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.api.auth import create_access_token, hash_password, verify_password
from src.api.deps import get_current_user, get_user_repository
from src.api.schemas import (
    LinkRequestCreate,
    LinkRequestView,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from src.config import get_settings
from src.db.link_request_repository import (
    LinkRequestConflictError,
    create_request,
    latest_request_for_user,
)
from src.db.resident_link_repository import get_link_status, get_verified_identity
from src.db.user_repository import UserAlreadyExistsError
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


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    req: RegisterRequest,
    users: Any = Depends(get_user_repository),
) -> UserResponse:
    """Đăng ký tài khoản mới — luôn tạo role='customer'. Không trả token.

    KHÔNG tạo liên kết cư dân. Role cũ tên 'resident' khiến đăng ký xong trông
    như đã là cư dân, và mọi chỗ kiểm "role == resident" để mở dịch vụ cư dân
    đều mở cho tài khoản vừa tạo xong.

    Trả 409 nếu username đã tồn tại (đồng bộ với nút trùng trong DB).
    """
    username = req.username.strip().lower()
    password_hash = hash_password(req.password)
    email = req.email.strip().lower() if req.email and req.email.strip() else None

    try:
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


@router.post(
    "/resident-link-requests",
    status_code=201,
    response_model=LinkRequestView,
    summary="Gửi yêu cầu liên kết căn hộ",
)
async def create_resident_link_request(
    request: LinkRequestCreate,
    user: dict = Depends(get_current_user),
) -> LinkRequestView:
    """Khách hàng KHAI căn hộ của mình. Yêu cầu luôn bắt đầu ở PENDING.

    Không có tham số nào cho trạng thái: quyền chỉ mở ở đường duyệt của admin.
    Đây là ranh giới quan trọng nhất của endpoint này — nếu người dùng tự khẳng
    định được mình sở hữu một căn hộ thì toàn bộ mô hình quyền cư dân chỉ còn
    là một biểu mẫu.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        created = await create_request(
            pool,
            user["id"],
            apartment_code=request.apartment_code.strip(),
            residential_area=request.residential_area.strip(),
            full_name=request.full_name.strip(),
        )
    except LinkRequestConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    finally:
        await pool.close()

    return LinkRequestView(
        request_id=created.request_id,
        apartment_code=created.apartment_code,
        residential_area=created.residential_area,
        status=created.status,
        created_at=created.created_at.isoformat() if created.created_at else None,
    )


@router.get(
    "/resident-link-requests/me",
    response_model=LinkRequestView | None,
    summary="Trạng thái yêu cầu liên kết căn hộ của chính mình",
)
async def my_resident_link_request(user: dict = Depends(get_current_user)) -> LinkRequestView | None:
    """Chỉ trả yêu cầu của CHÍNH tài khoản đang đăng nhập.

    Không nhận `user_id` trên đường dẫn hay query: nhận nghĩa là mở một endpoint
    đọc hồ sơ người khác, và mọi cách chặn sau đó chỉ là vá.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        found = await latest_request_for_user(pool, user["id"])
    finally:
        await pool.close()

    if found is None:
        return None
    return LinkRequestView(
        request_id=found.request_id,
        apartment_code=found.apartment_code,
        residential_area=found.residential_area,
        status=found.status,
        created_at=found.created_at.isoformat() if found.created_at else None,
        decided_at=found.decided_at.isoformat() if found.decided_at else None,
    )


# ===========================================================================
# /users — profile tự khai (Phase D). Router riêng vì PATCH /users/me nằm
# NGOÀI prefix /auth (UI gọi thẳng /api/v1/users/me).
# ===========================================================================

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
