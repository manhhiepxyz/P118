"""Từ đề xuất tới hàng đợi của ĐÚNG đơn vị — qua HTTP thật, và sống qua restart.

Đây là vòng khép kín của tính năng: khách chọn, khách bấm, đúng một đơn vị thấy
việc. Ba bất biến ghép lại, và mỗi cái đã có file riêng — ở đây chúng phải cùng
đúng trên một dòng dữ liệu:

    D   xác nhận là một transaction, chủ sở hữu lấy từ chứng từ
    A   đơn vị chỉ thấy và chỉ quyết định được việc của mình
    B   chứng từ là bằng chứng đối chiếu được

"Restart" ở đây là một pool MỚI, không dùng lại kết nối nào của lượt trước —
cùng thứ một tiến trình thứ hai nhìn thấy. Nếu có gì sống trong bộ nhớ thì đây
là chỗ nó lộ ra.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.common.feature_flags import SERVICE_PROVIDER_MATCHING
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.db.proposal_repository import de_xuat_dang_cho, doc_de_xuat, trang_thai_hieu_luc
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.service_approval import ProviderProposalRequiredError, ServiceApprovalBoundary
from tests.test_db.conftest import _register_and_login, dang_nhap_don_vi

DE_XUAT = "/api/v1/service-proposals"
DUYET = "/api/v1/service-approvals"
YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _dsn() -> str:
    import os

    return os.environ["TEST_DATABASE_URL"]


class ConnectorBaoGia:
    def __init__(self, gia=None) -> None:
        self.gia = gia or {"MOV-01": 430_000, "MOV-02": 470_000, "MOV-03": 420_000}

    async def xin_bao_gia_chuyen_nha(self, service_provider_id, payload):
        so_tien = self.gia.get(service_provider_id)
        if so_tien is None:
            return StandardResult.fail("NO_AVAILABILITY", "bận")
        return StandardResult.ok(
            data={
                "external_quote_id": f"Q-{service_provider_id}-{uuid.uuid4().hex[:8]}",
                "service_provider_id": service_provider_id,
                "amount": so_tien,
                "currency": "VND",
                "valid_until": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            }
        )


class _KhongChayGiCa:
    async def execute(self, plan, workflow_id=None, **kw):
        return workflow_id or str(uuid.uuid4()), {}


@pytest.fixture
def da_bat(client, monkeypatch):
    """Cờ bật + connector báo giá tiêm sẵn, TRÊN repository của `client`.

    Không tự gắn repository: fixture `client` đã gắn một cái, và nó bọc pool
    trong một lớp `close()` rỗng. Bọc là bắt buộc — mọi route đóng pool trong
    `finally`, nên một repository thứ hai trỏ thẳng vào pool dùng chung sẽ đóng
    nó ngay sau request đầu tiên, và mọi test sau đó chết với "pool is closed".
    """
    monkeypatch.setenv(SERVICE_PROVIDER_MATCHING, "1")
    monkeypatch.setattr("src.connectors.resident_services.ResidentServicesConnector", lambda **_: ConnectorBaoGia())
    return client


async def _khach_va_de_xuat(client, db_pool, ten: str) -> tuple[str, str, str]:
    """Khách thật đăng nhập, chạy qua cổng dịch vụ, nhận một đề xuất đang chờ."""
    token = await _register_and_login(client, ten)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", ten)
    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'chuyển nhà', 'PENDING', $2)",
        wid,
        uid,
    )
    plan = TaskPlan(
        goal="chuyển nhà",
        tasks=[Task(task_id="T1", tool="schedule_move", input=dict(YEU_CAU), depends_on=[])],
    )
    boundary = ServiceApprovalBoundary(_KhongChayGiCa(), approved=False, repository=await acquire_repository())
    with pytest.raises(ProviderProposalRequiredError):
        await boundary.execute(plan, wid)
    de_xuat = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")
    return token, wid, de_xuat.proposal_id


@pytest.mark.asyncio
async def test_before_confirming_no_provider_sees_anything(client, db_pool, da_bat):
    """MỌI đơn vị đều thấy hàng đợi rỗng — kể cả đơn vị được đề xuất.

    Đề xuất chưa phải cam kết. Nếu MOV-03 đã thấy việc lúc này thì họ có thể
    bấm duyệt cho một yêu cầu khách chưa đồng ý.
    """
    _, wid, _ = await _khach_va_de_xuat(client, db_pool, "kh_truoc_bam")

    for ma in ("MOV-01", "MOV-02", "MOV-03"):
        tok, _ = await dang_nhap_don_vi(client, db_pool, f"dv_{ma.lower().replace('-', '')}", don_vi=(ma,))
        hang_doi = (await client.get(DUYET, headers=_auth(tok))).json()["items"]
        assert [i for i in hang_doi if i["workflow_id"] == wid] == [], f"{ma} đã thấy việc"


@pytest.mark.asyncio
async def test_after_confirming_only_the_chosen_provider_sees_it(client, db_pool, da_bat):
    """Sau lượt bấm: MOV-03 thấy việc, hai đơn vị kia không."""
    token, wid, proposal_id = await _khach_va_de_xuat(client, db_pool, "kh_sau_bam")

    res = await client.post(f"{DE_XUAT}/{proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(token))
    assert res.status_code == 200, res.text
    assert res.json()["provider"]["id"] == "MOV-03"

    tok_chon, _ = await dang_nhap_don_vi(client, db_pool, "dv_duoc_chon", don_vi=("MOV-03",))
    thay = (await client.get(DUYET, headers=_auth(tok_chon))).json()["items"]
    cua_toi = [i for i in thay if i["workflow_id"] == wid]
    assert len(cua_toi) == 1 and cua_toi[0]["status"] == "AWAITING"

    for ma in ("MOV-01", "MOV-02"):
        tok, _ = await dang_nhap_don_vi(client, db_pool, f"dv_khong_{ma[-2:]}", don_vi=(ma,))
        khac = (await client.get(DUYET, headers=_auth(tok))).json()["items"]
        assert [i for i in khac if i["workflow_id"] == wid] == [], f"{ma} thấy việc của MOV-03"


@pytest.mark.asyncio
async def test_a_provider_that_was_not_chosen_gets_404_on_decide(client, db_pool, da_bat):
    """Gọi thẳng endpoint quyết định, vòng qua danh sách → 404, và dòng không đổi.

    Kẻ tấn công không đi qua danh sách. 404 chứ không 403: 403 xác nhận rằng
    dòng ấy có tồn tại.
    """
    token, wid, proposal_id = await _khach_va_de_xuat(client, db_pool, "kh_provider_khac")
    await client.post(f"{DE_XUAT}/{proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(token))
    tok_khac, _ = await dang_nhap_don_vi(client, db_pool, "dv_khong_duoc_chon", don_vi=("MOV-01",))

    res = await client.post(
        f"{DUYET}/{wid}/T1/decide",
        json={"decision": "reject", "reject_code": "SERVICE_UNAVAILABLE", "reject_reason": "không phải việc của tôi"},
        headers=_auth(tok_khac),
    )

    assert res.status_code == 404, res.text
    dong = await db_pool.fetchrow(
        "SELECT status, decided_by, service_provider_id FROM service_approvals WHERE workflow_id=$1::uuid",
        uuid.UUID(wid),
    )
    assert (dong["status"], dong["decided_by"], dong["service_provider_id"]) == ("AWAITING", None, "MOV-03")


@pytest.mark.asyncio
async def test_the_chosen_provider_can_decide(client, db_pool, da_bat):
    """Kiểm DƯƠNG — thiếu nó thì mọi 404 ở trên có thể đúng vì route hỏng."""
    token, wid, proposal_id = await _khach_va_de_xuat(client, db_pool, "kh_dv_quyet")
    await client.post(f"{DE_XUAT}/{proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(token))
    tok, _ = await dang_nhap_don_vi(client, db_pool, "dv_quyet_dinh", don_vi=("MOV-03",))

    res = await client.post(
        f"{DUYET}/{wid}/T1/decide",
        json={"decision": "reject", "reject_code": "SERVICE_UNAVAILABLE", "reject_reason": "đang bảo trì xe"},
        headers=_auth(tok),
    )

    assert res.status_code == 200, res.text
    assert (
        await db_pool.fetchval("SELECT status FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid))
        == "REJECTED"
    )


# ------------------------------------------------------------------- restart
@pytest.mark.asyncio
async def test_a_proposal_survives_a_restart_before_confirming(client, db_pool, da_bat):
    """Pool MỚI đọc lại: đề xuất còn đó và `can_confirm` vẫn `True`.

    Nếu có bất cứ thứ gì sống trong bộ nhớ thì đây là chỗ nó lộ ra — một tiến
    trình thứ hai không chia sẻ gì với tiến trình đầu ngoài database.
    """
    _, wid, proposal_id = await _khach_va_de_xuat(client, db_pool, "kh_restart_truoc")

    pool_moi = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
    try:
        doc_lai = await doc_de_xuat(pool_moi, proposal_id)
        assert doc_lai is not None and doc_lai.status == "PROPOSED"
        assert await trang_thai_hieu_luc(pool_moi, doc_lai) == ("PROPOSED", True)
        assert (
            await pool_moi.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid))
            == 0
        )
    finally:
        await pool_moi.close()


@pytest.mark.asyncio
async def test_a_confirmed_request_still_waits_for_the_right_provider_after_a_restart(client, db_pool, da_bat):
    """Sau restart, việc vẫn nằm ở ĐÚNG đơn vị — chủ sở hữu đến từ chứng từ."""
    token, wid, proposal_id = await _khach_va_de_xuat(client, db_pool, "kh_restart_sau")
    await client.post(f"{DE_XUAT}/{proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(token))

    pool_moi = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
    try:
        doc_lai = await doc_de_xuat(pool_moi, proposal_id)
        assert doc_lai.status == "CONFIRMED"
        assert await trang_thai_hieu_luc(pool_moi, doc_lai) == ("CONFIRMED", False)
        dong = await pool_moi.fetchrow(
            "SELECT status, service_provider_id FROM service_approvals WHERE workflow_id=$1::uuid",
            uuid.UUID(wid),
        )
        assert (dong["status"], dong["service_provider_id"]) == ("AWAITING", "MOV-03")
    finally:
        await pool_moi.close()


@pytest.mark.asyncio
async def test_running_the_boundary_again_after_a_restart_reuses_the_same_proposal(client, db_pool, da_bat):
    """Chạy tiếp sau restart dùng lại ĐÚNG đề xuất đã persist, không dựng cái mới."""
    _, wid, proposal_id = await _khach_va_de_xuat(client, db_pool, "kh_restart_chay_tiep")

    plan = TaskPlan(
        goal="chuyển nhà",
        tasks=[Task(task_id="T1", tool="schedule_move", input=dict(YEU_CAU), depends_on=[])],
    )
    boundary = ServiceApprovalBoundary(_KhongChayGiCa(), approved=False, repository=await acquire_repository())
    with pytest.raises(ProviderProposalRequiredError) as loi:
        await boundary.execute(plan, wid)

    assert (loi.value.context or {})["provider_proposals"][0]["proposal_id"] == proposal_id
    assert (
        await db_pool.fetchval(
            "SELECT count(*) FROM service_provider_proposals WHERE workflow_id=$1::uuid", uuid.UUID(wid)
        )
        == 1
    )
