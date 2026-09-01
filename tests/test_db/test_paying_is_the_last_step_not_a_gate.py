"""Thanh toán là việc CUỐI CÙNG, không phải cái cổng chặn mọi câu khác.

Chuỗi thật đã đo được — một yêu cầu ba dịch vụ
-----------------------------------------------
    tham quan    đơn vị TỪ CHỐI (hết khung 10:30) → câu hỏi "chọn giờ/ngày khác"
    chỗ đỗ xe    đã giữ được                      → thẻ chờ THANH TOÁN

Hai việc cùng chờ, và cả hai đều cần CHÍNH khách làm. Nhưng hệ thống chỉ mô
hình hoá được MỘT việc đang chờ, và nó xếp thanh toán lên trước:

    Bạn:    ok vậy đổi qua ngày 25
    P-118:  Khoản này cần bạn xác nhận rõ ràng. Bạn bấm "Xác nhận thanh toán"…
    Bạn:    tôi muốn đổi ngày trước rồi sẽ thanh toán sau
    P-118:  Mình chưa rõ ý bạn. Bạn muốn mình tiếp tục hay dừng lại?

Ngõ cụt: KHÔNG có ô nào để nhập ngày mới, và mọi câu đều rơi vào cổng thanh
toán. Khách gõ một câu hoàn toàn hợp lệ và hệ thống không có đường nào nhận nó.

Luật
----
Tiền là bước cuối. Nó nhắc, nó không chặn. Câu hỏi đang treo — thứ khách buộc
phải trả lời để phần còn lại chạy tiếp — luôn được nói trước.

Điều KHÔNG đổi: tiền chỉ chuyển khi có một hành động không thể hiểu nhầm. Bài
kiểm này nới thứ tự HIỂN THỊ, không nới cổng duyệt.
"""

from __future__ import annotations

import json
import uuid

import pytest

GOAL = "Đặt lịch tham quan, đăng ký xe và giữ chỗ đỗ xe."
NGAY = "2029-11-24"

_PLAN = {
    "goal": GOAL,
    "tasks": [
        {"task_id": "T1", "tool": "schedule_property_viewing", "depends_on": [], "input": {}},
        {"task_id": "T5", "tool": "book_parking", "depends_on": [], "input": {}},
        {"task_id": "T8", "tool": "pay_fee", "depends_on": ["T5"], "input": {}},
    ],
}


async def _seed_both_pending(pool, owner: str) -> str:
    """Đúng trạng thái đã đo: câu hỏi tham quan treo, thẻ thanh toán cũng treo."""
    # Báo giá đọc từ `parking_bookings`, KHÔNG từ snapshot trong
    # `payment_approvals` — nên chỗ đỗ phải có thật, nếu không view trả None và
    # bài kiểm xanh vì một lý do sai.
    tag = uuid.uuid4().hex[:6]
    await pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
        f" VALUES ('RES-{tag}','Nguyen Van A','A{tag}','Ocean Park') ON CONFLICT DO NOTHING"
    )
    await pool.execute(
        "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)"
        f" VALUES ('VEH-{tag}','RES-{tag}','51H-{tag}','car') ON CONFLICT DO NOTHING"
    )
    await pool.execute(
        "INSERT INTO parking_bookings (booking_id, vehicle_id, parking_zone, booking_date, amount, currency)"
        f" VALUES ('BOOK-P1','VEH-{tag}','ZONE_A',$1::text::date,150000,'VND')",
        NGAY,
    )
    wid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan, owner_user_id)"
        " VALUES ($1,$2,'WAITING_APPROVAL',$3::jsonb,$4)",
        wid,
        GOAL,
        json.dumps(_PLAN),
        owner,
    )
    # Tham quan bị từ chối vì hết khung giờ → có câu hỏi cho khách.
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
        " error_code, error_message, provider_submission_status)"
        " VALUES ($1,'T1','schedule_property_viewing','FAILED','[]'::jsonb,$2::jsonb,"
        " 'NO_AVAILABILITY','Khung giờ 10:30 ngày 2029-11-24 đã kín lịch.','UNKNOWN')",
        wid,
        json.dumps({"project_id": "PRJ-005", "viewing_date": NGAY, "viewing_time": "10:30"}),
    )
    await pool.execute(
        "INSERT INTO workflow_repair_hints (workflow_id, task_id, error_code, message)"
        " VALUES ($1,'T1','NO_AVAILABILITY','Khung giờ 10:30 ngày 2029-11-24 đã kín lịch.')",
        wid,
    )
    # Chỗ đỗ đã giữ được → thẻ thanh toán treo.
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
        " provider_submission_status) VALUES ($1,'T5','book_parking','SUCCESS','[]'::jsonb,$2::jsonb,$3::jsonb,"
        " 'ACKNOWLEDGED')",
        wid,
        json.dumps({"vehicle_id": "VEH-P1", "parking_zone": "ZONE_A", "booking_date": NGAY}),
        json.dumps({"booking_id": "BOOK-P1", "amount": 150000, "currency": "VND"}),
    )
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
        " provider_submission_status) VALUES ($1,'T8','pay_fee','WAITING_APPROVAL','[\"T5\"]'::jsonb,"
        " $2::jsonb,'NOT_SUBMITTED')",
        wid,
        json.dumps(
            {
                "booking_id": {"from_task": "T5", "field": "booking_id"},
                "amount": {"from_task": "T5", "field": "amount"},
                "currency": {"from_task": "T5", "field": "currency"},
            }
        ),
    )
    await pool.execute(
        "INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status)"
        " VALUES ($1,'T8','BOOK-P1',150000,'VND','AWAITING')",
        wid,
    )
    return str(wid)


