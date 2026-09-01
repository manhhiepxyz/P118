"""Patch Validator quyết định thay đổi nào được áp dụng. Model không quyết định gì.

Đây là nửa "hậu quả". Nó nhận một ĐỀ XUẤT — có thể do model sinh ra, có thể
không ổn định giữa hai lượt — và trả về một quyết định ĐỌC TỪ DATABASE, lặp lại
được, không phụ thuộc câu chữ người dùng đã gõ.

Ranh giới quan trọng nhất trong file này: Bước 1 KHÔNG gọi Executor, KHÔNG gọi
provider, KHÔNG mở lại task, KHÔNG ghi gì. Validator chỉ đọc và kết luận.

Bằng chứng "provider đã nhận request" KHÔNG suy ra từ `service_approvals` hay
`workflows.status`. Hai thứ đó nói về hàng đợi DUYỆT và về vòng đời workflow —
chúng không phải bản ghi của một lời gọi ra ngoài. Chừng nào chưa có
`external_request_id`, mọi câu hỏi cần biết điều đó phải FAIL-CLOSED.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest

from src.agents.intent_resolver import Intent, IntentProposal, ProposedChange
from src.orchestration.patch import (
    PatchOutcome,
    load_editable_plan,
    validate_patch,
)


def _future(days: int) -> str:
    """Ngày trong tương lai TÍNH TỪ HÔM NAY.

    Ngày cố định trong test tự hỏng khi nó thành quá khứ — và hỏng theo kiểu
    khó đọc: `_is_allowed_schedule_date` từ chối, bộ đọc trả `None`, rồi test
    báo `UNPARSABLE` cho một câu vốn hợp lệ.
    """
    return (date.today() + timedelta(days=days)).isoformat()


OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


async def _seed(
    pool,
    *,
    task_status: str = "PENDING",
    workflow_status: str = "CANCELLED",
    inputs: dict | None = None,
    tool: str = "schedule_property_viewing",
    owner: uuid.UUID = OWNER,
) -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, password_hash) VALUES ($1,$2,'x') ON CONFLICT DO NOTHING",
            owner,
            f"nguoi-{owner.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) VALUES ($1,'đặt lịch',$2,$3)",
            wid,
            workflow_status,
            owner,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1',$2,$3,$4::jsonb)",
            wid,
            tool,
            task_status,
            json.dumps(inputs or {"project_id": "PRJ-001", "viewing_date": _future(1), "viewing_time": "09:30"}),
        )
    return str(wid)


def _modify(field: str, value: str, **kw) -> IntentProposal:
    return IntentProposal(
        intent=Intent.MODIFY_EXISTING,
        changes=[ProposedChange(field=field, value=value)],
        confidence=kw.pop("confidence", 0.9),
        **kw,
    )


# --- Case A: sửa một ô, hợp lệ ----------------------------------------------


@pytest.mark.asyncio
async def test_a_valid_field_change_is_accepted_without_touching_anything(client, db_pool):
    """Case A. Ngày 22 → 30, task chưa chạy, không ai đang duyệt.

    `PATCH_ACCEPTED` nghĩa là "được phép", KHÔNG phải "đã làm". Bước 1 không ghi
    gì cả — test kiểm luôn điều đó.
    """
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_ACCEPTED
    assert decision.accepted == {"viewing_date": _future(30)}

    after = await db_pool.fetchval(
        "SELECT input_data->>'viewing_date' FROM workflow_tasks WHERE workflow_id=$1::uuid",
        uuid.UUID(workflow_id),
    )
    assert after == _future(1), "Bước 1 không được ghi gì"


@pytest.mark.asyncio
async def test_the_decision_carries_the_plan_version_it_was_made_against(client, db_pool):
    """Khoá lạc quan: quyết định phải mang theo phiên bản kế hoạch nó dựa trên.

    Không có nó thì giữa lúc validate và lúc persist, một task có thể chuyển
    PENDING → RUNNING và bản vá được ghi đè lên một việc đang chạy.
    """
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(OWNER))
    assert decision.plan_version == editable.plan_version
    assert decision.workflow_id == workflow_id


# --- Quyền, đọc từ PostgreSQL ------------------------------------------------


@pytest.mark.asyncio
async def test_another_person_cannot_patch_this_request(client, db_pool):
    """Quyền đọc từ database, không từ câu người dùng nói và không từ model."""
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(STRANGER))
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "NOT_OWNER"


# --- Case C: đổi hình dạng kế hoạch → Planner --------------------------------


@pytest.mark.asyncio
async def test_a_declared_scope_change_alone_is_not_evidence(client, db_pool):
    """Model nói phạm vi đổi; code không thấy bằng chứng nào.

    Hợp đồng CŨ ở đây là `REPLAN_REQUIRED` — và nó sai: một boolean do model
    sinh ra đủ để kích hoạt một lượt lập kế hoạch mới. Validator KHÔNG tự xoá
    task nào, và cũng không được tự gọi Planner hộ.
    """
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(
        _modify("viewing_date", _future(30), scope_change=True),
        editable,
        requester_user_id=str(OWNER),
    )
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "UNVERIFIED_SCOPE_CHANGE"
    assert decision.needs_clarification is True


@pytest.mark.asyncio
async def test_a_capability_field_goes_to_replan_even_if_the_model_says_otherwise(client, db_pool):
    """`wants_shuttle` quyết định task `book_shuttle` CÓ TỒN TẠI hay không.

    Đổi nó là đổi đồ thị công việc, không phải đổi một giá trị. Và điều đó được
    quyết định bằng code — model nói `scope_change=false` cũng không đổi được.
    """
    workflow_id = await _seed(
        db_pool,
        inputs={"project_id": "PRJ-001", "viewing_date": _future(1), "wants_shuttle": True},
    )
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(
        _modify("wants_shuttle", "false", scope_change=False), editable, requester_user_id=str(OWNER)
    )
    assert decision.outcome is PatchOutcome.REPLAN_REQUIRED
    assert decision.reason_code == "SHAPE_FIELD"


@pytest.mark.asyncio
async def test_a_field_that_is_not_in_the_plan_asks_the_user_instead_of_replanning(client, db_pool):
    """Ô model bịa ra KHÔNG được kích hoạt Planner.

    Cho một tên ô lạ mở đường tới lập kế hoạch mới là mở lại đúng lỗ mà tầng
    này sinh ra để đóng: một câu mơ hồ dựng lại một việc người dùng vừa dừng.
    Hỏi lại người dùng.
    """
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("plate_number", "30A-123.45"), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "UNKNOWN_FIELD"
    assert decision.needs_clarification is True


# --- Case E: task đã SUCCESS -------------------------------------------------


@pytest.mark.asyncio
async def test_a_completed_task_needs_a_business_action_not_a_replan(client, db_pool):
    """Case E. Task đã SUCCESS đã tạo cam kết thật ở phía đơn vị cung cấp.

    KHÔNG `REPLAN_REQUIRED`: Planner chung có thể dựng lại đúng tool ấy và đặt
    lần hai. Cần một hành động nghiệp vụ riêng (RESCHEDULE_VIEWING,
    CHANGE_BOOKING) — Phase 1 chỉ kết luận, chưa triển khai.
    """
    workflow_id = await _seed(db_pool, task_status="SUCCESS", workflow_status="FAILED")
    editable = await load_editable_plan(workflow_id)
    before = await db_pool.fetchrow(
        "SELECT status, input_data FROM workflow_tasks WHERE workflow_id=$1::uuid",
        uuid.UUID(workflow_id),
    )
    decision = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.BUSINESS_ACTION_REQUIRED
    assert decision.reason_code == "TASK_COMPLETED"
    assert decision.accepted == {}, "không có ô nào được chấp nhận"
    assert decision.outcome is not PatchOutcome.REPLAN_REQUIRED

    after = await db_pool.fetchrow(
        "SELECT status, input_data FROM workflow_tasks WHERE workflow_id=$1::uuid",
        uuid.UUID(workflow_id),
    )
    assert dict(after) == dict(before), "bước đã xong không được mutate, không được rerun"


@pytest.mark.asyncio
async def test_a_running_task_is_not_patched_underneath_itself(client, db_pool):
    # Workflow đã huỷ nhưng một bước vẫn RUNNING — xảy ra khi người dùng bấm
    # Dừng đúng lúc Executor đang gọi. Seed như vậy để cô lập ĐÚNG hàng rào
    # task-status, không để hàng rào workflow-status chặn trước.
    workflow_id = await _seed(db_pool, task_status="RUNNING", workflow_status="CANCELLED")
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "TASK_RUNNING"


# --- Hàng đợi duyệt: là TRẠNG THÁI, không phải bằng chứng đã gửi provider ----


@pytest.mark.asyncio
async def test_a_request_under_review_is_not_patched(client, db_pool):
    """Đơn vị đang xem xét thì không được sửa dưới tay họ.

    Lý do là `UNDER_REVIEW` — KHÔNG phải "đã gửi provider". `service_approvals`
    là hàng đợi quyết định nội bộ; nó không chứng minh có lời gọi nào đi ra.
    """
    from src.orchestration.service_approval import save_pending_service_approvals

    # `CANCELLED` ở cột trạng thái nhưng hồ sơ duyệt vẫn `AWAITING` — đo được
    # trên workflow thật, và chính là lý do hàng đợi phải được đọc riêng.
    workflow_id = await _seed(db_pool, task_status="WAITING_APPROVAL", workflow_status="CANCELLED")
    await save_pending_service_approvals(
        db_pool,
        workflow_id=workflow_id,
        rows=[
            {
                "task_id": "T1",
                "tool": "schedule_property_viewing",
                "service_label": "Đặt lịch tham quan",
                "details": {"viewing_date": _future(1)},
            }
        ],
    )
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "UNDER_REVIEW"


@pytest.mark.asyncio
async def test_provider_submission_is_reported_as_unknown_not_guessed(client, db_pool):
    """Chưa có `external_request_id` thì câu trả lời đúng là "không biết".

    Suy từ `workflows.status` hay từ hàng đợi duyệt là đoán, và đoán sai ở đây
    nghĩa là sửa một thứ đơn vị cung cấp đã nhận. Consequence Analysis (Bước 2)
    phải fail-closed trên đúng cờ này.
    """
    workflow_id = await _seed(db_pool, workflow_status="WAITING_APPROVAL")
    editable = await load_editable_plan(workflow_id)
    assert editable.provider_submission_known is False


# --- Đề xuất không dùng được -------------------------------------------------


@pytest.mark.asyncio
async def test_an_unparsable_value_is_rejected_by_the_canonical_parser(client, db_pool):
    """Model có thể viết ra bất cứ chuỗi nào. Parser mới quyết định nó là gì."""
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    for junk in ("2026-02-31", "hôm nào đó", ""):
        decision = await validate_patch(_modify("viewing_date", junk), editable, requester_user_id=str(OWNER))
        assert decision.outcome is PatchOutcome.PATCH_REJECTED, junk


@pytest.mark.asyncio
async def test_a_change_to_the_same_value_is_not_a_change(client, db_pool):
    """Chạy lại một kế hoạch y nguyên là một lượt gọi provider thừa."""
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("viewing_date", _future(1)), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "NO_EFFECTIVE_CHANGE"


@pytest.mark.asyncio
async def test_an_empty_or_unknown_proposal_never_reaches_the_planner(client, db_pool):
    """`PATCH_REJECTED`/`UNKNOWN` đi tới CLARIFICATION, không tới Planner.

    Chỉ `NEW_GOAL` mới được lập kế hoạch mới. Rơi về Planner từ một đề xuất
    hỏng là cách một câu mơ hồ dựng lại một việc người dùng vừa dừng.
    """
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    for proposal in (
        IntentProposal(intent=Intent.MODIFY_EXISTING, changes=[], confidence=0.9),
        IntentProposal(intent=Intent.UNKNOWN, confidence=0.1),
    ):
        decision = await validate_patch(proposal, editable, requester_user_id=str(OWNER))
        assert decision.outcome is not PatchOutcome.REPLAN_REQUIRED
        assert decision.needs_clarification is True
        assert decision.accepted == {}


@pytest.mark.asyncio
async def test_a_new_goal_is_the_only_thing_that_reaches_the_planner(client, db_pool):
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(
        IntentProposal(intent=Intent.NEW_GOAL, confidence=0.9), editable, requester_user_id=str(OWNER)
    )
    assert decision.outcome is PatchOutcome.NOT_A_PATCH
    assert decision.needs_clarification is False


# --- Ổn định: proposal có thể dao động, hậu quả thì không --------------------


@pytest.mark.asyncio
async def test_the_same_proposal_always_gives_the_same_consequence(client, db_pool):
    """Model không tất định. Tầng này thì phải."""
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    outcomes = set()
    for _ in range(5):
        decision = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(OWNER))
        outcomes.add((decision.outcome, tuple(sorted(decision.accepted.items()))))
    assert len(outcomes) == 1


# --- Trạng thái workflow: allowlist, fail-closed -----------------------------


@pytest.mark.parametrize("status", ["RUNNING", "PENDING", "SUCCESS", "WAITING_APPROVAL"])
@pytest.mark.asyncio
async def test_a_workflow_that_is_not_stopped_is_never_patched(client, db_pool, status):
    """Bốn trạng thái này đều nghĩa là có tiến trình hoặc có người đang tác động.

    Trước hàng rào này, Validator chỉ kiểm trạng thái TASK — nên một workflow
    `RUNNING` mà bước đang xét tình cờ còn `PENDING` vẫn vá được, ngay dưới tay
    Executor.
    """
    workflow_id = await _seed(db_pool, workflow_status=status)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "WORKFLOW_NOT_EDITABLE"


@pytest.mark.parametrize("status", ["CANCELLED", "FAILED"])
@pytest.mark.asyncio
async def test_only_a_stopped_workflow_is_editable(client, db_pool, status):
    workflow_id = await _seed(db_pool, workflow_status=status)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_ACCEPTED


def test_the_editable_allowlist_only_names_statuses_that_exist():
    """`NEEDS_INFORMATION` KHÔNG có trong `WorkflowStatus`.

    Thêm nó vào allowlist dựa trên phỏng đoán là mở một cửa cho một giá trị
    không bao giờ xuất hiện — hoặc tệ hơn, cho một giá trị nghĩa khác.
    """
    from src.common.enums import WorkflowStatus
    from src.orchestration.patch import EDITABLE_WORKFLOW_STATUSES

    real = {status.value for status in WorkflowStatus}
    assert EDITABLE_WORKFLOW_STATUSES <= real
    assert "NEEDS_INFORMATION" not in real


# --- Hàng đợi duyệt: CẢ HAI bảng ---------------------------------------------


@pytest.mark.asyncio
async def test_a_pending_payment_decision_blocks_the_patch(client, db_pool):
    """`payment_approvals` là bảng RIÊNG, không nằm trong `service_approvals`.

    Thiếu nó thì một yêu cầu đang chờ CHÍNH NGƯỜI DÙNG duyệt tiền vẫn sửa được
    — họ bấm duyệt một số tiền, hệ thống chạy một số khác.
    """
    workflow_id = await _seed(db_pool)
    async with db_pool.acquire() as conn:
        # Chuỗi khoá ngoại thật: resident → vehicle → booking → approval.
        await conn.execute(
            "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
            "VALUES ('RES-PATCH','Người Thử','A-101','Khu A') ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type) "
            "VALUES ('VEH-PATCH','RES-PATCH','30A-999.99','car') ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO parking_bookings (booking_id, vehicle_id, parking_zone, booking_date, amount, currency) "
            "VALUES ('BOOK-PATCH','VEH-PATCH','ZONE_A','2026-08-22',100000,'VND') ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status) "
            "VALUES ($1,'T1','BOOK-PATCH',100000,'VND','AWAITING')",
            uuid.UUID(workflow_id),
        )
    editable = await load_editable_plan(workflow_id)
    assert editable.has_open_approval is True
    decision = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "UNDER_REVIEW"


def test_viewing_approvals_share_the_service_queue_table():
    """`viewing_approvals` là VIEW trên `service_approvals`.

    Nên đọc bảng gốc là đủ; đọc thêm view chỉ đếm hai lần. Test này giữ giả
    định đó — nếu ai đó tách lịch tham quan ra bảng riêng, `_open_approvals`
    phải được sửa và test này nhắc.
    """
    from pathlib import Path

    schema = (Path(__file__).resolve().parents[2] / "src" / "db" / "schema.sql").read_text()
    assert "CREATE OR REPLACE VIEW viewing_approvals" in schema or "CREATE VIEW viewing_approvals" in schema


# --- Allowlist dương theo tool ----------------------------------------------


@pytest.mark.asyncio
async def test_a_field_in_the_plan_but_not_in_the_tool_allowlist_is_refused(client, db_pool):
    """Có bộ đọc KHÔNG đồng nghĩa được phép sửa.

    `consent` nằm trong kế hoạch, đọc được từ văn bản, và vẫn bị từ chối: một
    lời đồng ý phải được nói lại, không phải vá lại.
    """
    workflow_id = await _seed(
        db_pool,
        tool="register_property_interest",
        inputs={
            "project_id": "PRJ-001",
            "interest_type": "buy",
            "preferred_contact_time": "09:30",
            "consent": True,
        },
    )
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("consent", "không"), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "FIELD_NOT_PATCHABLE"


def test_the_tool_allowlist_never_names_an_authoritative_field():
    from src.common.field_parsers import AUTHORITATIVE_FIELDS
    from src.orchestration.patch import PATCHABLE_FIELDS_BY_TOOL

    for tool, fields in PATCHABLE_FIELDS_BY_TOOL.items():
        assert not (fields & AUTHORITATIVE_FIELDS), tool


def test_every_patchable_field_has_a_parser_and_belongs_to_its_tool():
    """Allowlist không được nêu một ô mà tool đó không có, hoặc không đọc được."""
    from src.common.field_parsers import FIELD_PARSERS
    from src.common.tool_contract import TOOL_CONTRACTS
    from src.orchestration.patch import PATCHABLE_FIELDS_BY_TOOL

    for tool, fields in PATCHABLE_FIELDS_BY_TOOL.items():
        assert tool in TOOL_CONTRACTS, tool
        assert fields <= set(TOOL_CONTRACTS[tool].inputs), (tool, sorted(fields))
        assert fields <= set(FIELD_PARSERS), (tool, sorted(fields - set(FIELD_PARSERS)))


# --- Ô trùng tên giữa hai task ----------------------------------------------


@pytest.mark.asyncio
async def test_the_same_field_in_two_tasks_is_ambiguous_not_first_wins(client, db_pool):
    """`project_id` có ở cả `schedule_property_viewing` lẫn
    `register_property_interest`.

    Chọn hộ occurrence đầu tiên là sửa một việc người dùng không nhắc tới — và
    làm việc đó trong im lặng.
    """
    wid = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, password_hash) VALUES ($1,$2,'x') ON CONFLICT DO NOTHING",
            OWNER,
            f"nguoi-{OWNER.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
            "VALUES ($1,'xem nhà và đăng ký quan tâm','CANCELLED',$2)",
            wid,
            OWNER,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) VALUES "
            "($1,'T1','schedule_property_viewing','PENDING',$2::jsonb), "
            "($1,'T2','register_property_interest','PENDING',$3::jsonb)",
            wid,
            json.dumps({"project_id": "PRJ-001", "viewing_date": _future(1), "viewing_time": "09:30"}),
            json.dumps(
                {"project_id": "PRJ-001", "interest_type": "buy", "preferred_contact_time": "09:30", "consent": True}
            ),
        )
    editable = await load_editable_plan(str(wid))
    assert len(editable.sites["project_id"]) == 2

    decision = await validate_patch(
        _modify("project_id", "Vinhomes Green Paradise"), editable, requester_user_id=str(OWNER)
    )
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "AMBIGUOUS_FIELD"
    assert decision.needs_clarification is True

    # Ô chỉ có ở MỘT task trong cùng kế hoạch ấy vẫn vá được bình thường.
    ok = await validate_patch(_modify("viewing_date", _future(30)), editable, requester_user_id=str(OWNER))
    assert ok.outcome is PatchOutcome.PATCH_ACCEPTED
    assert ok.targets == {"viewing_date": "T1"}


# --- Khoá lạc quan -----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_version_changes_when_anything_the_decision_relied_on_changes(client, db_pool):
    """Thiếu một phần trong vân tay là có một cách để thế giới đổi mà vân tay
    không đổi — và bản vá được ghi lên một kế hoạch khác kế hoạch đã thẩm định.
    """
    workflow_id = await _seed(db_pool)
    wid = uuid.UUID(workflow_id)
    base = (await load_editable_plan(workflow_id)).plan_version

    async def version_after(sql: str, *args) -> str:
        await db_pool.execute(sql, *args)
        return (await load_editable_plan(workflow_id)).plan_version

    changed = {
        "status": await version_after("UPDATE workflow_tasks SET status='RUNNING' WHERE workflow_id=$1", wid),
        "input": await version_after(
            'UPDATE workflow_tasks SET input_data = input_data || \'{"viewing_time":"10:00"}\'::jsonb '
            "WHERE workflow_id=$1",
            wid,
        ),
        "depends_on": await version_after(
            "UPDATE workflow_tasks SET depends_on = '[\"T0\"]'::jsonb WHERE workflow_id=$1", wid
        ),
        "tool": await version_after("UPDATE workflow_tasks SET tool='book_parking' WHERE workflow_id=$1", wid),
        "approval": await version_after(
            "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status) "
            "VALUES ($1,'T1','book_parking','Giữ chỗ đỗ xe','{}'::jsonb,'AWAITING')",
            wid,
        ),
    }
    seen = [base, *changed.values()]
    assert len(set(seen)) == len(seen), f"vân tay không đổi ở: {changed}"


def test_the_version_is_documented_as_detection_not_a_lock():
    """Vân tay phát hiện thay đổi; tự nó KHÔNG chống race.

    Giữa lúc tính và lúc so lại không có khoá nào. Phase 2 vẫn phải
    `SELECT ... FOR UPDATE`, đọc lại trong cùng transaction, so vân tay, rồi
    mới ghi. Test này giữ lời hứa đó nằm trong mã, không chỉ trong đầu ai đó.
    """
    from src.orchestration import patch

    doc = patch.__doc__ or ""
    assert "FOR UPDATE" in doc
    assert "KHÔNG chống race" in doc


# --- Kế hoạch ứng viên phải qua Validator ------------------------------------


@pytest.mark.asyncio
async def test_a_time_outside_the_tool_window_is_refused(client, db_pool):
    """`schedule_property_viewing` đóng cửa 17:30. Bộ đọc theo ô đã biết luật đó."""
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(_modify("viewing_time", "18:30"), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "UNPARSABLE"


@pytest.mark.asyncio
async def test_a_field_valid_on_its_own_can_still_break_the_whole_plan(client, db_pool):
    """Từng ô hợp lệ KHÔNG có nghĩa kế hoạch hợp lệ.

    `description` là ô văn bản tự do: bộ đọc chỉ đòi không rỗng và có trần độ
    dài, nên một URL đi qua nó trọn vẹn. Luật cấm URL/credential nằm ở
    `TaskPlanValidator._reject_sensitive_content` và nó soi CẢ kế hoạch — không
    bộ đọc theo ô nào nhìn thấy được.

    Đây là lý do bản vá phải được thẩm định như một KẾ HOẠCH, không phải như
    một tập giá trị rời rạc: một chuỗi vô hại ở mức ô vẫn có thể là thứ đi
    thẳng xuống provider.
    """
    workflow_id = await _seed(
        db_pool,
        tool="create_maintenance_request",
        inputs={
            "issue_type": "plumbing",
            "description": "vòi nước rỉ",
            "location": "tầng 3",
            "preferred_date": _future(1),
            "preferred_time": "09:30",
        },
    )
    editable = await load_editable_plan(workflow_id)

    from src.common.field_parsers import parse_field

    said = "vòi nước rỉ, ảnh ở http://example.com/anh.jpg"
    assert parse_field("description", said) == said, "ở mức ô thì không có gì sai"

    decision = await validate_patch(_modify("description", said), editable, requester_user_id=str(OWNER))
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "CANDIDATE_PLAN_INVALID"


@pytest.mark.asyncio
async def test_the_candidate_plan_is_never_written_down(client, db_pool):
    """Kế hoạch ứng viên chỉ tồn tại trong lời gọi. Phase 1 không ghi gì."""
    workflow_id = await _seed(db_pool)
    wid = uuid.UUID(workflow_id)
    before = [
        dict(r)
        for r in await db_pool.fetch(
            "SELECT task_id, tool, status, input_data, depends_on FROM workflow_tasks WHERE workflow_id=$1", wid
        )
    ]
    wf_before = dict(await db_pool.fetchrow("SELECT status, task_plan FROM workflows WHERE workflow_id=$1", wid))

    editable = await load_editable_plan(workflow_id)
    for said in (_future(30), "18:30", "hôm nào đó"):
        await validate_patch(_modify("viewing_time", said), editable, requester_user_id=str(OWNER))
        await validate_patch(_modify("viewing_date", said), editable, requester_user_id=str(OWNER))

    after = [
        dict(r)
        for r in await db_pool.fetch(
            "SELECT task_id, tool, status, input_data, depends_on FROM workflow_tasks WHERE workflow_id=$1", wid
        )
    ]
    wf_after = dict(await db_pool.fetchrow("SELECT status, task_plan FROM workflows WHERE workflow_id=$1", wid))
    assert after == before
    assert wf_after == wf_before
    assert await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id=$1", wid) == 0


def _identifiers_and_imports(path) -> set[str]:
    """Mọi TÊN thật sự dùng trong mã: định danh + module import.

    Đọc bằng `ast`, không bằng tìm chuỗi. Ghi chú và docstring nhắc tên một hàm
    ("xem `rerun_with_answers`") là tài liệu, không phải một lời gọi — một hàng
    rào không phân biệt được hai thứ đó sẽ hoặc báo động giả, hoặc bị nới cho
    đến khi vô dụng.
    """
    import ast

    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            names.update((node.module or "").split("."))
            names.update(alias.name for alias in node.names)
    return names


def test_phase_one_has_no_path_to_execution():
    """Thẩm định là ĐỌC. Kiểm bằng chính mã nguồn, không bằng lời hứa.

    Một lời gọi Executor/Connector/provider hay một lệnh mở lại task lọt vào
    đây sẽ biến "được phép sửa" thành "đã sửa" — mà hai thứ đó tách nhau chính
    là điểm của Phase 1.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "src" / "orchestration" / "patch.py"
    used = _identifiers_and_imports(path)
    for forbidden in (
        "Executor",
        "build_connectors",
        "rerun_with_answers",
        "amend_and_rerun",
        "reopen_cancelled_tasks",
        "reopen_cancelled_workflow",
        "update_task_status",
        "update_workflow_status",
        "update_workflow_task_plan",
        "save_pending_service_approvals",
        "save_pending_viewing_approval",
        "httpx",
    ):
        assert forbidden not in used, forbidden

    # Chỉ đọc. `conn.execute` là API asyncpg cho lệnh KHÔNG trả hàng — tức lệnh
    # ghi. Module này chỉ được dùng `conn.fetch`.
    assert "execute" not in used

    source = path.read_text()
    for verb in ("INSERT INTO", "DELETE FROM", "UPDATE workflow", "UPDATE service", "UPDATE payment"):
        assert verb not in source, verb


