"""Lịch tham quan cũng thuộc về MỘT đơn vị — cổng /viewing-approvals.

Vì sao có file này
------------------
Hàng đợi dịch vụ (`/service-approvals`) đã lọc theo đơn vị từ Phase A. Cổng
THAM QUAN là một route thứ hai đọc cùng bảng `service_approvals` (qua view
`viewing_approvals`), và nó KHÔNG lọc gì cả: tham số người duyệt ở route đọc
còn được đặt tên `_reviewer` — nó không được dùng.

Đo được trước khi sửa, trên stack thật: hai đơn vị khác nhau gọi
`GET /viewing-approvals` và nhận về 100 dòng GIỐNG HỆT nhau, kèm tên và số điện
thoại người yêu cầu. Rồi một đơn vị chuyển nhà từ chối được một lịch tham quan
của đơn vị kinh doanh, HTTP 200, và `decided_by` ghi tên họ.

Hai cổng đọc cùng một bảng thì phải cùng một luật. Một cổng lọc và một cổng
không lọc không phải "một chỗ quên" — nó là lời mời đi đường vòng.

Điều file này KHÔNG kiểm
------------------------
Đường DUYỆT gọi Tour provider thật (~30 giây, materialize lịch rồi đặt xe).
Mọi bài ở đây dùng TỪ CHỐI, vì thứ đang đo là quyền sở hữu chứ không phải
materialize — và một bài kiểm phụ thuộc mạng là một bài kiểm sẽ đỏ vì lý do
khác.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from tests.test_db.conftest import _register_and_login

VIEWING = "/api/v1/viewing-approvals"
SERVICE = "/api/v1/service-approvals"

BQL_SALES = "BQL-SALES"
MOV_01 = "MOV-01"
MOV_02 = "MOV-02"
FIX_01 = "FIX-01"

# Chuỗi CHỈ có trong dữ liệu gieo. Nếu nó xuất hiện trong response của một đơn
# vị không sở hữu thì PII đã rò, dù dòng ấy có hiện ra như một mục hay không.
TEN_MOI = "Trần Thị Chim Hoàng Yến"
SDT_MOI = "0900111222"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _tai_khoan(client, db_pool, username: str, role: str | None = None) -> tuple[str, str]:
    """Token cấp SAU khi role đã đổi — `require_roles` đọc role qua JWT."""
    await _register_and_login(client, username)
    if role is not None:
        await db_pool.execute("UPDATE users SET role = $2 WHERE username = $1", username, role)
    token = await _register_and_login(client, username)
    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    return token, str(user_id)


async def _gan_don_vi(db_pool, user_id: str, *don_vi: str) -> None:
    for ma in don_vi:
        await db_pool.execute(
            "INSERT INTO service_provider_accounts (user_id, service_provider_id) "
            "VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING",
            user_id,
            ma,
        )


async def _lich_tham_quan(db_pool, owner_user_id: str, service_provider_id: str | None) -> str:
    """Một lịch tham quan đang chờ duyệt. `None` = dòng cũ chưa có đơn vị."""
    wid = str(uuid.uuid4())
    chi_tiet = {
        "project_id": "VH-SGP",
        "project_name": "Vinhomes Sài Gòn Park",
        "viewing_date": "2026-12-01",
        "viewing_time": "09:00",
        "passenger_count": 2,
        "wants_shuttle": False,
    }
    ke_hoach = {
        "goal": "xem nhà",
        "tasks": [{"task_id": "T1", "tool": "schedule_property_viewing", "depends_on": [], "input": chi_tiet}],
    }
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, task_plan) "
        "VALUES ($1::uuid, 'xem nhà', 'WAITING_APPROVAL', $2::uuid, $3::jsonb)",
        wid,
        owner_user_id,
        json.dumps(ke_hoach),
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', 'schedule_property_viewing', 'WAITING_APPROVAL', '[]'::jsonb, $2::jsonb)",
        wid,
        json.dumps(chi_tiet),
    )
    await db_pool.execute(
        "INSERT INTO service_approvals "
        "(workflow_id, task_id, tool, service_label, details, status, service_provider_id, "
        " applicant_user_id, applicant_name, applicant_phone) "
        "VALUES ($1::uuid, 'T1', 'schedule_property_viewing', 'Lịch tham quan', $2::jsonb, 'AWAITING', $3, "
        "        $4::uuid, $5, $6)",
        wid,
        json.dumps(chi_tiet),
        service_provider_id,
        owner_user_id,
        TEN_MOI,
        SDT_MOI,
    )
    return wid


def _ma(body: dict) -> set[str]:
    return {m["workflow_id"] for m in (body.get("items") or [])}


async def _bo_ba(client, db_pool, hau_to: str):
    """Khách + bốn vai, và một lịch tham quan thuộc BQL-SALES."""
    _, khach_id = await _tai_khoan(client, db_pool, f"kh_tq_{hau_to}")
    tok_a, id_a = await _tai_khoan(client, db_pool, f"dv_sales_{hau_to}", role="provider")
    tok_b, id_b = await _tai_khoan(client, db_pool, f"dv_chuyennha_{hau_to}", role="provider")
    tok_c, id_c = await _tai_khoan(client, db_pool, f"dv_kiemnhiem_{hau_to}", role="provider")
    await _gan_don_vi(db_pool, id_a, BQL_SALES)
    await _gan_don_vi(db_pool, id_b, MOV_02)
    # C giữ NHIỀU đơn vị nhưng KHÔNG có BQL-SALES — nhiều quyền không phải mọi quyền.
    await _gan_don_vi(db_pool, id_c, MOV_01, MOV_02, FIX_01)
    wid = await _lich_tham_quan(db_pool, khach_id, BQL_SALES)
    return {"khach_id": khach_id, "a": tok_a, "b": tok_b, "c": tok_c, "wid": wid}


# ======================================================== đọc danh sách
@pytest.mark.asyncio
async def test_only_the_owning_unit_sees_the_viewing_request(client, db_pool):
    """A thấy; B và C không. C giữ ba đơn vị, không đơn vị nào là BQL-SALES."""
    d = await _bo_ba(client, db_pool, "doc")

    thay_a = _ma((await client.get(f"{VIEWING}?status=AWAITING", headers=_auth(d["a"]))).json())
    thay_b = _ma((await client.get(f"{VIEWING}?status=AWAITING", headers=_auth(d["b"]))).json())
    thay_c = _ma((await client.get(f"{VIEWING}?status=AWAITING", headers=_auth(d["c"]))).json())

    assert d["wid"] in thay_a, "đơn vị sở hữu không thấy việc của mình"
    assert d["wid"] not in thay_b, "đơn vị chuyển nhà đọc được lịch tham quan của đơn vị khác"
    assert d["wid"] not in thay_c, "tài khoản kiêm nhiệm đọc được ngoài tập đơn vị của nó"


@pytest.mark.asyncio
async def test_the_history_view_filters_too(client, db_pool):
    """Lịch sử là cổng đọc THỨ HAI trên cùng route — nó cũng phải lọc.

    Không truyền `status` nghĩa là "cả ba trạng thái", và đó chính là đường mà
    một bộ lọc gắn nhầm chỗ sẽ bỏ sót.
    """
    d = await _bo_ba(client, db_pool, "lichsu")
    await db_pool.execute(
        "UPDATE service_approvals SET status='REJECTED', decided_at=NOW() WHERE workflow_id=$1::uuid",
        d["wid"],
    )

    assert d["wid"] in _ma((await client.get(VIEWING, headers=_auth(d["a"]))).json())
    assert d["wid"] not in _ma((await client.get(VIEWING, headers=_auth(d["b"]))).json())
    assert d["wid"] not in _ma((await client.get(VIEWING, headers=_auth(d["c"]))).json())


@pytest.mark.asyncio
async def test_a_row_without_a_unit_is_invisible_to_everyone(client, db_pool):
    """`service_provider_id IS NULL` → không đơn vị nào thấy. Fail-closed.

    Mặc định "chưa gán thì ai cũng thấy" biến mọi dòng lịch sử thành lỗ hổng
    ngay khi cột được thêm — và màn hình trông vẫn đúng.
    """
    _, khach_id = await _tai_khoan(client, db_pool, "kh_tq_null")
    tok, uid = await _tai_khoan(client, db_pool, "dv_tq_null", role="provider")
    await _gan_don_vi(db_pool, uid, BQL_SALES)
    cu = await _lich_tham_quan(db_pool, khach_id, None)

    # CẢ HAI nhánh truy vấn. Có `status` và không có `status` là hai câu SQL
    # khác nhau, và một mệnh đề lọc sửa ở một câu thì câu kia vẫn hở. Bản đầu
    # của bài này chỉ hỏi một nhánh, và đột biến "cho NULL đi qua" sống sót.
    for duong in (VIEWING, f"{VIEWING}?status=AWAITING"):
        assert cu not in _ma((await client.get(duong, headers=_auth(tok))).json()), duong

    # Và không quyết định được: dòng không chủ không thuộc về ai.
    res = await client.post(
        f"{VIEWING}/{cu}/decide",
        json={"decision": "reject", "reject_reason": "x", "reject_code": "OTHER"},
        headers=_auth(tok),
    )
    assert res.status_code == 404, res.status_code


@pytest.mark.asyncio
async def test_a_provider_with_no_mapping_sees_nothing(client, db_pool):
    await _bo_ba(client, db_pool, "chuagan")
    tok, _ = await _tai_khoan(client, db_pool, "dv_tq_chuagan", role="provider")

    assert _ma((await client.get(VIEWING, headers=_auth(tok))).json()) == set()


# ======================================================== PII
@pytest.mark.asyncio
async def test_the_applicant_pii_never_reaches_a_unit_that_does_not_own_the_row(client, db_pool):
    """Tên và số điện thoại người yêu cầu chỉ đi qua SAU bộ lọc quyền sở hữu.

    Kiểm trên TOÀN BỘ thân response, không phải trên danh sách đã lọc: ẩn dòng
    nhưng vẫn để PII lọt qua một trường khác là vá nửa vời, và nửa còn lại là
    nửa rò.
    """
    d = await _bo_ba(client, db_pool, "pii")

    for vai, tok in (("B", d["b"]), ("C", d["c"])):
        for duong in (f"{VIEWING}?status=AWAITING", VIEWING):
            than = (await client.get(duong, headers=_auth(tok))).text
            assert TEN_MOI not in than, f"{vai} đọc được tên người yêu cầu ở {duong}"
            assert SDT_MOI not in than, f"{vai} đọc được số điện thoại ở {duong}"

    than_a = (await client.get(f"{VIEWING}?status=AWAITING", headers=_auth(d["a"]))).text
    assert TEN_MOI in than_a, "đơn vị sở hữu phải đọc được PII — họ cần gọi lại khách"


# ======================================================== quyết định
@pytest.mark.asyncio
async def test_a_unit_cannot_decide_a_viewing_it_does_not_own(client, db_pool):
    """404, KHÔNG phải 403. 403 xác nhận dòng ấy tồn tại."""
    d = await _bo_ba(client, db_pool, "quyet")

    for vai, tok in (("B", d["b"]), ("C", d["c"])):
        res = await client.post(
            f"{VIEWING}/{d['wid']}/decide",
            json={"decision": "reject", "reject_reason": "Thử vượt quyền.", "reject_code": "OTHER"},
            headers=_auth(tok),
        )
        assert res.status_code == 404, f"{vai} quyết định được: {res.status_code} {res.text}"
        assert res.status_code != 403, f"{vai} nhận 403 — câu trả lời ấy xác nhận dòng có thật"


@pytest.mark.asyncio
async def test_a_refused_decision_leaves_the_row_untouched(client, db_pool):
    """Chặn mà vẫn ghi là không chặn. Kiểm CẢ BA cột quyết định."""
    d = await _bo_ba(client, db_pool, "khongdoi")
    truoc = await db_pool.fetchrow(
        "SELECT status, decided_by, decided_at, reject_reason FROM service_approvals WHERE workflow_id=$1::uuid",
        d["wid"],
    )

    await client.post(
        f"{VIEWING}/{d['wid']}/decide",
        json={"decision": "reject", "reject_reason": "Thử vượt quyền.", "reject_code": "OTHER"},
        headers=_auth(d["b"]),
    )

    sau = await db_pool.fetchrow(
        "SELECT status, decided_by, decided_at, reject_reason FROM service_approvals WHERE workflow_id=$1::uuid",
        d["wid"],
    )
    assert dict(sau) == dict(truoc), f"dòng đã đổi: {dict(truoc)} → {dict(sau)}"
    assert sau["status"] == "AWAITING"
    # Workflow cũng không được đánh hỏng — `reject_viewing` đánh FAILED cả chuỗi.
    assert await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", d["wid"]) == (
        "WAITING_APPROVAL"
    )


@pytest.mark.asyncio
async def test_the_owning_unit_can_still_decide(client, db_pool):
    """Bộ lọc không được chặn nhầm người đúng — đó là nửa còn lại của bài kiểm."""
    d = await _bo_ba(client, db_pool, "dungquyen")

    res = await client.post(
        f"{VIEWING}/{d['wid']}/decide",
        json={"decision": "reject", "reject_reason": "Khu này kín lịch ngày đó.", "reject_code": "NO_AVAILABILITY"},
        headers=_auth(d["a"]),
    )

    assert res.status_code == 200, res.text
    assert (
        await db_pool.fetchval("SELECT status FROM service_approvals WHERE workflow_id=$1::uuid", d["wid"])
    ) == "REJECTED"


@pytest.mark.asyncio
async def test_an_unknown_workflow_and_a_foreign_one_are_indistinguishable(client, db_pool):
    """Hai câu trả lời phải GIỐNG NHAU, nếu không mã trạng thái chính là câu trả lời."""
    d = await _bo_ba(client, db_pool, "khongphanbiet")
    khong_co = str(uuid.uuid4())

    r_la = await client.post(
        f"{VIEWING}/{khong_co}/decide",
        json={"decision": "reject", "reject_reason": "x", "reject_code": "OTHER"},
        headers=_auth(d["b"]),
    )
    r_cua_nguoi_khac = await client.post(
        f"{VIEWING}/{d['wid']}/decide",
        json={"decision": "reject", "reject_reason": "x", "reject_code": "OTHER"},
        headers=_auth(d["b"]),
    )

    assert r_la.status_code == r_cua_nguoi_khac.status_code == 404
    assert r_la.json() == r_cua_nguoi_khac.json(), "hai câu trả lời khác nhau là một kênh rò"


@pytest.mark.asyncio
async def test_two_simultaneous_decisions_leave_exactly_one_winner(client, db_pool):
    """Hai lượt của CÙNG đơn vị sở hữu: đúng một lượt thắng.

    Kiểm sau khi thêm mệnh đề quyền sở hữu vào chính câu UPDATE — thêm một điều
    kiện vào `WHERE` là chỗ dễ làm hỏng tính nguyên tử nhất.
    """
    d = await _bo_ba(client, db_pool, "dongthoi")
    goi = lambda: client.post(  # noqa: E731
        f"{VIEWING}/{d['wid']}/decide",
        json={"decision": "reject", "reject_reason": "Hết lịch.", "reject_code": "NO_AVAILABILITY"},
        headers=_auth(d["a"]),
    )

    r1, r2 = await asyncio.gather(goi(), goi(), return_exceptions=True)
    ma_tra = [r.status_code for r in (r1, r2) if not isinstance(r, Exception)]

    assert ma_tra.count(200) == 1, f"số lượt thắng khác 1: {ma_tra}"


@pytest.mark.asyncio
async def test_the_refusal_says_nothing_about_the_applicant(client, db_pool):
    """Thân của câu 404 không được mang PII.

    Lọc danh sách rồi để tên khách rò qua một câu báo lỗi là vá đúng một nửa —
    và nửa còn lại vẫn trả lời được câu "ai đang xem nhà".
    """
    d = await _bo_ba(client, db_pool, "loikhongpii")

    res = await client.post(
        f"{VIEWING}/{d['wid']}/decide",
        json={"decision": "reject", "reject_reason": "x", "reject_code": "OTHER"},
        headers=_auth(d["b"]),
    )

    assert TEN_MOI not in res.text and SDT_MOI not in res.text, res.text


@pytest.mark.asyncio
async def test_no_unfiltered_aggregate_rides_along(client, db_pool):
    """Nếu response có một con số tổng thì nó phải đếm CÙNG tập với `items`.

    Hàng đợi dịch vụ từng trả `total` đếm cả bảng trong khi danh sách đã lọc:
    một đơn vị có 3 việc đọc được "3 / 290". Con số ấy không lộ nội dung, nhưng
    nó lộ QUY MÔ — và nó nói với người duyệt rằng có việc đang bị giấu.
    """
    d = await _bo_ba(client, db_pool, "khongtongsai")
    await _lich_tham_quan(db_pool, d["khach_id"], MOV_02)
    await _lich_tham_quan(db_pool, d["khach_id"], None)

    body = (await client.get(f"{VIEWING}?status=AWAITING", headers=_auth(d["a"]))).json()

    for khoa in ("total", "count", "tong"):
        if khoa in body:
            assert body[khoa] == len(body["items"]), f"{khoa}={body[khoa]} nhưng items={len(body['items'])}"


@pytest.mark.asyncio
async def test_the_write_itself_refuses_a_foreign_unit(client, db_pool):
    """Gọi THẲNG hàm ghi, không qua HTTP: mệnh đề quyền sở hữu phải nằm trong UPDATE.

    Cổng ở route quyết định ĐỊNH DẠNG câu trả lời; câu UPDATE bảo đảm KHÔNG
    GHI. Bài này đo cái thứ hai — nếu quyền sở hữu chỉ được kiểm ở route thì
    tầng dưới vẫn ghi được, và một đường gọi MỚI sẽ đi qua nó.
    """
    from src.orchestration.viewing_approval import REJECTED, record_viewing_decision

    d = await _bo_ba(client, db_pool, "tangduoi")

    assert await record_viewing_decision(db_pool, d["wid"], REJECTED, "ke_la", don_vi=[MOV_02]) is False
    assert await record_viewing_decision(db_pool, d["wid"], REJECTED, "ke_la", don_vi=[]) is False
    assert (
        await db_pool.fetchval("SELECT status FROM service_approvals WHERE workflow_id=$1::uuid", d["wid"])
    ) == "AWAITING"
    # Dòng KHÔNG CHỦ cũng không ghi được ở tầng này.
    #
    # Cổng ở route đã chặn nó rồi, nên không đường HTTP nào tới đây — và chính
    # vì thế phải kiểm riêng: hai hàng rào chỉ có giá trị khi mỗi hàng rào tự
    # đứng được. Đo được: đột biến "cho NULL đi qua ở câu GHI" sống sót cho tới
    # khi có ba dòng này.
    khong_chu = await _lich_tham_quan(db_pool, d["khach_id"], None)
    assert await record_viewing_decision(db_pool, khong_chu, REJECTED, "ai_do", don_vi=[BQL_SALES]) is False
    assert (
        await db_pool.fetchval("SELECT status FROM service_approvals WHERE workflow_id=$1::uuid", khong_chu)
    ) == "AWAITING"

    # Và người đúng vẫn ghi được — nếu không thì đây là một cái khoá chết.
    assert await record_viewing_decision(db_pool, d["wid"], REJECTED, "dung_nguoi", don_vi=[BQL_SALES]) is True


@pytest.mark.asyncio
async def test_a_row_written_through_the_old_view_has_no_owner_and_stays_hidden(client, db_pool):
    """Ghi qua view `viewing_approvals` sinh ra dòng VÔ CHỦ — và nó phải ẩn.

    Trigger `INSTEAD OF INSERT` của view không đặt `service_provider_id`. Đường
    ghi THẬT (`save_pending_viewing_approval`) đặt, vì nó gọi `don_vi_mac_dinh`;
    view thì không gọi được Python.

    Bài này ghim hành vi ấy ở trạng thái AN TOÀN: dòng vô chủ không ai thấy,
    không ai quyết định. Nó KHÔNG hợp thức hoá cái bẫy — xem ghi chú NỢ ở cuối
    file. Nó chỉ bảo đảm rằng nếu cái bẫy đổi, có người biết.
    """
    _, khach_id = await _tai_khoan(client, db_pool, "kh_tq_view")
    tok, uid = await _tai_khoan(client, db_pool, "dv_tq_view", role="provider")
    await _gan_don_vi(db_pool, uid, BQL_SALES)

    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'xem nhà', 'WAITING_APPROVAL', $2::uuid)",
        wid,
        khach_id,
    )
    await db_pool.execute(
        "INSERT INTO viewing_approvals (workflow_id, task_id, project_id, viewing_date, viewing_time) "
        "VALUES ($1::uuid, 'T1', 'PRJ-001', CURRENT_DATE + 30, '09:00')",
        wid,
    )

    chu = await db_pool.fetchval(
        "SELECT service_provider_id FROM service_approvals WHERE workflow_id = $1::uuid", wid
    )
    assert chu is None, f"trigger đã bắt đầu gán đơn vị ({chu}) — đọc lại ghi chú NỢ cuối file"
    assert wid not in _ma((await client.get(VIEWING, headers=_auth(tok))).json())


def test_no_http_route_asks_for_the_unfiltered_list():
    """`don_vi=None` là cửa dành cho công cụ nội bộ — không route nào được dùng.

    Kiểm bằng văn bản chứ không bằng hành vi: một route truyền `None` sẽ trả về
    đúng dữ liệu cho tài khoản demo đang được gắn mọi đơn vị, nên không bài kiểm
    hành vi nào bắt được nó cho tới khi có người thật bị lộ.
    """
    from pathlib import Path

    for ten in ("viewing_approval_routes.py", "service_approval_routes.py"):
        van_ban = (Path(__file__).resolve().parents[2] / "src" / "api" / ten).read_text(encoding="utf-8")
        assert "don_vi=None" not in van_ban, f"{ten} truyền don_vi=None"


# ======================================================== các vai khác
@pytest.mark.asyncio
async def test_admin_and_customer_stay_out_of_the_provider_gate(client, db_pool):
    """Admin giám sát qua `/admin`, khách đọc trạng thái workflow của mình."""
    d = await _bo_ba(client, db_pool, "vaikhac")
    tok_ad, _ = await _tai_khoan(client, db_pool, "ad_tq", role="admin")
    tok_kh, _ = await _tai_khoan(client, db_pool, "kh_tq_vai")

    for vai, tok in (("admin", tok_ad), ("khách", tok_kh)):
        assert (await client.get(VIEWING, headers=_auth(tok))).status_code == 403, vai
        res = await client.post(
            f"{VIEWING}/{d['wid']}/decide",
            json={"decision": "reject", "reject_reason": "x", "reject_code": "OTHER"},
            headers=_auth(tok),
        )
        assert res.status_code == 403, f"{vai} quyết định được: {res.status_code}"

    # Và chiều ngược lại: provider không mượn được đường giám sát.
    assert (await client.get("/api/v1/admin/requests", headers=_auth(d["a"]))).status_code == 403


@pytest.mark.asyncio
async def test_the_two_gates_agree_on_the_same_row(client, db_pool):
    """Cùng một dòng, hai cổng đọc, một câu trả lời.

    `schedule_property_viewing` nằm trong `service_approvals` như mọi dịch vụ
    khác, nên nó xuất hiện ở CẢ HAI cổng. Hai cổng nói khác nhau về cùng một
    dòng nghĩa là một trong hai đang sai — và kẻ tấn công sẽ dùng cổng nói có.
    """
    d = await _bo_ba(client, db_pool, "haicong")

    tq_b = _ma((await client.get(f"{VIEWING}?status=AWAITING", headers=_auth(d["b"]))).json())
    dv_b = _ma((await client.get(f"{SERVICE}?status=AWAITING", headers=_auth(d["b"]))).json())
    tq_a = _ma((await client.get(f"{VIEWING}?status=AWAITING", headers=_auth(d["a"]))).json())
    dv_a = _ma((await client.get(f"{SERVICE}?status=AWAITING", headers=_auth(d["a"]))).json())

    assert (d["wid"] in tq_b) == (d["wid"] in dv_b) is False
    assert (d["wid"] in tq_a) == (d["wid"] in dv_a) is True


# NỢ KỸ THUẬT — trigger ghi của view `viewing_approvals`.
#
# `INSTEAD OF INSERT` trên view không đặt `service_provider_id`, nên mọi dòng đi
# qua đường ấy là dòng VÔ CHỦ: không đơn vị nào thấy, không ai quyết định được,
# và khách chờ mãi mà hàng đợi thì rỗng.
#
# Hôm nay chỉ BÀI KIỂM ghi qua view; đường ghi thật của sản phẩm
# (`save_pending_viewing_approval`) ghi thẳng vào `service_approvals` kèm
# `don_vi_mac_dinh("schedule_property_viewing")`. Nên cái bẫy chưa chạm tới ai.
#
# CHƯA vá vì hai cách vá đều có giá:
#
#   * đặt `'BQL-SALES'` thẳng trong trigger là chép một dòng của bảng ánh xạ
#     tool → đơn vị sang SQL. Hai bản của một bảng ánh xạ là bản sẽ lệch, và lệch
#     ở đây nghĩa là quyền sở hữu sai — đúng thứ file này vừa sửa;
#   * cho trigger NÉM khi thiếu đơn vị là đúng hơn, nhưng nó là một migration
#     đổi hành vi của một đường mà mã cũ, script vận hành và test chưa viết đều
#     có thể đang dùng. Việc ấy xứng đáng một lượt riêng, có bài kiểm riêng.
#
# Nếu bạn thêm một đường ghi MỚI qua view này: đặt `service_provider_id` ngay
# tại chỗ, đừng chờ ai đó nhớ ra.
