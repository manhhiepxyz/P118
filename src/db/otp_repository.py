"""
src/db/otp_repository.py
P-118 — OTP Repository
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum

import asyncpg


class CooldownError(ValueError):
    """Lỗi khi gửi OTP quá nhanh."""

    pass


class OtpPurpose(StrEnum):
    """Mục đích là một phần danh tính của OTP; mã không dùng chéo luồng."""

    REGISTRATION = "registration"
    PASSWORD_RESET = "password_reset"


class OtpRepository:
    """Repository for managing registration OTPs."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_otp(
        self,
        email: str,
        otp_code: str,
        *,
        purpose: OtpPurpose,
        expires_in_minutes: int = 5,
    ) -> None:
        """Lưu hoặc cập nhật mã OTP cho một email. Chặn spam nếu yêu cầu gửi quá nhanh (< 60s)."""
        email_clean = email.strip().lower()

        async with self._pool.acquire() as conn:
            # Check cooldown
            row = await conn.fetchrow(
                "SELECT created_at FROM registration_otps WHERE email = $1 AND purpose = $2",
                email_clean,
                purpose.value,
            )
            if row and row["created_at"]:
                elapsed = (datetime.now(UTC) - row["created_at"]).total_seconds()
                if elapsed < 60:
                    raise CooldownError(
                        f"Đã đạt giới hạn gửi mã OTP. Vui lòng dùng tiếp được sau {60 - int(elapsed)} giây."
                    )

            expires_at = datetime.now(UTC) + timedelta(minutes=expires_in_minutes)
            query = """
                INSERT INTO registration_otps (email, purpose, otp_code, expires_at, created_at)
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (email, purpose) DO UPDATE
                SET otp_code = EXCLUDED.otp_code,
                    expires_at = EXCLUDED.expires_at,
                    created_at = NOW()
            """
            await conn.execute(query, email_clean, purpose.value, otp_code, expires_at)

    async def verify_otp(self, email: str, otp_code: str, *, purpose: OtpPurpose) -> bool:
        """
        Kiểm tra OTP có hợp lệ và còn hạn hay không.
        Nếu đúng, xóa bản ghi đó để tránh dùng lại (replay attack) và trả về True.
        Nếu sai hoặc hết hạn, trả về False.
        """
        email_clean = email.strip().lower()

        query = """
            WITH deleted AS (
                DELETE FROM registration_otps
                WHERE email = $1
                  AND purpose = $2
                  AND otp_code = $3
                  AND expires_at > NOW()
                RETURNING email
            )
            SELECT count(*) FROM deleted
        """
        async with self._pool.acquire() as conn:
            # Dọn dẹp OTP hết hạn (có thể chạy ngầm, nhưng ghép ở đây luôn cho tiện)
            await conn.execute("DELETE FROM registration_otps WHERE expires_at <= NOW()")

            result = await conn.fetchval(query, email_clean, purpose.value, otp_code)
            return result == 1
