"""Cấu trúc kế hoạch là kiến thức của code, không phải thứ model nghĩ lại mỗi lượt.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_plan_structure_is_not_a_guess.py

ĐO ĐƯỢC trên `llm_usage` + `workflows` của stack demo, 86 lượt Planner thật:

    plan  trung vị 32,98s   p90 78,28s   tổng 3390s     ← 89% toàn bộ thời gian model
    latency_ms = 161 + 7,71 × token_model_sinh          R² = 0,988

161 ms là TẤT CẢ phần không phải model — mạng, HTTP, database, xử lý prompt
12.000 token. 99,5% còn lại là model sinh token. Nên muốn nhanh hơn thì phải
giảm thứ nó phải nghĩ, không phải tối ưu phần của ta.

Và nó đang nghĩ lại những thứ KHÔNG BAO GIỜ ĐỔI. Một hình dạng kế hoạch xuất
hiện 19 lần, tốn 738 giây; chỉ 6 hình dạng là duy nhất trong 54 kế hoạch.

Dựng lại 54 kế hoạch ấy bằng bảng phụ thuộc suy ra từ bảng input→output trong
`planner_prompt.py` — không bịa cạnh nào:

    thứ tự các bước      38/38 khớp
    depends_on           38/38 khớp
    InputRef             149/149 khớp

Đối chứng âm, để biết phép so sánh không rỗng:

    xáo thứ tự tool         →  3/38 khớp
    bỏ bảng, đồ thị rỗng    →  0/38 khớp
    (16/54 kế hoạch có đồ thị RỖNG — khớp với chúng là tầm thường, đã tách riêng)

File này đóng băng kết quả ấy thành luật.
"""

from __future__ import annotations

import pytest

from src.agents.plan_assembly import assemble_plan


def _graph(plan) -> list[tuple[str, tuple[str, ...]]]:
    """Đồ thị theo TÊN TOOL — so sánh không phụ thuộc nhãn T1/T2."""
    ten = {t.task_id: t.tool for t in plan.tasks}
    return [(t.tool, tuple(sorted(ten[d] for d in t.depends_on))) for t in plan.tasks]


# Bốn hình dạng chiếm phần lớn 54 kế hoạch đã ghi. `depends_on` ở đây là
# nguyên văn thứ Planner đã sinh ra, không phải thứ tôi cho là đúng.
@pytest.mark.parametrize(
    ("tools", "mong_doi"),
    [
        # 19 lần, 738 giây. Hình dạng đắt nhất trong toàn bộ dữ liệu.
        (
            ["schedule_property_viewing", "register_vehicle", "book_parking", "pay_fee"],
            [
                ("schedule_property_viewing", ()),
                ("register_vehicle", ()),
                ("book_parking", ("register_vehicle",)),
                ("pay_fee", ("book_parking",)),
            ],
        ),
        # Thứ tự đầu vào ĐẢO LẠI phải ra cùng một đồ thị: code tự sắp topo, nên
        # tầng chọn dịch vụ chỉ cần trả về TẬP, không cần trả thứ tự.
        (
            ["pay_fee", "book_parking", "register_vehicle", "schedule_property_viewing"],
            [
                ("schedule_property_viewing", ()),
                ("register_vehicle", ()),
                ("book_parking", ("register_vehicle",)),
                ("pay_fee", ("book_parking",)),
            ],
        ),
        (
            ["schedule_property_viewing", "register_property_interest"],
            [("schedule_property_viewing", ()), ("register_property_interest", ())],
        ),
        (
            ["schedule_property_viewing", "book_shuttle"],
            [
                ("schedule_property_viewing", ()),
                ("book_shuttle", ("schedule_property_viewing",)),
            ],
        ),
    ],
)
def test_code_rebuilds_the_dependency_graph_the_planner_produced(tools, mong_doi):
    plan = assemble_plan("đặt dịch vụ", tools, {})
    assert sorted(_graph(plan)) == sorted(mong_doi)


def test_the_order_puts_every_producer_before_its_consumer():
    """Sắp topo, không phải giữ nguyên thứ tự người gọi đưa vào."""
    plan = assemble_plan("đỗ xe", ["pay_fee", "book_parking", "register_vehicle"], {})
    vi_tri = {t.tool: i for i, t in enumerate(plan.tasks)}
    assert vi_tri["register_vehicle"] < vi_tri["book_parking"] < vi_tri["pay_fee"]


def test_every_reference_points_at_the_task_that_produces_the_field():
    """149/149 InputRef trong dữ liệu đã ghi đều theo đúng luật này."""
    plan = assemble_plan(
        "đỗ xe", ["register_vehicle", "book_parking", "pay_fee"], {}
    )
    theo_tool = {t.tool: t for t in plan.tasks}
    xe = theo_tool["register_vehicle"].task_id
    cho = theo_tool["book_parking"].task_id

    assert theo_tool["book_parking"].input["vehicle_id"].from_task == xe
    assert theo_tool["book_parking"].input["vehicle_id"].field == "vehicle_id"
    for field in ("booking_id", "amount", "currency"):
        ref = theo_tool["pay_fee"].input[field]
        assert ref.from_task == cho, field
        assert ref.field == field


