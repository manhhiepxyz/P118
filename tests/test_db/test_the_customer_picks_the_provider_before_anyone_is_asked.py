"""Cờ bật: khách chọn đơn vị TRƯỚC, hàng đợi của đơn vị mở SAU.

Đây là bài kiểm cho đường production thật — `ServiceApprovalBoundary`, chỗ duy
nhất ghim hàng đợi duyệt cho một bước cần đơn vị. Chen vào đó nghĩa là mọi
đường dẫn tới hàng đợi đều đi qua cùng một luật; chen ở chỗ khác là để lại ít
nhất một đường không đi qua.

Hai bất biến lớn nhất:

  * cờ TẮT → không có gì đổi. Không chứng từ, không đề xuất, hàng đợi mở ngay
    như trước, với đơn vị mặc định.
  * cờ BẬT → có đề xuất, và `/review` CHƯA có việc. Ghim hàng đợi trước khi
    khách bấm nghĩa là đơn vị nhận việc trước khi khách chọn họ — và lúc khách
    đổi ý thì bên kia đã bắt đầu xếp lịch.

Và tính bất biến khi lặp: poll/continue nhiều lần không được sinh thêm chứng từ
hay đề xuất. Thiếu nó thì mỗi lượt `/continue` là một vòng hỏi giá mới, và cái
khách vừa nhìn thấy trên màn hình đã không còn xác nhận được nữa.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.common.enums import TaskStatus
from src.common.feature_flags import SERVICE_PROVIDER_MATCHING
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.db.proposal_repository import de_xuat_dang_cho, doc_de_xuat, xac_nhan_de_xuat
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.service_approval import (
    ProviderProposalRequiredError,
    ServiceApprovalBoundary,
    ServiceApprovalRequiredError,
)

YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}
GIA = {"MOV-01": 430_000, "MOV-02": 470_000, "MOV-03": 420_000}


class _KhongChayGiCa:
    """Ranh giới thực thi giả: cổng dịch vụ ngắt luồng TRƯỚC khi tới nó.

    Nếu nó được gọi cho một bước có cổng thì bài kiểm sai — nên nó ném, chứ
    không trả về một kết quả trông như thành công.
    """

    def __init__(self) -> None:
        self.da_goi: list[str] = []

    async def execute(self, plan, workflow_id=None, **kw):
        self.da_goi.append(",".join(t.task_id for t in plan.tasks))
        return workflow_id or str(uuid.uuid4()), {}


class ConnectorBaoGia:
    """Đơn vị báo giá qua một hợp đồng thật, đếm số lượt bị hỏi."""

    def __init__(self, gia=None, *, han_phut: int = 30) -> None:
        self.gia = GIA if gia is None else gia
        self.han_phut = han_phut
        self.so_luot = 0

    async def xin_bao_gia_chuyen_nha(self, service_provider_id, payload):
        self.so_luot += 1
        so_tien = self.gia.get(service_provider_id)
        if so_tien is None:
            return StandardResult.fail("NO_AVAILABILITY", "bận ngày đó")
        return StandardResult.ok(
            data={
                "external_quote_id": f"Q-{service_provider_id}-{uuid.uuid4().hex[:8]}",
                "service_provider_id": service_provider_id,
                "amount": so_tien,
                "currency": "VND",
                "valid_until": (datetime.now(UTC) + timedelta(minutes=self.han_phut)).isoformat(),
            }
        )


@pytest.fixture
def kho_that(db_pool, monkeypatch):
    """Repository THẬT gắn vào composition root — cùng đường production dùng.

    `ServiceApprovalBoundary` gọi `acquire_repository()` để lấy pool cho đường
    báo giá, và ngoài lifespan thì provider ấy chưa được cấu hình.
    """
    from src.db.workflow_repository import WorkflowRepository
    from src.orchestration.runtime_provider import clear_repository_provider, set_repository_provider

    repository = WorkflowRepository(db_pool)

    async def _provide():
        return repository

    set_repository_provider(_provide)
    try:
        yield repository
    finally:
        clear_repository_provider()


@pytest.fixture
def bao_gia_that(monkeypatch):
    """Tiêm connector báo giá vào ĐÚNG chỗ boundary dựng nó."""
    connector = ConnectorBaoGia()
    monkeypatch.setattr("src.connectors.resident_services.ResidentServicesConnector", lambda **_: connector)
    return connector


def _plan(them: dict | None = None) -> TaskPlan:
    return TaskPlan(
        goal="chuyển nhà",
        tasks=[Task(task_id="T1", tool="schedule_move", input={**YEU_CAU, **(them or {})}, depends_on=[])],
    )


async def _khach(db_pool, ten: str) -> str:
    uid = uuid.uuid4()
    await db_pool.execute(
        "INSERT INTO users (id, username, password_hash, role, full_name, phone) "
        "VALUES ($1::uuid, $2, 'x', 'customer', $3, '0900000000')",
        uid,
        ten,
        f"Khách {ten}",
    )
    return str(uid)


async def _mot_workflow_va_buoc(db_pool, chu: str) -> str:
    """Workflow + bước `schedule_move` thật, không chạy qua boundary."""
    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'chuyển nhà', 'PENDING', $2::uuid)",
        wid,
        chu,
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', 'schedule_move', 'PENDING', '[]'::jsonb, $2::jsonb)",
        wid,
        __import__("json").dumps(YEU_CAU),
    )
    return wid


async def _chay(db_pool, plan, chu: str) -> tuple[str, Exception | None]:
    """Chạy qua ĐÚNG cổng dịch vụ của production, trả (workflow_id, lỗi ngắt)."""
    repository = await acquire_repository()
    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'chuyển nhà', 'PENDING', $2::uuid)",
        wid,
        chu,
    )
    boundary = ServiceApprovalBoundary(_KhongChayGiCa(), approved=False, repository=repository)
    try:
        await boundary.execute(plan, wid)
    except (ProviderProposalRequiredError, ServiceApprovalRequiredError) as exc:
        return wid, exc
    return wid, None


async def _dem(db_pool, wid):
    return {
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


# ------------------------------------------------------------------ cờ TẮT
@pytest.mark.asyncio
async def test_with_the_flag_off_nothing_changes(db_pool, monkeypatch, kho_that, bao_gia_that):
    """Đường cũ chạy nguyên vẹn: hàng đợi mở ngay, đơn vị mặc định, không chứng từ.

    Đây là điều kiện để cờ có ý nghĩa. Một cờ mà đường tắt cũng đổi hành vi thì
    nó không phải công tắc — nó là một lượt refactor có hai nhánh.
    """
    monkeypatch.delenv(SERVICE_PROVIDER_MATCHING, raising=False)
    chu = await _khach(db_pool, "kh_co_tat")

    wid, loi = await _chay(db_pool, _plan(), chu)

    assert isinstance(loi, ServiceApprovalRequiredError)
    assert not isinstance(loi, ProviderProposalRequiredError)
    dem = await _dem(db_pool, wid)
    assert dem == {"quotes": 0, "proposals": 0, "approvals": 1}
    assert bao_gia_that.so_luot == 0, "cờ tắt mà vẫn đi hỏi giá"
    dong = await db_pool.fetchrow(
        "SELECT status, service_provider_id FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid)
    )
    assert (dong["status"], dong["service_provider_id"]) == ("AWAITING", "MOV-01"), "đơn vị mặc định đã đổi"


@pytest.mark.asyncio
async def test_the_flag_being_anything_but_one_is_off(db_pool, monkeypatch, kho_that, bao_gia_that):
    """Fail-closed ở đường production, không chỉ ở helper."""
    monkeypatch.setenv(SERVICE_PROVIDER_MATCHING, "true")
    chu = await _khach(db_pool, "kh_co_true")

    wid, loi = await _chay(db_pool, _plan(), chu)

    assert isinstance(loi, ServiceApprovalRequiredError)
    assert (await _dem(db_pool, wid))["proposals"] == 0


# ------------------------------------------------------------------ cờ BẬT
@pytest.fixture
def co_bat(monkeypatch):
    monkeypatch.setenv(SERVICE_PROVIDER_MATCHING, "1")


@pytest.mark.asyncio
async def test_with_the_flag_on_the_queue_stays_empty_until_the_customer_says_yes(
    db_pool, monkeypatch, kho_that, co_bat, bao_gia_that
):
    """Có đề xuất, và `/review` CHƯA có việc.

    Ghim hàng đợi trước khi khách bấm nghĩa là đơn vị nhận việc trước khi khách
    chọn họ — và lúc khách đổi ý thì bên kia đã bắt đầu xếp lịch.
    """
    chu = await _khach(db_pool, "kh_co_bat")

    wid, loi = await _chay(db_pool, _plan(), chu)

    assert isinstance(loi, ProviderProposalRequiredError)
    dem = await _dem(db_pool, wid)
    assert dem["quotes"] == 3
    assert dem["proposals"] == 1
    assert dem["approvals"] == 0, "hàng đợi đơn vị đã mở trước khi khách bấm"
    assert bao_gia_that.so_luot == 3

    payload = (loi.context or {})["provider_proposals"][0]
    assert payload["provider"] == {"id": "MOV-03", "name": "Dịch vụ An Khang"}
    assert payload["amount"] == 420_000 and payload["currency"] == "VND"
    assert payload["can_confirm"] is True
    assert payload["effective_status"] == "PROPOSED"
    assert payload["reason"] and payload["valid_until"]


@pytest.mark.asyncio
async def test_the_step_waits_for_the_customer_not_for_the_provider(
    db_pool, monkeypatch, kho_that, co_bat, bao_gia_that
):
    chu = await _khach(db_pool, "kh_cho_khach")
    wid, _ = await _chay(db_pool, _plan(), chu)

    assert (
        await db_pool.fetchval(
            "SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", uuid.UUID(wid)
        )
        == TaskStatus.WAITING_APPROVAL.value
    )
    assert (
        await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", uuid.UUID(wid))
        == "WAITING_APPROVAL"
    )


# ------------------------------------------------------- lặp lại không nhân bản
@pytest.mark.asyncio
async def test_running_the_same_step_again_does_not_duplicate_anything(
    db_pool, monkeypatch, kho_that, co_bat, bao_gia_that
):
    """Poll/continue nhiều lần → vẫn đúng một đề xuất, và KHÔNG hỏi giá lại.

    Thiếu vế này thì mỗi lượt `/continue` là một vòng hỏi giá mới — ba lời gọi
    HTTP, ba chứng từ, một đề xuất mới đẩy cái cũ sang SUPERSEDED — và cái
    khách đang nhìn trên màn hình đã không còn xác nhận được nữa.
    """
    repository = await acquire_repository()
    chu = await _khach(db_pool, "kh_lap_lai")
    wid, _ = await _chay(db_pool, _plan(), chu)
    dau = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")

    for _ in range(3):
        boundary = ServiceApprovalBoundary(_KhongChayGiCa(), approved=False, repository=repository)
        with pytest.raises(ProviderProposalRequiredError):
            await boundary.execute(_plan(), wid)

    dem = await _dem(db_pool, wid)
    assert dem == {"quotes": 3, "proposals": 1, "approvals": 0}, dem
    assert bao_gia_that.so_luot == 3, f"đã hỏi giá {bao_gia_that.so_luot} lượt cho một yêu cầu không đổi"
    assert (await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")).proposal_id == dau.proposal_id


@pytest.mark.asyncio
async def test_running_again_after_confirming_does_not_make_a_second_proposal(
    db_pool, monkeypatch, kho_that, co_bat, bao_gia_that
):
    """Sau khi khách bấm, lượt chạy tiếp KHÔNG dựng đề xuất mới.

    Đề xuất đã `CONFIRMED` nên `de_xuat_dang_cho` trả `None`; nếu chỗ này chỉ
    hỏi "còn cái nào đang chờ không" mà không hỏi "đã ai chốt chưa" thì mỗi
    lượt poll sau xác nhận lại mời khách chọn lại từ đầu.
    """
    repository = await acquire_repository()
    chu = await _khach(db_pool, "kh_sau_xac_nhan")
    wid, loi = await _chay(db_pool, _plan(), chu)
    proposal_id = (loi.context or {})["provider_proposals"][0]["proposal_id"]
    await xac_nhan_de_xuat(db_pool, proposal_id, owner_user_id=chu)

    boundary = ServiceApprovalBoundary(_KhongChayGiCa(), approved=False, repository=repository)
    with pytest.raises((ProviderProposalRequiredError, ServiceApprovalRequiredError)):
        await boundary.execute(_plan(), wid)

    tong = await db_pool.fetchval(
        "SELECT count(*) FROM service_provider_proposals WHERE workflow_id=$1::uuid", uuid.UUID(wid)
    )
    assert tong == 1, f"{tong} đề xuất sau một lượt xác nhận"
    assert (await doc_de_xuat(db_pool, proposal_id)).status == "CONFIRMED"


# ------------------------------------------------------------- yêu cầu đổi
@pytest.mark.asyncio
async def test_changing_the_request_supersedes_and_re_proposes(db_pool, monkeypatch, kho_that, co_bat, bao_gia_that):
    """Vân tay đổi → chứng từ VÀ đề xuất cũ sang SUPERSEDED, đề xuất mới dựng.

    Không tái dùng xác nhận cũ: cái cũ không còn `PROPOSED` nên nó không bấm
    được nữa, và khách phải đồng ý với con số MỚI.
    """
    repository = await acquire_repository()
    chu = await _khach(db_pool, "kh_doi_yeu_cau")
    wid, _ = await _chay(db_pool, _plan(), chu)
    cu = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")

    boundary = ServiceApprovalBoundary(_KhongChayGiCa(), approved=False, repository=repository)
    with pytest.raises(ProviderProposalRequiredError):
        await boundary.execute(_plan({"move_vehicle": "truck"}), wid)

    assert (await doc_de_xuat(db_pool, cu.proposal_id)).status == "SUPERSEDED"
    moi = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")
    assert moi is not None and moi.proposal_id != cu.proposal_id
    cu_bao_gia = await db_pool.fetchval(
        "SELECT count(*) FROM service_quotes WHERE workflow_id=$1::uuid AND status='SUPERSEDED'", uuid.UUID(wid)
    )
    assert cu_bao_gia == 3
    assert bao_gia_that.so_luot == 6, "yêu cầu đổi mà không hỏi giá lại"


@pytest.mark.asyncio
async def test_an_expired_proposal_is_replaced_not_reused(db_pool, monkeypatch, kho_that, co_bat, bao_gia_that):
    """Đề xuất hết hạn → chứng từ EXPIRED, đề xuất EXPIRED, và dựng cái MỚI.

    Khách phải xác nhận đề xuất mới. Dùng lại cái cũ nghĩa là chốt một cái giá
    đơn vị không còn giữ.
    """
    repository = await acquire_repository()
    chu = await _khach(db_pool, "kh_de_xuat_het_han")
    wid, _ = await _chay(db_pool, _plan(), chu)
    cu = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")
    await db_pool.execute(
        "UPDATE service_quotes SET valid_until = NOW() - INTERVAL '1 min' WHERE workflow_id=$1::uuid",
        uuid.UUID(wid),
    )

    boundary = ServiceApprovalBoundary(_KhongChayGiCa(), approved=False, repository=repository)
    with pytest.raises(ProviderProposalRequiredError):
        await boundary.execute(_plan(), wid)

    assert (await doc_de_xuat(db_pool, cu.proposal_id)).status == "EXPIRED"
    moi = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")
    assert moi is not None and moi.proposal_id != cu.proposal_id
    ket_qua = await xac_nhan_de_xuat(db_pool, cu.proposal_id, owner_user_id=chu)
    assert ket_qua.ket_qua == "ALREADY_DECIDED", "đề xuất hết hạn vẫn xác nhận được"


# ------------------------------------------------------------- không chọn được
@pytest.mark.asyncio
async def test_over_budget_never_switches_provider_and_never_opens_the_queue(
    db_pool, monkeypatch, kho_that, co_bat, bao_gia_that
):
    """Khách chỉ đích danh + vượt ngân sách → không đề xuất, không hàng đợi.

    Bên rẻ hơn đang nằm ngay đó và hệ thống KHÔNG lấy nó: hai điều kiện của
    khách mâu thuẫn nhau là chuyện của khách, và tự gỡ hộ là quyết định thay họ
    về tiền.

    Sở thích đi bằng THAM SỐ RIÊNG, không qua `task.input`. Đó là lý do bài
    kiểm này gọi thẳng `chuan_bi_de_xuat` thay vì đi qua `_park`: hôm nay chưa
    có nguồn nào cấp sở thích cho một bước, và giả vờ có bằng cách nhét hai
    khoá lạ vào `input` sẽ kiểm một đường không tồn tại.
    """
    from src.orchestration.provider_matching import TuyChonChonDonVi, chuan_bi_de_xuat

    chu = await _khach(db_pool, "kh_vuot_ngan_sach")
    wid = await _mot_workflow_va_buoc(db_pool, chu)

    ket_qua = await chuan_bi_de_xuat(
        db_pool,
        bao_gia_that,
        workflow_id=wid,
        task_id="T1",
        input_data=dict(YEU_CAU),
        tuy_chon=TuyChonChonDonVi(ten_don_vi="Đại Tín", max_price=450_000),
    )

    assert ket_qua.lua_chon.ket_qua == "OVER_BUDGET"
    assert ket_qua.de_xuat is None, "một lời từ chối đã thành đề xuất"
    dem = await _dem(db_pool, wid)
    assert dem["quotes"] == 3, "báo giá phải được ghim làm bằng chứng"
    assert dem["proposals"] == 0
    assert dem["approvals"] == 0, "vượt ngân sách mà vẫn gửi việc tới /review"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tuy_chon", "ly_do"),
    [
        ({"ten_don_vi": "chuyển nhà"}, "UNKNOWN_PROVIDER"),
        ({"ten_don_vi": "Chuyển nhà Thành Công"}, "UNKNOWN_PROVIDER"),
        ({"max_price": -1}, "INVALID_BUDGET"),
    ],
)
async def test_a_refusal_never_becomes_a_proposal_or_a_queue_entry(
    db_pool, monkeypatch, kho_that, co_bat, bao_gia_that, tuy_chon, ly_do
):
    """Fail-closed theo contract C: không đề xuất giả, không việc cho đơn vị."""
    from src.orchestration.provider_matching import TuyChonChonDonVi, chuan_bi_de_xuat

    chu = await _khach(db_pool, f"kh_tu_choi_{uuid.uuid4().hex[:6]}")
    wid = await _mot_workflow_va_buoc(db_pool, chu)

    ket_qua = await chuan_bi_de_xuat(
        db_pool,
        bao_gia_that,
        workflow_id=wid,
        task_id="T1",
        input_data=dict(YEU_CAU),
        tuy_chon=TuyChonChonDonVi(**tuy_chon),
    )

    assert ket_qua.lua_chon.ket_qua == ly_do
    assert ket_qua.de_xuat is None
    dem = await _dem(db_pool, wid)
    assert dem["proposals"] == 0 and dem["approvals"] == 0


@pytest.mark.asyncio
async def test_no_preference_is_the_shipping_default(db_pool, monkeypatch, kho_that, co_bat, bao_gia_that):
    """Đường thật hôm nay chạy với sở thích RỖNG — và nó chọn được.

    Đây là khẳng định quan trọng của E2: nhánh mặc định không phải "chưa làm
    xong", nó là nhánh đang phục vụ. Không có nguồn nào cấp sở thích, và không
    cần có để tính năng chạy.
    """
    from src.orchestration.provider_matching import KHONG_CO_TUY_CHON

    assert (KHONG_CO_TUY_CHON.ten_don_vi, KHONG_CO_TUY_CHON.max_price) == (None, None)
    chu = await _khach(db_pool, "kh_mac_dinh")
    wid, loi = await _chay(db_pool, _plan(), chu)
    assert isinstance(loi, ProviderProposalRequiredError)
    assert (loi.context or {})["provider_proposals"][0]["provider"]["id"] == "MOV-03"


@pytest.mark.asyncio
async def test_preferences_never_reach_the_provider_or_the_step(db_pool, monkeypatch, kho_that, co_bat):
    """Sở thích KHÔNG có mặt trong payload gửi đơn vị, và KHÔNG ghi vào bước.

    Hai luật, một bài kiểm, vì chúng là hai nửa của cùng một ranh giới. Ngân
    sách rời khỏi P-118 nghĩa là đơn vị định giá theo túi tiền người hỏi; sở
    thích ghi vào `task.input_data` nghĩa là nó đi theo bước tới mọi nơi bước
    đi — kể cả ra ngoài.
    """
    from src.orchestration.provider_matching import TuyChonChonDonVi, chuan_bi_de_xuat

    class ConnectorGhiLai(ConnectorBaoGia):
        def __init__(self) -> None:
            super().__init__()
            self.payload: list[dict] = []

        async def xin_bao_gia_chuyen_nha(self, service_provider_id, payload):
            self.payload.append(dict(payload))
            return await super().xin_bao_gia_chuyen_nha(service_provider_id, payload)

    gian_diep = ConnectorGhiLai()
    chu = await _khach(db_pool, "kh_khong_ro_ri")
    wid = await _mot_workflow_va_buoc(db_pool, chu)

    await chuan_bi_de_xuat(
        db_pool,
        gian_diep,
        workflow_id=wid,
        task_id="T1",
        input_data=dict(YEU_CAU),
        tuy_chon=TuyChonChonDonVi(ten_don_vi="Đại Tín", max_price=450_000),
    )

    assert gian_diep.payload, "không gọi đơn vị nào — bài kiểm sẽ xanh vì lý do sai"
    for payload in gian_diep.payload:
        assert set(payload) == set(YEU_CAU), f"payload mang thêm: {set(payload) - set(YEU_CAU)}"
    input_buoc = await db_pool.fetchval(
        "SELECT input_data FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", uuid.UUID(wid)
    )
    import json as _json

    input_buoc = _json.loads(input_buoc) if isinstance(input_buoc, str) else (input_buoc or {})
    assert "max_price" not in input_buoc and "provider_name_said" not in input_buoc


def test_the_move_contract_still_has_exactly_five_inputs():
    """Schema `schedule_move` KHÔNG được nới để nhận sở thích.

    Nới nó là mở một đường cho Planner ghi sở thích vào bước — và Validator sẽ
    cho qua, vì lúc ấy chúng là input hợp lệ. Bài kiểm này là khoá cửa: thêm
    một ô vào hợp đồng thì nó đỏ, và người thêm phải nói ra vì sao.
    """
    from src.common.tool_contract import TOOL_CONTRACTS

    o_vao = set(TOOL_CONTRACTS["schedule_move"].inputs)
    assert o_vao == {
        "move_date",
        "move_time",
        "move_vehicle",
        "needs_elevator",
        "needs_loading_support",
    }, o_vao
    assert "max_price" not in o_vao and "provider_name_said" not in o_vao


@pytest.mark.asyncio
async def test_no_provider_quoting_leaves_nothing_behind(db_pool, monkeypatch, kho_that, co_bat):
    """Không đơn vị nào báo giá → không đề xuất, và cũng không hàng đợi."""
    connector = ConnectorBaoGia(gia={})
    monkeypatch.setattr("src.connectors.resident_services.ResidentServicesConnector", lambda **_: connector)
    chu = await _khach(db_pool, "kh_khong_ai_bao_gia")

    wid, _ = await _chay(db_pool, _plan(), chu)

    dem = await _dem(db_pool, wid)
    assert dem == {"quotes": 0, "proposals": 0, "approvals": 0}
