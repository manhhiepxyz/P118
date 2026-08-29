"""Trust boundary cho Google login và reset-password."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api import auth_routes
from src.api.schemas import ResetPasswordRequest


def test_google_email_must_be_verified(monkeypatch):
    monkeypatch.setattr(auth_routes, "get_settings", lambda: SimpleNamespace(google_client_id="client-id"))
    monkeypatch.setattr(
        auth_routes.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {"email": "user@example.com", "email_verified": False},
    )

    with pytest.raises(HTTPException) as exc:
        auth_routes._verify_google_token("credential")

    assert exc.value.status_code == 401


def test_google_token_without_email_verified_claim_is_rejected(monkeypatch):
    monkeypatch.setattr(auth_routes, "get_settings", lambda: SimpleNamespace(google_client_id="client-id"))
    monkeypatch.setattr(
        auth_routes.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {"email": "user@example.com"},
    )

    with pytest.raises(HTTPException) as exc:
        auth_routes._verify_google_token("credential")

    assert exc.value.status_code == 401


def test_reset_password_uses_the_registration_password_floor():
    with pytest.raises(ValidationError):
        ResetPasswordRequest(email="user@example.com", otp_code="123456", new_password="short")

    valid = ResetPasswordRequest(email="user@example.com", otp_code="123456", new_password="matkhau123")
    assert valid.new_password == "matkhau123"


def test_vnpay_configuration_is_not_duplicated_or_removed_by_auth_integration():
    env_example = open(".env.example", encoding="utf-8").read()

    assert env_example.count("PAYMENT_PROVIDER=mock") == 1
    assert env_example.count("VNPAY_TMN_CODE=") == 1
    assert env_example.count("VNPAY_HASH_SECRET=") == 1
    assert "PUBLIC_BASE_URL=" in env_example
    assert "RESEND_FROM_EMAIL=no-reply@account.c3-app-118.io.vn" in env_example
