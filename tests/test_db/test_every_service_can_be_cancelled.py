"""Năm dịch vụ có hẹn, năm đường huỷ — cùng một khuôn, không dịch vụ nào bị bỏ lại.

Nút "Huỷ lịch" hiện trên MỌI thẻ kết quả có mốc thời gian. Một dịch vụ có nút mà
không có tool là một nút bấm được rồi không có gì xảy ra — và người duyệt bên
kia bấm đồng ý cho một việc không ai thực hiện.

Bài kiểm này đi từ hồ sơ của khách tới bản ghi ở phía đơn vị, cho từng dịch vụ.
Không mock connector: dùng chính app in-process của provider, nên nếu đường dẫn
hay tên field lệch thì nó đỏ ở đây chứ không đỏ trên máy người dùng.
"""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.orchestration import demo_service
from src.orchestration.service_approval import record_service_decision, save_support_request

NGAY = "2029-12-15"


def _provider(ten: str):
    """(app, connector factory) cho từng dịch vụ."""
    if ten == "resident_services":
        from src.connectors.resident_services import ResidentServicesConnector
        from src.services.mock.resident_services import resident_services_app

        return resident_services_app, ResidentServicesConnector
    from src.connectors.shuttle import ShuttleConnector
    from src.services.mock.shuttle import shuttle_app

    return shuttle_app, ShuttleConnector


async def _tao_o_provider(app, path: str, body: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://p") as c:
        res = await c.post(path, json=body)
    assert res.status_code in {200, 201}, res.text
    return res.json()["data"]


_CA = {
    "create_maintenance_request": {
        "provider": "resident_services",
        "path": "/api/resident-services/maintenance",
        "body": {
            "issue_type": "air_conditioning",
            "description": "May lanh khong mat",
            "location": "phong khach",
            "preferred_date": NGAY,
            "preferred_time": "09:00",
        },
        "input": {
            "issue_type": "air_conditioning",
            "description": "May lanh khong mat",
            "location": "phong khach",
            "preferred_date": NGAY,
            "preferred_time": "09:00",
        },
        "o_ma": "maintenance_id",
        "o_trang_thai": "maintenance_status",
        "tool_huy": "cancel_maintenance",
    },
    "schedule_move": {
        "provider": "resident_services",
        "path": "/api/resident-services/moves",
        # `body` gửi thẳng cho mock provider, `input` đi qua Validator. Cả hai
        # đều cần ba ô điểm đi / điểm đến / quy mô: provider trả 422 nếu thiếu,
        # Validator từ chối cả kế hoạch nếu thiếu. Hai toà cùng phường nên
        # `distance_band` ra `SAME_WARD` — trong vùng phục vụ.
        "body": {
            "move_date": NGAY,
            "move_time": "08:00",
            "move_origin_id": "MOVE-Q7-A1",
            "move_destination_id": "MOVE-Q7-A2",
            "move_size": "medium",
            "needs_elevator": True,
            "needs_loading_support": False,
            "move_vehicle": "van",
        },
        "input": {
            "move_date": NGAY,
            "move_time": "08:00",
            "move_origin_id": "MOVE-Q7-A1",
            "move_destination_id": "MOVE-Q7-A2",
            "move_size": "medium",
            "needs_elevator": True,
            "needs_loading_support": False,
            "move_vehicle": "van",
        },
        "o_ma": "move_request_id",
        "o_trang_thai": "move_status",
        "tool_huy": "cancel_move",
    },
    "book_shuttle": {
        "provider": "shuttle",
        "path": "/api/shuttles/bookings",
        "body": {"viewing_id": "VIEW-901", "tour_date": NGAY, "passenger_count": 2},
        "input": {"viewing_id": "VIEW-901", "tour_date": NGAY, "passenger_count": 2},
        "o_ma": "shuttle_id",
        "o_trang_thai": "shuttle_status",
        "tool_huy": "cancel_shuttle",
    },
}


@pytest.mark.parametrize("tool", sorted(_CA))
@pytest.mark.asyncio
async def test_the_provider_records_the_cancellation(client, db_pool, monkeypatch, tool: str):
    ca = _CA[tool]
    app, factory = _provider(ca["provider"])
    # Provider cố ý "xử lý" 30 giây khi ĐẶT xe. Không tắt thì bài kiểm này chờ
    # nửa phút cho một thứ nó không kiểm. Lệnh HUỶ không có độ trễ ấy — và đó
    # chính là thứ đang kiểm.
    from src.services.mock import shuttle as mock_shuttle

    monkeypatch.setattr(mock_shuttle, "SHUTTLE_BOOKING_DELAY_SECONDS", 0)
    ket_qua_tao = await _tao_o_provider(
        app,
        ca["path"],
        dict(ca["body"], viewing_id=f"VIEW-{uuid.uuid4().hex[:6]}") if tool == "book_shuttle" else ca["body"],
    )

    khach = AsyncClient(transport=ASGITransport(app=app))
    connector = factory(base_url="http://p", client=khach)
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [connector])

    wid = uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','SUCCESS')", wid)
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
        " provider_submission_status) VALUES ($1,'T1',$2,'SUCCESS','[]'::jsonb,$3::jsonb,$4::jsonb,'ACKNOWLEDGED')",
        wid,
        tool,
        # Kế hoạch được dựng LẠI từ chính dòng này ở mọi lượt resume, và nó phải
        # qua Validator. Một `input_data` rỗng làm cả lượt chạy đổ trước khi tới
        # được phần đang kiểm.
        json.dumps(ca["input"]),
        json.dumps(ket_qua_tao),
    )
    ho_so = await save_support_request(db_pool, workflow_id=str(wid), task_id="T1", kind="CANCEL", note="xin huỷ")

    await record_service_decision(db_pool, str(wid), ho_so, "APPROVED", decided_by="don_vi")
    await demo_service.resume_after_service_decision(str(wid))
    await khach.aclose()

    buoc = await db_pool.fetchrow(
        "SELECT tool, status, result_data, provider_submission_status FROM workflow_tasks"
        " WHERE workflow_id=$1::uuid AND task_id <> 'T1'",
        wid,
    )
    assert buoc is not None, f"{tool}: đơn vị đồng ý mà không có bước huỷ nào"
    assert buoc["tool"] == ca["tool_huy"], dict(buoc)
    assert buoc["status"] == "SUCCESS", dict(buoc)
    assert buoc["provider_submission_status"] == "ACKNOWLEDGED", "gọi ra ngoài mà không để lại bằng chứng"

    ket = buoc["result_data"]
    ket = json.loads(ket) if isinstance(ket, str) else ket
    assert ket[ca["o_trang_thai"]] == "CANCELLED", ket


