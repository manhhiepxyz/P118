"""Nhiều tin nhắn thuộc CÙNG một cuộc trò chuyện.

Trước đây `/start` sinh `session_id` mới ở mỗi lần gọi, nên mỗi câu người dùng
gõ là một cuộc riêng: không hỏi tiếp được, và Lịch sử thành nhật ký từng tin
nhắn. Đo trên dữ liệu thật: 201 workflow gốc, không session nào quá 2 workflow.
"""

from __future__ import annotations

import pytest

from tests.test_db.conftest import _register_and_login


async def _start(client, token, goal, session_id=None):
    body = {"goal": goal}
    if session_id:
        body["session_id"] = session_id
    return await client.post("/api/v1/workflows/demo/start", headers={"Authorization": f"Bearer {token}"}, json=body)


@pytest.mark.asyncio
async def test_two_messages_can_share_one_conversation(client):
    token = await _register_and_login(client, "nn_sess_share")
    first = (await _start(client, token, "xin chào")).json()
    second = (await _start(client, token, "bạn làm được gì", session_id=first["session_id"])).json()
    assert second["session_id"] == first["session_id"]


@pytest.mark.asyncio
async def test_no_session_means_a_new_conversation(client):
    token = await _register_and_login(client, "nn_sess_new")
    first = (await _start(client, token, "xin chào")).json()
    second = (await _start(client, token, "xin chào")).json()
    assert second["session_id"] != first["session_id"]


@pytest.mark.asyncio
async def test_someone_elses_session_is_never_joined(client):
    """Chốt quan trọng nhất: `session_id` là thứ client biết và gửi lại được.

    Nối được vào cuộc của người khác nghĩa là đọc được ngữ cảnh của họ. Phép
    chặn nằm trong SQL (`_load_session` lọc theo `user_id`), không ở tầng trên.
    """
    token_a = await _register_and_login(client, "nn_sess_a")
    token_b = await _register_and_login(client, "nn_sess_b")
    cua_a = (await _start(client, token_a, "xin chào")).json()["session_id"]

    cua_b = (await _start(client, token_b, "xin chào", session_id=cua_a)).json()["session_id"]

    assert cua_b != cua_a, "người B nối được vào cuộc trò chuyện của người A"


@pytest.mark.asyncio
async def test_a_made_up_session_falls_back_quietly(client):
    """Không báo lỗi: người dùng không sửa được gì, và câu họ gõ vẫn hợp lệ."""
    token = await _register_and_login(client, "nn_sess_bia")
    res = await _start(client, token, "xin chào", session_id="00000000-0000-0000-0000-000000000000")
    assert res.status_code in {200, 202}, res.text
    assert res.json()["session_id"] != "00000000-0000-0000-0000-000000000000"
