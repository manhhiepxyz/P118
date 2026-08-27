"""
src/db/otp_repository.py
P-118 — OTP Repository
"""

import asyncpg
from datetime import datetime, timezone, timedelta

class CooldownError(ValueError):
    """Lỗi khi gửi OTP quá nhanh."""
    pass


class OtpRepository:
    """Repository for managing registration OTPs."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_otp(self, email: str, otp_code: str, expires_in_minutes: int = 5) -> None:
        """Lưu hoặc cập nhật mã OTP cho một email. Chặn spam nếu yêu cầu gửi quá nhanh (< 60s)."""
        email_clean = email.strip().lower()
        
        async with self._pool.acquire() as conn:
            # Check cooldown
            row = await conn.fetchrow("SELECT created_at FROM registration_otps WHERE email = $1", email_clean)
            if row and row["created_at"]:
                elapsed = (datetime.now(timezone.utc) - row["created_at"]).total_seconds()
                if elapsed < 60:
                    raise CooldownError(f"Đã đạt giới hạn gửi mã OTP. Vui lòng dùng tiếp được sau {60 - int(elapsed)} giây.")

            expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
            query = """
                INSERT INTO registration_otps (email, otp_code, expires_at, created_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (email) DO UPDATE 
                SET otp_code = EXCLUDED.otp_code,
                    expires_at = EXCLUDED.expires_at,
                    created_at = NOW()
            """
            await conn.execute(query, email_clean, otp_code, expires_at)

    async def verify_otp(self, email: str, otp_code: str) -> bool:
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
                  AND otp_code = $2 
                  AND expires_at > NOW()
                RETURNING email
            )
            SELECT count(*) FROM deleted
        """
        async with self._pool.acquire() as conn:
            # Dọn dẹp OTP hết hạn (có thể chạy ngầm, nhưng ghép ở đây luôn cho tiện)
            await conn.execute("DELETE FROM registration_otps WHERE expires_at <= NOW()")
            
            result = await conn.fetchval(query, email_clean, otp_code)
            return result == 1
