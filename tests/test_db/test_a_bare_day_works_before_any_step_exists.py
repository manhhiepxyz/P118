""" "ngày 28" phải hiểu được KỂ CẢ khi chưa có bước nào để neo vào.

Owner: Thành Bảo (Decision layer)
File: tests/test_db/test_a_bare_day_works_before_any_step_exists.py

NGUYÊN VĂN, workflow aa53d5aa trên stack demo:

    P-118:  Ngày chuyển nhà chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.
    Bạn:    ngày 28
    P-118:  Ngày chuyển nhà chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.
    Bạn:    ngày 28/8/2026
    P-118:  Mình đã ghi nhận, để mình xử lý tiếp nhé.

Người dùng phải gõ lại cùng một ngày ở dạng đầy đủ. Mỗi lượt như thế tốn một
lần lập kế hoạch — đo được `plan 65,49s` cho chính workflow này. Hai lượt thừa
là hơn một phút chờ, và người dùng đã bỏ đi giữa chừng.

VÌ SAO NEO CŨ KHÔNG CHẠM TỚI

`/continue` đọc ngày để neo từ `workflow_tasks` — cùng nguồn mà biểu mẫu sửa
đọc. Nhưng ở đây Planner trả `NEEDS_INFORMATION` nên workflow CHƯA CÓ BƯỚC NÀO
(0 task). Không có bước thì không có neo, và "ngày 28" không đủ để dựng một
ngày đầy đủ.

VÌ SAO NEO VÀO HÔM NAY LÀ ĐÚNG Ở ĐÂY, DÙ `test_a_short_date_is_anchored_to_the_old_one`
ĐÃ CHỌN NGƯỢC LẠI

File ấy giải thích rõ: neo vào hôm nay sẽ kéo một lịch đặt cho tháng sau ngược
về tháng này, và với một lịch xa thì đó là ngày quá khứ. Đúng — KHI CÓ giá trị
cũ. Luật này chỉ chạy khi KHÔNG có giá trị nào, nên nó không đụng vào ca ấy.

Và ở đúng tình huống này, hôm nay chính là khung quy chiếu mà hệ thống vừa nêu
ra: câu hỏi là "hãy chọn một ngày TỪ HÔM NAY trở đi".

`rewrite_relative_dates` không bao giờ sinh ngày quá khứ khi neo vào hôm nay —
"ngày 20" với hôm nay 24/08 cho 2026-09-20, không phải 2026-08-20.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest

from tests.test_db.conftest import _register_and_login

# Ngày đã QUA trong tháng này, để chứng minh luật cuộn sang tháng sau. Dùng
# ngày mai thì test xanh cả khi luật sai.
NGAY_MAI = date.today() + timedelta(days=1)


async def _cho_ngay_chuyen_nha(pool, owner) -> str:
    """Workflow Planner đã trả NEEDS_INFORMATION: có clarification, KHÔNG có bước nào."""
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) VALUES ($1,$2,'PENDING',$3)",
            wid,
            "Đặt lịch chuyển nhà lúc 08:30 phương tiện Xe van cần thang máy",
            owner,
        )
        await conn.execute(
            "INSERT INTO workflow_clarifications (workflow_id, goal, missing_fields, resolved_at) "
            "VALUES ($1,$2,$3::jsonb,NULL)",
            wid,
            "Đặt lịch chuyển nhà lúc 08:30 phương tiện Xe van cần thang máy",
            json.dumps(["move_date"]),
        )
    return str(wid)


@pytest.mark.asyncio
async def test_a_bare_day_is_understood_with_no_task_to_anchor_on(client, db_pool, monkeypatch):
    """Đây là lượt thừa đã khiến người dùng chờ thêm 65 giây rồi bỏ đi."""
    from src.api import routes

    chay: list[dict] = []

    async def _ghi_lai(workflow_id, goal, *_a, **_kw):
        # Câu trả lời đã phân tích nằm trong ngữ cảnh của job, không phải tham số.
        chay.append(dict((routes._DEMO_JOBS.get(workflow_id) or {}).get("existing_context") or {}))

    monkeypatch.setattr(routes, "_run_demo_job", _ghi_lai)

    token = await _register_and_login(client, "ngay_tat_khong_co_buoc")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='ngay_tat_khong_co_buoc'")
    wid = await _cho_ngay_chuyen_nha(db_pool, owner)

    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"message": f"ngày {NGAY_MAI.day}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 202, res.text
    assert chay, "câu trả lời không được nhận — người dùng phải gõ lại ngày đầy đủ"
    assert chay[0].get("move_date") == NGAY_MAI.isoformat(), chay


@pytest.mark.asyncio
async def test_a_day_already_past_rolls_forward_never_backward(client, db_pool, monkeypatch):
    """Neo vào hôm nay KHÔNG được sinh ra một ngày trong quá khứ.

    Đó là điều `TaskPlanValidator` sẽ từ chối, và người dùng lại nhận đúng câu
    lỗi họ vừa cố sửa.
    """
    from src.api import routes

    chay: list[dict] = []

    async def _ghi_lai(workflow_id, goal, *_a, **_kw):
        # Câu trả lời đã phân tích nằm trong ngữ cảnh của job, không phải tham số.
        chay.append(dict((routes._DEMO_JOBS.get(workflow_id) or {}).get("existing_context") or {}))

    monkeypatch.setattr(routes, "_run_demo_job", _ghi_lai)

    token = await _register_and_login(client, "ngay_da_qua_cuon_toi")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='ngay_da_qua_cuon_toi'")
    wid = await _cho_ngay_chuyen_nha(db_pool, owner)

    hom_qua = date.today() - timedelta(days=1)
    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"message": f"ngày {hom_qua.day}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 202, res.text
    assert chay
    da_chon = chay[0].get("move_date")
    assert da_chon is not None
    assert date.fromisoformat(da_chon) >= date.today(), f"neo đẻ ra ngày quá khứ: {da_chon}"


@pytest.mark.asyncio
async def test_an_existing_date_still_wins_over_today(client, db_pool, monkeypatch):
    """CÓ bước rồi thì neo vào ngày của bước đó, KHÔNG neo vào hôm nay.

    Đây là luật `test_a_short_date_is_anchored_to_the_old_one` đã chốt, và lý do
    của nó: một lịch đặt cho tháng sau mà neo vào hôm nay sẽ bị kéo ngược về
    tháng này — với lịch đặt xa thì thành ngày quá khứ.

    Không có ca này thì đổi "neo or hôm nay" thành "luôn luôn hôm nay" vẫn xanh
    hết. Đã thử, và đó là lý do nó có mặt.
    """
    from src.api import routes

    chay: list[dict] = []

    async def _ghi_lai(workflow_id, goal, *_a, **_kw):
        chay.append(dict((routes._DEMO_JOBS.get(workflow_id) or {}).get("existing_context") or {}))

    monkeypatch.setattr(routes, "_run_demo_job", _ghi_lai)

    token = await _register_and_login(client, "ngay_cu_thang_hon_hom_nay")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='ngay_cu_thang_hon_hom_nay'")

    # Lịch đặt XA — ba tháng nữa. Neo vào hôm nay sẽ kéo nó về tháng này.
    xa = date.today() + timedelta(days=90)
    wid = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
            "VALUES ($1,'đặt lịch tham quan','CANCELLED',$2)",
            wid,
            owner,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1','schedule_property_viewing','CANCELLED',$2::jsonb)",
            wid,
            json.dumps({"project_id": "PRJ-004", "viewing_date": xa.isoformat(), "viewing_time": "09:30"}),
        )
        await conn.execute(
            "INSERT INTO workflow_clarifications (workflow_id, goal, missing_fields, resolved_at) "
            "VALUES ($1,'đặt lịch tham quan',$2::jsonb,NULL)",
            wid,
            json.dumps(["viewing_date"]),
        )

    ngay_moi = xa.replace(day=min(xa.day + 1, 28))
    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"message": f"ngày {ngay_moi.day}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 202, res.text
    assert chay
    da_chon = chay[0].get("viewing_date")
    assert da_chon == ngay_moi.isoformat(), f"neo sai tháng: {da_chon} (đáng ra {ngay_moi})"
