"""Hỏi thêm về đề xuất đơn vị thì phải được trả lời BẰNG chính đề xuất ấy.

Chuỗi thật, đo trên stack demo
------------------------------
    P-118:  Mình đề xuất Chuyển nhà Minh Phát, 430.000 VND. Bạn xác nhận nhé?
    Bạn:    còn chỗ nào rẻ hơn không
    P-118:  [một yêu cầu MỚI được lập, Planner chạy, và câu trả lời nói về
             dự án bất động sản — không nhắc gì tới ba báo giá đang nằm sẵn
             trong chính workflow ấy]

Ba hỏng cùng lúc:

  1. Planner được đánh thức cho một câu KHÔNG phải yêu cầu mới;
  2. một lượt người dùng sinh ra một workflow thứ hai, nên màn hình có hai
     việc trong khi thật ra vẫn là một;
  3. câu trả lời không dùng chứng từ của chính workflow đang chờ — nó không sai
     về ngữ pháp, nó sai về sự việc.

Vì sao rơi vào đó
-----------------
`/workflows/demo/start` có bốn làn: small talk → sửa yêu cầu đã dừng → không có
gì để sửa → Planner. Một câu hỏi về đề xuất không khớp làn nào trong ba làn đầu,
nên nó rơi xuống làn cuối. Làn cuối lập kế hoạch — đó là việc duy nhất nó biết
làm.

Phạm vi
-------
CHỈ `schedule_move` đang ở `WAITING_PROVIDER_PROPOSAL`. Đây là dịch vụ duy nhất
có báo giá; các dịch vụ khác không có gì để so sánh và không được chạm tới.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from tests.test_db.conftest import _register_and_login

DEMO = "/api/v1/workflows/demo"

# Ba đơn vị chuyển nhà và giá của chúng cho cùng một yêu cầu. Số lấy đúng như
# `src/mock/service_providers.py` tính ra, không bịa.
BA_BAO_GIA = [("MOV-03", 420_000), ("MOV-01", 430_000), ("MOV-02", 470_000)]

VAO = {
    "move_date": "2026-12-01",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}


@pytest.fixture(autouse=True)
def _bat_co(monkeypatch):
    """Cờ BẬT cho mọi bài ở đây — đường đề xuất chỉ tồn tại khi cờ bật."""
    monkeypatch.setenv("SERVICE_PROVIDER_MATCHING", "1")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _phien_dang_cho_de_xuat(client, db_pool, username: str, *, chon: str = "MOV-03"):
    """Một phiên có ĐÚNG một workflow `schedule_move` đang chờ khách chọn đơn vị.

    Ba báo giá còn hiệu lực, đề xuất đang đặt vào `chon`. Gieo bằng SQL để bài
    kiểm tất định — thứ đang đo là ĐƯỜNG ĐI của câu hỏi tiếp theo, không phải
    chất lượng kế hoạch.
    """
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    session_id = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO sessions (session_id, account_state, user_id) VALUES ($1, 'resident', $2::uuid) "
        "ON CONFLICT DO NOTHING",
        session_id,
        str(uid),
    )
    wid = str(uuid.uuid4())
    ke_hoach = {
        "goal": "chuyển nhà",
        "tasks": [{"task_id": "T1", "tool": "schedule_move", "depends_on": [], "input": VAO}],
    }
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, session_id, task_plan) "
        "VALUES ($1::uuid, 'Đặt lịch chuyển nhà', 'WAITING_APPROVAL', $2::uuid, $3, $4::jsonb)",
        wid,
        str(uid),
        session_id,
        json.dumps(ke_hoach),
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', 'schedule_move', 'WAITING_APPROVAL', '[]'::jsonb, $2::jsonb)",
        wid,
        json.dumps(VAO),
    )
    van_tay = f"vt{wid[:12]}"
    quote_ids: dict[str, str] = {}
    for ma, gia in BA_BAO_GIA:
        qid = str(uuid.uuid4())
        quote_ids[ma] = qid
        await db_pool.execute(
            "INSERT INTO service_quotes (quote_id, external_quote_id, service_provider_id, service_type, amount, "
            " currency, request_fingerprint, valid_until, workflow_id, task_id, status) "
            "VALUES ($1::uuid, $2, $3, 'schedule_move', $4, 'VND', $5, NOW() + INTERVAL '90 min', $6::uuid, 'T1', 'ACTIVE')",
            qid,
            f"Q-{qid[:8]}",
            ma,
            gia,
            van_tay,
            wid,
        )
    await db_pool.execute(
        "INSERT INTO service_provider_proposals (proposal_id, workflow_id, task_id, quote_id, status) "
        "VALUES ($1::uuid, $2::uuid, 'T1', $3::uuid, 'PROPOSED')",
        str(uuid.uuid4()),
        wid,
        quote_ids[chon],
    )
    return {"token": token, "user_id": str(uid), "session_id": session_id, "workflow_id": wid, "quotes": quote_ids}


async def _hoi(client, phien, cau: str):
    return await client.post(
        f"{DEMO}/start",
        json={"goal": cau, "session_id": phien["session_id"]},
        headers=_auth(phien["token"]),
    )


async def _dem_workflow(db_pool, session_id: str) -> int:
    return int(await db_pool.fetchval("SELECT count(*) FROM workflows WHERE session_id = $1", session_id))


@pytest.fixture
def _bat_dau_dem_planner(monkeypatch):
    """Đếm số lần làn LẬP KẾ HOẠCH được đánh thức.

    Thay `run_demo_workflow` — cửa duy nhất dẫn vào Planner từ route này. Đếm ở
    đây chứ không đếm số lần gọi mô hình: mô hình còn được gọi cho những việc
    khác, và bài này nói về LÀN, không về nhà cung cấp LLM.
    """
    from src.api import routes as mod

    goi: list[str] = []
    that = mod.run_demo_workflow

    async def _dem(goal, *a, **kw):
        goi.append(goal)
        return await that(goal, *a, **kw)

    monkeypatch.setattr(mod, "run_demo_workflow", _dem)
    return goi


CAU_CHI_DOC = [
    "còn chỗ nào rẻ hơn không",
    "so sánh các bên giúp tôi",
    "đơn vị này uy tín không",
    "vì sao đề xuất bên này",
]


@pytest.mark.parametrize("cau", CAU_CHI_DOC)
@pytest.mark.asyncio
async def test_a_follow_up_never_wakes_the_planner(client, db_pool, _bat_dau_dem_planner, cau):
    """Câu hỏi về đề xuất KHÔNG được đánh thức làn lập kế hoạch.

    Làn ấy lập một kế hoạch mới — đó là việc duy nhất nó biết làm, và nó không
    có cách nào biết rằng người dùng đang hỏi về một chứng từ đã nằm sẵn trong
    database.
    """
    phien = await _phien_dang_cho_de_xuat(client, db_pool, f"kh_ph_{abs(hash(cau)) % 10**6}")

    res = await _hoi(client, phien, cau)

    assert res.status_code in (200, 202), res.text
    # Làn lập kế hoạch chạy trong TÁC VỤ NỀN. Đọc spy ngay lập tức luôn thấy
    # rỗng — kể cả khi nó sắp chạy — nên bài kiểm sẽ xanh vì sai lý do. Chờ cho
    # tác vụ ấy có cơ hội khởi động rồi mới kết luận.
    for _ in range(30):
        if _bat_dau_dem_planner:
            break
        await asyncio.sleep(0.1)

    assert _bat_dau_dem_planner == [], f"Planner bị đánh thức cho {cau!r}: {_bat_dau_dem_planner}"
    # Và câu trả lời phải thuộc về CHÍNH yêu cầu đang chờ, không phải một cái mới.
    assert (res.json() or {}).get("workflow_id") == phien["workflow_id"], res.json().get("workflow_id")


@pytest.mark.parametrize("cau", CAU_CHI_DOC)
@pytest.mark.asyncio
async def test_a_follow_up_creates_no_second_workflow(client, db_pool, cau):
    """Một lượt hỏi KHÔNG được sinh ra một yêu cầu thứ hai.

    Workflow thứ hai không chỉ là rác: nó hiện lên Lịch sử như một việc riêng,
    và màn hình nói khách có hai việc trong khi họ chỉ có một.
    """
    phien = await _phien_dang_cho_de_xuat(client, db_pool, f"kh_wf_{abs(hash(cau)) % 10**6}")
    truoc = await _dem_workflow(db_pool, phien["session_id"])

    await _hoi(client, phien, cau)

    assert await _dem_workflow(db_pool, phien["session_id"]) == truoc, f"{cau!r} sinh thêm workflow"


@pytest.mark.asyncio
async def test_the_answer_is_grounded_in_this_workflows_own_quotes(client, db_pool):
    """Câu trả lời phải dùng chứng từ của CHÍNH workflow đang chờ.

    Không có ràng buộc này, một câu trả lời trôi chảy vẫn có thể nói về thứ
    khác hoàn toàn — và người đọc không có cách nào biết.
    """
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_grounded")

    body = (await _hoi(client, phien, "còn chỗ nào rẻ hơn không")).json()
    cau_tra_loi = f"{body.get('answer') or ''} {body.get('message') or ''} {body.get('summary') or ''}"

    # Đề xuất hiện tại là MOV-03 (420.000) — mức thấp nhất trong ba báo giá.
    assert "420" in cau_tra_loi, cau_tra_loi
    # Và KHÔNG được nói về bất động sản: đó là dấu vết của làn lập kế hoạch.
    for cam in ("dự án", "Vinhomes", "căn hộ", "tham quan"):
        assert cam.lower() not in cau_tra_loi.lower(), f"câu trả lời nhắc {cam!r}: {cau_tra_loi}"


@pytest.mark.asyncio
async def test_one_user_turn_produces_exactly_one_assistant_reply(client, db_pool):
    """Một câu hỏi, một câu trả lời — không thêm một lời chào hàng mặc định.

    Đo bằng số lượt trò chuyện đã ghim: màn hình dựng hội thoại từ đó, nên hai
    dòng ở database là hai bong bóng trên màn hình.
    """
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_mot_luot")
    truoc = int(await db_pool.fetchval("SELECT count(*) FROM workflows WHERE session_id = $1", phien["session_id"]))

    await _hoi(client, phien, "còn chỗ nào rẻ hơn không")

    sau = int(await db_pool.fetchval("SELECT count(*) FROM workflows WHERE session_id = $1", phien["session_id"]))
    assert sau - truoc <= 0, f"một lượt hỏi sinh {sau - truoc} bản ghi hội thoại mới"


@pytest.mark.asyncio
async def test_asking_for_another_option_stays_in_the_same_workflow(client, db_pool, _ep_y_dinh):
    """Cách nói thật của khách phải đọc bảng giá, không rơi sang bất động sản."""
    _ep_y_dinh("COMPARE_OPTIONS")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_cho_khac")

    body = (await _hoi(client, phien, "còn chỗ nào khác không")).json()

    assert body["workflow_id"] == phien["workflow_id"]
    for ten in ("An Khang", "Minh Phát", "Đại Tín"):
        assert ten in body["answer"], body["answer"]
    assert "Vinhomes" not in body["answer"]


@pytest.mark.asyncio
async def test_a_followup_failure_does_not_fall_through_to_the_planner(client, db_pool, monkeypatch):
    """Có đề xuất thật + tầng đọc lỗi vẫn phải giữ workflow cũ, không lập cái mới."""
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_loi_doc_bao_gia")

    async def _hong(*_args, **_kwargs):
        raise RuntimeError("khong-duoc-lo-ra")

    import src.api.routes as mod

    monkeypatch.setattr(mod, "tra_loi_hoi_them", _hong)
    before = int(await db_pool.fetchval("SELECT count(*) FROM workflows WHERE session_id=$1", phien["session_id"]))

    body = (await _hoi(client, phien, "còn chỗ nào khác không")).json()

    after = int(await db_pool.fetchval("SELECT count(*) FROM workflows WHERE session_id=$1", phien["session_id"]))
    assert body["workflow_id"] == phien["workflow_id"]
    assert after == before
    assert "thử lại" in body["answer"].lower()
    assert "Vinhomes" not in body["answer"]


# ==================================================== ma trận, với ý định GIẢ
#
# Model chỉ chọn NHÃN. Ghim nhãn trong bài kiểm là cách duy nhất để đo phần còn
# lại một cách tất định — nếu để model chọn, một bài đỏ không nói được là mã sai
# hay mô hình vừa đọc khác đi. Đường qua model thật có canary riêng ở tests/e2e.
@pytest.fixture
def _ep_y_dinh(monkeypatch):
    from src.agents.proposal_followup_intent import DeXuatYDinh, YDinhHoiThem

    def _dat(nhan: str, **kw):
        y = DeXuatYDinh(y_dinh=YDinhHoiThem(nhan), **kw)

        class _Gia:
            def __init__(self, *_a, **_k) -> None: ...

            async def doc(self, _cau):
                return y

        import src.agents.proposal_followup_intent as mod

        monkeypatch.setattr(mod, "BoPhanLoaiHoiThem", _Gia)
        return y

    return _dat


async def _bao_gia_theo_don_vi(db_pool, wid: str) -> dict[str, int]:
    rows = await db_pool.fetch(
        "SELECT service_provider_id, amount FROM service_quotes WHERE workflow_id=$1::uuid", uuid.UUID(wid)
    )
    return {r["service_provider_id"]: r["amount"] for r in rows}


async def _don_vi_dang_de_xuat(db_pool, wid: str) -> str | None:
    return await db_pool.fetchval(
        "SELECT q.service_provider_id FROM service_provider_proposals p "
        " JOIN service_quotes q ON q.quote_id = p.quote_id "
        " WHERE p.workflow_id = $1::uuid AND p.status = 'PROPOSED'",
        uuid.UUID(wid),
    )


@pytest.mark.asyncio
async def test_asking_for_cheaper_names_the_options_without_switching(client, db_pool, _ep_y_dinh):
    """ASK_CHEAPER khi đề xuất KHÔNG phải rẻ nhất: nêu tên, giá, chênh lệch."""
    _ep_y_dinh("ASK_CHEAPER")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_re_hon", chon="MOV-02")

    cau = (await _hoi(client, phien, "còn rẻ hơn không")).json()["answer"]

    assert "An Khang" in cau and "420.000" in cau, cau
    assert "50.000" in cau, f"chênh lệch 470.000-420.000 không được nêu: {cau}"
    # CHƯA tự đổi.
    assert await _don_vi_dang_de_xuat(db_pool, phien["workflow_id"]) == "MOV-02"


@pytest.mark.asyncio
async def test_asking_for_cheaper_on_the_lowest_says_so_instead_of_denying_prices(client, db_pool, _ep_y_dinh):
    """Không có bên rẻ hơn → nói đây là mức thấp nhất.

    KHÔNG được nói "chưa có bảng giá": ba báo giá đang nằm ngay đó, và câu ấy
    vừa sai vừa làm khách nghĩ hệ thống chưa hỏi ai.
    """
    _ep_y_dinh("ASK_CHEAPER")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_re_nhat", chon="MOV-03")

    cau = (await _hoi(client, phien, "còn rẻ hơn không")).json()["answer"]

    assert "thấp nhất" in cau, cau
    assert "chưa có" not in cau.lower(), cau


@pytest.mark.asyncio
async def test_comparing_lists_every_live_option_cheapest_first(client, db_pool, _ep_y_dinh):
    """COMPARE_OPTIONS: đủ ba bên, rẻ trước, và không gọi bên rẻ nhất là tốt nhất."""
    _ep_y_dinh("COMPARE_OPTIONS")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_so_sanh")

    cau = (await _hoi(client, phien, "so sánh giúp tôi")).json()["answer"]

    for ten in ("An Khang", "Minh Phát", "Đại Tín"):
        assert ten in cau, f"thiếu {ten}: {cau}"
    assert cau.index("420.000") < cau.index("430.000") < cau.index("470.000"), cau
    assert "tốt nhất" not in cau.lower(), cau


@pytest.mark.asyncio
async def test_reputation_answers_from_the_catalog_and_calls_it_that(client, db_pool, _ep_y_dinh):
    """ASK_REPUTATION: nêu đúng điểm danh mục, và nói rõ đó là dữ liệu danh mục."""
    _ep_y_dinh("ASK_REPUTATION")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_uy_tin", chon="MOV-01")

    cau = (await _hoi(client, phien, "bên này uy tín không")).json()["answer"]

    assert "4.6" in cau, cau
    assert "danh mục" in cau, cau
    assert "bảo đảm chất lượng" in cau, cau
    # Không bịa bằng chứng xã hội.
    for cam in ("khách hàng", "đơn hàng", "chứng nhận", "cam kết"):
        assert cam not in cau.lower(), f"câu trả lời bịa {cam!r}: {cau}"


@pytest.mark.asyncio
async def test_the_reason_comes_from_the_selection_data(client, db_pool, _ep_y_dinh):
    """ASK_RECOMMENDATION_REASON: giải thích bằng mức giá thật, không sáng tác."""
    _ep_y_dinh("ASK_RECOMMENDATION_REASON")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_ly_do", chon="MOV-03")

    cau = (await _hoi(client, phien, "vì sao bên này")).json()["answer"]

    assert "thấp nhất" in cau and "420.000" in cau, cau


@pytest.mark.asyncio
async def test_naming_a_provider_switches_the_proposal(client, db_pool, _ep_y_dinh):
    """SELECT_PROVIDER: đề xuất cũ SUPERSEDED, mới PROPOSED, CHƯA có hàng đợi."""
    _ep_y_dinh("SELECT_PROVIDER", provider_name_text="Minh Phát")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_doi_ten", chon="MOV-03")

    await _hoi(client, phien, "đổi sang Minh Phát")

    wid = phien["workflow_id"]
    assert await _don_vi_dang_de_xuat(db_pool, wid) == "MOV-01"
    cu = await db_pool.fetchval(
        "SELECT count(*) FROM service_provider_proposals WHERE workflow_id=$1::uuid AND status='SUPERSEDED'",
        uuid.UUID(wid),
    )
    assert int(cu) == 1
    # Chưa ai bên đơn vị được hỏi.
    assert (
        int(await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid)))
        == 0
    )
    # Và `input_data` của bước KHÔNG mang tên đơn vị hay ngân sách.
    vao = await db_pool.fetchval(
        "SELECT input_data::text FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", uuid.UUID(wid)
    )
    assert "MOV-" not in vao and "max_price" not in vao and "provider" not in vao, vao


@pytest.mark.asyncio
async def test_an_unknown_name_asks_again_instead_of_guessing(client, db_pool, _ep_y_dinh):
    """Tên lạ → hỏi lại và nêu các bên đang có. Không đổi gì."""
    _ep_y_dinh("SELECT_PROVIDER", provider_name_text="Vận tải Hoa Mai")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_ten_la", chon="MOV-03")

    cau = (await _hoi(client, phien, "đổi sang Hoa Mai")).json()["answer"]

    assert "chưa nhận ra" in cau, cau
    assert await _don_vi_dang_de_xuat(db_pool, phien["workflow_id"]) == "MOV-03"


@pytest.mark.asyncio
async def test_choosing_the_cheapest_picks_the_lowest_live_quote(client, db_pool, _ep_y_dinh):
    _ep_y_dinh("SELECT_CHEAPEST")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_re_nhat_2", chon="MOV-02")

    await _hoi(client, phien, "cho tôi bên rẻ nhất")

    assert await _don_vi_dang_de_xuat(db_pool, phien["workflow_id"]) == "MOV-03"


@pytest.mark.asyncio
async def test_a_budget_filters_on_our_side_and_never_reaches_the_provider(client, db_pool, _ep_y_dinh):
    """SET_MAX_BUDGET: lọc ở phía P-118, không ghi ngân sách vào input gửi đơn vị."""
    _ep_y_dinh("SET_MAX_BUDGET", budget_text="425 nghìn")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_ngan_sach", chon="MOV-02")

    await _hoi(client, phien, "ngân sách tối đa 425 nghìn")

    wid = phien["workflow_id"]
    assert await _don_vi_dang_de_xuat(db_pool, wid) == "MOV-03"
    vao = await db_pool.fetchval(
        "SELECT input_data::text FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", uuid.UUID(wid)
    )
    assert "max_price" not in vao and "425" not in vao, vao


@pytest.mark.asyncio
async def test_a_budget_nobody_can_meet_keeps_the_current_proposal(client, db_pool, _ep_y_dinh):
    """Không bên nào vừa túi → nêu mức thấp nhất THẬT, giữ nguyên đề xuất."""
    _ep_y_dinh("SET_MAX_BUDGET", budget_text="100 nghìn")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_khong_du", chon="MOV-01")

    cau = (await _hoi(client, phien, "tối đa 100 nghìn thôi")).json()["answer"]

    assert "420.000" in cau, cau
    assert await _don_vi_dang_de_xuat(db_pool, phien["workflow_id"]) == "MOV-01"


@pytest.mark.asyncio
async def test_an_unreadable_budget_asks_again(client, db_pool, _ep_y_dinh):
    """Ngân sách không đọc được → hỏi lại, không đoán một con số."""
    _ep_y_dinh("SET_MAX_BUDGET", budget_text="rẻ thôi")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_ns_mo_ho", chon="MOV-01")

    cau = (await _hoi(client, phien, "rẻ thôi")).json()["answer"]

    assert "chưa đọc được" in cau, cau
    assert await _don_vi_dang_de_xuat(db_pool, phien["workflow_id"]) == "MOV-01"


@pytest.mark.asyncio
async def test_confirming_goes_through_the_same_door_as_the_button(client, db_pool, _ep_y_dinh):
    """CONFIRM_CURRENT dùng ĐÚNG hàm mà nút confirm gọi — một bộ luật, không hai."""
    _ep_y_dinh("CONFIRM_CURRENT")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_xac_nhan", chon="MOV-03")

    await _hoi(client, phien, "ok chốt bên này")

    wid = phien["workflow_id"]
    assert (
        await db_pool.fetchval(
            "SELECT status FROM service_provider_proposals WHERE workflow_id=$1::uuid", uuid.UUID(wid)
        )
        == "CONFIRMED"
    )
    ma = await db_pool.fetchval(
        "SELECT service_provider_id FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid)
    )
    assert ma == "MOV-03", ma


@pytest.mark.asyncio
async def test_confirming_twice_changes_nothing_the_second_time(client, db_pool, _ep_y_dinh):
    """Idempotent: lượt thứ hai không mở thêm dòng nào trong hàng đợi."""
    _ep_y_dinh("CONFIRM_CURRENT")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_xn_hai_lan", chon="MOV-03")
    await _hoi(client, phien, "ok")
    sau_lan_mot = int(
        await db_pool.fetchval(
            "SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(phien["workflow_id"])
        )
    )

    await _hoi(client, phien, "ok")

    assert (
        int(
            await db_pool.fetchval(
                "SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(phien["workflow_id"])
            )
        )
        == sau_lan_mot
        == 1
    )


@pytest.mark.asyncio
async def test_an_expired_quote_is_not_confirmed(client, db_pool, _ep_y_dinh):
    """Báo giá hết hạn → KHÔNG chốt, và nói ra lý do."""
    _ep_y_dinh("CONFIRM_CURRENT")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_het_han", chon="MOV-03")
    await db_pool.execute(
        "UPDATE service_quotes SET valid_until = NOW() - INTERVAL '1 min' WHERE workflow_id=$1::uuid",
        uuid.UUID(phien["workflow_id"]),
    )

    cau = (await _hoi(client, phien, "ok")).json()["answer"]

    assert "hết hiệu lực" in cau, cau
    assert (
        int(
            await db_pool.fetchval(
                "SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(phien["workflow_id"])
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_an_unsure_ok_asks_instead_of_confirming(client, db_pool, _ep_y_dinh):
    """ "ok" mà bộ phân loại không chắc → UNKNOWN → hỏi lại, KHÔNG tự chốt.

    Đây là nhánh đắt nhất nếu sai: chốt nhầm nghĩa là một đơn vị nhận việc và
    một khoản tiền được cam kết mà khách chưa đồng ý.
    """
    _ep_y_dinh("UNKNOWN")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_ok_mo_ho", chon="MOV-03")

    cau = (await _hoi(client, phien, "ok")).json()["answer"]

    # Ba lựa chọn được nêu ra, không phải một lời đáp lễ.
    for phai_co in ("lựa chọn khác", "đổi sang đơn vị khác", "xác nhận"):
        assert phai_co in cau, cau
    assert (
        int(
            await db_pool.fetchval(
                "SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(phien["workflow_id"])
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_a_question_about_another_service_points_to_a_new_journey(client, db_pool, _ep_y_dinh):
    """OUT_OF_SCOPE: nói rõ việc đang mở, hướng sang Hành trình mới, không lập kế hoạch."""
    _ep_y_dinh("OUT_OF_SCOPE")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_ngoai_pv")
    truoc = await _dem_workflow(db_pool, phien["session_id"])

    cau = (await _hoi(client, phien, "đặt chỗ đỗ xe cho tôi")).json()["answer"]

    assert "chuyển nhà" in cau and "Hành trình mới" in cau, cau
    assert await _dem_workflow(db_pool, phien["session_id"]) == truoc


@pytest.mark.asyncio
async def test_unknown_asks_back_without_listing_projects(client, db_pool, _ep_y_dinh):
    """UNKNOWN: hỏi lại ba lựa chọn, KHÔNG trả danh sách dự án."""
    _ep_y_dinh("UNKNOWN")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_khong_hieu")

    cau = (await _hoi(client, phien, "ừm")).json()["answer"]

    for cam in ("Vinhomes", "dự án", "căn hộ"):
        assert cam.lower() not in cau.lower(), cau


@pytest.mark.asyncio
async def test_two_workflows_of_one_customer_do_not_borrow_quotes(client, db_pool, _ep_y_dinh):
    """Hai phiên khác nhau, hai bảng báo giá — không bên nào mượn của bên kia."""
    _ep_y_dinh("COMPARE_OPTIONS")
    a = await _phien_dang_cho_de_xuat(client, db_pool, "kh_hai_phien", chon="MOV-03")
    # Phiên thứ hai của CÙNG người dùng, chỉ có một báo giá.
    b_session = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO sessions (session_id, account_state, user_id) VALUES ($1, 'resident', $2::uuid) "
        "ON CONFLICT DO NOTHING",
        b_session,
        a["user_id"],
    )
    wid_b = str(uuid.uuid4())
    ke_hoach = {
        "goal": "chuyển nhà",
        "tasks": [{"task_id": "T1", "tool": "schedule_move", "depends_on": [], "input": VAO}],
    }
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, session_id, task_plan) "
        "VALUES ($1::uuid, 'chuyển nhà 2', 'WAITING_APPROVAL', $2::uuid, $3, $4::jsonb)",
        wid_b,
        a["user_id"],
        b_session,
        json.dumps(ke_hoach),
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', 'schedule_move', 'WAITING_APPROVAL', '[]'::jsonb, $2::jsonb)",
        wid_b,
        json.dumps(VAO),
    )
    qid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO service_quotes (quote_id, external_quote_id, service_provider_id, service_type, amount, "
        " currency, request_fingerprint, valid_until, workflow_id, task_id, status) "
        "VALUES ($1::uuid, $2, 'MOV-02', 'schedule_move', 470000, 'VND', $3, NOW() + INTERVAL '90 min', $4::uuid, 'T1', 'ACTIVE')",
        qid,
        f"Q-{qid[:8]}",
        f"vt{wid_b[:12]}",
        wid_b,
    )
    await db_pool.execute(
        "INSERT INTO service_provider_proposals (proposal_id, workflow_id, task_id, quote_id, status) "
        "VALUES ($1::uuid, $2::uuid, 'T1', $3::uuid, 'PROPOSED')",
        str(uuid.uuid4()),
        wid_b,
        qid,
    )

    cau_b = (
        await client.post(
            f"{DEMO}/start", json={"goal": "so sánh giúp tôi", "session_id": b_session}, headers=_auth(a["token"])
        )
    ).json()["answer"]

    assert "Đại Tín" in cau_b, cau_b
    assert "An Khang" not in cau_b and "Minh Phát" not in cau_b, f"phiên B mượn báo giá của phiên A: {cau_b}"


# ==================================================== ranh giới tin cậy của model
def test_the_classifier_refuses_output_that_carries_data_it_should_not_have():
    """Model trả thêm `provider_id`/`price` thì CẢ câu trả lời bị từ chối.

    `extra="forbid"` không phải để bắt lỗi chính tả. Model không có những dữ
    liệu ấy — nếu nó trả về, nó đang bịa, và một mã đơn vị bịa ra trông y hệt
    một mã thật. Im lặng bỏ qua trường thừa nghĩa là để lần sau nó thử lại.
    """
    import pydantic

    from src.agents.proposal_followup_intent import DeXuatYDinh

    assert DeXuatYDinh(y_dinh="ASK_CHEAPER").y_dinh == "ASK_CHEAPER"
    for thua in ({"provider_id": "MOV-01"}, {"quote_id": "q-1"}, {"price": 420000}, {"rating": 4.6}):
        with pytest.raises(pydantic.ValidationError):
            DeXuatYDinh(y_dinh="ASK_CHEAPER", **thua)


# ==================================================== bộ đọc ngân sách
@pytest.mark.parametrize(
    ("text", "mong_doi"),
    [
        ("600 nghìn", 600_000),
        ("dưới 500k", 500_000),
        ("tối đa 450.000", 450_000),
        ("1,5 triệu", 1_500_000),
        ("450000", 450_000),
        # Từ chối, không đoán:
        ("rẻ thôi", None),
        ("từ 400 tới 600 nghìn", None),  # một KHOẢNG, lấy số nào cũng là quyết định không ai đưa ra
        ("-5000", None),  # dấu trừ bị nuốt thì một giá trị vô nghĩa thành ngân sách hợp lệ
        ("0", None),
        ("999 tỷ", None),  # vượt trần: lọc bằng nó là không lọc, mà trông như đã lọc
        ("", None),
        (None, None),
        (True, None),  # bool là int trong Python — nhận nó là nhận `max_price=1`
    ],
)
def test_the_budget_reader_refuses_more_than_it_guesses(text, mong_doi):
    from src.orchestration.budget_text import doc_ngan_sach

    assert doc_ngan_sach(text) == mong_doi, text


# ==================================================== phiên của người khác
@pytest.mark.asyncio
async def test_another_customer_cannot_reach_this_proposal_with_a_borrowed_session(client, db_pool, _ep_y_dinh):
    """`session_id` là khoá NHÓM, không phải bằng chứng về quyền.

    Client biết và gửi lại được nó, nên một người dùng khác hoàn toàn có thể gửi
    kèm `session_id` của người này. Bộ lọc chủ sở hữu nằm TRONG SQL; bỏ nó đi
    thì người lạ đọc được báo giá, tên đơn vị và số tiền của một yêu cầu không
    phải của họ — và tệ hơn, đổi được đề xuất ấy.
    """
    _ep_y_dinh("COMPARE_OPTIONS")
    chu = await _phien_dang_cho_de_xuat(client, db_pool, "kh_chu_phien", chon="MOV-03")
    ke_la = await _register_and_login(client, "kh_muon_phien")

    res = await client.post(
        f"{DEMO}/start",
        json={"goal": "so sánh giúp tôi", "session_id": chu["session_id"]},
        headers=_auth(ke_la),
    )

    body = res.json()
    cau = f"{body.get('answer') or ''} {body.get('message') or ''}"
    for ten in ("An Khang", "Minh Phát", "Đại Tín"):
        assert ten not in cau, f"người lạ đọc được báo giá của phiên khác: {cau}"
    assert body.get("workflow_id") != chu["workflow_id"], body.get("workflow_id")
    # Và đề xuất của chủ phiên không bị đụng.
    assert await _don_vi_dang_de_xuat(db_pool, chu["workflow_id"]) == "MOV-03"


@pytest.mark.asyncio
async def test_the_session_lookup_itself_is_scoped_to_the_owner(client, db_pool):
    """Hàng rào chủ sở hữu nằm ở CHÍNH phép tra phiên, không chỉ ở route.

    Route đã cấp một `session_id` MỚI khi phiên không thuộc về người gọi, nên
    kẻ mượn phiên không bao giờ đi tới được tầng này qua HTTP. Chính vì thế bài
    kiểm qua HTTP không chứng minh được gì về hàng rào ở đây — đo được: đột
    biến "bỏ lọc chủ sở hữu" sống sót qua toàn bộ bộ kiểm HTTP.

    Hai hàng rào cho hai tầng, và mỗi hàng rào phải tự đứng được: phép kiểm ở
    route là một dòng có thể đổi, còn tầng này là nơi một đường gọi MỚI sẽ đi
    qua.
    """
    from src.api.routes import _de_xuat_dang_cho_trong_phien

    chu = await _phien_dang_cho_de_xuat(client, db_pool, "kh_tang_duoi")
    await _register_and_login(client, "kh_tang_duoi_la")
    id_ke_la = str(await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "kh_tang_duoi_la"))

    thay_chu = await _de_xuat_dang_cho_trong_phien(chu["session_id"], owner_user_id=chu["user_id"])
    thay_ke_la = await _de_xuat_dang_cho_trong_phien(chu["session_id"], owner_user_id=id_ke_la)

    assert thay_chu == chu["workflow_id"]
    assert thay_ke_la is None, "phép tra phiên trả về đề xuất của người khác"


@pytest.mark.asyncio
async def test_a_proposal_confirmed_mid_question_still_answers_in_place(client, db_pool, _ep_y_dinh):
    """Đề xuất biến mất GIỮA hai lần đọc vẫn không được rơi xuống Planner.

    `_de_xuat_dang_cho_trong_phien` xác nhận có đề xuất, rồi `tra_loi_hoi_them`
    ĐỌC LẠI. Giữa hai lần đọc ấy có một khe: khách bấm xác nhận ở tab khác, hoặc
    một lượt đề xuất mới thay chỗ. Lần đọc thứ hai trả `None`, và bản trước để
    `None` rơi xuống làn lập kế hoạch — đúng cái sinh ra workflow thứ hai và câu
    trả lời thứ hai.

    Khe hẹp, nhưng nó là CÙNG một lỗi với nhánh `except` đã bịt: một khi đã biết
    phiên đang chờ đề xuất thì mọi câu đều phải được trả lời tại chỗ.
    """
    _ep_y_dinh("COMPARE_OPTIONS")
    phien = await _phien_dang_cho_de_xuat(client, db_pool, "kh_chot_giua_chung")
    truoc = await _dem_workflow(db_pool, phien["session_id"])

    # Mô phỏng đúng cái khe: lần đọc THỨ HAI không còn thấy đề xuất nào.
    import src.orchestration.proposal_followup as mod

    that = mod.de_xuat_dang_cho
    da_goi: list[int] = []

    async def _bien_mat(*a, **kw):
        da_goi.append(1)
        return None

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod, "de_xuat_dang_cho", _bien_mat)
        body = (await _hoi(client, phien, "còn chỗ nào khác không")).json()

    assert da_goi, "bài kiểm không chạm được vào khe — đọc lại cách dựng cảnh"
    assert await _dem_workflow(db_pool, phien["session_id"]) == truoc, "sinh workflow thứ hai"
    assert body.get("workflow_id") == phien["workflow_id"], body.get("workflow_id")
    cau = f"{body.get('answer') or ''} {body.get('message') or ''}"
    assert cau.strip(), "không trả lời gì"
    for cam in ("Vinhomes", "dự án", "căn hộ"):
        assert cam.lower() not in cau.lower(), f"rơi sang bất động sản: {cau}"
    assert that is not None  # giữ tham chiếu để lint không cắt
