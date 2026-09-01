"""OTP đăng ký và OTP đặt lại mật khẩu không được dùng chéo."""

import pytest

from src.db.otp_repository import OtpPurpose, OtpRepository
from tests._otp_registration import MAT_KHAU, dang_ky_qua_duong_that, email_cua


@pytest.mark.asyncio
async def test_registration_code_cannot_reset_a_password(db_pool):
    repo = OtpRepository(db_pool)
    await repo.save_otp("user@example.com", "111111", purpose=OtpPurpose.REGISTRATION)

    assert await repo.verify_otp("user@example.com", "111111", purpose=OtpPurpose.PASSWORD_RESET) is False
    assert await repo.verify_otp("user@example.com", "111111", purpose=OtpPurpose.REGISTRATION) is True


@pytest.mark.asyncio
async def test_reset_code_does_not_replace_a_registration_code(db_pool):
    repo = OtpRepository(db_pool)
    await repo.save_otp("user@example.com", "111111", purpose=OtpPurpose.REGISTRATION)
    await repo.save_otp("user@example.com", "222222", purpose=OtpPurpose.PASSWORD_RESET)

    assert await repo.verify_otp("user@example.com", "111111", purpose=OtpPurpose.REGISTRATION) is True
    assert await repo.verify_otp("user@example.com", "222222", purpose=OtpPurpose.PASSWORD_RESET) is True


@pytest.mark.asyncio
async def test_password_reset_changes_the_password_through_the_real_routes(client, hop_thu_otp):
    username = "reset_password_customer"
    email = email_cua(username)
    await dang_ky_qua_duong_that(client, username)

    requested = await client.post("/api/v1/auth/forgot-password", json={"email": email})
    code = hop_thu_otp.ma_moi_nhat(email)
    reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": email, "otp_code": code, "new_password": "matkhau-moi-123"},
    )

    assert requested.status_code == 200
    assert reset.status_code == 200
    assert (
        await client.post("/api/v1/auth/login", json={"username": username, "password": MAT_KHAU})
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "matkhau-moi-123"},
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_forgot_password_does_not_reveal_account_or_cooldown(client, hop_thu_otp):
    username = "reset_privacy_customer"
    email = email_cua(username)
    await dang_ky_qua_duong_that(client, username)

    existing_first = await client.post("/api/v1/auth/forgot-password", json={"email": email})
    existing_again = await client.post("/api/v1/auth/forgot-password", json={"email": email})
    unknown = await client.post("/api/v1/auth/forgot-password", json={"email": "missing@example.com"})

    assert existing_first.status_code == existing_again.status_code == unknown.status_code == 200
    assert existing_first.json() == existing_again.json() == unknown.json()
