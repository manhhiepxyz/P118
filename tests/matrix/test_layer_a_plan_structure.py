"""Tầng A — cấu trúc kế hoạch cho cả 26 tổ hợp, thuần deterministic.

Không LLM, không mạng, không database. Ở đây chỉ hỏi một câu: với mỗi tổ hợp
capability, kế hoạch canonical có ĐÚNG hình dạng mà contract đòi hỏi không.

Kỳ vọng được TÍNH từ danh mục `tests/matrix/capabilities.py`, không viết tay.
26 kỳ vọng viết tay là 26 chỗ để một giá trị bị sửa cho khớp output hiện tại.
"""

from __future__ import annotations

import pytest

from src.agents.validator import TaskPlanValidator
from src.common.agent_tool_policy import AGENT_FORBIDDEN_TOOLS, AGENT_REACHABLE_TOOLS
from src.common.task_plan import InputRef
from src.common.tool_contract import TOOL_CONTRACTS
from tests.matrix.capabilities import ALL_CAPABILITIES, build_plan, combos, expected_tools

COMBOS = combos()
IDS = ["+".join(c) for c in COMBOS]


def test_the_matrix_has_exactly_twenty_six_combinations():
    assert len(COMBOS) == 26
    assert len({tuple(sorted(c)) for c in COMBOS}) == 26


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
def test_the_plan_carries_exactly_the_expected_tools(codes):
    """Multiset — không thiếu, không thừa, và ĐÚNG số lần lặp."""
    plan = build_plan(codes)
    assert sorted(t.tool for t in plan.tasks) == sorted(expected_tools(codes))


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
def test_no_plan_reaches_a_forbidden_tool(codes):
    tools = {t.tool for t in build_plan(codes).tasks}
    assert not (tools & AGENT_FORBIDDEN_TOOLS)
    assert tools <= AGENT_REACHABLE_TOOLS


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
def test_task_ids_are_unique(codes):
    ids = [t.task_id for t in build_plan(codes).tasks]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
def test_the_validator_accepts_the_canonical_plan(codes):
    TaskPlanValidator.validate(build_plan(codes))


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
def test_independent_capabilities_never_depend_on_each_other(codes):
    """Phụ thuộc giả tuần tự hoá thứ vốn chạy song song.

    Nó chỉ lộ ra khi một nhánh phải chờ duyệt — lúc ấy nhánh kia đứng im vô cớ,
    và không màn hình nào giải thích được vì sao.
    """
    plan = build_plan(codes)
    owner: dict[str, str] = {}
    cursor = 0
    for code in codes:
        for _ in ALL_CAPABILITIES[code].steps:
            owner[plan.tasks[cursor].task_id] = code
            cursor += 1

    for task in plan.tasks:
        for dependency in task.depends_on:
            assert owner[dependency] == owner[task.task_id], (
                f"{task.task_id}({owner[task.task_id]}) phụ thuộc {dependency}({owner[dependency]})"
            )


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
def test_every_input_ref_points_at_a_real_output_of_a_real_dependency(codes):
    plan = build_plan(codes)
    by_id = {t.task_id: t for t in plan.tasks}
    for task in plan.tasks:
        for name, value in task.input.items():
            if not isinstance(value, InputRef):
                continue
            assert value.from_task in by_id, (task.task_id, name)
            assert value.from_task in task.depends_on, f"{task.task_id}.{name} không khai báo phụ thuộc"
            source = by_id[value.from_task]
            assert value.field in TOOL_CONTRACTS[source.tool].outputs, (source.tool, value.field)


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
def test_every_required_input_is_present(codes):
    plan = build_plan(codes)
    for task in plan.tasks:
        required = TaskPlanValidator.REQUIRED_INPUTS.get(task.tool, frozenset())
        assert required <= set(task.input), (task.tool, sorted(required - set(task.input)))


# --- Ba mối nối liên bước, kiểm từng cái ------------------------------------


@pytest.mark.parametrize("codes", [c for c in COMBOS if "V" in c], ids=[i for i in IDS if "V" in i.split("+")])
def test_the_shuttle_takes_its_viewing_id_from_the_viewing(codes):
    plan = build_plan(codes)
    shuttle = next(t for t in plan.tasks if t.tool == "book_shuttle")
    ref = shuttle.input["viewing_id"]
    assert isinstance(ref, InputRef) and ref.field == "viewing_id"
    source = next(t for t in plan.tasks if t.task_id == ref.from_task)
    assert source.tool == "schedule_property_viewing"


@pytest.mark.parametrize("codes", [c for c in COMBOS if "P" in c], ids=[i for i in IDS if "P" in i.split("+")])
def test_the_parking_takes_its_vehicle_id_from_the_registration(codes):
    plan = build_plan(codes)
    parking = next(t for t in plan.tasks if t.tool == "book_parking")
    ref = parking.input["vehicle_id"]
    assert isinstance(ref, InputRef) and ref.field == "vehicle_id"
    source = next(t for t in plan.tasks if t.task_id == ref.from_task)
    assert source.tool == "register_vehicle"


@pytest.mark.parametrize("codes", [c for c in COMBOS if "P" in c], ids=[i for i in IDS if "P" in i.split("+")])
def test_all_three_payment_refs_come_from_one_and_the_same_booking(codes):
    """Trộn ba tham chiếu từ hai booking là trả tiền booking này bằng giá booking kia.

    Số tiền, loại tiền và mã đặt chỗ phải cùng một nguồn — nếu không, một khoản
    tiền đi ra với số của một chỗ đỗ khác, và mọi lớp kiểm đều thấy hợp lệ.
    """
    plan = build_plan(codes)
    payment = next(t for t in plan.tasks if t.tool == "pay_fee")
    sources = {payment.input[name].from_task for name in ("booking_id", "amount", "currency")}
    assert len(sources) == 1, sources
    # Lấy ra MỘT lần: gọi `pop()` bên trong generator sẽ rút cạn set ở mỗi vòng.
    source_id = sources.pop()
    source = next(t for t in plan.tasks if t.task_id == source_id)
    assert source.tool == "book_parking"
    for name in ("booking_id", "amount", "currency"):
        assert payment.input[name].field == name


# --- Hai biến thể -----------------------------------------------------------


def test_the_viewing_only_variant_has_no_shuttle():
    plan = build_plan(("V0",))
    assert [t.tool for t in plan.tasks] == ["schedule_property_viewing"]
    TaskPlanValidator.validate(plan)


def test_the_parking_without_payment_variant_has_no_pay_fee():
    plan = build_plan(("P0",))
    assert [t.tool for t in plan.tasks] == ["register_vehicle", "book_parking"]
    TaskPlanValidator.validate(plan)


def test_the_full_combination_covers_every_reachable_tool():
    """Tổ hợp đủ 5 capability phải chạm ĐÚNG 8 tool, mỗi tool một lần."""
    plan = build_plan(("V", "C", "M", "R", "P"))
    tools = [t.tool for t in plan.tasks]
    assert len(tools) == 8
    assert set(tools) == AGENT_REACHABLE_TOOLS
    assert len(set(tools)) == len(tools)
