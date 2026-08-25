"""Đổi một vế của khung giờ là một câu trả lời TRỌN VẸN.

Đo được trên stack demo, workflow 7b79bfa2 của người dùng thật:

    P-118: Không có lịch trống. Khung giờ 10:00 ngày 2026-08-27 đã kín lịch.
           Bạn chọn giờ HOẶC ngày khác giúp mình nhé.
    Bạn:   đổi qua ngày 28
    P-118: Mình cần thêm thông tin để lập kế hoạch: giờ muốn tham quan.

Câu từ chối hứa đổi MỘT trong hai là đủ; mã lại đòi cả hai. Giá trị cũ vẫn nằm
nguyên trong bước đã huỷ và không ai rút lại nó:

    T1  {"viewing_date": "2026-08-27", "viewing_time": "10:00"}   CANCELLED
    hồ sơ hỏi  missing_fields    ["viewing_time"]
               existing_context  … "viewing_date": "2026-08-28" …   ← không có giờ

Vì sao nó rơi vào đây: `_NO_AVAILABILITY_FIELDS` coi khung giờ là một CẶP, đúng
— nhưng nhánh trả-lời-một-phần trong `/continue` chặn trước khi tới
`rerun_with_answers`. Nhánh ấy được viết cho lượt hỏi ĐẦU, lúc chưa có kế hoạch
nào: thiếu ô nào thì phải hỏi ô đó, không có chỗ nào khác lấy giá trị.

Lượt SỬA thì khác hẳn — kế hoạch cũ còn nguyên và đã qua Validator, nên ô không
đổi tự giữ giá trị cũ. Với một hồ sơ hỏi sinh ra từ repair hint, trả lời một
phần KHÔNG phải là trả lời thiếu.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.test_db.conftest import _register_and_login

NGAY_MOI = "2026-08-28"


async def _khung_gio_bi_tu_choi(pool, owner) -> str:
    """Dựng đúng hình dạng đã đo: kế hoạch có T1 bị huỷ + repair hint + hồ sơ hỏi."""
    wid = uuid.uuid4()
    ke_hoach = {
        "goal": "Đặt lịch tham quan Vinhomes Pearl Bay",
        "tasks": [
            {
                "task_id": "T1",
                "tool": "schedule_property_viewing",
                "depends_on": [],
                "input": {
                    "project_id": "PRJ-004",
                    "viewing_date": "2026-08-27",
                    "viewing_time": "10:00",
                },
            }
        ],
    }
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, task_plan) "
            "VALUES ($1,$2,'FAILED',$3,$4::jsonb)",
            wid,
            ke_hoach["goal"],
            owner,
            json.dumps(ke_hoach),
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
            "VALUES ($1,'T1','schedule_property_viewing','CANCELLED','[]'::jsonb,$2::jsonb)",
            wid,
            json.dumps(ke_hoach["tasks"][0]["input"]),
        )
        await conn.execute(
            "INSERT INTO workflow_repair_hints (workflow_id, task_id, error_code, message) "
            "VALUES ($1,'T1','NO_AVAILABILITY',$2)",
            wid,
            "Khung giờ 10:00 ngày 2026-08-27 đã kín lịch.",
        )
        await conn.execute(
            "INSERT INTO workflow_clarifications (workflow_id, goal, missing_fields, resolved_at) "
            "VALUES ($1,$2,$3::jsonb,NULL)",
            wid,
            ke_hoach["goal"],
            json.dumps(["viewing_date", "viewing_time"]),
        )
    return str(wid)


@pytest.mark.asyncio
async def test_answering_only_the_date_does_not_ask_for_the_time_again(client, db_pool):
    ten = "doi_mot_ve"
    token = await _register_and_login(client, ten)
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", ten)
    wid = await _khung_gio_bi_tu_choi(db_pool, owner)

    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"message": f"đổi qua ngày {NGAY_MOI}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    con_thieu = list(body.get("missing_fields") or [])
    assert "viewing_time" not in con_thieu, (
        "người dùng đã nói 10:00 và chưa rút lại; đổi ngày không phải là xoá giờ. "
        f"Hệ thống hỏi lại: {con_thieu} — {body.get('question')!r}"
    )
    assert body.get("status") != "NEEDS_INFORMATION", (
        f"trả lời một phần cho một lượt SỬA phải đi tiếp, không hỏi lại: {body}"
    )


@pytest.mark.asyncio
async def test_answering_only_the_time_is_a_whole_answer_too(client, db_pool):
    """Đối xứng: đổi giờ mà giữ ngày cũng phải đi tiếp."""
    ten = "doi_ve_gio"
    token = await _register_and_login(client, ten)
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", ten)
    wid = await _khung_gio_bi_tu_choi(db_pool, owner)

    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"message": "đổi qua 14:30"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert "viewing_date" not in list(body.get("missing_fields") or []), (
        f"đổi giờ mà bị hỏi lại ngày: {body.get('question')!r}"
    )


@pytest.mark.asyncio
async def test_a_first_time_question_still_asks_for_everything(client, db_pool):
    """Đừng nới lỏng lượt hỏi ĐẦU: chưa có kế hoạch thì không có gì để giữ lại."""
    from tests.test_db.test_every_service_keeps_what_you_already_told_it import _mo_cau_hoi

    ten = "lan_hoi_dau"
    token = await _register_and_login(client, ten)
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", ten)
    wid = await _mo_cau_hoi(
        db_pool,
        owner,
        "schedule_property_viewing",
        ["project_id", "viewing_date", "viewing_time"],
    )
    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"message": NGAY_MOI},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body.get("status") == "NEEDS_INFORMATION"
    assert "viewing_time" in list(body.get("missing_fields") or []), f"lượt hỏi đầu mà bỏ qua ô chưa ai trả lời: {body}"
