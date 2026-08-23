"""Đơn vị duyệt lời xin huỷ thì lịch phải BIẾN MẤT ở phía họ, không chỉ ở màn hình.

Vấn đề đo được
--------------
Nút "Dừng yêu cầu này" gọi `repository.cancel_workflow` — nó đánh dấu
`workflows` và `workflow_tasks` là `CANCELLED` trong database của CHÍNH hệ thống
này, và không nói gì với đơn vị. `VIEW-014` vẫn nằm nguyên bên tour provider.

Khách bấm "đã huỷ", màn hình nói đã huỷ, và hôm sau vẫn có người chờ họ tới xem
nhà. Đó không phải một lỗi hiển thị: chỗ ấy vẫn bị giữ, và người khác không đặt
được.

Nên huỷ phải là một LỜI GỌI RA NGOÀI, đi qua đúng provider gateway như mọi lời
gọi khác — có bằng chứng gửi đi, có khoá idempotency, có đường ghi lại kết quả.

Ranh giới
---------
`cancel_property_viewing` nằm trong `AGENT_FORBIDDEN_TOOLS`: nó chỉ có nghĩa khi
đã có `viewing_id` thật từ một bước đã chạy. Cho Planner lập kế hoạch với nó là
cho model tự viết ra một mã lịch — và mã ấy có thể là lịch của người khác.
"""

from __future__ import annotations

import json
import typing

import pytest

from src.common.enums import ErrorCode
from src.common.tool_contract import TOOL_CONTRACTS
from src.connectors.tour import TourConnector

# --- hợp đồng: tool có thật, và Planner không chạm được ----------------------


def test_the_tool_is_registered_everywhere_it_must_be():
    from src.agents.validator import TaskPlanValidator
    from src.common.agent_tool_policy import AGENT_FORBIDDEN_TOOLS
    from src.common.submission import EXTERNAL_ID_FIELD_BY_TOOL
    from src.common.task_plan import AllowedTool

    assert "cancel_property_viewing" in TOOL_CONTRACTS
    assert "cancel_property_viewing" in typing.get_args(AllowedTool)
    assert "cancel_property_viewing" in TaskPlanValidator.ALLOWED_TOOLS
    assert EXTERNAL_ID_FIELD_BY_TOOL.get("cancel_property_viewing") == "viewing_id"
    assert "cancel_property_viewing" in AGENT_FORBIDDEN_TOOLS, (
        "Planner lập kế hoạch được với nó nghĩa là model tự viết ra một mã lịch"
    )


def test_it_asks_for_nothing_the_user_could_type():
    """Chỉ `viewing_id` — một mã do provider cấp, đọc từ kết quả đã chạy."""
    assert set(TOOL_CONTRACTS["cancel_property_viewing"].inputs) == {"viewing_id"}


# --- qua provider thật (mock in-process) ------------------------------------


