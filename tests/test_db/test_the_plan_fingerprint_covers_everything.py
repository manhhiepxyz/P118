"""Vân tay kế hoạch phải bao phủ MỌI thứ một quyết định sửa phụ thuộc vào.

Reviewer đo được, cùng một task, chỉ khác bằng chứng gửi provider:

    NOT_SUBMITTED, external_id=None      → 483bf264151b9b76
    ACKNOWLEDGED,  external_id=BOOK-1    → 483bf264151b9b76

Hai thế giới khác hẳn nhau, một vân tay. Consequence Analysis (Phase 2B) đọc
đúng bằng chứng ấy để quyết định "sửa tại chỗ" hay "phải là một hành động
nghiệp vụ mới". Vân tay không đổi nghĩa là một `PatchDecision` tính khi task
chưa gửi vẫn ghi được sau khi provider đã xác nhận.

Phần thứ hai: chỉ được có MỘT bản cài đặt. Báo cáo trước nói `patch.py` và
repository dùng chung, nhưng `patch.py` vẫn giữ `_plan_version` riêng — hai
hàm, hai câu trả lời, và không ai so chúng.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from src.common.plan_fingerprint import plan_version_of

_SRC = Path(__file__).resolve().parents[2] / "src"


def _row(**over):
    base = {
        "task_id": "T1",
        "tool": "book_parking",
        "depends_on": [],
        "status": "PENDING",
        "input_data": {"parking_zone": "ZONE_A"},
        "provider_submission_status": "NOT_SUBMITTED",
        "external_request_id": None,
        "provider_idempotency_key": None,
    }
    base.update(over)
    return base


def test_the_submission_status_changes_the_fingerprint():
    before = plan_version_of([_row()], [])
    after = plan_version_of([_row(provider_submission_status="ACKNOWLEDGED")], [])
    assert before != after


def test_an_external_id_changes_the_fingerprint():
    before = plan_version_of([_row(provider_submission_status="ACKNOWLEDGED")], [])
    after = plan_version_of([_row(provider_submission_status="ACKNOWLEDGED", external_request_id="BOOK-1")], [])
    assert before != after


def test_the_idempotency_key_changes_the_fingerprint():
    """Khoá quyết định lần gửi sau có rơi vào bản ghi cũ hay không.

    Nó thuộc invariant của quyết định sửa, nên nó phải nằm trong vân tay.
    """
    assert plan_version_of([_row()], []) != plan_version_of([_row(provider_idempotency_key="K1")], [])


def test_the_exact_pair_the_reviewer_measured_now_differs():
    a = plan_version_of([_row(provider_submission_status="NOT_SUBMITTED", external_request_id=None)], [])
    b = plan_version_of([_row(provider_submission_status="ACKNOWLEDGED", external_request_id="BOOK-1")], [])
    assert a != b


def test_row_order_does_not_change_the_fingerprint():
    """Thứ tự PostgreSQL trả về không phải một thay đổi của thế giới."""
    rows = [_row(task_id="T1"), _row(task_id="T2", tool="pay_fee")]
    assert plan_version_of(rows, []) == plan_version_of(list(reversed(rows)), [])


def test_a_missing_evidence_column_is_normalised_not_crashed():
    """Row đọc từ một truy vấn không chọn cột bằng chứng vẫn phải tính được.

    Và nó phải cho CÙNG giá trị với row có cột nhưng để trống — nếu không, hai
    đường đọc khác nhau sinh hai vân tay cho cùng một trạng thái.
    """
    lean = {"task_id": "T1", "tool": "book_parking", "depends_on": [], "status": "PENDING", "input_data": {}}
    full = dict(lean, provider_submission_status=None, external_request_id=None, provider_idempotency_key=None)
    assert plan_version_of([lean], []) == plan_version_of([full], [])


# --- Một bản cài đặt duy nhất ------------------------------------------------


def test_only_one_module_defines_a_fingerprint_function():
    owners = []
    for path in _SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef) and "plan_version" in node.name:
                owners.append((path.name, node.name))
    assert owners == [("plan_fingerprint.py", "plan_version_of")], owners


def test_the_patch_validator_no_longer_carries_its_own_copy():
    source = (_SRC / "orchestration" / "patch.py").read_text()
    assert "def _plan_version" not in source
    assert "plan_version_of" in source


@pytest.mark.asyncio
async def test_the_validate_path_and_the_locked_path_agree(client, db_pool):
    """Cùng trạng thái → cùng version, dù đọc từ hai đường khác nhau.

    Đây là bất biến làm cho khoá lạc quan có nghĩa: tầng thẩm định tính một
    lần, repository tính lại sau khi đã khoá hàng, và chúng phải khớp.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
    from src.orchestration.patch import load_editable_plan

    wid = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','CANCELLED')", wid)
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1','book_parking','PENDING','{\"parking_zone\":\"ZONE_A\"}'::jsonb)",
            wid,
        )
    editable = await load_editable_plan(str(wid))
    snapshot = await PostgreSQLWorkflowStateRepository(db_pool).lock_workflow_for_amendment(
        str(wid), expected_plan_version=editable.plan_version
    )
    assert snapshot.conflict is None, snapshot.conflict
    assert snapshot.plan_version == editable.plan_version

    # Đổi bằng chứng → CẢ HAI đường phải thấy version mới.
    await db_pool.execute(
        "UPDATE workflow_tasks SET provider_submission_status='ACKNOWLEDGED', external_request_id='BOOK-9' "
        "WHERE workflow_id=$1",
        wid,
    )
    moved = await load_editable_plan(str(wid))
    assert moved.plan_version != editable.plan_version
    stale = await PostgreSQLWorkflowStateRepository(db_pool).lock_workflow_for_amendment(
        str(wid), expected_plan_version=editable.plan_version
    )
    assert stale.conflict == "PLAN_VERSION_CHANGED"