# `pay_fee` KHÔNG được là một lựa chọn của model — nó là HỆ QUẢ.
#
# Đo trên 54 kế hoạch: 37/37 kế hoạch có `book_parking` đều có `pay_fee`, và
# không có `pay_fee` nào đứng một mình. Không một ngoại lệ theo cả hai chiều.
#
# Để nó trên thực đơn của model thì độ chính xác chọn dịch vụ tụt từ 96% xuống
# 65% — bảy trên tám ca lệch là cùng một lỗi: model quên `pay_fee`. Ba mươi mốt
# điểm phần trăm sai số do ta tự tạo ra khi hỏi model một thứ code đã biết chắc.
def test_paying_is_a_consequence_of_booking_not_a_choice():
    plan = assemble_plan("đỗ xe", ["book_parking"], {})
    tools = [t.tool for t in plan.tasks]
    assert "pay_fee" in tools
    assert "register_vehicle" in tools, "book_parking cần một xe đã đăng ký"


def test_paying_never_appears_without_something_that_charges():
    plan = assemble_plan("tham quan", ["schedule_property_viewing"], {})
    assert "pay_fee" not in [t.tool for t in plan.tasks]


def test_the_values_the_caller_extracted_land_on_the_right_task():
    plan = assemble_plan(
        "tham quan",
        ["schedule_property_viewing"],
        {"project_id": "PRJ-004", "viewing_date": "2026-09-10", "viewing_time": "09:30"},
    )
    (task,) = plan.tasks
    assert task.input["project_id"] == "PRJ-004"
    assert task.input["viewing_date"] == "2026-09-10"
    assert task.input["viewing_time"] == "09:30"


def test_a_value_for_another_tool_is_not_smeared_onto_this_one():
    """`parking_zone` không được rơi vào task tham quan."""
    plan = assemble_plan(
        "tham quan",
        ["schedule_property_viewing"],
        {"project_id": "PRJ-004", "viewing_date": "2026-09-10",
         "viewing_time": "09:30", "parking_zone": "ZONE_A"},
    )
    (task,) = plan.tasks
    assert "parking_zone" not in task.input


# XE ĐƯA ĐÓN ĐI THAM QUAN NGÀY NÀO? Ngày tham quan. Không phải câu hỏi.
#
# Ca thật, workflow 8d4cd25c: yêu cầu ghi "Đặt lịch tham quan … ngày 2026-08-27
# lúc 11:30 xe đưa đón cho 1 khách". Không có câu nào nói ngày xe đón, vì không
# ai nói — nó hiển nhiên là ngày đi xem.
#
# Đường nhanh bị cấm suy đoán nên để trống `tour_date`, kế hoạch trượt Validator
# và rơi về Planner: `fast_plan 2,25s` rồi `plan 75,13s`. Người dùng chờ 75 giây
# cho một thứ suy ra được bằng một phép gán.
#
# Kiểm trên mọi kế hoạch đã ghi có cả hai bước: 4/4 lần `tour_date` bằng đúng
# `viewing_date`, không lần nào lệch.
def test_the_shuttle_goes_on_the_day_of_the_viewing():
    plan = assemble_plan(
        "tham quan và xe đưa đón",
        ["schedule_property_viewing", "book_shuttle"],
        {"project_id": "PRJ-005", "viewing_date": "2026-08-27", "viewing_time": "11:30",
         "passenger_count": 1},
    )
    xe = next(t for t in plan.tasks if t.tool == "book_shuttle")
    assert xe.input["tour_date"] == "2026-08-27"


def test_an_explicit_shuttle_date_is_not_overwritten():
    """Người dùng nói rõ ngày khác thì nghe họ, đừng tự sửa."""
    plan = assemble_plan(
        "tham quan và xe đưa đón",
        ["schedule_property_viewing", "book_shuttle"],
        {"project_id": "PRJ-005", "viewing_date": "2026-08-27", "viewing_time": "11:30",
         "tour_date": "2026-08-28", "passenger_count": 1},
    )
    xe = next(t for t in plan.tasks if t.tool == "book_shuttle")
    assert xe.input["tour_date"] == "2026-08-28"


def test_a_shuttle_without_a_viewing_derives_nothing():
    """Không có bước tham quan thì không có gì để sao chép — để trống, đừng bịa.

    `viewing_date` CÓ trong giá trị đọc được, nhưng kế hoạch không có bước tham
    quan. Không có ca này thì bỏ điều kiện "bước nguồn phải có mặt" vẫn xanh —
    và lúc ấy một ngày thuộc về việc khác bị chép sang xe đưa đón.
    """
    plan = assemble_plan(
        "xe đưa đón",
        ["book_shuttle"],
        {"passenger_count": 1, "viewing_date": "2026-08-27"},
    )
    (xe,) = plan.tasks
    assert "tour_date" not in xe.input
