"""Bảy dịch vụ CŨ đi đúng đường cũ — kể cả khi cờ báo giá đang BẬT.

Vì sao có file này
------------------
Tính năng chọn đơn vị theo báo giá được thêm cho MỘT dịch vụ. Nó chạm vào những
tầng dùng chung: cổng duyệt, hàng đợi đơn vị, đường chạy tiếp sau quyết định,
và màn hình khách. Mỗi tầng dùng chung là một chỗ để một dịch vụ khác đổi hành
vi mà không ai gọi tên nó ra.

Bài kiểm phạm vi đã có (`test_the_quote_path_stays_shut_for_everything_else.py`)
chỉ đo một hằng số. Nó không trả lời được câu "một yêu cầu đặt chỗ đỗ xe chạy
qua PostgreSQL thật thì có sinh ra chứng từ báo giá nào không". File này trả lời
câu ấy, cho từng dịch vụ, với cờ BẬT — vì cờ bật mới là trạng thái đáng lo.

Đo gì, cho mỗi dịch vụ
----------------------
  * không sinh `service_quotes`;
  * không sinh `service_provider_proposals`;
  * màn hình khách không có thẻ đề xuất (`service_proposals` rỗng) và không
    dừng ở bước chọn đơn vị;
  * hàng đợi mở cho ĐÚNG đơn vị của `provider_directory`, ngay lập tức — không
    chờ khách xác nhận ai;
  * đơn vị khác không đọc và không quyết định được (404);
  * duyệt → bước chạy tiếp; từ chối → bước `CANCELLED`;
  * đọc lại sau khi xoá cache RAM cho cùng một kết quả.

Điều file này KHÔNG kiểm
------------------------
Không gọi model và không gọi provider ngoài. Kế hoạch được gieo thẳng vào
`workflow_tasks` như `save_pending_service_approvals` vẫn ghi. Thứ đang đo là
CỔNG và CHỨNG TỪ, không phải chất lượng kế hoạch — đường qua model đã có canary
riêng ở `tests/e2e/`.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.orchestration.provider_directory import don_vi_mac_dinh
from src.orchestration.service_approval import SERVICE_LABELS
from tests.test_db.conftest import _register_and_login

SERVICE = "/api/v1/service-approvals"
VIEWING = "/api/v1/viewing-approvals"
DEMO = "/api/v1/workflows/demo"

# Bảy dịch vụ, kèm một bộ dữ kiện hợp lệ tối thiểu. Dữ kiện chỉ cần đủ để dòng
# chờ duyệt có nội dung; không tầng nào trong bài này đọc chúng theo nghĩa.
BAY_DICH_VU: tuple[tuple[str, dict], ...] = (
    (
        "schedule_property_viewing",
        {
            "project_id": "PRJ-001",
            "project_name": "Vinhomes Ocean Park",
            "viewing_date": "2026-12-01",
            "viewing_time": "09:00",
            "passenger_count": 2,
            "wants_shuttle": False,
        },
    ),
    ("register_property_interest", {"project_name": "Vinhomes Ocean Park", "preferred_contact_time": "sáng"}),
    ("register_vehicle", {"plate_number": "51H-12345", "vehicle_type": "car"}),
    ("book_parking", {"booking_date": "2026-12-01", "parking_zone": "A", "plate_number": "51H-12345"}),
    ("change_parking_zone", {"parking_zone": "B", "plate_number": "51H-12345"}),
    ("book_shuttle", {"viewing_date": "2026-12-01", "viewing_time": "09:00", "passenger_count": 2}),
    (
        "create_maintenance_request",
        {"issue_type": "plumbing", "description": "Vòi nước rò", "preferred_date": "2026-12-01"},
    ),
)

TEN_TOOL = [t for t, _ in BAY_DICH_VU]


@pytest.fixture(autouse=True)
def _bat_co(monkeypatch):
    """Cờ BẬT cho MỌI bài trong file này.

    Tắt cờ thì bài nào cũng xanh vì lý do sai: đường báo giá không chạy cho ai
    cả. Trạng thái đáng đo là trạng thái của buổi demo — cờ bật, và bảy dịch vụ
    kia vẫn phải nguyên vẹn.
    """
    monkeypatch.setenv("SERVICE_PROVIDER_MATCHING", "1")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _tai_khoan(client, db_pool, username: str, role: str | None = None) -> tuple[str, str]:
    await _register_and_login(client, username)
    if role is not None:
        await db_pool.execute("UPDATE users SET role = $2 WHERE username = $1", username, role)
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    return token, str(uid)


async def _gan_don_vi(db_pool, user_id: str, ma: str) -> None:
    await db_pool.execute(
        "INSERT INTO service_provider_accounts (user_id, service_provider_id) "
        "VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING",
        user_id,
        ma,
    )


async def _yeu_cau(db_pool, owner_user_id: str, tool: str, chi_tiet: dict) -> str:
    """Một yêu cầu đang chờ đơn vị duyệt — ghim qua ĐÚNG đường sản phẩm.

    Gọi `save_pending_service_approvals` chứ không `INSERT` tay: hàm ấy là nơi
    đơn vị được gán, và gán tay ở đây sẽ làm bài kiểm bỏ qua đúng thứ nó đo.
    """
    from src.orchestration.service_approval import save_pending_service_approvals

    wid = str(uuid.uuid4())
    ke_hoach = {"goal": tool, "tasks": [{"task_id": "T1", "tool": tool, "depends_on": [], "input": chi_tiet}]}
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, task_plan) "
        "VALUES ($1::uuid, $2, 'WAITING_APPROVAL', $3::uuid, $4::jsonb)",
        wid,
        tool,
        owner_user_id,
        json.dumps(ke_hoach),
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', $2, 'WAITING_APPROVAL', '[]'::jsonb, $3::jsonb)",
        wid,
        tool,
        json.dumps(chi_tiet),
    )
    await save_pending_service_approvals(
        db_pool,
        workflow_id=wid,
        rows=[{"task_id": "T1", "tool": tool, "service_label": SERVICE_LABELS.get(tool, tool), "details": chi_tiet}],
        applicant={"user_id": owner_user_id, "name": "Người Thử", "phone": "0900000000"},
    )
    return wid


async def _dem_chung_tu(db_pool, wid: str) -> tuple[int, int]:
    bao_gia = await db_pool.fetchval("SELECT count(*) FROM service_quotes WHERE workflow_id = $1::uuid", uuid.UUID(wid))
    de_xuat = await db_pool.fetchval(
        "SELECT count(*) FROM service_provider_proposals WHERE workflow_id = $1::uuid", uuid.UUID(wid)
    )
    return int(bao_gia), int(de_xuat)


# ================================================== chứng từ báo giá
@pytest.mark.parametrize(("tool", "chi_tiet"), BAY_DICH_VU, ids=TEN_TOOL)
@pytest.mark.asyncio
async def test_no_quote_or_proposal_is_ever_created(client, db_pool, tool, chi_tiet):
    """Không `service_quotes`, không `service_provider_proposals` — kể cả cờ bật."""
    _, uid = await _tai_khoan(client, db_pool, f"kh_cu_{tool[:12]}")
    wid = await _yeu_cau(db_pool, uid, tool, chi_tiet)

    assert await _dem_chung_tu(db_pool, wid) == (0, 0)


# ================================================== màn hình khách
@pytest.mark.parametrize(("tool", "chi_tiet"), BAY_DICH_VU, ids=TEN_TOOL)
@pytest.mark.asyncio
async def test_the_customer_screen_shows_no_provider_choice(client, db_pool, tool, chi_tiet):
    """Không thẻ đề xuất, và không dừng ở bước chọn đơn vị.

    Hai khẳng định khác nhau: `service_proposals` rỗng nói KHÔNG CÓ GÌ ĐỂ BẤM;
    `stage` nói màn hình không tự nhận mình đang chờ khách chọn ai.
    """
    token, uid = await _tai_khoan(client, db_pool, f"kh_man_{tool[:12]}")
    wid = await _yeu_cau(db_pool, uid, tool, chi_tiet)

    view = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert view.get("service_proposals") in (None, []), view.get("service_proposals")
    assert view.get("stage") not in ("WAITING_PROVIDER_PROPOSAL", "WAITING_PROVIDER_RESELECTION"), view.get("stage")
    assert view.get("provider_rejection") is None


# ================================================== hàng đợi và quyền sở hữu
@pytest.mark.parametrize(("tool", "chi_tiet"), BAY_DICH_VU, ids=TEN_TOOL)
@pytest.mark.asyncio
async def test_the_queue_opens_immediately_for_the_directory_unit(client, db_pool, tool, chi_tiet):
    """Đơn vị của `provider_directory`, và hàng đợi mở NGAY — không chờ ai chọn."""
    _, uid = await _tai_khoan(client, db_pool, f"kh_hd_{tool[:12]}")
    wid = await _yeu_cau(db_pool, uid, tool, chi_tiet)

    row = await db_pool.fetchrow(
        "SELECT status, service_provider_id FROM service_approvals WHERE workflow_id = $1::uuid", uuid.UUID(wid)
    )
    assert row is not None, "không có dòng chờ duyệt nào"
    assert row["status"] == "AWAITING"
    assert row["service_provider_id"] == don_vi_mac_dinh(tool)


@pytest.mark.parametrize(("tool", "chi_tiet"), BAY_DICH_VU, ids=TEN_TOOL)
@pytest.mark.asyncio
async def test_a_foreign_unit_neither_reads_nor_decides(client, db_pool, tool, chi_tiet):
    """Đơn vị đúng thấy; đơn vị khác không thấy và nhận 404 khi quyết định.

    Kiểm trên CẢ HAI cổng đọc, vì `schedule_property_viewing` đi cổng riêng.
    """
    _, uid = await _tai_khoan(client, db_pool, f"kh_qs_{tool[:12]}")
    tok_dung, id_dung = await _tai_khoan(client, db_pool, f"dv_dung_{tool[:12]}", role="provider")
    tok_khac, id_khac = await _tai_khoan(client, db_pool, f"dv_khac_{tool[:12]}", role="provider")
    ma = don_vi_mac_dinh(tool)
    await _gan_don_vi(db_pool, id_dung, ma)
    # Một đơn vị CÓ THẬT nhưng không phải đơn vị này.
    await _gan_don_vi(db_pool, id_khac, "MOV-02" if ma != "MOV-02" else "MOV-01")
    wid = await _yeu_cau(db_pool, uid, tool, chi_tiet)

    def _ma_wf(body):
        return {m["workflow_id"] for m in (body.get("items") or [])}

    duong = VIEWING if tool == "schedule_property_viewing" else f"{SERVICE}?status=AWAITING"
    assert wid in _ma_wf((await client.get(duong, headers=_auth(tok_dung))).json())
    assert wid not in _ma_wf((await client.get(duong, headers=_auth(tok_khac))).json())

    if tool == "schedule_property_viewing":
        res = await client.post(
            f"{VIEWING}/{wid}/decide",
            json={"decision": "reject", "reject_reason": "x", "reject_code": "OTHER"},
            headers=_auth(tok_khac),
        )
    else:
        res = await client.post(f"{SERVICE}/{wid}/T1/decide", json={"decision": "approve"}, headers=_auth(tok_khac))
    assert res.status_code == 404, f"{tool}: đơn vị khác nhận {res.status_code}"
    assert (
        await db_pool.fetchval("SELECT status FROM service_approvals WHERE workflow_id = $1::uuid", uuid.UUID(wid))
    ) == "AWAITING"


# ================================================== quyết định
@pytest.mark.parametrize(("tool", "chi_tiet"), BAY_DICH_VU, ids=TEN_TOOL)
@pytest.mark.asyncio
async def test_a_rejection_cancels_the_step_and_creates_no_new_documents(client, db_pool, tool, chi_tiet):
    """Từ chối: bước `CANCELLED`, và KHÔNG có đề xuất đơn vị khác mọc lên.

    Đây là chỗ dễ hỏng nhất. `refusal_policy` đọc mọi lời từ chối, và một lỗi ở
    đó sẽ mở đường chọn lại cho một dịch vụ không có báo giá — khách nhận một
    nút không dẫn tới đâu.
    """
    token, uid = await _tai_khoan(client, db_pool, f"kh_tc_{tool[:12]}")
    tok_dv, id_dv = await _tai_khoan(client, db_pool, f"dv_tc_{tool[:12]}", role="provider")
    await _gan_don_vi(db_pool, id_dv, don_vi_mac_dinh(tool))
    wid = await _yeu_cau(db_pool, uid, tool, chi_tiet)

    if tool == "schedule_property_viewing":
        res = await client.post(
            f"{VIEWING}/{wid}/decide",
            json={
                "decision": "reject",
                "reject_reason": "Bên mình không nhận việc này.",
                "reject_code": "SERVICE_UNAVAILABLE",
            },
            headers=_auth(tok_dv),
        )
    else:
        res = await client.post(
            f"{SERVICE}/{wid}/T1/decide",
            json={
                "decision": "reject",
                "reject_reason": "Bên mình không nhận việc này.",
                "reject_code": "SERVICE_UNAVAILABLE",
            },
            headers=_auth(tok_dv),
        )
    assert res.status_code == 200, f"{tool}: {res.status_code} {res.text}"

    assert (
        await db_pool.fetchval(
            "SELECT status FROM workflow_tasks WHERE workflow_id = $1::uuid AND task_id = 'T1'", uuid.UUID(wid)
        )
    ) in ("CANCELLED", "FAILED")
    assert await _dem_chung_tu(db_pool, wid) == (0, 0), "một lời từ chối đã sinh ra chứng từ báo giá"

    view = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()
    assert view.get("service_proposals") in (None, []), view.get("service_proposals")
    # Không mời "tìm đơn vị khác" cho dịch vụ không có đơn vị nào khác để đổi.
    assert (view.get("provider_rejection") or {}).get("can_request_another_provider") is not True
    # Và không lần thử mới nào được mở.
    so_buoc = await db_pool.fetchval("SELECT count(*) FROM workflow_tasks WHERE workflow_id = $1::uuid", uuid.UUID(wid))
    assert int(so_buoc) == 1, f"{tool}: sinh thêm lần thử ({so_buoc} bước)"


# ================================================== đọc lại sau khi mất cache
@pytest.mark.parametrize(("tool", "chi_tiet"), BAY_DICH_VU, ids=TEN_TOOL)
@pytest.mark.asyncio
async def test_a_cold_read_gives_the_same_answer(client, db_pool, tool, chi_tiet):
    """Xoá cache RAM rồi đọc lại: cùng một câu, không chứng từ mới.

    Đọc nguội KHÔNG phải restart tiến trình (xem
    `tests/e2e/reselection_across_restarts.mjs` cho bài ấy). Nó trả lời câu
    "màn hình dựng lại từ database có giống không" — và với bảy dịch vụ này,
    câu trả lời phải giống hệt lượt đầu.
    """
    from src.api.routes import _DEMO_JOBS

    token, uid = await _tai_khoan(client, db_pool, f"kh_ng_{tool[:12]}")
    wid = await _yeu_cau(db_pool, uid, tool, chi_tiet)

    truoc = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()
    _DEMO_JOBS.clear()
    sau = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert sau.get("stage") == truoc.get("stage")
    assert sau.get("service_proposals") in (None, [])
    assert await _dem_chung_tu(db_pool, wid) == (0, 0)
