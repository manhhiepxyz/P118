"""
src/api/auth.py
P-118 — Auth primitives (stdlib only)

Owner: Hoàng Anh

Quyết định đã chốt với chủ sở hữu: KHÔNG thêm dependency (bcrypt/pyjwt
cần pip install). Dùng:
  - Password hash : hashlib.scrypt (memory-hard, salt random 16B mỗi user)
  - Access token  : JWT-shaped HS256 (header.payload.signature) tự dựng bằng
                    hmac + base64 — payload tương thích để sau này đổi sang
                    pyjwt nếu cần. Không refresh token; TTL 24h (demo).

Password_hash lưu ở cột `users.password_hash` dạng:
    scrypt:<n>:<r>:<p>:<salt_b64>:<hash_b64>
Tham số lưu kèm để sau này nâng độ khó không phá hash cũ.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException

from src.config import get_settings

# scrypt params — n=2**14 (~50–100ms/hash, chấp nhận được cho demo; để cao hơn
# sẽ làm register/login và test DB chậm rõ rệt).
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

_TOKEN_HEADER = {"alg": "HS256", "typ": "JWT"}


# ---------------------------------------------------------------------------
# Password hashing — hashlib.scrypt
# ---------------------------------------------------------------------------


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    """Hash mật khẩu bằng scrypt với salt random 16B; trả chuỗi tự mô tả."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}:{_b64url_encode(salt)}:{_b64url_encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Verify mật khẩu với chuỗi stored; False (không raise) nếu định dạng sai."""
    try:
        scheme, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split(":")
        if scheme != "scrypt":
            return False
        salt = _b64url_decode(salt_b64)
        expected = _b64url_decode(hash_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)  # constant-time
    except (ValueError, TypeError, AssertionError):
        return False


# ---------------------------------------------------------------------------
# Access token — JWT-shaped HMAC-SHA256
# ---------------------------------------------------------------------------


def _settings_secret() -> str:
    secret = get_settings().jwt_secret
    if not secret:
        raise HTTPException(status_code=500, detail="JWT_SECRET chưa được cấu hình.")
    return secret


def create_access_token(user: dict) -> str:
    """Tạo access token HS256 chứa sub/username/role + iat/exp (24h)."""
    settings = get_settings()
    secret = _settings_secret()
    now = int(time.time())
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "iat": now,
        "exp": now + settings.jwt_expire_minutes * 60,
    }
    header_b64 = _b64url_encode(json.dumps(_TOKEN_HEADER, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict:
    """Giải mã + xác thực chữ ký token; trả payload. 401 nếu không hợp lệ/hết hạn."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        secret = get_settings().jwt_secret
        expected = hmac.new(secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_decode(signature_b64), expected):
            raise HTTPException(status_code=401, detail="Token không hợp lệ.")
        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            raise HTTPException(status_code=401, detail="Token đã hết hạn.")
        return payload
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Token không hợp lệ.") from None
