"""Đường nhanh: hỏng thì CHẬM, không được hỏng thành SAI.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_fast_lane_fails_slow_not_wrong.py

Planner chiếm 89% thời gian gọi model (trung vị 32,98s, p90 78,28s trên 86 lượt
thật), và phần cấu trúc nó sinh ra là cơ học — `src/agents/plan_assembly.py`
dựng lại 38/38 đồ thị và 149/149 InputRef của các kế hoạch đã ghi.

Nên đường nhanh là: một lượt gọi RẺ chọn dịch vụ + trích giá trị (đo được:
trung vị 1,56s, p90 1,83s), code lắp kế hoạch, rồi kế hoạch ấy đi qua ĐÚNG
`TaskPlanValidator` mà kế hoạch của Planner đi qua.

CA ĐO ĐƯỢC BUỘC PHẢI CÓ FILE NÀY:

    "cho mình xin cái chỗ để xe khu B từ 5/9 nhé, xe wave biển 51H-12345"
       → booking_date = "2023-09-05"

Model tự đoán năm và trượt ba năm. Đủ ô, đúng định dạng, lọt mọi phép kiểm
hình thức. `TaskPlanValidator` chặn bằng "has booking_date in the past" — và
đó là lý do đường nhanh KHÔNG được có cổng kiểm riêng: mọi kế hoạch phải đi
qua cùng một cửa với luật ngày quá khứ, chân trời, enum, ô bắt buộc.

Trên 14 câu khó cố ý (tiếng Anh, khẩu ngữ, trộn ngôn ngữ, ngoài phạm vi, mơ
hồ): 3 đi đường nhanh và cả 3 ĐÚNG, 0 kế hoạch sai lọt tới thực thi. Bốn câu
ngoài phạm vi ("vay 500 triệu", "thời tiết Hạ Long", "property tax", "giúp tôi
với") đều trả tools rỗng — không câu nào bịa ra dịch vụ.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.agents.fast_lane import FastLane, _DuDoan

# Ngày phải nằm TRONG chân trời `TaskPlanValidator` cho phép, nếu không test sẽ
# xanh vì sai lý do — rơi về Planner do quá xa chứ không do luật đang kiểm.
SAP_TOI = (date.today() + timedelta(days=7)).isoformat()


class _FakeLLM:
    """Runnable giả — không lượt gọi model thật nào trong test."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list = []

    def with_structured_output(self, schema, **_kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _lane(*responses) -> FastLane:
    return FastLane(_FakeLLM(list(responses)))


@pytest.mark.asyncio
async def test_a_complete_request_becomes_a_plan_without_the_planner():
    lane = _lane(
        _DuDoan(
            tools=["schedule_property_viewing"],
            project_name="Vinhomes Pearl Bay",
            viewing_date=SAP_TOI,
            viewing_time="09:30",
        )
    )
    plan = await lane.plan("đặt lịch tham quan Pearl Bay")
    assert plan is not None
    (task,) = plan.tasks
    assert task.tool == "schedule_property_viewing"
    assert task.input["project_id"] == "PRJ-004"
    assert task.input["viewing_date"] == SAP_TOI


# ĐÂY LÀ LUẬT TRUNG TÂM CỦA FILE.
#
# Nguyên văn ca đã đo: người dùng nói "từ 5/9", model trả "2023-09-05". Kế
# hoạch đầy đủ, định dạng đúng, và SAI ba năm. Nếu đường nhanh tự kiểm bằng
# "đủ ô chưa" thì nó đặt chỗ đỗ xe cho một ngày đã qua.
@pytest.mark.asyncio
async def test_a_date_in_the_past_falls_back_instead_of_booking():
    lane = _lane(
        _DuDoan(
            tools=["book_parking"],
            plate_number="51H-12345",
            vehicle_type="motorcycle",
            booking_date="2023-09-05",
            parking_zone="ZONE_B",
        )
    )
    assert await lane.plan("chỗ để xe khu B từ 5/9, xe wave 51H-12345") is None


@pytest.mark.asyncio
async def test_a_missing_required_field_falls_back():
    """Thiếu `viewing_time`. Không được đoán, không được điền mặc định."""
    lane = _lane(
        _DuDoan(
            tools=["schedule_property_viewing"],
            project_name="Vinhomes Pearl Bay",
            viewing_date=SAP_TOI,
        )
    )
    assert await lane.plan("đặt lịch tham quan Pearl Bay") is None


@pytest.mark.asyncio
async def test_a_project_name_that_is_not_in_the_catalogue_falls_back():
    """Tên dự án lạ KHÔNG được ánh xạ về một dự án mặc định."""
    lane = _lane(
        _DuDoan(
            tools=["schedule_property_viewing"],
            project_name="Vinhomes Không Có Thật",
            viewing_date=SAP_TOI,
            viewing_time="09:30",
        )
    )
    assert await lane.plan("đặt lịch tham quan chỗ nào đó") is None


@pytest.mark.asyncio
async def test_a_request_with_no_service_falls_back():
    """ "thời tiết Hạ Long ngày mai thế nào" — 0 dịch vụ. Không phải việc của lane này."""
    lane = _lane(_DuDoan(tools=[]))
    assert await lane.plan("thời tiết Hạ Long ngày mai thế nào") is None


@pytest.mark.asyncio
async def test_a_model_error_falls_back_and_never_escapes():
    """Model hỏng thì rơi về Planner, KHÔNG ném lỗi lên người dùng."""
    lane = _lane(RuntimeError("provider 503"))
    assert await lane.plan("đặt lịch tham quan Pearl Bay") is None


@pytest.mark.asyncio
async def test_paying_is_added_by_code_and_the_whole_plan_still_validates():
    """`pay_fee` không có trong thực đơn model; code thêm, và plan vẫn qua cửa."""
    lane = _lane(
        _DuDoan(
            tools=["book_parking"],
            plate_number="51H-12345",
            vehicle_type="motorcycle",
            booking_date=SAP_TOI,
            parking_zone="ZONE_B",
        )
    )
    plan = await lane.plan("giữ chỗ đỗ xe khu B", existing_context={"resident_id": "RES-ABC"})
    assert plan is not None
    tools = [t.tool for t in plan.tasks]
    assert tools.index("register_vehicle") < tools.index("book_parking") < tools.index("pay_fee")


@pytest.mark.asyncio
async def test_the_account_supplies_the_resident_id_not_the_sentence():
    """`resident_id` đến từ ngữ cảnh tài khoản. Model không được bịa ra nó."""
    lane = _lane(
        _DuDoan(
            tools=["book_parking"],
            plate_number="51H-12345",
            vehicle_type="motorcycle",
            booking_date=SAP_TOI,
            parking_zone="ZONE_B",
        )
    )
    plan = await lane.plan("giữ chỗ đỗ xe", existing_context={"resident_id": "RES-ABC"})
    assert plan is not None
    xe = next(t for t in plan.tasks if t.tool == "register_vehicle")
    assert xe.input["resident_id"] == "RES-ABC"


@pytest.mark.asyncio
async def test_without_a_resident_id_the_lane_falls_back():
    """Không có resident_id thì `register_vehicle` thiếu ô bắt buộc → Planner."""
    lane = _lane(
        _DuDoan(
            tools=["book_parking"],
            plate_number="51H-12345",
            vehicle_type="motorcycle",
            booking_date=SAP_TOI,
            parking_zone="ZONE_B",
        )
    )
    assert await lane.plan("giữ chỗ đỗ xe") is None


@pytest.mark.asyncio
async def test_a_date_beyond_the_horizon_falls_back_too():
    """Quá xa cũng là sai. Cùng cửa, cùng luật — `TaskPlanValidator` quyết."""
    qua_xa = (date.today() + timedelta(days=3650)).isoformat()
    lane = _lane(
        _DuDoan(
            tools=["schedule_property_viewing"],
            project_name="Vinhomes Pearl Bay",
            viewing_date=qua_xa,
            viewing_time="09:30",
        )
    )
    assert await lane.plan("đặt lịch tham quan Pearl Bay") is None


def test_paying_is_never_on_the_menu_the_model_chooses_from():
    """Quyết định ĐO ĐƯỢC, không suy ra được từ hành vi — nên khoá thẳng ở đây.

    `pay_fee` là HỆ QUẢ của `book_parking`, không phải lựa chọn: 37/37 kế hoạch
    đã ghi có book_parking đều có pay_fee, và không có pay_fee nào đứng một mình.

    Để nó trên thực đơn thì độ chính xác chọn dịch vụ tụt 96% → 65% trên 54 goal
    thật — bảy trên tám ca lệch là cùng một lỗi: model quên nó. Đó là 31 điểm
    phần trăm sai số ta tự tạo ra khi hỏi model thứ code đã biết chắc.

    Không test hành vi nào bắt được việc thêm lại dòng ấy: LLM giả trong file
    này không bao giờ trả `pay_fee`, và tác hại chỉ hiện ra với model thật. Nên
    luật được khoá bằng chính bảng hằng, kèm lý do.
    """
    from src.agents.fast_lane import MENU
    from src.agents.plan_assembly import HE_QUA

    assert "pay_fee" not in MENU
    assert "pay_fee" in HE_QUA["book_parking"]
