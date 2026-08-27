"""Chỉ `SELECTED` mới sinh ra đề xuất. Năm kết quả còn lại thì không.

Bước C trả về sáu kết quả có kiểu; đúng MỘT là "đã chọn được". Năm cái còn lại
là câu hỏi hoặc lời từ chối, và chúng KHÔNG được ghim gì.

Nghe hiển nhiên, nhưng đây chính là chỗ hiển nhiên hay hỏng. Điều kiện tự nhiên
nhất để viết là `if ket_qua.bao_gia is not None` — và nó SAI: `OVER_BUDGET`
cũng mang theo một báo giá, của đơn vị khách chỉ định, để nói ra nó đắt bao
nhiêu. Ghim theo điều kiện ấy nghĩa là một lời từ chối vì vượt ngân sách trở
thành một đề xuất mời khách bấm đồng ý, với đúng con số vừa bị nói là quá đắt.

Đây cũng là ranh giới đọc/ghi của cả tính năng: bước C không ghi gì, và lượt
ghim ở đây là lượt ghi duy nhất của đường đề xuất.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.db.proposal_repository import de_xuat_dang_cho
from src.db.quote_repository import luu_bao_gia
from src.orchestration.proposal_service import de_xuat_don_vi_cho_buoc
from src.orchestration.quote import van_tay_yeu_cau

DICH_VU = "schedule_move"
YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}
VAN_TAY = van_tay_yeu_cau(YEU_CAU)


async def _workflow(db_pool) -> str:
    wid = str(uuid.uuid4())
    uid = uuid.uuid4()
    await db_pool.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES ($1::uuid, $2, 'x', 'customer')",
        uid,
        f"kh_{uid.hex[:8]}",
    )
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'chuyển nhà', 'PENDING', $2::uuid)",
        wid,
        uid,
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
        "VALUES ($1::uuid, 'T1', 'schedule_move', 'PENDING', '[]'::jsonb)",
        wid,
    )
    return wid


async def _bao_gia(db_pool, wid, don_vi, gia):
    return await luu_bao_gia(
        db_pool,
        external_quote_id=f"Q-{uuid.uuid4().hex[:10]}",
        service_provider_id=don_vi,
        service_type=DICH_VU,
        amount=gia,
        currency="VND",
        request_fingerprint=VAN_TAY,
        valid_until=datetime.now(UTC) + timedelta(minutes=30),
        workflow_id=wid,
        task_id="T1",
    )


async def _de_xuat(db_pool, wid, **kw):
    return await de_xuat_don_vi_cho_buoc(
        db_pool,
        workflow_id=wid,
        task_id="T1",
        service_type=DICH_VU,
        request_fingerprint=VAN_TAY,
        **kw,
    )


@pytest.mark.asyncio
async def test_a_real_choice_becomes_a_proposal(db_pool):
    """Kiểm DƯƠNG. Thiếu nó thì mọi khẳng định "không ghim" bên dưới có thể
    đúng chỉ vì hàm hỏng."""
    wid = await _workflow(db_pool)
    re_nhat = await _bao_gia(db_pool, wid, "MOV-03", 420_000)
    await _bao_gia(db_pool, wid, "MOV-02", 470_000)

    lua_chon, de_xuat = await _de_xuat(db_pool, wid)

    assert lua_chon.ket_qua == "SELECTED"
    assert de_xuat is not None
    assert de_xuat.quote_id == re_nhat.quote_id
    assert de_xuat.status == "PROPOSED"


@pytest.mark.asyncio
async def test_over_budget_is_a_refusal_not_a_proposal(db_pool):
    """Ca then chốt: `OVER_BUDGET` MANG một báo giá, và vẫn không được ghim.

    Khách nói "cho tôi Đại Tín, trong 450 nghìn". Đại Tín báo 470. Kết quả mang
    theo chứng từ ấy để tầng trên nói được "đắt hơn 20 nghìn" — chứ không phải
    để ai đó đem đi ghim.
    """
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-03", 420_000)
    await _bao_gia(db_pool, wid, "MOV-02", 470_000)

    lua_chon, de_xuat = await _de_xuat(db_pool, wid, ten_don_vi_khach_noi="Đại Tín", max_price=450_000)

    assert lua_chon.ket_qua == "OVER_BUDGET"
    assert lua_chon.bao_gia is not None, "ca kiểm mất ý nghĩa nếu kết quả không mang báo giá"
    assert de_xuat is None, "một lời từ chối đã thành đề xuất"
    assert await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1") is None


@pytest.mark.asyncio
async def test_a_budget_nobody_fits_is_a_refusal_not_a_proposal(db_pool):
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-03", 420_000)

    lua_chon, de_xuat = await _de_xuat(db_pool, wid, max_price=100_000)

    assert (lua_chon.ket_qua, de_xuat) == ("OVER_BUDGET", None)
    assert await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1") is None


@pytest.mark.asyncio
async def test_an_unknown_name_is_a_question_not_a_proposal(db_pool):
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-03", 420_000)

    lua_chon, de_xuat = await _de_xuat(db_pool, wid, ten_don_vi_khach_noi="chuyển nhà")

    assert (lua_chon.ket_qua, de_xuat) == ("UNKNOWN_PROVIDER", None)
    assert await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1") is None


@pytest.mark.asyncio
async def test_nothing_to_choose_from_is_not_a_proposal(db_pool):
    wid = await _workflow(db_pool)
    lua_chon, de_xuat = await _de_xuat(db_pool, wid)
    assert (lua_chon.ket_qua, de_xuat) == ("NO_AVAILABLE_QUOTE", None)


@pytest.mark.asyncio
async def test_an_unreadable_budget_is_not_a_proposal(db_pool):
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-03", 420_000)
    lua_chon, de_xuat = await _de_xuat(db_pool, wid, max_price=-1)
    assert (lua_chon.ket_qua, de_xuat) == ("INVALID_BUDGET", None)


@pytest.mark.asyncio
async def test_a_refusal_leaves_the_step_untouched(db_pool):
    """Không ghim thì cũng KHÔNG đổi trạng thái bước.

    Một lời từ chối đẩy bước sang `WAITING_APPROVAL` sẽ dựng màn hình chờ cho
    một việc không ai đang làm — và không có gì để bấm.
    """
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-03", 420_000)

    await _de_xuat(db_pool, wid, max_price=100_000)

    assert (
        await db_pool.fetchval(
            "SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", uuid.UUID(wid)
        )
        == "PENDING"
    )
    assert await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", uuid.UUID(wid)) == "PENDING"


@pytest.mark.asyncio
async def test_choosing_again_replaces_the_proposal_that_was_waiting(db_pool):
    """Đề xuất lần hai đẩy lần một sang SUPERSEDED — đúng một cái đang sống."""
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-03", 420_000)
    await _bao_gia(db_pool, wid, "MOV-02", 470_000)

    _, lan_dau = await _de_xuat(db_pool, wid)
    _, lan_hai = await _de_xuat(db_pool, wid, ten_don_vi_khach_noi="Đại Tín")

    assert lan_dau.proposal_id != lan_hai.proposal_id
    dang_cho = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")
    assert dang_cho.proposal_id == lan_hai.proposal_id
    assert (
        await db_pool.fetchval(
            "SELECT status FROM service_provider_proposals WHERE proposal_id=$1::uuid",
            uuid.UUID(lan_dau.proposal_id),
        )
        == "SUPERSEDED"
    )