@pytest.mark.parametrize("tool", sorted(_CA))
def test_the_cancel_tool_is_owned_and_out_of_reach(tool: str):
    """Mỗi tool huỷ phải có ĐÚNG một connector nhận, và Planner không chạm được."""
    from src.common.agent_tool_policy import AGENT_FORBIDDEN_TOOLS
    from src.orchestration.deps import build_connectors

    tool_huy = _CA[tool]["tool_huy"]
    chu = [c for c in build_connectors() if tool_huy in c.tool_names]

    assert len(chu) == 1, f"{tool_huy}: {len(chu)} connector nhận"
    assert chu[0].is_retry_safe(tool_huy) is True, "huỷ là phép GÁN — gọi lại phải an toàn"
    assert tool_huy in AGENT_FORBIDDEN_TOOLS, "Planner lập kế hoạch được với một lệnh huỷ"


# --- phía giao diện ----------------------------------------------------------


def test_the_card_warns_about_the_money_before_the_click():
    """Luật hoàn tiền là TẤT ĐỊNH, nên hệ thống biết kết cục trước khi khách bấm.

    Giấu nó tới sau khi đơn vị duyệt là để họ bấm một nút mà không biết nó tốn
    bao nhiêu. Frontend không có hạ tầng test nên kiểm bằng cách đọc file TSX.
    """
    from pathlib import Path

    card = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "workspace" / "ResultSummary.tsx"
    ).read_text(encoding="utf-8")

    assert "24 * 60 * 60 * 1000" in card, "thẻ không tính mốc 24 giờ"
    assert "sẽ không được hoàn" in card, "không nói trước là mất tiền"
    assert "window.confirm" in card, "cảnh báo hiện ra nhưng vẫn gửi dù khách chưa đồng ý"
    # Cảnh báo CHỈ cho lệnh huỷ. "Đổi lịch" không đụng tới khoản đã trả.
    nho = card[card.index("async function nho(") :][:900]
    assert "kind === 'CANCEL'" in nho, "cảnh báo mất tiền hiện cả khi khách chỉ xin đổi lịch"
