"""
tests/test_db/test_user_repository.py
P-118 — Integration test: UserRepository (auth)

Chạy thật với PostgreSQL test DB (cần TEST_DATABASE_URL; skip local nếu thiếu,
FAIL trong CI — xem tests/_dbcheck.py). Dùng `db_pool` session fixture +
clean_tables TRUNCATE sau mỗi test.
"""

from __future__ import annotations

import asyncpg
import pytest

from src.api.auth import hash_password, verify_password
from src.db import UserAlreadyExistsError, UserRepository


def make_user_repo(pool: asyncpg.Pool) -> UserRepository:
    return UserRepository(pool)


@pytest.mark.asyncio
async def test_create_user_strips_password_hash(db_pool):
    """create_user trả row KHÔNG kèm password_hash (không lộ qua API)."""
    repo = make_user_repo(db_pool)
    user = await repo.create_user("nguyen.van.a", hash_password("matkhau123"))

    assert user["role"] == "customer"
    assert user["username"] == "nguyen.van.a"
    assert "password_hash" not in user
    assert user["id"]


@pytest.mark.asyncio
async def test_get_user_by_username_includes_hash(db_pool):
    """get_user_by_username trả password_hash — cần cho login verify."""
    repo = make_user_repo(db_pool)
    password_hash = hash_password("matkhau123")
    await repo.create_user("nguyen.van.a", password_hash)

    user = await repo.get_user_by_username("nguyen.van.a")
    assert user is not None
    assert user["password_hash"] == password_hash


@pytest.mark.asyncio
async def test_get_user_by_id_roundtrip(db_pool):
    repo = make_user_repo(db_pool)
    created = await repo.create_user("user.b", hash_password("matkhau123"), role="customer")
    fetched = await repo.get_user_by_id(created["id"])
    assert fetched is not None
    assert fetched["username"] == "user.b"
    assert fetched["role"] == "customer"


@pytest.mark.asyncio
async def test_default_role_is_customer(db_pool):
    repo = make_user_repo(db_pool)
    user = await repo.create_user("user.c", hash_password("matkhau123"))
    assert user["role"] == "customer"


@pytest.mark.asyncio
async def test_duplicate_username_raises(db_pool):
    repo = make_user_repo(db_pool)
    await repo.create_user("user.d", hash_password("matkhau123"))
    with pytest.raises(UserAlreadyExistsError):
        await repo.create_user("user.d", hash_password("matkhau123"))


@pytest.mark.asyncio
async def test_hash_verify_roundtrip_against_db(db_pool):
    """hash → store → đọc lại → verify đúng; mật khẩu sai → False."""
    repo = make_user_repo(db_pool)
    password_hash = hash_password("matkhau123")
    await repo.create_user("user.e", password_hash)

    user = await repo.get_user_by_username("user.e")
    assert verify_password("matkhau123", user["password_hash"]) is True
    assert verify_password("sai-mat-khau", user["password_hash"]) is False


@pytest.mark.asyncio
async def test_get_user_missing_returns_none(db_pool):
    repo = make_user_repo(db_pool)
    assert await repo.get_user_by_username("khong-ton-tai") is None
    assert await repo.get_user_by_id("00000000-0000-0000-0000-000000000000") is None