async def _dang_nhap(client, db_pool) -> tuple[str, str]:
    from tests.test_db.conftest import _register_and_login

    username = f"cuoi_{uuid.uuid4().hex[:8]}"
    token = await _register_and_login(client, username)
    owner = await db_pool.fetchval("SELECT id::text FROM users WHERE username=$1", username)
    return token, owner


@pytest.mark.asyncio
async def test_the_question_is_shown_not_the_payment(client, db_pool):
    """Đây là ngõ cụt được báo."""
    token, owner = await _dang_nhap(client, db_pool)
    wid = await _seed_both_pending(db_pool, owner)

    res = await client.get(f"/api/v1/workflows/demo/{wid}", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "NEEDS_INFORMATION", f"thẻ thanh toán vẫn che câu hỏi: {body['status']}"
    assert body["missing_fields"], "không có ô nào để khách nhập ngày mới"
    assert "viewing_date" in body["missing_fields"] or "viewing_time" in body["missing_fields"], body["missing_fields"]


@pytest.mark.asyncio
async def test_the_quote_is_still_there_to_be_paid(client, db_pool):
    """Nhường chỗ KHÔNG phải biến mất. Khoản tiền vẫn phải đọc được.

    Nếu báo giá mất theo, khách trả lời xong câu hỏi rồi không còn gì để trả —
    và một khoản đã giữ chỗ thật thì không được im lặng biến mất.
    """
    token, owner = await _dang_nhap(client, db_pool)
    wid = await _seed_both_pending(db_pool, owner)

    body = (await client.get(f"/api/v1/workflows/demo/{wid}", headers={"Authorization": f"Bearer {token}"})).json()

    bao_gia = body.get("payment_quote") or {}
    assert bao_gia.get("amount") == 150000, f"báo giá biến mất khi câu hỏi thắng: {bao_gia}"
    assert bao_gia.get("booking_id") == "BOOK-P1"


@pytest.mark.asyncio
async def test_with_no_question_open_the_payment_card_still_wins(client, db_pool):
    """Không có câu hỏi nào thì thẻ thanh toán vẫn là việc đang chờ như cũ."""
    token, owner = await _dang_nhap(client, db_pool)
    wid = await _seed_both_pending(db_pool, owner)
    await db_pool.execute("DELETE FROM workflow_repair_hints WHERE workflow_id=$1::uuid", wid)

    body = (await client.get(f"/api/v1/workflows/demo/{wid}", headers={"Authorization": f"Bearer {token}"})).json()

    assert body["status"] == "WAITING_APPROVAL", body["status"]
    assert (body.get("payment_quote") or {}).get("amount") == 150000


@pytest.mark.asyncio
async def test_the_payment_gate_itself_is_not_loosened(client, db_pool):
    """Nới thứ tự HIỂN THỊ, không nới cổng tiền.

    `pay_fee` vẫn phải nằm ở `WAITING_APPROVAL` và khoản duyệt vẫn `AWAITING`
    cho tới khi có một hành động không thể hiểu nhầm.
    """
    token, owner = await _dang_nhap(client, db_pool)
    wid = await _seed_both_pending(db_pool, owner)

    await client.get(f"/api/v1/workflows/demo/{wid}", headers={"Authorization": f"Bearer {token}"})

    assert (
        await db_pool.fetchval("SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T8'", wid)
        == "WAITING_APPROVAL"
    )
    assert await db_pool.fetchval("SELECT status FROM payment_approvals WHERE workflow_id=$1::uuid", wid) == "AWAITING"


# --- phía giao diện: thẻ tiền thôi bắt giữ câu ------------------------------


def _khong_ghi_chu(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        for mo, dong in (("{/*", "*/}"), ("/*", "*/"), ("//", "\n")):
            if text.startswith(mo, i):
                ket = text.find(dong, i)
                i = len(text) if ket < 0 else ket + len(dong)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _workspace() -> str:
    from pathlib import Path

    return _khong_ghi_chu(
        (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "JourneyWorkspacePage.tsx").read_text(
            encoding="utf-8"
        )
    )


def test_the_payment_card_no_longer_captures_every_message():
    """Đây là ngõ cụt được báo, ở phía client."""
    code = _workspace()

    assert "laVeTien" in code, "mọi câu vẫn bị thẻ thanh toán bắt giữ"
    assert "nhacTraTien" in code, "đi tiếp mà không nhắc khách còn khoản chưa trả"


def test_only_sentences_about_the_money_stay_with_the_payment_card():
    """Đồng ý, từ chối, và câu HỎI về chính khoản ấy — ba thứ đó ở lại."""
    code = _workspace()
    dieu_kien = code[code.index("const laVeTien") :][:200]

    for y in ("'APPROVE'", "'REJECT'", "'QUESTION'"):
        assert y in dieu_kien, f"{y} không còn được thẻ thanh toán xử lý: {dieu_kien}"


def test_the_money_gate_itself_is_untouched():
    """Nới chỗ BẮT GIỮ, không nới chỗ DUYỆT.

    `resolve()` vẫn phải đòi bấm đúng nút hoặc nói thẳng ra là đồng ý trả tiền.
    """
    from pathlib import Path

    gate = _khong_ghi_chu(
        (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "pendingAction.ts").read_text(
            encoding="utf-8"
        )
    )

    assert "source !== 'button' && !explicit" in gate, "cổng tiền đã bị nới"
    assert "EXPLICIT_PAYMENT_APPROVAL" in gate


@pytest.mark.asyncio
async def test_a_short_date_is_understood_through_the_real_route(client, db_pool):
    """ "đổi qua ngày 25" đi qua ĐÚNG đường `/continue`, không phải hàm rời.

    Bộ neo có thể đúng mà đường dẫn vẫn không truyền giá trị cũ xuống — đó là
    cách nó đã hỏng lần đầu: `rewrite_relative_dates` chạy tốt ở lane sửa yêu
    cầu, còn `/continue` thì không gọi tới.
    """
    token, owner = await _dang_nhap(client, db_pool)
    wid = await _seed_both_pending(db_pool, owner)
    # Lượt hỏi phải đang mở thì `/continue` mới nhận câu trả lời.
    from src.orchestration.runtime_provider import acquire_repository

    repository = await acquire_repository()
    await repository.save_clarification(
        wid,
        session_id=None,
        parent_workflow_id=None,
        goal=GOAL,
        missing_fields=["viewing_date"],
        question="Khung giờ 10:30 ngày 2029-11-24 đã kín lịch. Bạn chọn ngày khác giúp mình nhé.",
        existing_context={},
    )

    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"message": "đổi qua ngày 25"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code != 422, f"vẫn không đọc được ngày nói tắt: {res.text[:200]}"
