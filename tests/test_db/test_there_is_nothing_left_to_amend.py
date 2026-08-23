"""Không có gì để sửa thì nói ngay, đừng lập kế hoạch 125 giây rồi trả về 0.

Owner: Thành Bảo (Decision layer)
File: tests/test_db/test_there_is_nothing_left_to_amend.py

ĐO ĐƯỢC trên `llm_usage` của stack demo, 86 lượt Planner thật:

    plan     trung vị 32,98s   p90 78,28s   tổng 3390s   ← 89% thời gian gọi model
    respond  trung vị  1,55s   p90  1,93s   tổng  425s

27% thời gian Planner (903s / 33 lượt) sinh ra KHÔNG TASK NÀO. Bới ra thì phần
lớn là một vòng lặp: người dùng gõ câu sửa, phiên chưa từng đặt được gì để sửa,
Planner chạy nửa phút rồi trả về rỗng — và họ gõ lại. Nguyên văn một phiên, ba
lượt liên tiếp cách nhau 15 giây, cả ba đều 0 task:

    13:31:34  "Đặt lịch tham quan Vinhomes Hải Vân Bay ngày…"   CANCELLED
    13:31:49  "đổi chỗ đỗ xe q"                                 CANCELLED
    13:32:04  "đổi chỗ đỗ xe qua khu B"          125,0 GIÂY  →  CANCELLED

Kiểm 19 lượt sửa/huỷ bị lãng phí: 18 lượt trong phiên KHÔNG có yêu cầu nào còn
sửa được. Lane sửa không hỏng — nó tìm đúng và không thấy gì. Cái sai là mất
30–125 giây để đi tới kết luận đó, trong khi `wants_to_amend` trả lời trong vài
mili-giây và bảng workflow trả lời trong một truy vấn.

VÌ SAO PHẢI CÓ HÀNG RÀO `_has_service_intent`: "đặt lịch tham quan Vinhomes
Pearl Bay ngày 2026-09-02" mất 96 giây và cũng ra 0 task, nhưng nó là yêu cầu
MỚI — chặn nó lại là từ chối một người đang muốn đặt chỗ. Luật này chỉ được
chạm vào câu vừa nói tới việc SỬA vừa KHÔNG mang theo một dịch vụ để đặt.
Đối chiếu trên chính 33 lượt đã ghi: bắt 16 lượt (448s), thả đúng những lượt
đặt mới và lượt sửa có mục tiêu thật ("đăng ký lại vào khu B", 77,3s).
"""

from __future__ import annotations

import json
import uuid

import pytest

import src.api.routes as routes
from src.api.routes import _nothing_left_to_amend
from tests.test_db.conftest import _register_and_login


async def _a_user(pool) -> tuple[str, str]:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, password_hash) VALUES ($1,$2,'x')",
            user_id,
            f"nguoi-{user_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO sessions (session_id, user_id, account_state) VALUES ($1,$2,'resident')",
            str(session_id),
            user_id,
        )
    return str(user_id), str(session_id)


async def _seed(pool, *, session_id, owner_user_id, status="CANCELLED", with_task=True) -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, session_id, owner_user_id) "
            "VALUES ($1,'đặt lịch tham quan',$2,$3,$4)",
            wid,
            status,
            session_id,
            uuid.UUID(owner_user_id),
        )
        if with_task:
            await conn.execute(
                "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
                "VALUES ($1,'T1','book_viewing',$2,$3::jsonb)",
                wid,
                status,
                json.dumps({"project_id": "PRJ-001", "viewing_date": "2026-08-29", "viewing_time": "09:30"}),
            )
    return str(wid)


@pytest.mark.asyncio
async def test_a_change_request_with_no_target_is_answered_at_once(client, db_pool):
    """Phiên chưa đặt được gì. "đổi qua khu B" không được đánh thức Planner."""
    user_id, session_id = await _a_user(db_pool)
    user = {"id": user_id}
    assert await _nothing_left_to_amend("đổi qua khu B", session_id=session_id, user=user) is True


