"""Bất biến mà harness E2E dựa vào để dừng TRƯỚC Planner.

Nếu một tài khoản chưa liên kết trông giống tài khoản đã xác minh, thì một bước
seed hỏng sẽ không phân biệt được với setup đúng — và lỗi lại bị quy cho model.
"""

from __future__ import annotations

import pytest

from tests.test_db.conftest import _register_and_login


@pytest.mark.asyncio
async def test_a_user_without_a_verified_link_never_looks_like_a_resident(client, db_pool):
    """Bất biến mà harness dựa vào để dừng TRƯỚC Planner.

    Nếu điều này sai, một seed hỏng sẽ trông giống hệt một tài khoản hợp lệ và
    lỗi lại bị quy cho model.
    """
    token = await _register_and_login(client, "nn_harness_chua_lienket")

    me = (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})).json()
    caps = (await client.get("/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"})).json()

    assert me["resident_verification_status"] == "NOT_LINKED"
    assert me["apartment_code"] is None
    assert not any(c["available"] for c in caps["capabilities"] if c["requires_resident"])
