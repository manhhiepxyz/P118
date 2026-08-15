"""Ma trận contract cho cả 9 tool: schema ↔ Validator ↔ Connector cùng một luật.

Trước khi có `src/common/tool_contract.py`, luật của một tool nằm rải ở ba nơi
và không nơi nào biết nơi kia. Validator chấp nhận `transaction_type="hack"`,
`max_price=-1`, `currency="USD"`, `needs_elevator="yes"` — những plan mà
provider chắc chắn từ chối, nhưng chỉ vỡ ra ở tận Executor.

Các test ở đây kiểm HÀNH VI (dựng plan rồi validate), không assert chuỗi có
tồn tại trong source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.validator import TaskPlanValidator
from src.common.task_plan import InputRef, Task, TaskPlan
from src.common.tool_contract import TOOL_CONTRACTS
from src.orchestration.deps import build_connectors

# Một input hợp lệ tối thiểu cho từng tool. Ngày đặt xa để không bao giờ rơi
# vào quá khứ khi suite chạy lại sau này.
VALID_INPUTS: dict[str, dict] = {
    "search_properties": {
        "transaction_type": "rent",
        "property_type": "apartment",
        "residential_area": "Vinhomes Ocean Park",
        "max_price": 20_000_000,
    },
    "schedule_property_viewing": {
        "project_id": "PRJ-001",
        "viewing_date": "2030-12-10",
        "viewing_time": "10:00",
    },
    "register_property_interest": {
        "project_id": "PRJ-001",
        "interest_type": "consultation",
        "preferred_contact_time": "14:30",
        "consent": True,
    },
    "create_maintenance_request": {
        "issue_type": "air_conditioning",
        "description": "May lanh chay nhung khong mat",
        "location": "phong khach",
        "preferred_date": "2030-12-10",
        "preferred_time": "09:00",
    },
    "schedule_move": {
        "move_date": "2030-12-10",
        "move_time": "14:00",
        "needs_elevator": True,
        "needs_loading_support": False,
        "move_vehicle": "truck",
    },
    "register_resident": {
        "full_name": "Lam Thanh Bao",
        "apartment_code": "A1201",
        "residential_area": "Vinhomes Ocean Park",
    },
    "register_vehicle": {
        "resident_id": "RES-001",
        "plate_number": "51A-12345",
        "vehicle_type": "car",
    },
    "book_parking": {
        "vehicle_id": "VEH-001",
        "booking_date": "2030-12-10",
        "parking_zone": "ZONE_A",
    },
    "pay_fee": {"booking_id": "BOOK-001", "amount": 150_000, "currency": "VND"},
}

ALL_TOOLS = sorted(TOOL_CONTRACTS)


def _plan(tool: str, input_data: dict) -> TaskPlan:
    return TaskPlan(goal="Contract matrix", tasks=[Task(task_id="T1", tool=tool, depends_on=[], input=input_data)])


# ---------------------------------------------------------------------------
# Đồng bộ ba tầng
# ---------------------------------------------------------------------------


def test_contract_covers_exactly_the_nine_shared_tools() -> None:
    assert len(ALL_TOOLS) == 9
    assert set(ALL_TOOLS) == TaskPlanValidator.ALLOWED_TOOLS


@pytest.mark.parametrize("tool", ALL_TOOLS)
def test_valid_input_passes_for_every_tool(tool: str) -> None:
    """Ma trận không được siết tới mức chặn cả plan đúng."""
    assert TaskPlanValidator.validate(_plan(tool, VALID_INPUTS[tool])) is not None


@pytest.mark.parametrize("tool", ALL_TOOLS)
def test_required_inputs_agree_with_validator_table(tool: str) -> None:
    assert TOOL_CONTRACTS[tool].required == TaskPlanValidator.REQUIRED_INPUTS[tool]


def test_connector_output_fields_match_the_contract() -> None:
    """Output contract phải khớp đúng danh sách field Connector lọc ra.

    Nếu Connector đổi danh sách mà quên cập nhật contract, InputRef sẽ trỏ tới
    field không còn tồn tại và chỉ vỡ lúc chạy thật.
    """
    declared = {
        "search_properties": {"properties", "result_count"},
        "schedule_property_viewing": {
            "viewing_id",
            "project_id",
            "project_name",
            "viewing_date",
            "viewing_time",
            "viewing_status",
            "contact_name",
            "contact_phone",
        },
        "register_property_interest": {
            "interest_id",
            "project_id",
            "project_name",
            "interest_status",
            "contact_channel",
        },
        "create_maintenance_request": {
            "maintenance_id",
            "maintenance_status",
            "appointment_date",
            "appointment_time",
        },
        "schedule_move": {"move_request_id", "move_status", "move_date", "move_time", "elevator_slot"},
        "register_resident": {"resident_id"},
        "register_vehicle": {"vehicle_id"},
        "book_parking": {"booking_id", "parking_zone", "booking_date", "amount", "currency"},
        "pay_fee": {"payment_id", "payment_status"},
    }
    for tool, fields in declared.items():
        assert set(TOOL_CONTRACTS[tool].outputs) == fields, tool


# Chủ sở hữu canonical của từng tool. Đây là bảng mà runtime PHẢI khớp.
EXPECTED_OWNERS: dict[str, str] = {
    "search_properties": "PropertyConnector",
    "schedule_property_viewing": "TourConnector",
    "register_property_interest": "ConsultationConnector",
    "register_resident": "ResidentConnector",
    "register_vehicle": "TransportConnector",
    "book_parking": "TransportConnector",
    "pay_fee": "PaymentConnector",
    "create_maintenance_request": "ResidentServicesConnector",
    "schedule_move": "ResidentServicesConnector",
}


def test_every_tool_is_owned_by_exactly_one_connector() -> None:
    """Dựng runtime THẬT và kiểm quyền sở hữu trên đó.

    Bản cũ tự liệt kê năm connector trong test. Danh sách viết tay đó lệch khỏi
    `build_connectors()` mà không ai biết: khi TourConnector và
    ConsultationConnector được thêm vào runtime, test vẫn xanh trong khi nó
    không còn kiểm runtime nữa. Ở đây gọi thẳng factory, nên test không thể tụt
    lại phía sau cấu hình thật.
    """
    connectors = build_connectors()

    owners: dict[str, list[str]] = {tool: [] for tool in ALL_TOOLS}
    for connector in connectors:
        for tool in connector.tool_names:
            owners.setdefault(tool, []).append(type(connector).__name__)

    unowned = sorted(tool for tool, names in owners.items() if not names)
    shared = {tool: names for tool, names in owners.items() if len(names) > 1}
    unexpected = sorted(set(owners) - set(ALL_TOOLS))

    assert not unowned, f"tool không có connector nào phục vụ: {unowned}"
    assert not shared, f"tool có nhiều hơn một chủ, ai thắng tuỳ thứ tự đăng ký: {shared}"
    assert not unexpected, f"connector khai tool ngoài contract: {unexpected}"
    assert {tool: names[0] for tool, names in owners.items()} == EXPECTED_OWNERS


def test_runtime_exposes_exactly_the_nine_canonical_tools() -> None:
    connectors = build_connectors()

    registered = [tool for connector in connectors for tool in connector.tool_names]

    assert len(registered) == 9, f"số tool đăng ký lệch: {sorted(registered)}"
    assert set(registered) == set(ALL_TOOLS)


def test_shuttle_connector_is_absent_from_the_default_runtime() -> None:
    """`book_shuttle` là experimental — runtime mặc định không được biết tới nó.

    Source vẫn nằm trong repo nên đọc code dễ tưởng tool còn dùng được. Ràng
    buộc thật nằm ở registry: nó phải vắng mặt. Bật lại mà chưa đổi `tour_id`
    thành `viewing_id` sẽ tạo một tool Planner không cấp nổi input.
    """
    connectors = build_connectors()

    assert not any(type(c).__name__ == "ShuttleConnector" for c in connectors)
    assert "book_shuttle" not in {tool for c in connectors for tool in c.tool_names}


# ---------------------------------------------------------------------------
# Case invalid — mỗi case ứng với một guard cụ thể
# ---------------------------------------------------------------------------

INVALID_CASES = [
    # (nhãn, tool, input, mảnh message mong đợi)
    ("enum transaction_type sai", "search_properties", {"transaction_type": "hack"}, "transaction_type"),
    ("enum property_type sai", "search_properties", {"property_type": "villa"}, "property_type"),
    ("max_price âm", "search_properties", {"max_price": -1}, "max_price"),
    ("max_price bằng 0", "search_properties", {"max_price": 0}, "max_price"),
    ("max_price là chuỗi", "search_properties", {"max_price": "20000000"}, "max_price"),
    ("interest_type sai", "register_property_interest", {"interest_type": "nonsense"}, "interest_type"),
    (
        "preferred_contact_time sai",
        "register_property_interest",
        {"preferred_contact_time": "midnight"},
        "preferred_contact_time",
    ),
    ("consent False", "register_property_interest", {"consent": False}, "consent"),
    ("consent chuỗi 'true'", "register_property_interest", {"consent": "true"}, "consent"),
    ("needs_elevator 'yes'", "schedule_move", {"needs_elevator": "yes"}, "needs_elevator"),
    ("needs_loading_support 'no'", "schedule_move", {"needs_loading_support": "no"}, "needs_loading_support"),
    ("move_vehicle 'plane'", "schedule_move", {"move_vehicle": "plane"}, "move_vehicle"),
    ("amount âm", "pay_fee", {"amount": -1}, "amount"),
    ("currency USD", "pay_fee", {"currency": "USD"}, "currency"),
    ("amount là bool", "pay_fee", {"amount": True}, "amount"),
    ("amount là chuỗi", "pay_fee", {"amount": "150000"}, "amount"),
    ("issue_type sai", "create_maintenance_request", {"issue_type": "ufo"}, "issue_type"),
    ("description rỗng", "create_maintenance_request", {"description": "   "}, "description"),
    ("vehicle_type sai", "register_vehicle", {"vehicle_type": "helicopter"}, "vehicle_type"),
    ("plate_number rỗng", "register_vehicle", {"plate_number": ""}, "plate_number"),
    ("parking_zone ZONE_C", "book_parking", {"parking_zone": "ZONE_C"}, "parking_zone"),
    ("full_name toàn khoảng trắng", "register_resident", {"full_name": "  "}, "full_name"),
    ("booking_date sai định dạng", "book_parking", {"booking_date": "10-12-2030"}, "booking_date"),
    ("viewing_time sai định dạng", "schedule_property_viewing", {"viewing_time": "25:99"}, "viewing_time"),
]


@pytest.mark.parametrize(
    ("label", "tool", "override", "expected_field"),
    INVALID_CASES,
    ids=[case[0] for case in INVALID_CASES],
)
def test_invalid_input_is_rejected_before_the_connector(
    label: str, tool: str, override: dict, expected_field: str
) -> None:
    plan = _plan(tool, {**VALID_INPUTS[tool], **override})

    with pytest.raises(ValueError) as exc_info:
        TaskPlanValidator.validate(plan)

    assert expected_field in str(exc_info.value), label


@pytest.mark.parametrize("tool", ALL_TOOLS)
def test_unexpected_input_field_is_rejected(tool: str) -> None:
    plan = _plan(tool, {**VALID_INPUTS[tool], "debug_override": "x"})

    with pytest.raises(ValueError, match="unexpected input field 'debug_override'"):
        TaskPlanValidator.validate(plan)


# ---------------------------------------------------------------------------
# InputRef
# ---------------------------------------------------------------------------


def test_input_ref_to_a_field_the_source_tool_never_returns_is_rejected() -> None:
    plan = TaskPlan(
        goal="Chuỗi phụ thuộc",
        tasks=[
            Task(task_id="T1", tool="register_vehicle", depends_on=[], input=VALID_INPUTS["register_vehicle"]),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    **VALID_INPUTS["book_parking"],
                    # register_vehicle chỉ trả `vehicle_id`.
                    "vehicle_id": InputRef(from_task="T1", field="booking_id"),
                },
            ),
        ],
    )

    with pytest.raises(ValueError, match="does not return"):
        TaskPlanValidator.validate(plan)


def test_input_ref_with_incompatible_kind_is_rejected() -> None:
    """`book_parking.amount` là integer; không nhét được vào ô `booking_id` string."""
    plan = TaskPlan(
        goal="Chuỗi phụ thuộc",
        tasks=[
            Task(task_id="T1", tool="book_parking", depends_on=[], input=VALID_INPUTS["book_parking"]),
            Task(
                task_id="T2",
                tool="pay_fee",
                depends_on=["T1"],
                input={
                    "booking_id": InputRef(from_task="T1", field="amount"),
                    "amount": InputRef(from_task="T1", field="amount"),
                    "currency": InputRef(from_task="T1", field="currency"),
                },
            ),
        ],
    )

    with pytest.raises(ValueError, match="expects string"):
        TaskPlanValidator.validate(plan)


def test_correct_payment_chain_still_passes() -> None:
    """Chuỗi book_parking → pay_fee đúng 1:1 vẫn phải qua được."""
    plan = TaskPlan(
        goal="Đặt chỗ rồi thanh toán",
        tasks=[
            Task(task_id="T1", tool="book_parking", depends_on=[], input=VALID_INPUTS["book_parking"]),
            Task(
                task_id="T2",
                tool="pay_fee",
                depends_on=["T1"],
                input={
                    "booking_id": InputRef(from_task="T1", field="booking_id"),
                    "amount": InputRef(from_task="T1", field="amount"),
                    "currency": InputRef(from_task="T1", field="currency"),
                },
            ),
        ],
    )

    assert TaskPlanValidator.validate(plan) is plan


# ---------------------------------------------------------------------------
# Lỗi không được rò dữ liệu
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "0912345678",
        "079201001234",
        "sk-live-abcdef123456",  # secret-fixture
        "Nguyen Van A",
    ],
)
def test_contract_violation_message_never_echoes_the_offending_value(secret: str) -> None:
    """Message chỉ nêu tên field và LUẬT, không nêu giá trị nhận được."""
    plan = _plan("register_vehicle", {**VALID_INPUTS["register_vehicle"], "vehicle_type": secret})

    with pytest.raises(ValueError) as exc_info:
        TaskPlanValidator.validate(plan)

    message = str(exc_info.value)
    assert secret not in message
    assert "vehicle_type" in message


# ---------------------------------------------------------------------------
# Luật amount phải giống nhau ở MỌI tầng
#
# Từng có lúc Tool Contract cho >=0, mock schema cho ge=0, schema.sql cho >=0
# còn migration lại bắt >0. Plan với amount=0 qua được Validator rồi mới vỡ ở
# INSERT — lỗi hiện ra ở tầng sai, và chỉ hiện khi đã chạm database thật.
# ---------------------------------------------------------------------------


def test_payment_amount_must_be_positive_at_the_contract_layer() -> None:
    plan = _plan("pay_fee", {**VALID_INPUTS["pay_fee"], "amount": 0})

    with pytest.raises(ValueError, match="amount"):
        TaskPlanValidator.validate(plan)


def test_booking_quote_amount_must_be_positive_too() -> None:
    spec = TOOL_CONTRACTS["book_parking"].outputs["amount"]
    assert spec.check(0) is not None
    assert spec.check(150_000) is None


def test_amount_rule_matches_the_provider_pydantic_model() -> None:
    """Cùng một giá trị phải bị từ chối ở cả Tool Contract lẫn provider."""
    from pydantic import ValidationError

    from src.mock.schemas import PayFeeRequest

    for rejected in (0, -1):
        assert TOOL_CONTRACTS["pay_fee"].inputs["amount"].check(rejected) is not None
        with pytest.raises(ValidationError):
            PayFeeRequest(booking_id="BOOK-001", amount=rejected, currency="VND")

    assert TOOL_CONTRACTS["pay_fee"].inputs["amount"].check(150_000) is None
    assert PayFeeRequest(booking_id="BOOK-001", amount=150_000, currency="VND").amount == 150_000


def test_database_check_constraints_agree_with_the_contract() -> None:
    """schema.sql phải bắt > 0, không phải >= 0."""
    schema = (Path(__file__).parents[1] / "src" / "db" / "schema.sql").read_text(encoding="utf-8")

    assert "CHECK (amount >= 0)" not in schema
    assert schema.count("CHECK (amount > 0)") == 2  # parking_bookings + payments
