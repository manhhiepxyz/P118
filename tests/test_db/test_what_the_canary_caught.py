"""Ba lỗi canary tìm ra trên đường thật, và hợp đồng NHIỀU đề xuất.

Cả ba chỉ hiện ra khi đi HẾT vòng qua route sản phẩm, và không bài kiểm nào lúc
ấy bắt được: tầng nghiệp vụ đã đúng, tầng trình bày thì chưa. Đó là lý do file
này đi qua `GET /workflows/demo/{id}` chứ không gọi hàm dựng response —
`_waiting_proposal_view` gọi trực tiếp sẽ luôn xanh, kể cả khi không route nào
gọi tới nó.

  A. Đề xuất biến mất sau restart. Database vẫn giữ `PROPOSED` còn hạn, lượt
     xác nhận vẫn chạy — chỉ giao diện mất nút. Khách mắc kẹt.
  B. Câu chat gọi việc chọn đơn vị là "xác nhận khoản thanh toán". Khách đi tìm
     một nút trả tiền không tồn tại.
  C. `.replace(",", ".")` trên cả câu nuốt luôn dấu phẩy tiếng Việt.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.common.feature_flags import SERVICE_PROVIDER_MATCHING
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.db.proposal_repository import de_xuat_dang_cho
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
    """Cờ bật + connector báo giá, TRÊN repository của `client`.

    Không tự gắn repository: `client` đã gắn một cái và bọc pool trong lớp
    `close()` rỗng. Mọi route đóng pool trong `finally`, nên một repository thứ
    hai trỏ thẳng vào pool dùng chung sẽ đóng nó ngay sau request đầu tiên.
    """
    monkeypatch.setenv(SERVICE_PROVIDER_MATCHING, "1")
    monkeypatch.setattr("src.connectors.resident_services.ResidentServicesConnector", lambda **_: ConnectorBaoGia())
    return client


async def _toi_de_xuat(client, db_pool, ten: str, *, so_buoc: int = 1):
    """Đi qua ĐÚNG cổng dịch vụ của production tới trạng thái chờ khách bấm."""
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
    for i in range(so_buoc):
        # Hai bước phải là hai YÊU CẦU khác nhau, nếu không chúng dùng chung
        # vân tay và bài kiểm không phân biệt được cross-wire.
        rieng = {**YEU_CAU, "move_date": f"2026-09-{25 + i:02d}"}
        tasks.append(Task(task_id=f"T{i + 1}", tool="schedule_move", input=rieng, depends_on=[]))
        await db_pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
            "VALUES ($1::uuid, $2, 'schedule_move', 'PENDING', '[]'::jsonb, $3::jsonb)",
            wid,
            f"T{i + 1}",
            json.dumps(rieng),
        )
    boundary = ServiceApprovalBoundary(_KhongChayGiCa(), approved=False, repository=await acquire_repository())
    with pytest.raises(ProviderProposalRequiredError):
        await boundary.execute(TaskPlan(goal="chuyển nhà", tasks=tasks), wid)
    return token, wid


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


# ==================================================================== A
@pytest.mark.asyncio
async def test_the_proposal_survives_a_cold_read(client, db_pool, da_bat):
    """`GET` dựng lại từ DATABASE vẫn mang đủ đề xuất để bấm.

    "Restart" ở đây là `_DEMO_JOBS` trống VÀ một pool mới — đúng thứ một tiến
    trình thứ hai nhìn thấy. Nếu đường dựng response chỉ biết đọc cache thì đây
    là chỗ nó lộ ra.

    Đo được trên canary trước khi vá: `approval_actor=None`,
    `provider_proposal=None`, trong khi database giữ nguyên một đề xuất
    `PROPOSED` còn hạn và `/service-proposals/{id}/confirm` vẫn chạy hoàn hảo.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_doc_nguoi")
    goc = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")

    # Mất sạch cache: đây là toàn bộ khác biệt giữa "vừa chạy xong" và "vừa
    # restart", và nó là chỗ ba loại chờ trước đã lần lượt quên.
    _DEMO_JOBS.clear()

    res = await client.get(f"{DEMO}/{wid}", headers=_auth(token))

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["approval_actor"] == "USER"
    assert len(body["service_proposals"]) == 1
    de_xuat = body["service_proposals"][0]
    assert de_xuat["proposal_id"] == goc.proposal_id
    assert de_xuat["can_confirm"] is True
    assert de_xuat["effective_status"] == "PROPOSED"
    assert de_xuat["provider"]["id"] and de_xuat["amount"] and de_xuat["valid_until"]
    # Alias vẫn có khi đúng một đề xuất.
    assert body["provider_proposal"] == de_xuat


