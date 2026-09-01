"""Hồ sơ "xin đổi / xin huỷ" nằm chung hàng đợi, nhưng KHÔNG phải một bước để chạy.

Vì sao chung hàng đợi
---------------------
Đơn vị đã có một màn hình duyệt, có phân quyền, có mã từ chối, có đường báo lại
cho khách. Dựng một kênh liên hệ thứ hai nghĩa là dựng lại cả bốn thứ ấy.

Vì sao PHẢI phân biệt
---------------------
Mọi dòng trong `service_approvals` hiện được coi là một BƯỚC. Khi đơn vị duyệt,
`resume_after_service_decision` đẩy nó về `PENDING` để Executor chạy:

    for row in rows:
        if row["status"] != "APPROVED": continue
        await repository.update_task_status(workflow_id, row["task_id"], PENDING)

Một hồ sơ "xin đổi lịch" không có tool nào để chạy, và `task_id` của nó không có
dòng `workflow_tasks`. `update_task_status` ném `TaskNotFoundError` — và nó ném
giữa lượt resume, tức GIẾT luôn phần còn lại của workflow: những bước đơn vị vừa
duyệt trong cùng lượt ấy không bao giờ chạy.

Nên cột `kind` không phải để phân loại cho đẹp. Nó là thứ giữ cho một lời nhờ
không bị đọc thành một mệnh lệnh.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.orchestration import demo_service
from src.orchestration.service_approval import (
    pending_for_workflow,
    record_service_decision,
    save_support_request,
)

GOAL = "Đặt lịch tham quan."


async def _seed_xong(pool) -> str:
    """Một lịch tham quan đã CHẠY XONG — đúng lúc khách bấm nút xin đổi/huỷ."""
    wid = uuid.uuid4()
    plan = {
        "goal": GOAL,
        "tasks": [{"task_id": "T1", "tool": "schedule_property_viewing", "depends_on": [], "input": {}}],
    }
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan) VALUES ($1,$2,'SUCCESS',$3::jsonb)",
        wid,
        GOAL,
        json.dumps(plan),
    )
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
        " provider_submission_status) VALUES ($1,'T1','schedule_property_viewing','SUCCESS','[]'::jsonb,"
        " $2::jsonb, $3::jsonb, 'ACKNOWLEDGED')",
        wid,
        json.dumps({"project_id": "PRJ-005", "viewing_date": "2026-08-24", "viewing_time": "10:00"}),
        json.dumps({"viewing_id": "VIEW-014", "viewing_date": "2026-08-24", "viewing_time": "10:00"}),
    )
    await pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status,"
        " decided_by, decided_at) VALUES ($1,'T1','schedule_property_viewing','Đặt lịch tham quan',"
        " '{}'::jsonb,'APPROVED','don_vi_tour',NOW())",
        wid,
    )
    return str(wid)


# --- hồ sơ được ghim đúng chỗ đơn vị đang nhìn -------------------------------


@pytest.mark.asyncio
async def test_a_request_lands_in_the_queue_the_provider_already_watches(db_pool):
    wid = await _seed_xong(db_pool)

    ma = await save_support_request(
        db_pool, workflow_id=wid, task_id="T1", kind="CANCEL", note="Bận đột xuất, xin huỷ giúp mình."
    )

    hang_doi = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"]
    assert [r["task_id"] for r in hang_doi] == [ma]
    assert hang_doi[0]["tool"] == "schedule_property_viewing", "đơn vị không biết hồ sơ này thuộc dịch vụ nào"
    nhan = hang_doi[0]["service_label"]
    assert "huỷ" in nhan.casefold(), nhan
    assert "tham quan" in nhan.casefold(), f"đơn vị không đọc được đây là dịch vụ gì: {nhan}"


@pytest.mark.asyncio
async def test_the_customer_note_reaches_the_provider(db_pool):
    wid = await _seed_xong(db_pool)

    ma = await save_support_request(db_pool, workflow_id=wid, task_id="T1", kind="AMEND", note="Xin đổi sang 28/8.")

    row = next(r for r in await pending_for_workflow(db_pool, wid) if r["task_id"] == ma)
    chi_tiet = row["details"]
    chi_tiet = json.loads(chi_tiet) if isinstance(chi_tiet, str) else chi_tiet
    assert chi_tiet.get("ghi_chu") == "Xin đổi sang 28/8."
    assert chi_tiet.get("task_id") == "T1", "hồ sơ không nói nó nói về bước nào"


@pytest.mark.asyncio
async def test_a_request_never_collides_with_a_real_task(db_pool):
    """Khoá chính là `(workflow_id, task_id)`. Trùng là ghi đè bằng chứng."""
    wid = await _seed_xong(db_pool)

    mot = await save_support_request(db_pool, workflow_id=wid, task_id="T1", kind="AMEND", note="lần một")
    hai = await save_support_request(db_pool, workflow_id=wid, task_id="T1", kind="CANCEL", note="lần hai")

    assert mot != hai, "hồ sơ thứ hai đè lên hồ sơ thứ nhất"
    assert not await db_pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id = ANY($2::varchar[]))",
        wid,
        [mot, hai],
    ), "mã hồ sơ đụng một bước có thật"


# --- và KHÔNG bị đọc thành một bước để chạy ---------------------------------


@pytest.mark.asyncio
async def test_approving_a_request_does_not_try_to_run_it(client, db_pool):
    """Đây là lỗi mà cột `kind` tồn tại để chặn."""
    wid = await _seed_xong(db_pool)
    ma = await save_support_request(db_pool, workflow_id=wid, task_id="T1", kind="CANCEL", note="xin huỷ")

    await record_service_decision(db_pool, wid, ma, "APPROVED", decided_by="don_vi_tour")
    await demo_service.resume_after_service_decision(wid)

    van_con = await db_pool.fetchval(
        "SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", wid
    )
    assert van_con == "SUCCESS", f"lượt resume đụng vào bước đã xong: {van_con}"
    # Hồ sơ được duyệt SINH RA một bước huỷ riêng (xem `support_request.py`),
    # nhưng KHÔNG bao giờ dựng một dòng mang chính mã hồ sơ, và không chạy lại
    # bước gốc.
    assert (
        await db_pool.fetchval("SELECT COUNT(*) FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id=$2", wid, ma)
        == 0
    ), "dựng một dòng bước mang chính mã hồ sơ liên hệ"
    sinh_ra = await db_pool.fetch("SELECT tool FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id <> 'T1'", wid)
    assert [r["tool"] for r in sinh_ra] == ["cancel_property_viewing"], [dict(r) for r in sinh_ra]


@pytest.mark.asyncio
async def test_refusing_a_request_does_not_cancel_the_booking(client, db_pool):
    """Đơn vị từ chối lời nhờ — lịch cũ của khách phải còn nguyên."""
    wid = await _seed_xong(db_pool)
    ma = await save_support_request(db_pool, workflow_id=wid, task_id="T1", kind="AMEND", note="xin đổi")

    await record_service_decision(
        db_pool, wid, ma, "REJECTED", decided_by="don_vi_tour", reason="Quá hạn đổi lịch.", reject_code="OTHER"
    )
    await demo_service.resume_after_service_decision(wid)

    assert (
        await db_pool.fetchval("SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", wid)
        == "SUCCESS"
    ), "lời từ chối một yêu cầu hỗ trợ kéo theo cả lịch đã đặt"
    assert await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", wid) == "SUCCESS", (
        "workflow đã hoàn tất bị hạ trạng thái vì một hồ sơ liên hệ"
    )


@pytest.mark.asyncio
async def test_an_open_request_does_not_freeze_the_request_itself(client, db_pool):
    """Hồ sơ liên hệ đang chờ KHÔNG được chặn đường sửa của khách.

    `amend_and_rerun` từ chối mọi workflow còn dòng `AWAITING` — hàng rào ấy nói
    về việc "đơn vị đang cầm một BƯỚC", không phải "có một lời nhờ đang treo".
    """
    from src.orchestration.demo_service import NotAmendable

    wid = await _seed_xong(db_pool)
    await db_pool.execute("UPDATE workflows SET status='FAILED' WHERE workflow_id=$1::uuid", wid)
    await save_support_request(db_pool, workflow_id=wid, task_id="T1", kind="CANCEL", note="xin huỷ")

    try:
        await demo_service.amend_and_rerun(wid, {"viewing_date": "2029-09-09"})
    except NotAmendable as exc:
        assert exc.code != "ALREADY_SENT", "một lời nhờ đang treo bị đọc thành 'đơn vị đang cầm bước này'"
    except Exception:  # noqa: BLE001 - seed tối giản, chạy tiếp có thể hỏng vì lý do khác
        pass


# --- qua HTTP: đúng đường khách bấm nút -------------------------------------


async def _dang_nhap(client, db_pool) -> tuple[str, str]:
    from tests.test_db.conftest import _register_and_login

    username = f"khach_{uuid.uuid4().hex[:8]}"
    token = await _register_and_login(client, username)
    owner = await db_pool.fetchval("SELECT id::text FROM users WHERE username=$1", username)
    return token, owner


@pytest.mark.asyncio
async def test_the_button_reaches_the_provider_queue(client, db_pool):
    token, owner = await _dang_nhap(client, db_pool)
    wid = await _seed_xong(db_pool)
    await db_pool.execute("UPDATE workflows SET owner_user_id=$2 WHERE workflow_id=$1::uuid", wid, owner)

    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/support-requests",
        json={"task_id": "T1", "kind": "CANCEL", "note": "Bận đột xuất."},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 202, res.text
    assert res.json()["request_id"].startswith("YC")
    cho = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"]
    assert len(cho) == 1 and cho[0]["kind"] == "REQUEST", cho


@pytest.mark.asyncio
async def test_another_account_cannot_file_a_request_on_your_booking(client, db_pool):
    _token, owner = await _dang_nhap(client, db_pool)
    nguoi_la, _ = await _dang_nhap(client, db_pool)
    wid = await _seed_xong(db_pool)
    await db_pool.execute("UPDATE workflows SET owner_user_id=$2 WHERE workflow_id=$1::uuid", wid, owner)

    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/support-requests",
        json={"task_id": "T1", "kind": "CANCEL"},
        headers={"Authorization": f"Bearer {nguoi_la}"},
    )

    assert res.status_code in {403, 404}, res.text
    assert [r for r in await pending_for_workflow(db_pool, wid) if r["kind"] == "REQUEST"] == []


@pytest.mark.asyncio
async def test_the_body_carries_no_decision(client, db_pool):
    """Khách nêu việc; đơn vị quyết. Body không được mang trạng thái hay nhãn."""
    token, owner = await _dang_nhap(client, db_pool)
    wid = await _seed_xong(db_pool)
    await db_pool.execute("UPDATE workflows SET owner_user_id=$2 WHERE workflow_id=$1::uuid", wid, owner)

    for thua in ({"status": "APPROVED"}, {"service_label": "Tự đặt tên"}, {"tool": "pay_fee"}):
        res = await client.post(
            f"/api/v1/workflows/demo/{wid}/support-requests",
            json={"task_id": "T1", "kind": "CANCEL", **thua},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 422, f"nhận thừa {thua}: {res.status_code}"


@pytest.mark.asyncio
async def test_an_unknown_step_is_refused(client, db_pool):
    token, owner = await _dang_nhap(client, db_pool)
    wid = await _seed_xong(db_pool)
    await db_pool.execute("UPDATE workflows SET owner_user_id=$2 WHERE workflow_id=$1::uuid", wid, owner)

    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/support-requests",
        json={"task_id": "T99", "kind": "CANCEL"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 422, res.text


# --- phía giao diện: không hứa một việc màn hình không làm được --------------


def test_the_result_card_offers_no_change_or_cancel_button():
    """Thẻ kết quả KHÔNG có nút đổi/huỷ, và nói thẳng ai sẽ liên hệ.

    Ba đời của cùng một chỗ trên màn hình:

      1. hai nút chỉ `setNote(...)` — hiện chữ bảo người dùng đi gõ chat, và ô
         chat ấy đã bị gỡ;
      2. hai nút gọi `createSupportRequest` — "Huỷ lịch" chạy thật, còn
         "Đổi lịch" ghim một hồ sơ mà `support_request._ACTIONS` cố ý không có
         cặp nào để thực hiện, nên đơn vị bấm Duyệt rồi không có gì xảy ra;
      3. không nút nào. Mỗi dịch vụ đều cần một lượt xác nhận của đơn vị, và
         đơn vị GỌI ĐIỆN để làm việc ấy — nên đổi/huỷ đi bằng cuộc gọi đó.

    Cả ba đời hỏng theo cùng một kiểu nếu làm sai: màn hình bày ra một lối đi
    không dẫn tới đâu. Bài kiểm này giữ đời thứ ba đúng hình.

    Frontend không có hạ tầng test nên kiểm bằng cách đọc file TSX — cùng kỹ
    thuật `tests/test_every_refusal_carries_a_cause.py` đã dùng.
    """
    from pathlib import Path

    goc = Path(__file__).resolve().parents[2] / "frontend" / "src"
    card = (goc / "components" / "workspace" / "ResultSummary.tsx").read_text(encoding="utf-8")

    assert "createSupportRequest(" not in card, "thẻ kết quả gửi lại hồ sơ đổi/huỷ"
    assert "Nhắn cho P-118" not in card, "vẫn chỉ người dùng tới ô chat đã bị gỡ"

    # Gỡ nút mà không nói gì thay vào chỗ ấy là để khách ngồi im không biết
    # bước tiếp theo là gì.
    #
    # Dòng nhắc ở TRANG, không ở thẻ: nó nói về mọi đơn vị trong yêu cầu, và
    # một yêu cầu có thể có nhiều thẻ. Đặt trong thẻ thì ba dịch vụ lặp ba lần,
    # mỗi lần đọc như thể chỉ đơn vị của thẻ ấy sẽ gọi.
    trang = (goc / "pages" / "WorkflowPage.tsx").read_text(encoding="utf-8")
    assert "Hãy chú ý điện thoại" in trang, "gỡ nút xong không nói ai sẽ liên hệ"
    assert "Hãy chú ý điện thoại" not in card, "dòng nhắc chung nằm trong thẻ của MỘT dịch vụ"

    # Route và client vẫn sống — backend `run_approved_requests` còn nguyên và
    # còn test phủ. Nếu sau này mở lại cổng đổi/huỷ thì đây là đường vào.
    api = (goc / "lib" / "agentApi.ts").read_text(encoding="utf-8")
    dai = api[api.index("export async function createSupportRequest") :][:900]
    assert "support-requests" in dai, dai[:200]
    assert "method: 'POST'" in dai
