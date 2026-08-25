"""Mỗi ô người dùng phải trả lời đều có bộ đọc — và chỉ những ô ấy.

Nhánh "còn một field thì lấy nguyên câu" đã bị bỏ. Bỏ nó là đúng, nhưng nó từng
che một lỗ: ô nào không ai viết bộ đọc vẫn nhận được giá trị. Bỏ mà không thay
thì lỗ ấy đổi thành một lỗ khác — ô không có bộ đọc trở nên KHÔNG TRẢ LỜI ĐƯỢC,
lặng lẽ, và người dùng bị hỏi lại mãi một câu.

Đo được trước khi sửa:

    parse_field("preferred_contact_time", "14:30") is None

`register_property_interest` đòi ô ấy, nên toàn bộ luồng đăng ký quan tâm không
hoàn tất được bằng hội thoại.

File này khoá cả hai phía: đủ bộ đọc cho ô người dùng trả lời, và KHÔNG có bộ
đọc cho ô có thẩm quyền. Thêm một ô vào contract mà quên bộ đọc thì suite đỏ.
"""

from __future__ import annotations

import pytest

from src.agents.planner import _BACKEND_VALIDATED_FIELDS
from src.common.field_parsers import (
    AUTHORITATIVE_FIELDS,
    FIELD_PARSERS,
    LEGACY_ONLY_FIELDS,
    MAX_SCHEDULE_HORIZON_DAYS,
    USER_ANSWERABLE_FIELDS,
    _spec_for,
    parse_field,
)
from src.common.tool_contract import TOOL_CONTRACTS


def test_every_user_answerable_contract_field_has_a_parser():
    missing = sorted(USER_ANSWERABLE_FIELDS - set(FIELD_PARSERS))
    assert missing == [], f"ô trong contract nhưng không có bộ đọc: {missing}"


def test_every_field_the_planner_expects_from_the_user_has_a_parser():
    """`_BACKEND_VALIDATED_FIELDS` là tập Planner coi là "người dùng trả lời được"."""
    missing = sorted(_BACKEND_VALIDATED_FIELDS - set(FIELD_PARSERS))
    assert missing == [], f"Planner hỏi nhưng không đọc được: {missing}"


def test_no_authoritative_field_can_be_read_from_free_text():
    """`resident_id`, `booking_id`, `amount`, `currency`... là dữ liệu của
    provider hoặc của phiên đăng nhập. Cho một câu người dùng gõ trở thành nguồn
    của chúng là mở lại đúng lỗ hổng mà trust boundary sinh ra để chặn.
    """
    leaked = sorted(AUTHORITATIVE_FIELDS & set(FIELD_PARSERS))
    assert leaked == [], f"ô có thẩm quyền lại đọc được từ văn bản: {leaked}"
    for name in AUTHORITATIVE_FIELDS:
        assert parse_field(name, "BOOK-001") is None
        assert parse_field(name, "999999") is None


def test_a_field_declared_differently_by_two_tools_gets_no_shared_parser():
    """Hai tool khai báo khác nhau cho cùng tên ô thì không có "một" luật để áp.

    Đoán bừa một bên là chọn hộ người dùng. `_spec_for` trả `None`, và ô ấy
    không vào bảng bộ đọc — nên nếu chuyện đó xảy ra, test parity ở trên đỏ và
    có người phải quyết định.
    """
    for name in sorted(USER_ANSWERABLE_FIELDS):
        declared = [c.inputs[name] for c in TOOL_CONTRACTS.values() if name in c.inputs]
        if len({(d.kind, d.enum, d.minimum, d.exclusive_minimum) for d in declared}) > 1:
            assert _spec_for(name) is None


# --- Chính các ô đã thiếu ----------------------------------------------------


@pytest.mark.parametrize(
    ("field", "said", "expected"),
    [
        # Đây là ô đã hỏng, và câu đo được.
        ("preferred_contact_time", "14:30", "14:30"),
        ("preferred_contact_time", "2 giờ chiều", None),  # ngoài cú pháp đã hỗ trợ
        ("preferred_contact_time", "19:00", None),  # ngoài giờ làm việc của bộ phận tư vấn
        ("interest_type", "tôi muốn thuê", "rent"),
        ("interest_type", "cần tư vấn thêm", "consultation"),
        ("interest_type", "consultation", "consultation"),
        ("issue_type", "điều hoà hỏng", "air_conditioning"),
        ("issue_type", "mất điện", "electrical"),
        ("issue_type", "ống nước rò rỉ", "plumbing"),
        ("issue_type", "chuyện gì đó", None),
        ("description", "vòi nước phòng tắm bị rỉ", "vòi nước phòng tắm bị rỉ"),
        ("description", "   ", None),
        ("location", "tầng 3, phòng 302", "tầng 3, phòng 302"),
        ("move_vehicle", "xe tải nhỏ", "van"),
        ("move_vehicle", "xe tải", "truck"),
        ("move_vehicle", "không cần xe", "none"),
    ],
)
def test_the_fields_that_had_no_parser_now_read_correctly(field, said, expected):
    assert parse_field(field, said) == expected


def test_a_legacy_only_field_has_no_parser_at_all():
    """`search_properties` đã bị loại khỏi Agent (tìm kiếm là chức năng
    marketplace). Ô của nó còn trong `TOOL_CONTRACTS` để tương thích cũ, nhưng
    Agent không hỏi, không vá, và KHÔNG cần bộ đọc.

    Viết một bộ đọc cho chúng — kể cả một bộ đọc tốt, biết "5 triệu" là
    5.000.000 — là làm sống lại một capability đã bị loại, ở một chỗ không ai
    nghĩ tới khi ra quyết định sản phẩm.
    """
    for name in ("transaction_type", "property_type", "max_price", "residential_area"):
        assert name in LEGACY_ONLY_FIELDS
        assert name not in FIELD_PARSERS
        assert parse_field(name, "5 triệu") is None
        assert parse_field(name, "căn hộ") is None


def test_a_legacy_field_is_still_declared_by_the_provider_contract():
    """Đóng đường tới, KHÔNG xoá contract/provider.

    Xoá là một thay đổi rộng hơn hẳn, và không cần thiết để đạt mục tiêu: thứ
    phải đóng là ĐƯỜNG TỚI từ Agent, không phải sự tồn tại của connector.
    """
    from src.common.tool_contract import TOOL_CONTRACTS

    assert "search_properties" in TOOL_CONTRACTS
    assert {"transaction_type", "property_type", "max_price"} <= set(TOOL_CONTRACTS["search_properties"].inputs)


def test_a_free_text_field_is_bounded():
    """Một mô tả 50.000 ký tự không phải mô tả; nó là payload, và nó đi thẳng
    xuống provider."""
    assert parse_field("description", "x" * 501) is None
    assert parse_field("description", "x" * 500) == "x" * 500


def test_the_planning_horizon_matches_the_validator():
    """1825 ngày là NĂM năm. Ghi chú cũ nói "hai năm" và nó sai.

    Trị số LẤY TỪ Validator, không chép: hai bảng nói cùng một luật mà nằm hai
    nơi thì sớm muộn cũng lệch, và bên lỏng hơn thành bên thật.
    """
    from src.agents.validator import TaskPlanValidator

    assert MAX_SCHEDULE_HORIZON_DAYS == TaskPlanValidator.MAX_HORIZON_DAYS
    assert MAX_SCHEDULE_HORIZON_DAYS == 1825
    assert MAX_SCHEDULE_HORIZON_DAYS / 365 == 5.0