@pytest.mark.asyncio
async def test_the_quote_behind_the_proposal_is_the_same_one(client, db_pool, da_bat):
    """Đọc lại phải trỏ về ĐÚNG chứng từ cũ, không phải một chứng từ mới."""
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_cung_chung_tu")
    quote_goc = (await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")).quote_id
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    pid = body["service_proposals"][0]["proposal_id"]
    quote_sau = await db_pool.fetchval(
        "SELECT quote_id FROM service_provider_proposals WHERE proposal_id=$1::uuid", uuid.UUID(pid)
    )
    assert str(quote_sau) == quote_goc


@pytest.mark.asyncio
async def test_reading_is_read_only_even_after_a_cold_start(client, db_pool, da_bat):
    """`GET` lặp lại không tạo thêm chứng từ, đề xuất hay việc cho đơn vị.

    Một `GET` chữa dữ liệu là một `GET` đổi trạng thái, và nó sẽ đổi từ một tab
    đang mở, một lượt poll, một con bot quét link.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_doc_khong_ghi")
    truoc = await _dem(db_pool, wid)

    for _ in range(4):
        _DEMO_JOBS.clear()
        assert (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).status_code == 200

    assert await _dem(db_pool, wid) == truoc
    assert truoc["approvals"] == 0, "hàng đợi đơn vị đã mở trước khi khách bấm"


# ==================================================================== B
_TU_CAM = ("thanh toán", "khoản phí", "trừ tiền", "chuyển khoản", "phí dịch vụ")


@pytest.mark.asyncio
async def test_the_chat_never_calls_a_proposal_a_payment(client, db_pool, da_bat):
    """Trước khi khách bấm, KHÔNG câu nào nói tới tiền phải trả.

    Đo được trên canary khi câu còn do model viết, nguyên văn:

        "Mình cần bạn xác nhận khoản thanh toán để chốt lịch chuyển nhà…"

    Không có khoản thanh toán nào. Việc cần làm là chọn đơn vị, và khách đọc
    câu ấy sẽ đi tìm một nút trả tiền không tồn tại.

    Kiểm CẢ BA trường ra màn hình — `message`, `summary`, `answer`. Một trường
    đúng và hai trường sai vẫn là màn hình sai.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_khong_goi_la_tien")
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    for truong in ("message", "summary", "answer"):
        cau = (body.get(truong) or "").lower()
        assert cau, f"{truong} rỗng — bài kiểm sẽ xanh vì lý do sai"
        for tu in _TU_CAM:
            assert tu not in cau, f"{truong} gọi việc chọn đơn vị là {tu!r}: {body[truong]}"
    # Và nó phải nói ĐÚNG việc cần làm, không chỉ tránh nói sai.
    assert "xác nhận" in (body["summary"] or "").lower()
    assert (body["service_proposals"][0]["provider"]["name"] or "") in body["summary"]


@pytest.mark.asyncio
async def test_the_answer_key_separates_the_two_user_waits(client, db_pool, da_bat):
    """Hai trạng thái `USER` nối tiếp nhau phải có KHOÁ khác nhau.

    Khách CHỌN ĐƠN VỊ, rồi (sau khi đơn vị duyệt) khách XÁC NHẬN TIỀN. Cả hai
    đều `WAITING_APPROVAL:USER`. Cùng khoá nghĩa là câu của tình huống trước
    sống tiếp qua tình huống sau — đúng lỗi mà `approval_actor` đã được thêm
    vào để chữa, chỉ ở một tầng sâu hơn.
    """
    from src.api.routes import answer_key

    chon_don_vi = answer_key("WAITING_APPROVAL", "USER", de_xuat_don_vi=True)
    tra_tien = answer_key("WAITING_APPROVAL", "USER", de_xuat_don_vi=False)
    assert chon_don_vi != tra_tien, "hai tình huống dùng chung một khoá câu trả lời"
    assert answer_key("WAITING_APPROVAL", "PROVIDER") not in (chon_don_vi, tra_tien)


@pytest.mark.asyncio
async def test_a_stored_payment_answer_is_never_shown_for_a_proposal(client, db_pool, da_bat):
    """Câu đã ghi cho tình huống TIỀN không được hiện ra ở tình huống CHỌN.

    Gieo thẳng một câu về thanh toán vào `workflows`, đúng như một lượt trước
    đã ghi nó, rồi đọc lại ở trạng thái đề xuất.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_cau_cu_ve_tien")
    await db_pool.execute(
        "UPDATE workflows SET assistant_answer = $2, assistant_for_status = 'WAITING_APPROVAL:USER', "
        "assistant_response_state = 'READY', assistant_updated_at = NOW() WHERE workflow_id = $1::uuid",
        uuid.UUID(wid),
        "Mình cần bạn xác nhận khoản thanh toán để chốt lịch nhé.",
    )
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert "thanh toán" not in (body["answer"] or "").lower(), body["answer"]


# ==================================================================== C
@pytest.mark.asyncio
async def test_vietnamese_commas_survive_the_number_formatting(client, db_pool, da_bat):
    """Dấu phẩy tiếng Việt giữ nguyên; chỉ chuỗi SỐ được chuẩn hoá.

    `f"…của bạn, {gia:,.0f}…".replace(",", ".")` thay dấu phẩy trên CẢ câu:

        "Đơn vị phù hợp nhất với yêu cầu của bạn. báo giá 420.000 VND."

    Một phép thay thế mù trên cả câu sẽ luôn tìm thấy nhiều hơn thứ nó định
    tìm. Kiểm ở RESPONSE cuối, không ở helper: helper đúng mà đường ghép câu
    sai thì màn hình vẫn sai.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_dau_phay")
    _DEMO_JOBS.clear()

    de_xuat = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()["service_proposals"][0]

    ly_do = de_xuat["reason"]
    assert "của bạn, báo giá" in ly_do, f"dấu phẩy bị nuốt: {ly_do}"
    assert "của bạn. báo giá" not in ly_do
    # Số tiền vẫn theo cách viết Việt Nam.
    assert "420.000" in ly_do and "420,000" not in ly_do
    assert ly_do.endswith("VND.")


@pytest.mark.asyncio
async def test_the_summary_keeps_its_punctuation_too(client, db_pool, da_bat):
    """Cùng luật cho câu tóm tắt — nó đi qua một đường ghép khác."""
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_dau_phay_summary")
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert "420.000 VND" in body["summary"], body["summary"]
    assert "420,000" not in body["summary"]


# ============================================================ nhiều đề xuất
@pytest.mark.asyncio
async def test_two_independent_moves_get_two_proposals(client, db_pool, da_bat):
    """Hai bước `schedule_move` độc lập → HAI đề xuất, không phải một.

    `TaskPlanValidator` cho qua một kế hoạch như vậy — đã kiểm. Bản đầu trả về
    "đề xuất đầu tiên" với lý do "một kế hoạch không có hai lần chuyển nhà", và
    giả định ấy sai theo cách IM LẶNG: bước thứ hai được ghim vào database,
    không bao giờ xuất hiện trên màn hình, và nằm `WAITING_APPROVAL` mãi mãi.

    `provider_proposal` là `None` khi có nhiều hơn một. Chọn cái đầu làm alias
    là một quyết định giao diện không ai chủ ý đưa ra, và nó biến "khách còn
    hai việc" thành "khách còn một việc" mà không có gì báo.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_hai_buoc", so_buoc=2)
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert body["approval_actor"] == "USER"
    assert len(body["service_proposals"]) == 2, body["service_proposals"]
    assert [d["task_id"] for d in body["service_proposals"]] == ["T1", "T2"], "thứ tự không tất định"
    assert body["provider_proposal"] is None, "âm thầm chọn đề xuất đầu tiên làm alias"
    assert all(d["can_confirm"] for d in body["service_proposals"])
    assert (await _dem(db_pool, wid))["approvals"] == 0


@pytest.mark.asyncio
async def test_each_proposal_points_at_its_own_task_and_quote(client, db_pool, da_bat):
    """Không cross-wire: chứng từ của bước nào neo vào bước ấy.

    Hai bước mang hai YÊU CẦU khác nhau (khác ngày), nên hai vân tay khác nhau.
    Nếu đường ghép lẫn chứng từ thì vân tay sẽ không khớp bước — và đó là thứ
    duy nhất phân biệt được "đúng bước" với "trùng hợp cùng giá".
    """
    from src.api.routes import _DEMO_JOBS
    from src.orchestration.quote import van_tay_yeu_cau

    token, wid = await _toi_de_xuat(client, db_pool, "kh_khong_lan_lon", so_buoc=2)
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    for de_xuat in body["service_proposals"]:
        task_id = de_xuat["task_id"]
        row = await db_pool.fetchrow(
            "SELECT p.task_id AS p_task, q.task_id AS q_task, q.request_fingerprint "
            "  FROM service_provider_proposals p JOIN service_quotes q ON q.quote_id = p.quote_id "
            " WHERE p.proposal_id = $1::uuid",
            uuid.UUID(de_xuat["proposal_id"]),
        )
        assert row["p_task"] == task_id and row["q_task"] == task_id, dict(row)
        goc = await db_pool.fetchval(
            "SELECT input_data FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id=$2",
            uuid.UUID(wid),
            task_id,
        )
        goc = json.loads(goc) if isinstance(goc, str) else goc
        assert row["request_fingerprint"] == van_tay_yeu_cau(goc), f"{task_id}: vân tay không khớp bước"


@pytest.mark.asyncio
async def test_confirming_one_proposal_leaves_the_other_waiting(client, db_pool, da_bat):
    """Bấm đồng ý cho T1 KHÔNG chốt T2, và chỉ mở hàng đợi cho T1.

    Đây là lý do danh sách phải có thật: hai việc, hai quyết định, hai lượt
    bấm. Một lượt bấm chốt cả hai là quyết định thay khách về một khoản tiền họ
    chưa xem.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_bam_mot_cai", so_buoc=2)
    _DEMO_JOBS.clear()
    truoc = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()["service_proposals"]
    t1 = next(d for d in truoc if d["task_id"] == "T1")
    t2 = next(d for d in truoc if d["task_id"] == "T2")

    res = await client.post(
        f"/api/v1/service-proposals/{t1['proposal_id']}/confirm",
        json={"decision": "confirm"},
        headers=_auth(token),
    )

    assert res.status_code == 200, res.text
    assert (
        await db_pool.fetchval(
            "SELECT status FROM service_provider_proposals WHERE proposal_id=$1::uuid",
            uuid.UUID(t2["proposal_id"]),
        )
        == "PROPOSED"
    ), "bấm một cái đã chốt cả cái kia"
    dong = await db_pool.fetch(
        "SELECT task_id, service_provider_id FROM service_approvals WHERE workflow_id=$1::uuid",
        uuid.UUID(wid),
    )
    assert [r["task_id"] for r in dong] == ["T1"], "mở hàng đợi cho bước chưa được đồng ý"
    assert dong[0]["service_provider_id"] == t1["provider"]["id"]

    _DEMO_JOBS.clear()
    con_lai = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()
    assert [d["task_id"] for d in con_lai["service_proposals"]] == ["T2"]
    assert con_lai["provider_proposal"]["proposal_id"] == t2["proposal_id"], "còn một thì alias phải có"


@pytest.mark.asyncio
async def test_a_provider_only_sees_the_step_that_was_confirmed(client, db_pool, da_bat):
    """Đơn vị của T2 chưa thấy gì cho tới khi T2 được đồng ý."""
    from src.api.routes import _DEMO_JOBS
    from tests.test_db.conftest import dang_nhap_don_vi

    token, wid = await _toi_de_xuat(client, db_pool, "kh_don_vi_theo_buoc", so_buoc=2)
    _DEMO_JOBS.clear()
    ds = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()["service_proposals"]
    t1, t2 = ds[0], ds[1]
    await client.post(
        f"/api/v1/service-proposals/{t1['proposal_id']}/confirm",
        json={"decision": "confirm"},
        headers=_auth(token),
    )

    for ma in {t1["provider"]["id"], t2["provider"]["id"]}:
        tok, _ = await dang_nhap_don_vi(client, db_pool, f"dv_{ma.lower().replace('-', '')}", don_vi=(ma,))
        thay = [
            i
            for i in (await client.get("/api/v1/service-approvals", headers=_auth(tok))).json()["items"]
            if i["workflow_id"] == wid
        ]
        mong_doi = ["T1"] if ma == t1["provider"]["id"] else []
        assert [i["task_id"] for i in thay] == mong_doi, f"{ma} thấy {thay}"


@pytest.mark.asyncio
async def test_a_cold_read_neither_loses_nor_duplicates_the_list(client, db_pool, da_bat):
    """Đọc lại nhiều lần sau khi mất cache: vẫn đúng hai, không thêm không bớt."""
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_doc_lai_hai_cai", so_buoc=2)
    truoc = await _dem(db_pool, wid)

    ket_qua = []
    for _ in range(3):
        _DEMO_JOBS.clear()
        body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()
        ket_qua.append(tuple(d["proposal_id"] for d in body["service_proposals"]))

    assert len(set(ket_qua)) == 1, f"danh sách đổi giữa các lượt đọc: {ket_qua}"
    assert len(ket_qua[0]) == 2
    assert await _dem(db_pool, wid) == truoc


@pytest.mark.asyncio
async def test_the_summary_names_both_units_when_there_are_two(client, db_pool, da_bat):
    """Câu chat nói ra là có HAI việc.

    Một câu nói về một đơn vị trong khi khách còn hai việc phải bấm là nói dối
    về khối lượng công việc còn lại — họ bấm xong một nút rồi gặp một nút nữa
    mà chưa từng được báo.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_cau_hai_viec", so_buoc=2)
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    summary = body["summary"]
    assert "2 việc" in summary, summary
    for d in body["service_proposals"]:
        assert d["provider"]["name"] in summary, f"thiếu {d['provider']['name']}: {summary}"
    for tu in _TU_CAM:
        assert tu not in summary.lower()


@pytest.mark.asyncio
async def test_the_answer_layer_also_knows_the_customer_is_still_choosing(client, db_pool, da_bat):
    """Lớp SINH CÂU TRẢ LỜI cũng phải thấy đề xuất, không chỉ đường `GET`.

    Hai đường dựng view từ database và cả hai phải xếp hạng loại chờ giống
    nhau: `_demo_workflow_status` cho `GET`, và `_public_view_from_db` cho lớp
    trả lời — đường mọi workflow đi sau restart và sau MỌI lượt quyết định.

    Đo được bằng mutation: bỏ nhánh đề xuất khỏi `_public_view_from_db` KHÔNG
    làm bài kiểm nào đỏ khi mọi bài đều đi qua `GET`. Nhánh ấy vẫn mang trách
    nhiệm thật — nó quyết định KHOÁ câu trả lời và bộ dữ kiện đưa cho model —
    nên nó cần một bài kiểm nhìn thấy được.

    `request_fresh_answer` là cơ chế THẬT, không phải helper của test: mọi
    route quyết định đều gọi nó. Nó ghim khoá vào database TRƯỚC khi gọi model,
    nên khoá đọc lại được mà không cần một lượt gọi model nào.
    """
    from src.api.routes import _DEMO_JOBS, drain_demo_tasks, request_fresh_answer

    token, wid = await _toi_de_xuat(client, db_pool, "kh_lop_tra_loi")
    del token
    _DEMO_JOBS.clear()
    await db_pool.execute(
        "UPDATE workflows SET assistant_for_status = NULL, assistant_answer = NULL WHERE workflow_id = $1::uuid",
        uuid.UUID(wid),
    )

    request_fresh_answer(wid)
    await drain_demo_tasks()

    khoa = await db_pool.fetchval(
        "SELECT assistant_for_status FROM workflows WHERE workflow_id = $1::uuid", uuid.UUID(wid)
    )
    assert khoa == "WAITING_APPROVAL:USER:PROPOSAL", (
        f"lớp trả lời nghĩ workflow đang ở {khoa!r}, không phải đang chờ khách chọn đơn vị"
    )
    assert (await _dem(db_pool, wid))["approvals"] == 0


@pytest.mark.asyncio
async def test_a_proposal_whose_quote_died_is_not_offered(client, db_pool, da_bat):
    """Đề xuất còn `PROPOSED` nhưng chứng từ đã chết thì KHÔNG hiện nút bấm.

    `status='PROPOSED'` một mình là fail-OPEN: chứng từ có thể vừa hết hạn
    trong khi lượt dọn chưa chạy tới, và lúc ấy cột vẫn ghi `PROPOSED`. Dựng
    một cái nút bấm vào là lỗi thì tệ hơn không dựng nút.

    Ở đây có HAI bước: một chết, một sống. Danh sách phải còn đúng cái sống —
    lọc hết hoặc không lọc gì đều sai theo hai hướng khác nhau.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_mot_cai_chet", so_buoc=2)
    _DEMO_JOBS.clear()
    ds = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()["service_proposals"]
    chet = next(d for d in ds if d["task_id"] == "T1")
    await db_pool.execute(
        "UPDATE service_quotes SET valid_until = NOW() - INTERVAL '1 min' "
        "WHERE quote_id = (SELECT quote_id FROM service_provider_proposals WHERE proposal_id = $1::uuid)",
        uuid.UUID(chet["proposal_id"]),
    )

    _DEMO_JOBS.clear()
    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert [d["task_id"] for d in body["service_proposals"]] == ["T2"], body["service_proposals"]
    assert body["provider_proposal"]["task_id"] == "T2"
    # Cột vẫn `PROPOSED` — chưa ai dọn, và một lượt ĐỌC không được dọn hộ.
    assert (
        await db_pool.fetchval(
            "SELECT status FROM service_provider_proposals WHERE proposal_id=$1::uuid",
            uuid.UUID(chet["proposal_id"]),
        )
        == "PROPOSED"
    )


@pytest.mark.asyncio
async def test_when_every_quote_died_there_is_no_confirm_button_at_all(client, db_pool, da_bat):
    """Chết hết thì không còn `service_proposals`, và cũng không có alias."""
    from src.api.routes import _DEMO_JOBS

    token, wid = await _toi_de_xuat(client, db_pool, "kh_chet_het")
    await db_pool.execute(
        "UPDATE service_quotes SET valid_until = NOW() - INTERVAL '1 min' WHERE workflow_id = $1::uuid",
        uuid.UUID(wid),
    )
    _DEMO_JOBS.clear()

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert body["service_proposals"] == []
    assert body["provider_proposal"] is None
    assert body["approval_actor"] != "USER" or body["stage"] != "WAITING_PROVIDER_PROPOSAL"
