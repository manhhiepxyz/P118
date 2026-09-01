"""Đơn vị từ chối → khách CHỦ ĐỘNG tìm đơn vị khác. Không tự chuyển.

Cám dỗ lớn nhất khi đơn vị từ chối là lặng lẽ hỏi giá lại và đề xuất đơn vị
tiếp theo. Nó sai theo ba cách cùng lúc:

  * Khách không biết lời từ chối đã xảy ra. Họ đồng ý với "Đại Tín, 470.000" và
    một lát sau nhận hoá đơn của một công ty khác với một con số khác.
  * Lý do từ chối chết trong database. "Hết xe ngày ấy" có thể đổi quyết định
    của khách — họ có thể muốn đổi NGÀY thay vì đổi đơn vị.
  * Một chuỗi từ chối liên tiếp thành một vòng lặp tự động không ai bấm dừng.

Nên: giữ nguyên mọi bằng chứng, NÓI RA lý do, và chờ một lượt bấm.

RANH GIỚI với luồng sửa lỗi đã có
---------------------------------
Không phải lời từ chối nào cũng dẫn tới "tìm đơn vị khác". `NO_AVAILABILITY`
trên `schedule_move` đã có một đường TỐT HƠN: hệ thống hỏi khách một ngày khác
(`repair.py` map nó sang `move_date`/`move_time`). Đơn vị vẫn nhận việc, chỉ là
không nhận ngày ấy — và đổi ngày rẻ hơn đổi đơn vị.

Chọn lại đơn vị là đường cho lời từ chối KHÔNG CÓ Ô NÀO ĐỂ SỬA:
`SERVICE_UNAVAILABLE`, `INVALID_REQUEST`, `OTHER`. Đó là lúc đơn vị nói "chúng
tôi không làm được việc này", và thứ thay thế được là chính họ.

Đo được khi chưa có ranh giới này: bốn bài kiểm của luồng sửa lỗi cũ chuyển
sang đỏ, vì màn hình bắt đầu mời "tìm đơn vị khác" cho một yêu cầu chỉ cần đổi
ngày.

Và lần thử MỚI, không mở lại lần cũ. `service_approvals` của T1 mang
`REJECTED`, `reject_code`, lý do và chữ ký người quyết định — đó là một sự kiện
đã xảy ra, và ghi đè nó là xoá dấu vết một quyết định thật.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.common.feature_flags import SERVICE_PROVIDER_MATCHING
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.db.proposal_repository import de_xuat_dang_cho, xac_nhan_de_xuat
from src.orchestration.provider_reselection import (
    KetQuaChonLai,
    don_vi_da_tu_choi,
    goc_lan_thu,
    loi_tu_choi_dang_cho_khach,
    mo_lan_chon_lai,
)
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.service_approval import ProviderProposalRequiredError, ServiceApprovalBoundary
from tests.test_db.conftest import _register_and_login, dang_nhap_don_vi

DEMO = "/api/v1/workflows/demo"
DUYET = "/api/v1/service-approvals"
YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}
LY_DO = "Đội xe bên mình đang bảo trì toàn bộ, xin phép từ chối."


def _auth(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


class ConnectorBaoGia:
    """Ba đơn vị, giá cố định. MOV-03 rẻ nhất, rồi MOV-01, rồi MOV-02."""

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
    monkeypatch.setenv(SERVICE_PROVIDER_MATCHING, "1")
    monkeypatch.setattr("src.connectors.resident_services.ResidentServicesConnector", lambda **_: ConnectorBaoGia())
    return client


async def _den_luc_bi_tu_choi(client, db_pool, ten: str, *, ly_do: str = LY_DO, ma: str = "SERVICE_UNAVAILABLE"):
    """Chạy tới trạng thái: khách đã đồng ý, và ĐƠN VỊ vừa từ chối."""
    token = await _register_and_login(client, ten)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", ten)
    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'chuyển nhà', 'PENDING', $2)",
        wid,
        uid,
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', 'schedule_move', 'PENDING', '[]'::jsonb, $2::jsonb)",
        wid,
        json.dumps(YEU_CAU),
    )
    boundary = ServiceApprovalBoundary(_KhongChayGiCa(), approved=False, repository=await acquire_repository())
    plan = TaskPlan(
        goal="chuyển nhà",
        tasks=[Task(task_id="T1", tool="schedule_move", input=dict(YEU_CAU), depends_on=[])],
    )
    with pytest.raises(ProviderProposalRequiredError):
        await boundary.execute(plan, wid)

    de_xuat = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")
    await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=str(uid))
    ma_don_vi = await db_pool.fetchval(
        "SELECT service_provider_id FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid)
    )
    tok_dv, _ = await dang_nhap_don_vi(
        client, db_pool, f"dv_{ten}_{ma_don_vi.lower().replace('-', '')}", don_vi=(ma_don_vi,)
    )
    res = await client.post(
        f"{DUYET}/{wid}/T1/decide",
        json={"decision": "reject", "reject_code": ma, "reject_reason": ly_do},
        headers=_auth(tok_dv),
    )
    assert res.status_code == 200, res.text
    return token, str(uid), wid, ma_don_vi, de_xuat


async def _dem(db_pool, wid):
    return {
        "tasks": await db_pool.fetchval(
            "SELECT count(*) FROM workflow_tasks WHERE workflow_id=$1::uuid", uuid.UUID(wid)
        ),
        "quotes": await db_pool.fetchval(
            "SELECT count(*) FROM service_quotes WHERE workflow_id=$1::uuid", uuid.UUID(wid)
        ),
        "proposals": await db_pool.fetchval(
            "SELECT count(*) FROM service_provider_proposals WHERE workflow_id=$1::uuid", uuid.UUID(wid)
        ),
        "approvals": await db_pool.fetchval(
            "SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid)
        ),
    }


# ==================================================== 1. từ chối giữ bằng chứng
@pytest.mark.asyncio
async def test_a_rejection_changes_nothing_it_should_not(client, db_pool, da_bat):
    """Đơn vị từ chối → mọi bằng chứng giữ nguyên, KHÔNG có gì mới được tạo.

    Chứng từ và đề xuất vẫn `CONFIRMED`: khách ĐÃ đồng ý, và lời từ chối không
    xoá được việc đó. Dòng duyệt vẫn `REJECTED` với mã, lý do và chữ ký.
    """
    _, _, wid, ma, de_xuat = await _den_luc_bi_tu_choi(client, db_pool, "kh_tu_choi_giu")

    dem = await _dem(db_pool, wid)
    assert dem == {"tasks": 1, "quotes": 3, "proposals": 1, "approvals": 1}, dem
    dong = await db_pool.fetchrow(
        "SELECT status, reject_code, reject_reason, decided_by, service_provider_id "
        "FROM service_approvals WHERE workflow_id=$1::uuid",
        uuid.UUID(wid),
    )
    assert dong["status"] == "REJECTED"
    assert dong["reject_code"] == "SERVICE_UNAVAILABLE"
    assert dong["reject_reason"] == LY_DO
    assert dong["decided_by"], "quyết định không ghi ai đã ký"
    assert dong["service_provider_id"] == ma
    assert (
        await db_pool.fetchval(
            "SELECT status FROM service_provider_proposals WHERE proposal_id=$1::uuid",
            uuid.UUID(de_xuat.proposal_id),
        )
        == "CONFIRMED"
    )
    assert (
        await db_pool.fetchval(
            "SELECT q.status FROM service_quotes q JOIN service_provider_proposals p ON p.quote_id=q.quote_id "
            "WHERE p.proposal_id=$1::uuid",
            uuid.UUID(de_xuat.proposal_id),
        )
        == "CONFIRMED"
    )


@pytest.mark.asyncio
async def test_a_rejection_never_opens_a_new_attempt_by_itself(client, db_pool, da_bat):
    """Không tự chuyển đơn vị. Poll bao nhiêu lượt cũng không sinh lần thử mới.

    Đây là luật quan trọng nhất của cả bước này. Tự chuyển nghĩa là khách nhận
    hoá đơn của một công ty họ chưa từng chọn.
    """
    from src.api.routes import _DEMO_JOBS

    token, _, wid, _, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_khong_tu_chuyen")
    truoc = await _dem(db_pool, wid)

    for _ in range(3):
        _DEMO_JOBS.clear()
        assert (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).status_code == 200

    assert await _dem(db_pool, wid) == truoc


@pytest.mark.asyncio
async def test_the_customer_reads_the_real_reason_and_never_the_word_waiting(client, db_pool, da_bat):
    """Khách thấy LÝ DO THẬT và một hành động, không thấy "đang chờ đơn vị".

    Câu "đang chờ đơn vị cung cấp dịch vụ xác nhận" đã hết đúng từ lúc đơn vị
    bấm từ chối — không ai đang chờ, việc đã dừng, và khách là người duy nhất
    còn phải quyết định.
    """
    from src.api.routes import _DEMO_JOBS

    token, _, wid, ma, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_doc_ly_do")
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert body["stage"] == "WAITING_PROVIDER_RESELECTION", body["stage"]
    assert body["approval_actor"] == "USER"
    tu_choi = body["provider_rejection"]
    assert tu_choi["rejected_task_id"] == "T1"
    assert tu_choi["rejected_provider"]["id"] == ma
    assert tu_choi["rejected_provider"]["name"] and tu_choi["rejected_provider"]["name"] != ma
    assert tu_choi["reject_code"] == "SERVICE_UNAVAILABLE"
    assert tu_choi["sanitized_reason"] == LY_DO
    assert tu_choi["can_request_another_provider"] is True
    for truong in ("message", "summary", "answer"):
        cau = (body.get(truong) or "").lower()
        assert cau, f"{truong} rỗng"
        assert "đang chờ đơn vị" not in cau, f"{truong}: {body[truong]}"
    assert LY_DO in body["summary"], "lý do thật không tới được màn hình"
    assert body["status"] != "SUCCESS"


@pytest.mark.asyncio
async def test_the_reason_is_cleaned_but_not_censored(client, db_pool, da_bat):
    """Ký tự điều khiển bị cắt; nội dung nghiệp vụ giữ nguyên.

    Câu này do NGƯỜI của đơn vị gõ và đi thẳng tới một người khác. Lọc nội dung
    thì hệ thống đang biên tập lời của một bên thứ ba; không lọc ký tự điều
    khiển thì nó đưa một chuỗi thô ra màn hình.
    """
    from src.api.routes import _DEMO_JOBS

    ban = "Xe   hỏng\tđột xuất,\n xin lỗi bạn."
    token, _, wid, _, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_lam_sach", ly_do=ban)
    _DEMO_JOBS.clear()

    tu_choi = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()["provider_rejection"]

    assert tu_choi["sanitized_reason"] == "Xe hỏng đột xuất, xin lỗi bạn."


# ==================================================== 2. quyền
@pytest.mark.asyncio
async def test_only_the_owner_can_ask_for_another_provider(client, db_pool, da_bat):
    """Người khác 404; đơn vị và admin 403.

    404 chứ không 403 với khách khác: 403 xác nhận rằng workflow ấy có thật.
    """
    _, _, wid, _, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_chu_that_f")
    duong = f"/api/v1/service-proposals/workflows/{wid}/request-another-provider"

    khach_khac = await _register_and_login(client, "kh_nguoi_la_f")
    assert (await client.post(duong, json={"task_id": "T1"}, headers=_auth(khach_khac))).status_code == 404

    tok_dv, _ = await dang_nhap_don_vi(client, db_pool, "dv_bam_ho_f", don_vi=("MOV-01",))
    assert (await client.post(duong, json={"task_id": "T1"}, headers=_auth(tok_dv))).status_code == 403

    await _register_and_login(client, "qt_bam_ho_f")
    await db_pool.execute("UPDATE users SET role='admin' WHERE username='qt_bam_ho_f'")
    tok_qt = await _register_and_login(client, "qt_bam_ho_f")
    assert (await client.post(duong, json={"task_id": "T1"}, headers=_auth(tok_qt))).status_code == 403

    assert (await _dem(db_pool, wid))["tasks"] == 1, "một lượt bị chặn vẫn mở lần thử mới"


@pytest.mark.asyncio
async def test_the_body_cannot_name_a_provider_or_a_price(client, db_pool, da_bat):
    """Client gửi kèm đơn vị hay giá → 422.

    Đơn vị nào được đề xuất là kết quả của luật chọn trên tập còn lại, không
    phải của một tham số — và một tham số nhận được thì sớm muộn sẽ có người
    tin nó.
    """
    token, _, wid, _, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_body_gia_f")
    duong = f"/api/v1/service-proposals/workflows/{wid}/request-another-provider"

    for than in (
        {"task_id": "T1", "service_provider_id": "MOV-02"},
        {"task_id": "T1", "amount": 1_000},
        {"task_id": "T1", "exclude": ["MOV-01"]},
        {},
    ):
        assert (await client.post(duong, json=than, headers=_auth(token))).status_code == 422, than
    assert (await _dem(db_pool, wid))["tasks"] == 1


@pytest.mark.asyncio
async def test_a_task_that_was_not_rejected_cannot_be_reselected(client, db_pool, da_bat):
    """Chưa bị từ chối thì không có gì để chọn lại — mở lần thử ở đây nghĩa là
    huỷ một yêu cầu đang chờ đơn vị quyết định."""
    from src.api.routes import _DEMO_JOBS

    token = await _register_and_login(client, "kh_chua_tu_choi")
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username = 'kh_chua_tu_choi'")
    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'chuyển nhà', 'PENDING', $2)",
        wid,
        uid,
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', 'schedule_move', 'PENDING', '[]'::jsonb, $2::jsonb)",
        wid,
        json.dumps(YEU_CAU),
    )
    _DEMO_JOBS.clear()

    res = await client.post(
        f"/api/v1/service-proposals/workflows/{wid}/request-another-provider",
        json={"task_id": "T1"},
        headers=_auth(token),
    )

    assert res.status_code == 409, res.text
    assert (await _dem(db_pool, wid))["tasks"] == 1


# ==================================================== 3. lần thử mới
@pytest.mark.asyncio
async def test_the_customer_action_opens_exactly_one_new_attempt(client, db_pool, da_bat):
    """Bấm → T1 thành CANCELLED, T1R2 ra đời với CÙNG input, và có đề xuất mới.

    Dòng duyệt của T1 KHÔNG bị đụng tới: nó là bằng chứng của một quyết định đã
    xảy ra. Và T1R2 CHƯA có dòng duyệt nào — hàng đợi chỉ mở sau khi khách đồng
    ý với đơn vị mới.
    """
    token, _, wid, bi_tu_choi, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_mo_lan_moi")

    res = await client.post(
        f"/api/v1/service-proposals/workflows/{wid}/request-another-provider",
        json={"task_id": "T1"},
        headers=_auth(token),
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "PROPOSED"
    assert body["new_task_id"] == "T1R2"
    assert body["approval_actor"] == "USER"

    buoc = {
        r["task_id"]: r["status"]
        for r in await db_pool.fetch(
            "SELECT task_id, status FROM workflow_tasks WHERE workflow_id=$1::uuid", uuid.UUID(wid)
        )
    }
    assert buoc == {"T1": "CANCELLED", "T1R2": "WAITING_APPROVAL"}, buoc
    assert await db_pool.fetchval(
        "SELECT input_data FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1R2'",
        uuid.UUID(wid),
    ), "lần thử mới mất input nghiệp vụ"

    cu = await db_pool.fetchrow(
        "SELECT status, reject_reason FROM service_approvals WHERE workflow_id=$1::uuid AND task_id='T1'",
        uuid.UUID(wid),
    )
    assert (cu["status"], cu["reject_reason"]) == ("REJECTED", LY_DO), "bằng chứng cũ bị ghi đè"
    assert (
        await db_pool.fetchval(
            "SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid AND task_id='T1R2'",
            uuid.UUID(wid),
        )
        == 0
    ), "mở hàng đợi cho đơn vị mới trước khi khách đồng ý"

    moi = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1R2")
    assert moi is not None and moi.task_id == "T1R2"
    ma_moi = await db_pool.fetchval(
        "SELECT service_provider_id FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(moi.quote_id)
    )
    assert ma_moi != bi_tu_choi, "đề xuất lại chính đơn vị vừa từ chối"


@pytest.mark.asyncio
async def test_the_rejected_provider_is_excluded_from_the_persisted_record(client, db_pool, da_bat):
    """Tập loại trừ đọc từ DỮ LIỆU, và phủ cả chuỗi lần thử."""
    _, _, wid, ma, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_loai_tru")

    loai = await don_vi_da_tu_choi(db_pool, workflow_id=wid, task_id="T1")
    assert loai == frozenset({ma})
    # Cùng việc, lần thử khác — tập loại trừ phải giống nhau.
    assert await don_vi_da_tu_choi(db_pool, workflow_id=wid, task_id="T1R2") == frozenset({ma})
    assert goc_lan_thu("T1R3") == "T1"


@pytest.mark.asyncio
async def test_pressing_twice_opens_only_one_attempt(client, db_pool, da_bat):
    """Bấm đúp → một lần thử, và lượt thứ hai nói rõ nó đến muộn.

    200 chứ không 409: lượt thứ hai không làm gì thêm, và thứ khách cần thấy là
    đề xuất đã có. Trả lỗi biến một cú bấm đúp thành thông báo đỏ cho một việc
    đã thành công.
    """
    token, _, wid, _, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_bam_dup_f")
    duong = f"/api/v1/service-proposals/workflows/{wid}/request-another-provider"

    dau = await client.post(duong, json={"task_id": "T1"}, headers=_auth(token))
    lai = await client.post(duong, json={"task_id": "T1"}, headers=_auth(token))

    assert dau.status_code == 200 and dau.json()["outcome"] == "PROPOSED"
    assert lai.status_code == 200, lai.text
    assert lai.json()["outcome"] == "ALREADY_REOPENED"
    dem = await _dem(db_pool, wid)
    assert dem["tasks"] == 2, dem
    assert dem["proposals"] == 2, dem


@pytest.mark.asyncio
async def test_when_every_provider_has_refused_nobody_is_resurrected(client, db_pool, da_bat):
    """Ba đơn vị lần lượt từ chối → `NO_ALTERNATIVE_PROVIDER`, không hồi sinh ai.

    Chạy HẾT vòng ba lần thật, không gieo tắt: chỉ có đi qua chuỗi
    `T1 → T1R2 → T1R3` mới kiểm được rằng tập loại trừ phủ CẢ CHUỖI. Gieo ba
    dòng từ chối trên ba `task_id` rời nhau sẽ xanh mà không chứng minh gì —
    `goc_lan_thu` gom theo phần gốc, và ba id rời nhau là ba việc khác nhau.

    Đề xuất lại một đơn vị đã nói không sẽ bị từ chối lần nữa, và lần này khách
    mất thêm một vòng chờ.
    """
    token, uid, wid, dau_tien, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_het_don_vi")
    duong = f"/api/v1/service-proposals/workflows/{wid}/request-another-provider"
    da_tu_choi = [dau_tien]

    for buoc_cu, buoc_moi in (("T1", "T1R2"), ("T1R2", "T1R3")):
        res = await client.post(duong, json={"task_id": buoc_cu}, headers=_auth(token))
        assert res.status_code == 200, res.text
        assert res.json()["new_task_id"] == buoc_moi

        de_xuat = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id=buoc_moi)
        ma = await db_pool.fetchval(
            "SELECT service_provider_id FROM service_quotes WHERE quote_id=$1::uuid",
            uuid.UUID(de_xuat.quote_id),
        )
        assert ma not in da_tu_choi, f"{buoc_moi} đề xuất lại đơn vị đã từ chối: {ma}"
        da_tu_choi.append(ma)

        await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=uid)
        tok_dv, _ = await dang_nhap_don_vi(client, db_pool, f"dv_vong_{ma.lower().replace('-', '')}", don_vi=(ma,))
        assert (
            await client.post(
                f"{DUYET}/{wid}/{buoc_moi}/decide",
                json={
                    "decision": "reject",
                    "reject_code": "SERVICE_UNAVAILABLE",
                    "reject_reason": "bên mình cũng không nhận được",
                },
                headers=_auth(tok_dv),
            )
        ).status_code == 200

    assert len(set(da_tu_choi)) == 3, da_tu_choi
    assert await don_vi_da_tu_choi(db_pool, workflow_id=wid, task_id="T1R3") == frozenset(da_tu_choi)

    het = await client.post(duong, json={"task_id": "T1R3"}, headers=_auth(token))

    assert het.status_code == 409, het.text
    assert "không còn đơn vị nào khác" in het.json()["detail"].lower()
    assert (await _dem(db_pool, wid))["tasks"] == 3, "hết đơn vị mà vẫn mở lần thử thứ tư"
    assert (
        await db_pool.fetchval(
            "SELECT count(*) FROM service_provider_proposals WHERE workflow_id=$1::uuid AND status='PROPOSED'",
            uuid.UUID(wid),
        )
        == 0
    ), "dựng một đề xuất giả khi đã hết đơn vị"


# ==================================================== 4. hết vòng
@pytest.mark.asyncio
async def test_the_second_provider_only_sees_it_after_the_customer_agrees(client, db_pool, da_bat):
    """Đề xuất mới → khách bấm → CHỈ đơn vị mới thấy việc → approve → SUCCESS."""
    from src.api.routes import _DEMO_JOBS

    token, uid, wid, bi_tu_choi, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_het_vong")
    await client.post(
        f"/api/v1/service-proposals/workflows/{wid}/request-another-provider",
        json={"task_id": "T1"},
        headers=_auth(token),
    )
    moi = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1R2")
    ma_moi = await db_pool.fetchval(
        "SELECT service_provider_id FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(moi.quote_id)
    )

    _DEMO_JOBS.clear()
    truoc = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()
    assert truoc["stage"] == "WAITING_PROVIDER_PROPOSAL", truoc["stage"]
    assert [d["task_id"] for d in truoc["service_proposals"]] == ["T1R2"]
    assert truoc["provider_rejection"] is None, "lời từ chối cũ vẫn hiện cạnh đề xuất mới"

    tok_moi, _ = await dang_nhap_don_vi(client, db_pool, f"dv_moi_{ma_moi.lower().replace('-', '')}", don_vi=(ma_moi,))
    thay = [
        i
        for i in (await client.get(DUYET, headers=_auth(tok_moi))).json()["items"]
        if i["workflow_id"] == wid and i["task_id"] == "T1R2"
    ]
    assert thay == [], "đơn vị mới đã thấy việc trước khi khách đồng ý"

    res = await client.post(
        f"/api/v1/service-proposals/{moi.proposal_id}/confirm",
        json={"decision": "confirm"},
        headers=_auth(token),
    )
    assert res.status_code == 200, res.text
    dong = await db_pool.fetch(
        "SELECT task_id, status, service_provider_id FROM service_approvals "
        "WHERE workflow_id=$1::uuid ORDER BY task_id",
        uuid.UUID(wid),
    )
    assert [(r["task_id"], r["status"], r["service_provider_id"]) for r in dong] == [
        ("T1", "REJECTED", bi_tu_choi),
        ("T1R2", "AWAITING", ma_moi),
    ]

    tok_cu, _ = await dang_nhap_don_vi(
        client, db_pool, f"dv_cu_{bi_tu_choi.lower().replace('-', '')}", don_vi=(bi_tu_choi,)
    )
    cua_cu = [
        i
        for i in (await client.get(DUYET, headers=_auth(tok_cu))).json()["items"]
        if i["workflow_id"] == wid and i["task_id"] == "T1R2"
    ]
    assert cua_cu == [], "đơn vị đã từ chối vẫn thấy lần thử mới"


@pytest.mark.asyncio
async def test_the_history_keeps_both_attempts_in_their_own_roles(client, db_pool, da_bat):
    """T1 và T1R2 cùng tồn tại, mỗi cái mang đúng vai của nó."""
    token, _, wid, _, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_lich_su")
    await client.post(
        f"/api/v1/service-proposals/workflows/{wid}/request-another-provider",
        json={"task_id": "T1"},
        headers=_auth(token),
    )

    buoc = {
        r["task_id"]: r["status"]
        for r in await db_pool.fetch(
            "SELECT task_id, status FROM workflow_tasks WHERE workflow_id=$1::uuid", uuid.UUID(wid)
        )
    }
    assert buoc == {"T1": "CANCELLED", "T1R2": "WAITING_APPROVAL"}
    de_xuat = {
        r["task_id"]: r["status"]
        for r in await db_pool.fetch(
            "SELECT task_id, status FROM service_provider_proposals WHERE workflow_id=$1::uuid",
            uuid.UUID(wid),
        )
    }
    assert de_xuat == {"T1": "CONFIRMED", "T1R2": "PROPOSED"}


# ============================================ 5. đọc nguội (KHÔNG phải restart)
@pytest.mark.asyncio
async def test_a_cold_read_at_every_gap_stays_on_the_same_state(client, db_pool, da_bat):
    """Ba khe, ba lần ĐỌC NGUỘI — không khe nào sinh thêm gì.

    Đọc nguội KHÔNG phải restart tiến trình. `_DEMO_JOBS.clear()` chỉ xoá cache
    trong RAM; tiến trình vẫn sống, nên mọi thứ dựng lúc startup — pool, migration,
    biến cấu hình đã đọc — vẫn nguyên. Bài này trả lời "màn hình có dựng lại được
    từ database không", và đó là một câu hỏi thật, đáng giữ.

    Câu nó KHÔNG trả lời được là "một tiến trình THỨ HAI đọc dữ liệu này thì thấy
    gì". Chỉ `tests/e2e/reselection_across_restarts.mjs` trả lời được — nó giết
    backend và khởi động lại ở đúng ba khe này.
    """
    from src.api.routes import _DEMO_JOBS

    token, _, wid, _, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_ba_khe")

    # Khe 1: sau khi bị từ chối, trước khi khách bấm.
    _DEMO_JOBS.clear()
    a = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()
    assert a["stage"] == "WAITING_PROVIDER_RESELECTION"
    sau_a = await _dem(db_pool, wid)

    await client.post(
        f"/api/v1/service-proposals/workflows/{wid}/request-another-provider",
        json={"task_id": "T1"},
        headers=_auth(token),
    )
    # Khe 2: sau khi bấm, trước khi đồng ý với đề xuất mới.
    _DEMO_JOBS.clear()
    b = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()
    assert b["stage"] == "WAITING_PROVIDER_PROPOSAL"
    sau_b = await _dem(db_pool, wid)
    _DEMO_JOBS.clear()
    assert (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()["service_proposals"][0]["proposal_id"] == b[
        "service_proposals"
    ][0]["proposal_id"]
    assert await _dem(db_pool, wid) == sau_b

    moi = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1R2")
    await client.post(
        f"/api/v1/service-proposals/{moi.proposal_id}/confirm",
        json={"decision": "confirm"},
        headers=_auth(token),
    )
    # Khe 3: sau khi đồng ý, trước khi đơn vị thứ hai quyết định.
    _DEMO_JOBS.clear()
    c = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()
    sau_c = await _dem(db_pool, wid)
    _DEMO_JOBS.clear()
    assert (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).status_code == 200
    assert await _dem(db_pool, wid) == sau_c
    assert c["approval_actor"] == "PROVIDER", c["approval_actor"]
    assert sau_a["tasks"] == 1 and sau_b["tasks"] == 2 and sau_c["tasks"] == 2


@pytest.mark.asyncio
async def test_the_rejection_disappears_from_the_reader_once_it_is_handled(client, db_pool, da_bat):
    """Sau khi mở lần thử mới, lời từ chối cũ không còn là việc đang chờ.

    Để cả hai cùng hiện nghĩa là màn hình có hai việc trong khi thật ra chỉ có
    một — và khách sẽ bấm "tìm đơn vị khác" thêm lần nữa.
    """
    token, _, wid, _, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_an_loi_cu")
    assert await loi_tu_choi_dang_cho_khach(db_pool, workflow_id=wid) is not None

    await client.post(
        f"/api/v1/service-proposals/workflows/{wid}/request-another-provider",
        json={"task_id": "T1"},
        headers=_auth(token),
    )

    assert await loi_tu_choi_dang_cho_khach(db_pool, workflow_id=wid) is None


@pytest.mark.asyncio
async def test_the_domain_call_is_idempotent_too(client, db_pool, da_bat):
    """Gọi thẳng hàm domain hai lần cũng chỉ mở một lần thử.

    Endpoint đã có bài kiểm riêng; đây là hàng rào ở tầng dưới, để một đường
    gọi MỚI không phải tự nhớ luật.
    """
    _, uid, wid, _, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_domain_idem")
    repository = await acquire_repository()
    connector = ConnectorBaoGia()

    dau = await mo_lan_chon_lai(db_pool, repository, connector, workflow_id=wid, task_id="T1", owner_user_id=uid)
    lai = await mo_lan_chon_lai(db_pool, repository, connector, workflow_id=wid, task_id="T1", owner_user_id=uid)

    assert dau.ket_qua is KetQuaChonLai.PROPOSED
    assert lai.ket_qua is KetQuaChonLai.ALREADY_REOPENED
    assert (await _dem(db_pool, wid))["tasks"] == 2


# ==================================================== 6. terminal review
@pytest.mark.asyncio
async def test_an_unclassifiable_refusal_stops_and_says_so(client, db_pool, da_bat):
    """`OTHER` → nói ra lý do, KHÔNG nút "tìm đơn vị khác", không tạo gì mới.

    Hệ thống chưa biết đi tiếp thế nào. Dựng nút ở đây là hứa một đường không
    tồn tại, và khách bấm sẽ nhận một lỗi cho một việc họ làm đúng.

    Và KHÔNG dựng một lời hứa "bộ phận hỗ trợ sẽ liên hệ": chưa có chức năng hỗ
    trợ nào đứng sau câu ấy.
    """
    from src.api.routes import _DEMO_JOBS

    token, _, wid, ma, _ = await _den_luc_bi_tu_choi(
        client, db_pool, "kh_terminal", ly_do="Bên mình không nhận đơn loại này.", ma="OTHER"
    )
    truoc = await _dem(db_pool, wid)
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert body["stage"] == "WAITING_PROVIDER_RESELECTION", body["stage"]
    tu_choi = body["provider_rejection"]
    assert tu_choi is not None, "lời từ chối không phân loại được đã biến mất khỏi màn hình"
    assert tu_choi["can_request_another_provider"] is False
    assert tu_choi["rejected_provider"]["id"] == ma
    assert tu_choi["sanitized_reason"] == "Bên mình không nhận đơn loại này."
    for truong in ("message", "summary", "answer"):
        cau = (body.get(truong) or "").lower()
        assert "đang chờ" not in cau, f"{truong}: {body[truong]}"
    assert "chưa tự xử lý tiếp được" in body["summary"]
    assert await _dem(db_pool, wid) == truoc


@pytest.mark.asyncio
async def test_a_terminal_refusal_cannot_be_forced_into_a_new_attempt(client, db_pool, da_bat):
    """Gọi thẳng endpoint cho một lời từ chối `TERMINAL_REVIEW` → không mở gì.

    Giao diện không dựng nút, nhưng endpoint là một bề mặt riêng. Kẻ gọi thẳng
    không đi qua giao diện, và luật phải đứng ở tầng dưới.
    """
    token, _, wid, _, _ = await _den_luc_bi_tu_choi(client, db_pool, "kh_terminal_ep", ly_do="không nhận", ma="OTHER")
    truoc = await _dem(db_pool, wid)

    res = await client.post(
        f"/api/v1/service-proposals/workflows/{wid}/request-another-provider",
        json={"task_id": "T1"},
        headers=_auth(token),
    )

    assert res.status_code == 409, res.text
    assert await _dem(db_pool, wid) == truoc


@pytest.mark.asyncio
async def test_a_no_availability_refusal_keeps_the_date_question(client, db_pool, da_bat):
    """`NO_AVAILABILITY` giữ nguyên đường sửa NGÀY — không mời đổi đơn vị.

    Đơn vị vẫn nhận việc, chỉ không nhận ngày ấy, và đổi ngày rẻ hơn đổi đơn
    vị. Cho khách chọn giữa hai đường là một quyết định sản phẩm chưa được đưa
    ra — xem ghi chú NỢ ở `refusal_policy`.
    """
    from src.api.routes import _DEMO_JOBS

    token, _, wid, _, _ = await _den_luc_bi_tu_choi(
        client, db_pool, "kh_giu_sua_ngay", ly_do="Hết xe ngày đó.", ma="NO_AVAILABILITY"
    )
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert body["provider_rejection"] is None, "lời từ chối sửa-được lại rơi vào màn chọn lại"
    assert body["stage"] != "WAITING_PROVIDER_RESELECTION"
    assert (await _dem(db_pool, wid))["tasks"] == 1