@pytest.mark.asyncio
async def test_the_provider_forgets_the_slot(client):
    """Huỷ xong thì chính khung giờ ấy phải đặt lại được."""
    from httpx import ASGITransport, AsyncClient

    from src.services.mock.tour import store, tour_app

    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://tour") as tour:
        dat = await tour.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-005", "viewing_date": "2029-07-07", "viewing_time": "10:30"},
        )
        assert dat.status_code == 201, dat.text
        ma = dat.json()["data"]["viewing_id"]

        # Khung giờ đang bận: đặt trùng bị từ chối.
        trung = await tour.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-005", "viewing_date": "2029-07-07", "viewing_time": "10:30"},
        )
        assert trung.status_code == 409, trung.text

        huy = await tour.post(f"/api/property/viewings/{ma}/cancel")
        assert huy.status_code == 200, huy.text
        assert huy.json()["data"]["viewing_status"] == "CANCELLED"

        lai = await tour.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-005", "viewing_date": "2029-07-07", "viewing_time": "10:30"},
        )
        assert lai.status_code == 201, "huỷ rồi mà khung giờ vẫn bị giữ"
    assert store.tour_bookings[ma]["viewing_status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_cancelling_twice_is_not_an_error():
    """Gọi lại sau timeout là chuyện bình thường; lần hai phải im lặng thành công."""
    from httpx import ASGITransport, AsyncClient

    from src.services.mock.tour import tour_app

    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://tour") as tour:
        dat = await tour.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-005", "viewing_date": "2029-07-08", "viewing_time": "09:00"},
        )
        ma = dat.json()["data"]["viewing_id"]

        assert (await tour.post(f"/api/property/viewings/{ma}/cancel")).status_code == 200
        lan_hai = await tour.post(f"/api/property/viewings/{ma}/cancel")

    assert lan_hai.status_code == 200, lan_hai.text
    assert lan_hai.json()["data"]["viewing_status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_an_unknown_id_is_refused_not_invented():
    from httpx import ASGITransport, AsyncClient

    from src.services.mock.tour import tour_app

    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://tour") as tour:
        res = await tour.post("/api/property/viewings/VIEW-KHONG-CO/cancel")

    assert res.status_code == 404, res.text


# --- connector: chuẩn hoá đúng, và an toàn khi gọi lại -----------------------


def test_the_connector_owns_the_tool():
    connector = TourConnector(base_url="http://tour")

    assert "cancel_property_viewing" in connector.tool_names
    assert connector.is_retry_safe("cancel_property_viewing") is True, (
        "huỷ là phép GÁN, không phải phép cộng — gọi lại vẫn ra đúng một trạng thái"
    )


@pytest.mark.asyncio
async def test_a_missing_id_never_reaches_the_provider():
    connector = TourConnector(base_url="http://khong-ton-tai.invalid")

    ket_qua = await connector.execute("cancel_property_viewing", {})

    assert ket_qua.success is False
    assert ket_qua.error_code is ErrorCode.INVALID_INPUT


# --- nối vào luồng: đơn vị duyệt lời xin huỷ ---------------------------------


@pytest.mark.asyncio
async def test_approving_the_request_actually_cancels_the_viewing(client, db_pool, monkeypatch):
    """Từ nút của khách tới khung giờ được trả về kho — hết đường."""
    import uuid as _uuid

    from httpx import ASGITransport, AsyncClient

    from src.orchestration import demo_service
    from src.orchestration.service_approval import record_service_decision, save_support_request
    from src.services.mock.tour import store, tour_app

    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://tour") as tour:
        dat = await tour.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-005", "viewing_date": "2029-07-09", "viewing_time": "10:30"},
        )
        ma_lich = dat.json()["data"]["viewing_id"]

    # Connector thật, trỏ vào chính app in-process ở trên.
    from src.connectors.tour import TourConnector

    khach = AsyncClient(transport=ASGITransport(app=tour_app))
    connector = TourConnector(base_url="http://tour", client=khach)
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [connector])

    wid = _uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','SUCCESS')", wid)
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
        " provider_submission_status) VALUES ($1,'T1','schedule_property_viewing','SUCCESS','[]'::jsonb,"
        " $2::jsonb, $3::jsonb, 'ACKNOWLEDGED')",
        wid,
        json.dumps({"project_id": "PRJ-005", "viewing_date": "2029-07-09", "viewing_time": "10:30"}),
        json.dumps({"viewing_id": ma_lich}),
    )
    ho_so = await save_support_request(db_pool, workflow_id=str(wid), task_id="T1", kind="CANCEL", note="xin huỷ")

    await record_service_decision(db_pool, str(wid), ho_so, "APPROVED", decided_by="don_vi_tour")
    await demo_service.resume_after_service_decision(str(wid))

    assert store.tour_bookings[ma_lich]["viewing_status"] == "CANCELLED", "đơn vị đồng ý mà lịch vẫn còn"

    buoc = await db_pool.fetch(
        "SELECT task_id, tool, status, provider_submission_status FROM workflow_tasks"
        " WHERE workflow_id=$1::uuid AND tool='cancel_property_viewing'",
        wid,
    )
    assert len(buoc) == 1, [dict(r) for r in buoc]
    assert buoc[0]["status"] == "SUCCESS"
    assert buoc[0]["provider_submission_status"] == "ACKNOWLEDGED", "gọi ra ngoài mà không để lại bằng chứng"

    # Lượt resume chạy ở MỌI quyết định. Lần hai không được gọi huỷ thêm lần nữa.
    await demo_service.resume_after_service_decision(str(wid))
    lai = await db_pool.fetchval(
        "SELECT COUNT(*) FROM workflow_tasks WHERE workflow_id=$1::uuid AND tool='cancel_property_viewing'", wid
    )
    assert lai == 1, f"dựng thêm một bước huỷ ở lượt sau: {lai}"
    await khach.aclose()


