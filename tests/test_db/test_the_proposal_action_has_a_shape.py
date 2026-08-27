"""Hình dạng của một việc khách phải quyết — có KIỂU, không phải một dict.

Trước đó `service_proposals` là `list[dict[str, Any]]`, và một dict chấp nhận
mọi thứ: thiếu `valid_until` thì giao diện vẽ một thẻ không có hạn, thừa
`quote_id` thì một định danh nội bộ đi thẳng ra màn hình. Cả hai đều im lặng
cho tới khi có người nhìn thấy.

`ServiceProposalActionView` với `extra="forbid"` biến hai lỗi ấy thành lỗi lúc
dựng response — sớm nhất có thể, và ở phía server.

Hai đường dựng response phải cho CÙNG một hình dạng: `_demo_workflow_status`
(đường `GET`) và `_public_view_from_db` (đường lớp trả lời, và là đường mọi
workflow đi sau restart). Kiểm một đường rồi tin đường kia là cách hai đường
lệch nhau.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.common.feature_flags import SERVICE_PROVIDER_MATCHING
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.models.schemas import ServiceProposalActionView, ServiceProposalProviderView
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.service_approval import ProviderProposalRequiredError, ServiceApprovalBoundary
from tests.test_db.conftest import _register_and_login

DEMO = "/api/v1/workflows/demo"
YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}

DAY_DU = {
    "proposal_id": "p-1",
    "task_id": "T1",
    "provider": {"id": "MOV-03", "name": "Dịch vụ An Khang"},
    "amount": 420_000,
    "currency": "VND",
    "reason": "Đơn vị phù hợp nhất với yêu cầu của bạn, báo giá 420.000 VND.",
    "valid_until": "2026-09-30T10:00:00+00:00",
    "effective_status": "PROPOSED",
    "can_confirm": True,
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class ConnectorBaoGia:
    async def xin_bao_gia_chuyen_nha(self, service_provider_id, payload):
        gia = {"MOV-01": 430_000, "MOV-02": 470_000, "MOV-03": 420_000}[service_provider_id]
        return StandardResult.ok(
            data={
                "external_quote_id": f"Q-{service_provider_id}-{uuid.uuid4().hex[:8]}",
                "service_provider_id": service_provider_id,
                "amount": gia,
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


async def _toi_de_xuat(client, db_pool, ten: str, *, buoc: tuple[str, ...] = ("T1",)):
    token = await _register_and_login(client, ten)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", ten)
    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'chuyển nhà', 'PENDING', $2)",
        wid,
        uid,
    )
    tasks = []
    for i, task_id in enumerate(buoc):
        rieng = {**YEU_CAU, "move_date": f"2026-09-{20 + i:02d}"}
        tasks.append(Task(task_id=task_id, tool="schedule_move", input=rieng, depends_on=[]))
        await db_pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
            "VALUES ($1::uuid, $2, 'schedule_move', 'PENDING', '[]'::jsonb, $3::jsonb)",
            wid,
            task_id,
            json.dumps(rieng),
        )
    boundary = ServiceApprovalBoundary(_KhongChayGiCa(), approved=False, repository=await acquire_repository())
    with pytest.raises(ProviderProposalRequiredError):
        await boundary.execute(TaskPlan(goal="chuyển nhà", tasks=tasks), wid)
    return token, wid


# ------------------------------------------------------------------ kiểu
def test_a_complete_action_validates():
    view = ServiceProposalActionView.model_validate(DAY_DU)
    assert view.provider == ServiceProposalProviderView(id="MOV-03", name="Dịch vụ An Khang")
    assert view.can_confirm is True


@pytest.mark.parametrize("thieu", sorted(DAY_DU))
def test_every_field_is_required(thieu):
    """Không trường nào tuỳ chọn.

    Thiếu `valid_until` thì giao diện vẽ một thẻ không có hạn; thiếu `task_id`
    thì hai đề xuất không phân biệt được cái nào cho việc nào. Cả hai đều là
    một màn hình sai, không phải một màn hình thiếu.
    """
    with pytest.raises(ValidationError):
        ServiceProposalActionView.model_validate({k: v for k, v in DAY_DU.items() if k != thieu})


@pytest.mark.parametrize("thua", ["quote_id", "request_fingerprint", "workflow_id", "service_provider_id"])
def test_internal_evidence_can_never_be_added(thua):
    """`extra="forbid"` chặn định danh nội bộ đi ra màn hình.

    `quote_id` và `request_fingerprint` là chứng cứ nội bộ; khách không có gì
    để làm với chúng, và cái duy nhất họ cần gửi lại là `proposal_id`. Nếu một
    ngày ai đó "tiện tay" thêm chúng vào payload thì lỗi phải nổ ở đây, không
    phải ở một lượt rà soát bảo mật sáu tháng sau.
    """
    with pytest.raises(ValidationError):
        ServiceProposalActionView.model_validate({**DAY_DU, thua: "gì đó"})


@pytest.mark.parametrize("gia", [0, -1, "420000", 420_000.0, True, None])
def test_an_amount_that_is_not_a_positive_integer_is_refused(gia):
    """`"420000"` là VI PHẠM hợp đồng, không phải một con số cần ép kiểu.

    Pydantic mặc định nhận nó và ép sang `int` — im lặng, và che mất việc một
    tầng nào đó đang trả tiền dưới dạng chuỗi. `True` cũng vậy: `bool` là `int`
    trong Python, nên nó lọt qua mọi phép kiểm ngây thơ và thành "1 đồng".
    """
    with pytest.raises(ValidationError):
        ServiceProposalActionView.model_validate({**DAY_DU, "amount": gia})


@pytest.mark.parametrize("co", ["true", "1", 1, None])
def test_can_confirm_is_never_coerced(co):
    """`"false"` ép thành `True` là cách một cái nút bấm không được lại hiện ra."""
    with pytest.raises(ValidationError):
        ServiceProposalActionView.model_validate({**DAY_DU, "can_confirm": co})


def test_an_unknown_effective_status_is_refused():
    with pytest.raises(ValidationError):
        ServiceProposalActionView.model_validate({**DAY_DU, "effective_status": "PENDING"})


def test_the_provider_needs_both_a_code_and_a_name():
    for thieu in ("id", "name"):
        xau = {k: v for k, v in DAY_DU["provider"].items() if k != thieu}
        with pytest.raises(ValidationError):
            ServiceProposalActionView.model_validate({**DAY_DU, "provider": xau})


# ------------------------------------------------ cùng hình dạng ở hai đường
@pytest.mark.asyncio
async def test_both_response_paths_give_the_same_shape(client, db_pool, da_bat):
    """`GET` và đường dựng lại từ database phải cho CÙNG một hình dạng.

    Đường thứ hai là đường mọi workflow đi sau restart. Kiểm một đường rồi tin
    đường kia là cách hai đường lệch nhau — và chúng đã lệch một lần: nhánh đề
    xuất được thêm vào `GET` trước, `_public_view_from_db` sau.
    """
    from src.api.routes import _DEMO_JOBS, _public_view_from_db

    token, wid = await _toi_de_xuat(client, db_pool, "kh_hai_duong")
    _DEMO_JOBS.clear()

    qua_http = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()
    dung_lai = await _public_view_from_db(wid)

    assert dung_lai is not None
    assert len(qua_http["service_proposals"]) == 1
    assert len(dung_lai.service_proposals) == 1
    a = qua_http["service_proposals"][0]
    b = dung_lai.service_proposals[0].model_dump()
    assert set(a) == set(b), f"hai đường trả hai bộ trường: {set(a) ^ set(b)}"
    assert a["proposal_id"] == b["proposal_id"] and a["task_id"] == b["task_id"]
    assert dung_lai.stage == "WAITING_PROVIDER_PROPOSAL"


@pytest.mark.asyncio
async def test_the_alias_is_the_very_same_object(client, db_pool, da_bat):
    """Alias và phần tử trong danh sách là MỘT, không phải hai lần dựng.

    Hai mapper nghĩa là hai chỗ để lệch nhau, và chúng sẽ lệch đúng ở trường ai
    đó thêm sau này. Kiểm bằng `is`, không phải `==`.
    """
    from src.api.routes import _DEMO_JOBS, _public_view_from_db

    _, wid = await _toi_de_xuat(client, db_pool, "kh_alias_mot_object")
    _DEMO_JOBS.clear()

    view = await _public_view_from_db(wid)

    assert view.provider_proposal is view.service_proposals[0]


@pytest.mark.asyncio
async def test_the_response_refuses_a_malformed_item(client, db_pool, da_bat):
    """Một item hỏng làm lượt dựng response NỔ, không lọt ra ngoài.

    Đây là điều `dict[str, Any]` không làm được: nó nhận mọi thứ, và cái sai đi
    tiếp cho tới màn hình.
    """
    from src.api.routes import _khoi_de_xuat

    with pytest.raises(ValidationError):
        _khoi_de_xuat([{k: v for k, v in DAY_DU.items() if k != "valid_until"}])
    with pytest.raises(ValidationError):
        _khoi_de_xuat([{**DAY_DU, "quote_id": str(uuid.uuid4())}])


# ------------------------------------------------------------- thứ tự
@pytest.mark.asyncio
async def test_the_list_is_stable_by_task_id_not_by_plan_order(client, db_pool, da_bat):
    """Thứ tự là ỔN ĐỊNH THEO `task_id`, và đó là toàn bộ lời hứa.

    `T10` sắp TRƯỚC `T2` vì `task_id` là chuỗi và phép so là so chuỗi. Đó
    KHÔNG phải thứ tự kế hoạch — gọi nó là "thứ tự kế hoạch" sẽ đúng với `T1,
    T2, T3` rồi sai lặng lẽ ở bước thứ mười.

    Bài kiểm này khoá HÀNH VI THẬT, không khoá điều ta mong muốn. Muốn thứ tự
    theo kế hoạch thì cần một cột thứ tự canonical — chưa có, và không dựng
    trong lượt này.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_thu_tu", buoc=("T1", "T2", "T10"))
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    thu_tu = [d["task_id"] for d in body["service_proposals"]]
    assert thu_tu == ["T1", "T10", "T2"], thu_tu
    assert body["provider_proposal"] is None
    # Lặp lại nhiều lượt đọc vẫn cùng một thứ tự — đó là phần "ổn định".
    for _ in range(3):
        _DEMO_JOBS.clear()
        lai = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()
        assert [d["task_id"] for d in lai["service_proposals"]] == thu_tu
