"""Chính sách tool sống ở MỘT chỗ, và chỗ đó không phụ thuộc tầng trên.

`src/common/field_parsers.py` từng `import src.agents.planner` để lấy
`PLANNER_ALLOWED_TOOLS`. Chiều phụ thuộc ấy sai: `common` là tầng dưới cùng —
`agents`, `api`, `orchestration` đều dựng trên nó. Một import ngược biến đồ thị
module thành vòng tiềm tàng, và nó buộc mọi thứ chạm `field_parsers` phải kéo
theo cả Planner.

Ba tập nằm ở `src/common/agent_tool_policy.py`, và mọi tầng đọc từ đó:

    PROVIDER_TOOLS         10   có connector, tồn tại thật
    AGENT_FORBIDDEN_TOOLS   2   register_resident, search_properties
    AGENT_REACHABLE_TOOLS   8   Planner lập được, Patch sửa được
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.common.agent_tool_policy import (
    AGENT_FORBIDDEN_TOOLS,
    AGENT_REACHABLE_TOOLS,
    PROVIDER_TOOLS,
)

_SRC = Path(__file__).resolve().parents[1] / "src"


def test_the_three_sets_are_consistent():
    assert len(PROVIDER_TOOLS) == 16
    assert AGENT_FORBIDDEN_TOOLS == frozenset(
        {
            "register_resident",
            "search_properties",
            # Hai tool "sửa thứ đã tồn tại". Cả hai chỉ có nghĩa khi mang một mã
            # do provider cấp từ một bước ĐÃ CHẠY; cho Planner lập kế hoạch với
            # chúng là cho model tự viết ra mã ấy — và mã ấy có thể là của người
            # khác. Đường sửa lỗi dựng chúng từ kết quả đã chạy, không từ câu chữ.
            "change_parking_zone",
            "cancel_property_viewing",
            "cancel_parking",
            "cancel_maintenance",
            "cancel_move",
            "cancel_shuttle",
        }
    )
    assert AGENT_REACHABLE_TOOLS == PROVIDER_TOOLS - AGENT_FORBIDDEN_TOOLS
    assert len(AGENT_REACHABLE_TOOLS) == 8


def test_the_provider_set_matches_the_type_contract():
    import typing

    from src.common.task_plan import AllowedTool

    assert PROVIDER_TOOLS == frozenset(typing.get_args(AllowedTool))


@pytest.mark.parametrize("module", sorted(p for p in _SRC.joinpath("common").glob("*.py")))
def test_a_common_module_never_imports_a_higher_layer(module):
    """`common` là tầng dưới cùng. Nó không được biết `agents`/`api`/`orchestration`.

    Kiểm bằng `ast` chứ không grep: một tên trong ghi chú ("xem `planner.py`")
    là tài liệu, không phải một phụ thuộc.
    """
    # Ba tầng mà `common` tuyệt đối không được biết. `services` KHÔNG có trong
    # danh sách này: `src/common/failures.py` đã import `src.services.llm` từ
    # trước, và gỡ nó là một thay đổi ngoài phạm vi. Ghi ra đây thay vì mở rộng
    # danh sách rồi im lặng — nó là nợ đã biết, không phải nợ được tha.
    higher = {"agents", "api", "orchestration"}
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src."):
            layer = (node.module or "").split(".")[1]
            assert layer not in higher, f"{module.name} → {node.module}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src."):
                    assert alias.name.split(".")[1] not in higher, f"{module.name} → {alias.name}"


def test_nobody_keeps_a_second_copy_of_the_allowlist():
    """Hai bản của một chính sách là hai câu trả lời cho một câu hỏi.

    Mọi tầng phải ĐỌC từ `agent_tool_policy`, không tự dựng lại tập của mình.
    Kiểm bằng `ast`: chỉ tính phép GÁN ở mức module, không tính import lại dưới
    một tên khác (đó là đọc, không phải định nghĩa).
    """
    names = {"PROVIDER_TOOLS", "AGENT_FORBIDDEN_TOOLS", "AGENT_REACHABLE_TOOLS"}
    owners = set()
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            if names & set(targets):
                owners.add(path.name)
    assert owners == {"agent_tool_policy.py"}, sorted(owners)


def test_the_schedule_policy_also_has_one_home():
    """Cùng vấn đề, cùng cách chữa: `field_parsers` từng import
    `src.agents.validator` chỉ để lấy trần thời gian và khung giờ."""
    from src.agents.validator import TaskPlanValidator
    from src.common.field_parsers import MAX_SCHEDULE_HORIZON_DAYS
    from src.common.schedule_policy import MAX_HORIZON_DAYS, TIME_INPUTS

    assert MAX_SCHEDULE_HORIZON_DAYS == MAX_HORIZON_DAYS == TaskPlanValidator.MAX_HORIZON_DAYS
    assert TaskPlanValidator.TIME_INPUTS is TIME_INPUTS


def test_every_layer_reads_from_the_same_source():
    from src.agents.planner import PLANNER_ALLOWED_TOOLS, PLANNER_FORBIDDEN_TOOLS
    from src.common import field_parsers
    from src.orchestration.patch import PATCHABLE_FIELDS_BY_TOOL

    assert PLANNER_FORBIDDEN_TOOLS == AGENT_FORBIDDEN_TOOLS
    assert PLANNER_ALLOWED_TOOLS == AGENT_REACHABLE_TOOLS
    assert field_parsers.AGENT_REACHABLE_TOOLS == AGENT_REACHABLE_TOOLS
    assert set(PATCHABLE_FIELDS_BY_TOOL) <= AGENT_REACHABLE_TOOLS