# --- scope_change do model tự khai: KHÔNG cấp quyền --------------------------


@pytest.mark.asyncio
async def test_a_model_claim_of_scope_change_never_reaches_the_planner(client, db_pool):
    """`scope_change` là model TỰ NÓI. Nó không được là cổng vào Planner.

    Trước khi sửa, `scope_change=true` một mình đủ để trả `REPLAN_REQUIRED` —
    nghĩa là một model dao động, hoặc một câu người dùng gõ nhằm điều khiển
    model, kích hoạt được một lượt lập kế hoạch mới chỉ bằng một boolean. Đó
    đúng là thứ tầng này sinh ra để chặn.

    Không có bằng chứng deterministic thì hỏi lại người dùng.
    """
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(
        IntentProposal(intent=Intent.MODIFY_EXISTING, changes=[], scope_change=True, confidence=1.0),
        editable,
        requester_user_id=str(OWNER),
    )
    assert decision.outcome is not PatchOutcome.REPLAN_REQUIRED
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "UNVERIFIED_SCOPE_CHANGE"
    assert decision.needs_clarification is True


@pytest.mark.asyncio
async def test_an_invented_field_plus_a_scope_claim_still_does_not_replan(client, db_pool):
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(
        _modify("khong_ton_tai", "gì đó", scope_change=True),
        editable,
        requester_user_id=str(OWNER),
    )
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.needs_clarification is True