@pytest.mark.asyncio
async def test_a_refused_request_never_calls_the_provider(client, db_pool, monkeypatch):
    import uuid as _uuid

    from src.orchestration import demo_service
    from src.orchestration.service_approval import record_service_decision, save_support_request

    goi: list = []

    class _Khong:
        tool_names = ["cancel_property_viewing"]

        def is_retry_safe(self, tool_name: str) -> bool:
            return True

        async def execute(self, tool, payload, context=None):
            goi.append(tool)
            raise AssertionError("gọi provider cho một hồ sơ bị từ chối")

    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [_Khong()])

    wid = _uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','SUCCESS')", wid)
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
        " provider_submission_status) VALUES ($1,'T1','schedule_property_viewing','SUCCESS','[]'::jsonb,"
        " $2::jsonb, $3::jsonb, 'ACKNOWLEDGED')",
        wid,
        json.dumps({"project_id": "PRJ-005", "viewing_date": "2029-07-10", "viewing_time": "10:30"}),
        json.dumps({"viewing_id": "VIEW-KHONG-DUOC-HUY"}),
    )
    ho_so = await save_support_request(db_pool, workflow_id=str(wid), task_id="T1", kind="CANCEL")

    await record_service_decision(
        db_pool, str(wid), ho_so, "REJECTED", decided_by="don_vi_tour", reason="Quá hạn huỷ.", reject_code="OTHER"
    )
    await demo_service.resume_after_service_decision(str(wid))

    assert goi == []
    assert (
        await db_pool.fetchval(
            "SELECT COUNT(*) FROM workflow_tasks WHERE workflow_id=$1::uuid AND tool='cancel_property_viewing'", wid
        )
        == 0
    )


@pytest.mark.asyncio
async def test_an_approved_amend_never_guesses_a_new_time(client, db_pool, monkeypatch):
    """ "Đồng ý cho đổi" chưa nói đổi sang lúc nào. Tự chọn là bịa ra một quyết định."""
    import uuid as _uuid

    from src.orchestration import demo_service
    from src.orchestration.service_approval import record_service_decision, save_support_request

    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [])

    wid = _uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','SUCCESS')", wid)
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
        " provider_submission_status) VALUES ($1,'T1','schedule_property_viewing','SUCCESS','[]'::jsonb,"
        " $2::jsonb, $3::jsonb, 'ACKNOWLEDGED')",
        wid,
        json.dumps({"project_id": "PRJ-005", "viewing_date": "2029-07-11", "viewing_time": "10:30"}),
        json.dumps({"viewing_id": "VIEW-GIU-NGUYEN"}),
    )
    ho_so = await save_support_request(db_pool, workflow_id=str(wid), task_id="T1", kind="AMEND", note="xin đổi")

    await record_service_decision(db_pool, str(wid), ho_so, "APPROVED", decided_by="don_vi_tour")
    await demo_service.resume_after_service_decision(str(wid))

    assert await db_pool.fetchval("SELECT COUNT(*) FROM workflow_tasks WHERE workflow_id=$1::uuid", wid) == 1, (
        "dựng một bước cho một lời đồng ý chưa nói đổi sang lúc nào"
    )


@pytest.mark.asyncio
async def test_cancelling_gives_the_seat_back_to_the_pool():
    """Huỷ phải trả suất về `tour_load`, không chỉ đánh dấu dòng đặt chỗ.

    Hai bộ đếm độc lập: `tour_bookings` trả lời "ai đang giữ", `tour_load` trả
    lời "khung này còn chỗ không". Sửa một mà quên hai thì khung vừa huỷ vẫn báo
    kín — và không ai đặt lại được, kể cả chính người vừa huỷ.

    Kiểm bằng SỨC CHỨA chứ không bằng trùng giờ: hai lần đặt cùng giờ đã bị một
    luật khác chặn, nên phép kiểm ấy vẫn xanh dù `tour_load` sai hẳn.
    """
    from httpx import ASGITransport, AsyncClient

    from src.services.mock.tour import store, tour_app

    ngay = "2029-07-21"
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://tour") as tour:
        dat = await tour.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-005", "viewing_date": ngay, "viewing_time": "09:00"},
        )
        assert dat.status_code == 201, dat.text
        ma = dat.json()["data"]["viewing_id"]
        cho = store.tour_bookings[ma]
        khoa = (cho["residential_area"], cho["tour_date"], cho["tour_slot"])

        # Ép khung này KÍN: một suất, và suất ấy đang là của chính lịch trên.
        store.tour_slots[(cho["residential_area"], cho["tour_slot"])] = 1
        kin = await tour.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-005", "viewing_date": ngay, "viewing_time": "09:30"},
        )
        assert kin.status_code == 409 and "NO_AVAILABILITY" in kin.text, kin.text

        assert (await tour.post(f"/api/property/viewings/{ma}/cancel")).status_code == 200
        assert store.tour_load.get(khoa, 0) == 0, (
            f"huỷ xong mà khung vẫn tính là đang dùng: {store.tour_load.get(khoa)}"
        )

        lai = await tour.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-005", "viewing_date": ngay, "viewing_time": "09:30"},
        )
        assert lai.status_code == 201, f"huỷ rồi mà khung vẫn báo kín: {lai.text}"

        # Huỷ lần hai KHÔNG được trả thêm một suất chưa từng tồn tại.
        await tour.post(f"/api/property/viewings/{ma}/cancel")
        assert store.tour_load.get(khoa, 0) == 1, "huỷ lần hai trừ tiếp, mở ra một suất không có thật"