@pytest.mark.asyncio
async def test_a_stopped_request_that_can_still_be_fixed_is_left_alone(client, db_pool):
    """CÓ mục tiêu sửa thật thì luật này phải im — lane sửa mới là nơi xử lý.

    Đây là hàng rào quan trọng nhất: nếu luật bắt cả trường hợp này thì người
    dùng vừa bấm Dừng, gõ "đổi sang ngày 30", và bị trả lời "không có gì để
    sửa" — trong khi yêu cầu của họ đang nằm đó, sửa được.
    """
    user_id, session_id = await _a_user(db_pool)
    await _seed(db_pool, session_id=session_id, owner_user_id=user_id)
    user = {"id": user_id}
    assert await _nothing_left_to_amend("đổi sang ngày 30", session_id=session_id, user=user) is False


@pytest.mark.asyncio
async def test_a_stopped_request_with_no_plan_is_not_a_target(client, db_pool):
    """Yêu cầu đã dừng nhưng CHƯA có bước nào thì không có ô nào để sửa.

    Đúng ba workflow trong phiên đo được ở đầu file: `task_plan` khác NULL
    nhưng không có bước nào. Nếu chỉ xét trạng thái thì chúng trông như mục
    tiêu sửa hợp lệ, và mỗi lần người dùng thử lại, lượt hỏng vừa rồi lại chen
    lên đứng trước — lần sau còn chậm hơn lần trước.
    """
    user_id, session_id = await _a_user(db_pool)
    await _seed(db_pool, session_id=session_id, owner_user_id=user_id, with_task=False)
    user = {"id": user_id}
    assert await _nothing_left_to_amend("đổi qua khu B", session_id=session_id, user=user) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "goal",
    [
        # Yêu cầu MỚI. 96 giây và 0 task, nhưng chặn nó là từ chối người muốn đặt.
        "Đặt lịch tham quan Vinhomes Pearl Bay ngày 2026-09-02 lúc 12:30",
        "đặt lịch tham quan dự án và chỗ đỗ xe cho tôi",
        "đăng ký quan tâm nhận tư vấn Vinhomes Hải Vân Bay",
        "hãy gọi cho tôi lúc 10:00",
        # Hai câu này là lý do `mentions_a_service` tồn tại: chúng nói "lại"
        # nên `wants_to_amend` bắt, NHƯNG chúng mang theo một dịch vụ để đặt.
        # Trong một phiên trống, "đăng ký lại chỗ đỗ xe khu B" là một yêu cầu
        # đặt chỗ hoàn toàn bình thường — trả lời "không có gì để sửa" là từ
        # chối một người đang muốn dùng dịch vụ.
        #
        # Không có hai ca này thì bỏ hẳn hàng rào ấy đi mọi test vẫn xanh — đã
        # thử, và đó là lý do chúng có mặt.
        "đăng ký lại vào khu B",
        "tôi muốn đăng ký lại chỗ đỗ xe khu B",
    ],
)
async def test_a_brand_new_request_is_never_short_circuited(client, db_pool, goal: str):
    """Phiên trống, nhưng đây là yêu cầu mới — phải xuống Planner."""
    user_id, session_id = await _a_user(db_pool)
    user = {"id": user_id}
    assert await _nothing_left_to_amend(goal, session_id=session_id, user=user) is False


@pytest.mark.asyncio
async def test_the_finished_request_of_another_session_is_not_borrowed(client, db_pool):
    """Mục tiêu sửa bó trong CHÍNH phiên. Không mượn của phiên khác."""
    user_id, session_id = await _a_user(db_pool)
    _, phien_khac = await _a_user(db_pool)
    await _seed(db_pool, session_id=phien_khac, owner_user_id=user_id)
    user = {"id": user_id}
    assert await _nothing_left_to_amend("đổi sang ngày 30", session_id=session_id, user=user) is True


