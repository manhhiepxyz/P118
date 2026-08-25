"""Mọi dịch vụ, cùng một luật: trả lời từng phần được nhận, và không mất.

Owner: Thành Bảo (Decision layer)
File: tests/test_db/test_every_service_keeps_what_you_already_told_it.py

YÊU CẦU SINH RA FILE NÀY, nguyên văn: "phải chắc là tất cả dịch vụ đều được quy
định như thế này, tôi không muốn sửa dịch vụ tham quan xong qua test đặt chuyển
nhà thì nhắn ngắt quãng nó lại quên thông tin".

Nên file này KHÔNG viết tay từng dịch vụ. Nó chạy vòng qua
`TaskPlanValidator.REQUIRED_INPUTS` — bảng mà chính Validator dùng để quyết một
kế hoạch có chạy được không. Thêm một dịch vụ mới vào bảng ấy mà quên hỗ trợ
trả lời từng phần thì file này đỏ, không cần ai nhớ ra.

CHUỖI ĐÃ ĐO ĐƯỢC trên stack demo, phiên e88a96e1 — đây là thứ phải không bao
giờ lặp lại:

    "đặt lịch tham quan"   thiếu: project, ngày, giờ
    "Vinhomes Ocean Park"  thiếu: ngày, giờ          ← project ĐƯỢC NHẬN
    "ngày 23/8/2026"       thiếu: project, ngày, giờ ← MẤT LUÔN project
    "12:00"                thiếu: project, ngày
    "27/8/2026"            thiếu: project, giờ       ← ngày nhận, MẤT giờ

Một giá trị bị từ chối không chỉ rơi — nó xoá sạch những ô đã nhận trước đó.
Năm lượt, ~112 giây gọi model, để nhập ba ô.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest

from src.agents.validator import TaskPlanValidator as V
from src.common.field_parsers import BOOLEAN_FIELDS, DATE_FIELDS, TIME_FIELDS
from tests.test_db.conftest import _register_and_login

# Ô do BƯỚC TRƯỚC sinh ra, hoặc do tài khoản cấp — người dùng không gõ chúng.
KHONG_PHAI_NGUOI_DUNG_GO = frozenset({"vehicle_id", "booking_id", "viewing_id", "amount", "currency", "resident_id"})

# Tool Planner được phép dùng. `cancel_*` và `change_parking_zone` bị cấm lập kế
# hoạch (xem `AGENT_FORBIDDEN_TOOLS`), `search_properties`/`register_resident`
# không nằm trong danh mục demo.
DICH_VU = sorted(
    tool
    for tool in V.REQUIRED_INPUTS
    if not tool.startswith("cancel_")
    and tool not in {"change_parking_zone", "search_properties", "register_resident", "pay_fee"}
)

SAP_TOI = date.today() + timedelta(days=10)


def o_nguoi_dung_go(tool: str) -> list[str]:
    return sorted(V.REQUIRED_INPUTS[tool] - KHONG_PHAI_NGUOI_DUNG_GO)


def _canonical(field: str) -> str:
    """`project_id` và `project_name` là hai tên cho cùng một câu hỏi."""
    return "project_id" if field == "project_name" else field


def _cong_khai(field: str) -> str:
    """Tên ô như giao diện thấy. `project_id` là mã nội bộ; người dùng nói TÊN."""
    return "project_name" if field == "project_id" else field


# Mẫu cho những ô mà bảng của Validator KHÔNG mô tả hết.
#
# `ENUM_INPUTS` chỉ có `vehicle_type` và `parking_zone`; `move_vehicle` và
# `interest_type` cũng là tập đóng nhưng được đọc trong `field_parsers`, không
# khai báo ở Validator. Ghi thẳng ra đây thay vì đoán.
#
# `consent` CỐ Ý chặt hơn mọi ô boolean khác: `_parse_consent` đòi lời đồng ý
# rõ ràng, "có" không đủ. Đó là thiết kế đúng cho một ô đồng thuận, nên mẫu
# phải theo nó — không phải sửa hệ thống cho vừa test.
MAU_RIENG = {
    "project_id": "Vinhomes Ocean Park",
    "plate_number": "51H-12345",
    "passenger_count": "2",
    "description": "Điều hoà hư",
    "location": "P-101",
    "move_vehicle": "van",
    "interest_type": "thuê",
    "consent": "tôi đồng ý",
}


def gia_tri_mau(tool: str, field: str) -> str:
    """Một giá trị HỢP LỆ cho ô này, suy từ bảng của Validator khi có."""
    if field in DATE_FIELDS:
        return SAP_TOI.strftime("%d/%m/%Y")
    if field in TIME_FIELDS:
        gio = V.TIME_INPUTS.get(tool)
        return gio[1].strftime("%H:%M") if gio else "09:00"
    chon = V.ENUM_INPUTS.get((tool, field))
    if chon:
        return sorted(chon)[0]
    if field in MAU_RIENG:
        return MAU_RIENG[field]
    if field in BOOLEAN_FIELDS:
        return "có"
    raise AssertionError(
        f"{tool}.{field} chưa có giá trị mẫu. Thêm một dịch vụ thì phải khai ở "
        f"`MAU_RIENG` — im lặng trả về chuỗi bất kỳ sẽ làm test XANH GIẢ."
    )


async def _mo_cau_hoi(pool, owner, tool: str, thieu: list[str]) -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) VALUES ($1,$2,'PENDING',$3)",
            wid,
            f"dùng dịch vụ {tool}",
            owner,
        )
        await conn.execute(
            "INSERT INTO workflow_clarifications (workflow_id, goal, missing_fields, resolved_at) "
            "VALUES ($1,$2,$3::jsonb,NULL)",
            wid,
            f"dùng dịch vụ {tool}",
            json.dumps([_cong_khai(f) for f in thieu]),
        )
    return str(wid)


@pytest.mark.parametrize("tool", DICH_VU)
@pytest.mark.asyncio
async def test_answering_one_field_at_a_time_never_loses_the_earlier_ones(client, db_pool, monkeypatch, tool: str):
    """Gõ từng ô một. Ô đã nhận phải còn nguyên ở MỌI lượt sau."""
    from src.api import routes

    lap_ke_hoach: list[dict] = []

    async def _ghi_lai(workflow_id, goal, *_a, **_kw):
        lap_ke_hoach.append(dict((routes._DEMO_JOBS.get(workflow_id) or {}).get("existing_context") or {}))

    monkeypatch.setattr(routes, "_run_demo_job", _ghi_lai)

    ten = f"tung_o_{tool[:18]}"
    token = await _register_and_login(client, ten)
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", ten)

    o = o_nguoi_dung_go(tool)
    assert o, f"{tool} không có ô nào người dùng gõ — kiểm lại bảng loại trừ"

    wid = await _mo_cau_hoi(db_pool, owner, tool, o)

    # Trả lời đúng ô hệ thống ĐANG HỎI, không theo thứ tự tôi tự đặt — đó là
    # cách người dùng hành xử, và nó chịu được việc một câu rơi vào ô khác:
    # "Điều hoà hư" được đọc thành `issue_type` chứ không phải `description`,
    # và đó là cách đọc hợp lý.
    #
    # BẢO ĐẢM ĐANG KIỂM: tập ô đã biết KHÔNG BAO GIỜ CO LẠI.
    da_biet: set[str] = set()
    con_hoi = [_cong_khai(f) for f in o]
    for _ in range(len(o) + 3):
        if not con_hoi:
            break
        res = await client.post(
            f"/api/v1/workflows/demo/{wid}/continue",
            json={"message": gia_tri_mau(tool, _canonical(con_hoi[0]))},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 202, f"{tool}/{con_hoi[0]}: {res.text}"
        body = res.json()

        if body.get("status") == "NEEDS_INFORMATION":
            assert not lap_ke_hoach, f"{tool}: lập kế hoạch khi còn thiếu {con_hoi}"
            # Đọc thứ hệ thống GHI XUỐNG, không phải thứ giữ trong RAM: câu hỏi
            # còn dở phải sống sót qua restart, nếu không thì nó vẫn quên.
            ghi = await db_pool.fetchval(
                "SELECT existing_context FROM workflow_clarifications WHERE workflow_id=$1::uuid",
                wid,
            )
            biet = json.loads(ghi) if isinstance(ghi, str) else dict(ghi or {})
            con_hoi = list(body.get("missing_fields") or [])
        else:
            assert len(lap_ke_hoach) == 1, f"{tool}: {len(lap_ke_hoach)} lần lập kế hoạch"
            biet = lap_ke_hoach[0]
            con_hoi = []

        co = {_canonical(k) for k in biet if _canonical(k) in {_canonical(x) for x in o}}
        mat = da_biet - co
        assert not mat, f"{tool}: quên mất {sorted(mat)} — chỉ còn giữ {sorted(co)}"
        assert co > da_biet or not con_hoi, f"{tool}: không tiến thêm được ô nào ({sorted(co)})"
        da_biet = co

    assert not con_hoi, f"{tool}: sau {len(o) + 3} lượt vẫn còn thiếu {con_hoi}"
    assert da_biet == {_canonical(f) for f in o}, f"{tool}: thiếu {sorted({_canonical(f) for f in o} - da_biet)}"


@pytest.mark.asyncio
async def test_a_failed_write_never_costs_the_user_their_turn(monkeypatch):
    """Ghi hồ sơ hỏng thì người dùng phải nói lại MỘT ô, không mất cả lượt.

    Nhánh trả lời một phần ghi lại phần còn thiếu. Nếu lượt ghi ấy hỏng, hai
    hướng lệch nhau hoàn toàn:

        ném lỗi lên  → người dùng mất CẢ câu trả lời lẫn lượt hỏi; hồ sơ vẫn mở
                       với danh sách cũ, và họ không biết vì sao
        nuốt lỗi     → họ nói lại ô vừa nói; khó chịu, nhưng không mất gì

    Mutation đổi `logger.warning` thành `raise` KHÔNG bị test nào bắt trước khi
    có ca này — cùng loại lỗ hổng đã gặp hai lần khác trong ngày, và luôn ở
    nhánh xử lý sự cố.

    Ép hỏng ở LƯỢT ĐỌC REPOSITORY, không thay cả hàm: thay cả hàm thì chính
    `try/except` đang cần kiểm bị bỏ qua, và bài kiểm xanh mà không canh gì.
    """
    from src.api import routes

    async def _hong(*_a, **_kw):
        raise RuntimeError("database không ghi được")

    monkeypatch.setattr(routes, "acquire_repository", _hong)

    # Không được ném. Trả None, và lượt của người dùng đi tiếp.
    assert (
        await routes._save_clarification_safely(
            "00000000-0000-0000-0000-000000000000",
            session_id=None,
            goal="đặt lịch tham quan",
            missing_fields=["viewing_time"],
            existing_context={"project_id": "PRJ-004"},
        )
        is None
    )


@pytest.mark.asyncio
async def test_the_cached_question_shrinks_too_not_just_the_stored_one(client, db_pool, monkeypatch):
    """Danh sách ô còn thiếu phải co lại ở CẢ RAM lẫn database.

    Route đọc `missing_fields` từ `_DEMO_JOBS[...]["response"]` khi cache còn
    nóng, và chỉ đọc hồ sơ đã ghim khi cache trống. Bản sửa đầu của nhánh
    trả-lời-một-phần chỉ ghi database — nên mọi lượt sau vẫn đọc DANH SÁCH GỐC
    từ RAM và hỏi lại những ô vừa trả lời xong.

    Đo được trên stack demo, hồ sơ trong database đã có đủ `project_id` lẫn
    `viewing_date` mà câu hỏi vẫn nói thiếu cả hai:

        'Vinhomes Pearl Bay' → thiếu [viewing_date, viewing_time]
        '2026-08-30'         → thiếu [project_name, viewing_time]   ← mất project
        '09:30'              → thiếu [project_name, viewing_date]   ← mất ngày

    Bài kiểm liệt kê bảy dịch vụ ở trên KHÔNG bắt được: nó gieo thẳng vào
    database và không bao giờ làm nóng `_DEMO_JOBS`, nên nó chỉ đi đường
    đọc-từ-database — đúng đường duy nhất vốn đã chạy đúng.
    """
    from src.api import routes

    async def _khong_lap_ke_hoach(*_a, **_kw):
        raise AssertionError("trả lời một phần mà vẫn lập kế hoạch lại")

    monkeypatch.setattr(routes, "_run_demo_job", _khong_lap_ke_hoach)

    ten = "cache_nong"
    token = await _register_and_login(client, ten)
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", ten)
    wid = await _mo_cau_hoi(db_pool, owner, "schedule_property_viewing", ["project_id", "viewing_date", "viewing_time"])

    # LÀM NÓNG cache đúng như một lượt `/start` thật để lại.
    routes._DEMO_JOBS[wid] = {
        "stage": "NEEDS_INFORMATION",
        "message": "Thiếu thông tin.",
        "plan": None,
        "events": [],
        "goal": "đặt lịch tham quan",
        "existing_context": {},
        "response": routes.DemoWorkflowResponse(
            status="NEEDS_INFORMATION",
            question="Thiếu thông tin.",
            missing_fields=["project_name", "viewing_date", "viewing_time"],
        ),
    }
    try:
        con_hoi = ["project_name", "viewing_date", "viewing_time"]
        mau = {
            "project_name": "Vinhomes Ocean Park",
            "viewing_date": SAP_TOI.strftime("%d/%m/%Y"),
            "viewing_time": "09:30",
        }
        da_gui = []
        for _ in range(3):
            if not con_hoi:
                break
            o = con_hoi[0]
            res = await client.post(
                f"/api/v1/workflows/demo/{wid}/continue",
                json={"message": mau[o]},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert res.status_code == 202, f"{o}: {res.text}"
            da_gui.append(o)
            body = res.json()
            if body.get("status") != "NEEDS_INFORMATION":
                con_hoi = []
                break
            con_hoi = list(body.get("missing_fields") or [])
            quay_lai = [f for f in da_gui if f in con_hoi]
            assert not quay_lai, (
                f"sau khi trả lời {da_gui}, hệ thống hỏi lại {quay_lai} — cache trong RAM vẫn giữ danh sách gốc"
            )
    finally:
        routes._DEMO_JOBS.pop(wid, None)


@pytest.mark.asyncio
async def test_reloading_the_page_mid_conversation_shows_the_shrunken_question(client, db_pool, monkeypatch):
    """Tải lại trang giữa cuộc trò chuyện phải thấy ĐÚNG câu hỏi vừa nhận.

    `GET /workflows/demo/{id}` ghi đè `message` bằng `job["message"]` (routes.py,
    nhánh cache nóng). Nhánh trả-lời-một-phần cập nhật `job["response"]` nhưng
    KHÔNG cập nhật `job["message"]`, nên hai nguồn nói lệch nhau:

        POST     thiếu [description, location, preferred_date, preferred_time]
                 "Mình cần thêm: mô tả sự cố, vị trí, ngày, giờ"
        TẢI LẠI  thiếu [description, location, preferred_date, preferred_time]
                 "Mình cần thêm: HẠNG MỤC, mô tả sự cố, vị trí, ngày, giờ"  ← ô đã trả lời

    `missing_fields` đúng, chỉ câu chữ sai — nên bài kiểm nào chỉ soi
    `missing_fields` cũng xanh. Người dùng đọc câu chữ, không đọc mảng.
    """
    from src.api import routes

    async def _khong_lap_ke_hoach(*_a, **_kw):
        raise AssertionError("trả lời một phần mà vẫn lập kế hoạch lại")

    monkeypatch.setattr(routes, "_run_demo_job", _khong_lap_ke_hoach)

    ten = "tai_lai_trang"
    token = await _register_and_login(client, ten)
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", ten)
    wid = await _mo_cau_hoi(
        db_pool,
        owner,
        "create_maintenance_request",
        ["issue_type", "description", "location", "preferred_date", "preferred_time"],
    )
    cau_goc = "Mình cần thêm thông tin để lập kế hoạch: hạng mục cần bảo trì, mô tả sự cố, vị trí cần sửa chữa, ngày muốn bảo trì và giờ muốn bảo trì."
    routes._DEMO_JOBS[wid] = {
        "stage": "NEEDS_INFORMATION",
        "message": cau_goc,
        "plan": None,
        "events": [],
        "goal": "báo bảo trì căn hộ",
        "existing_context": {},
        "response": routes.DemoWorkflowResponse(
            status="NEEDS_INFORMATION",
            message=cau_goc,
            answer=cau_goc,
            question=cau_goc,
            missing_fields=["issue_type", "description", "location", "preferred_date", "preferred_time"],
        ),
    }
    try:
        headers = {"Authorization": f"Bearer {token}"}
        res = await client.post(f"/api/v1/workflows/demo/{wid}/continue", json={"message": "Điều hoà"}, headers=headers)
        assert res.status_code == 202, res.text
        vua_nhan = res.json()

        tai_lai = await client.get(f"/api/v1/workflows/demo/{wid}", headers=headers)
        assert tai_lai.status_code == 200, tai_lai.text
        sau = tai_lai.json()

        assert sau.get("missing_fields") == vua_nhan.get("missing_fields")
        assert sau.get("message") == vua_nhan.get("message"), (
            "tải lại trang thấy câu hỏi khác với câu vừa nhận:\n"
            f"  POST    {vua_nhan.get('message')!r}\n"
            f"  TẢI LẠI {sau.get('message')!r}"
        )
        # Bong bóng chat đọc `answer`, không đọc `message` — nên nó phải co
        # lại cùng lúc, nếu không màn hình vẫn treo câu hỏi cũ.
        for truong in ("answer", "question"):
            assert sau.get(truong) == vua_nhan.get(truong), (
                f"tải lại trang thấy {truong} khác với câu vừa nhận:\n"
                f"  POST    {vua_nhan.get(truong)!r}\n"
                f"  TẢI LẠI {sau.get(truong)!r}"
            )
        for truong in ("message", "answer", "question"):
            assert "hạng mục" not in (sau.get(truong) or ""), f"{truong} hỏi lại ô đã trả lời: {sau.get(truong)!r}"
    finally:
        routes._DEMO_JOBS.pop(wid, None)