@pytest.mark.asyncio
async def test_full_confidence_buys_no_authority(client, db_pool):
    """Một ngưỡng tin cậy làm cổng nghĩa là model tự cấp quyền bằng cách trả 1.0."""
    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(
        _modify("viewing_date", _future(30), scope_change=True, confidence=1.0),
        editable,
        requester_user_id=str(OWNER),
    )
    assert decision.outcome is PatchOutcome.PATCH_REJECTED
    assert decision.reason_code == "UNVERIFIED_SCOPE_CHANGE"


@pytest.mark.asyncio
async def test_a_reason_code_is_not_evidence_either(client, db_pool):
    """`reason_code` cũng do model viết. Nó là nhãn để quan sát, không phải cớ."""
    from src.agents.intent_resolver import ReasonCode

    workflow_id = await _seed(db_pool)
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(
        IntentProposal(
            intent=Intent.MODIFY_EXISTING,
            changes=[ProposedChange(field="viewing_date", value=_future(30))],
            scope_change=False,
            reason_code=ReasonCode.CAPABILITY_ADDED,
            confidence=1.0,
        ),
        editable,
        requester_user_id=str(OWNER),
    )
    # Không có thay đổi deterministic nào đụng hình dạng → vá bình thường.
    assert decision.outcome is PatchOutcome.PATCH_ACCEPTED