@pytest.mark.asyncio
async def test_the_start_route_answers_without_ever_planning(client, db_pool, monkeypatch):
    """Tầng route, không phải hàm rời: Planner KHÔNG được chạy, và không tạo bước nào.

    `_run_demo_job` là đường duy nhất dẫn tới Planner từ `/start`. Thay nó bằng
    một hàm nổ: nếu nhánh mới lỡ rơi xuống lane cũ thì test hỏng ngay ở đây,
    chứ không im lặng tốn thêm 30 giây trên máy người dùng.
    """
    token = await _register_and_login(client, "khong_con_gi_de_sua")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khong_con_gi_de_sua'")
    session_id = str(uuid.uuid4())
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (session_id, user_id, account_state) VALUES ($1,$2,'resident')",
            session_id,
            owner,
        )

    async def _khong_duoc_lap_ke_hoach(*_args, **_kwargs):
        raise AssertionError("Planner bị đánh thức cho một câu không có gì để sửa")

    monkeypatch.setattr(routes, "_run_demo_job", _khong_duoc_lap_ke_hoach)

    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "đổi qua khu B", "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["status"] == "CHAT"
    assert "chưa thấy yêu cầu nào" in (payload.get("answer") or "")
    assert await db_pool.fetchval(
        "SELECT COUNT(*) FROM workflow_tasks WHERE workflow_id=$1::uuid", payload["workflow_id"]
    ) == 0


@pytest.mark.asyncio
async def test_a_finished_booking_is_not_the_same_as_nothing_to_amend(client, db_pool):
    """HỒI QUY THẬT, do chính luật này gây ra. Người dùng đang nhìn lịch đã đặt.

    Nguyên văn, workflow 208d9ada trên stack demo:

        Bạn:    tôi muốn đổi lịch
        P-118:  Mình chưa thấy yêu cầu nào đang dừng để sửa trong cuộc trò
                chuyện này.

    Họ vừa đặt xong lịch tham quan và đang chờ trả 150.000đ cho chỗ đỗ xe. Yêu
    cầu nằm ngay đó. Câu trả lời ấy nói dối họ.

    Nguyên nhân: `_amend_target` chỉ nhận CANCELLED/FAILED/WAITING_APPROVAL —
    đúng cho việc SỬA (một đơn đã hoàn tất là cam kết thật, không đè lên được).
    Nhưng "không sửa được" KHÁC "không có gì để sửa", và luật này đã trộn hai
    thứ đó làm một.

    Trước khi có luật, câu này rơi xuống Planner: chậm và vô dụng. Sau khi có:
    nhanh và SAI. Nhanh mà sai thì tệ hơn — người dùng tin câu trả lời và đi
    tìm lịch của họ ở chỗ khác.

    Nên luật chỉ được nói "không có gì để sửa" khi trong phiên KHÔNG có yêu cầu
    nào cả. Có mà không sửa được là một câu trả lời khác, và không phải việc
    của hàm này.
    """
    user_id, session_id = await _a_user(db_pool)
    await _seed(db_pool, session_id=session_id, owner_user_id=user_id, status="SUCCESS")
    user = {"id": user_id}
    assert await _nothing_left_to_amend("tôi muốn đổi lịch", session_id=session_id, user=user) is False
    assert await _nothing_left_to_amend("đổi qua ngày 24", session_id=session_id, user=user) is False


@pytest.mark.asyncio
async def test_a_database_failure_is_never_reported_as_nothing_to_amend(client, db_pool, monkeypatch):
    """Đọc hỏng thì rơi về Planner — KHÔNG được biến thành lời khẳng định.

    Hàng rào "phiên này đã có yêu cầu chưa" hỏi database. Khi câu hỏi ấy không
    trả lời được, có hai hướng sai lệch nhau hoàn toàn:

        coi như phiên TRỐNG   → nói "không có gì để sửa"  → nói dối, nhanh
        coi như phiên CÓ      → xuống Planner              → chậm, đúng

    Chọn nhầm hướng thì một sự cố database biến thành một câu khẳng định sai
    gửi thẳng cho người dùng — đúng lỗi vừa xảy ra ở workflow 208d9ada, chỉ
    khác nguyên nhân. Mutation đổi `return True` thành `return False` ở nhánh
    lỗi KHÔNG bị test nào bắt trước khi có ca này.
    """
    user_id, session_id = await _a_user(db_pool)

    async def _hong(*_args, **_kwargs):
        raise RuntimeError("database không đọc được")

    monkeypatch.setattr(routes, "acquire_repository", _hong)
    user = {"id": user_id}
    assert await _nothing_left_to_amend("đổi qua khu B", session_id=session_id, user=user) is False