@pytest.mark.asyncio
async def test_only_a_shape_field_the_code_recognises_reaches_the_planner(client, db_pool):
    """Bằng chứng deterministic DUY NHẤT ở Phase 1: allowlist `SHAPE_FIELDS`."""
    workflow_id = await _seed(
        db_pool,
        inputs={"project_id": "PRJ-001", "viewing_date": _future(1), "wants_shuttle": True},
    )
    editable = await load_editable_plan(workflow_id)
    decision = await validate_patch(
        _modify("wants_shuttle", "không", scope_change=False), editable, requester_user_id=str(OWNER)
    )
    assert decision.outcome is PatchOutcome.REPLAN_REQUIRED
    assert decision.reason_code == "SHAPE_FIELD"


# --- search_properties không còn nằm trong tầm với của Agent -----------------


def test_the_planner_cannot_produce_a_property_search():
    """Quyết định sản phẩm: tìm kiếm/listing là chức năng marketplace.

    Ràng buộc phải nằm ở CODE, không ở prompt — đã quan sát trên model thật:
    nó tự thêm một tool prompt đã dặn không dùng.
    """
    from src.agents.planner import PLANNER_ALLOWED_TOOLS, PLANNER_FORBIDDEN_TOOLS

    assert "search_properties" in PLANNER_FORBIDDEN_TOOLS
    assert "search_properties" not in PLANNER_ALLOWED_TOOLS


def test_a_property_search_cannot_be_patched_either():
    from src.orchestration.patch import PATCHABLE_FIELDS_BY_TOOL

    assert "search_properties" not in PATCHABLE_FIELDS_BY_TOOL
    patchable = {f for fields in PATCHABLE_FIELDS_BY_TOOL.values() for f in fields}
    assert not (patchable & {"transaction_type", "property_type", "max_price"})


def test_the_agent_never_asks_for_a_legacy_only_field():
    """Ô chỉ thuộc tool không-với-tới-được thì Agent không được hỏi, không được
    vá, và không cần bộ đọc — kể cả khi nó vẫn còn trong `TOOL_CONTRACTS`.
    """
    from src.common.field_parsers import FIELD_PARSERS, LEGACY_ONLY_FIELDS

    assert {"transaction_type", "property_type", "max_price"} <= LEGACY_ONLY_FIELDS
    assert not (LEGACY_ONLY_FIELDS & set(FIELD_PARSERS))
