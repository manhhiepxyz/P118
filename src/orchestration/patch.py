"""Patch Validator — quyết định thay đổi nào ĐƯỢC PHÉP áp dụng.

Owner: Thành Bảo (Decision layer)
File: src/orchestration/patch.py

Đây là nửa "hậu quả" của nguyên tắc: không ràng buộc ngôn ngữ người dùng, ràng
buộc hậu quả mà ngôn ngữ đó có thể gây ra. Nửa "ngôn ngữ" là
`src/agents/intent_resolver.py`, và tầng này KHÔNG tin gì ở đó:

  - Model có thể dao động giữa hai lượt cho cùng một câu (đo được trong chính
    dự án này — xem `rerun_with_answers`). Tầng này thì không: cùng một đề xuất,
    cùng một trạng thái database → cùng một quyết định.
  - Model nói `scope_change=false` cũng không cứu được một ô quyết định HÌNH
    DẠNG kế hoạch. Luật ở đây thắng.
  - Model bịa tên ô thì ô ấy đi tới CLARIFICATION, không tới Planner.

Phạm vi Phase 1 — cố ý hẹp
--------------------------
Tầng này chỉ ĐỌC và KẾT LUẬN. Không ghi database, không gọi Executor, không gọi
Connector, không gọi provider, không mở lại task. `PATCH_ACCEPTED` nghĩa là
"được phép", không phải "đã làm".

Bằng chứng "provider đã nhận request"
-------------------------------------
`service_approvals` và `payment_approvals` là hàng đợi QUYẾT ĐỊNH nội bộ.
`workflows.status` là vòng đời workflow. Không cái nào là bản ghi của một lời
gọi ra ngoài, nên không cái nào được dùng làm bằng chứng provider đã nhận
request.

Hệ thống hiện CHƯA có `external_request_id`/`provider_submission_status` ở bất
kỳ bảng nào. Vì vậy `EditablePlan.provider_submission_known` luôn `False`, và
Consequence Analysis (Phase 2) phải FAIL-CLOSED trên đúng cờ đó. Hàng đợi vẫn
được đọc — với đúng nghĩa của nó: `UNDER_REVIEW`, "có người đang phải quyết
định, đừng sửa dưới tay họ".

Khoá lạc quan — và giới hạn của nó
----------------------------------
`plan_version` là VÂN TAY của trạng thái đọc được tại thời điểm thẩm định. Nó
phát hiện được "có thứ gì đó đã đổi giữa lúc validate và lúc persist", nhưng
**tự nó KHÔNG chống race**: giữa lúc tính vân tay và lúc so lại, không có khoá
nào cả. Phase 2 vẫn bắt buộc `SELECT ... FOR UPDATE`, đọc lại
owner/status/approval/version trong cùng transaction, so vân tay, rồi mới ghi.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from src.common.enums import TaskStatus, WorkflowStatus
from src.common.field_parsers import FIELD_PARSERS, parse_field
from src.common.plan_fingerprint import plan_version_of
from src.orchestration.runtime_provider import acquire_repository

logger = logging.getLogger(__name__)


# Trạng thái workflow ĐƯỢC PHÉP sửa. Allowlist, fail-closed.
#
# Chỉ hai giá trị, và cả hai đều nghĩa là "đã dừng lại rồi": `CANCELLED` (người
# dùng bấm Dừng) và `FAILED` (chạy hỏng). Mọi trạng thái khác — `PENDING`,
# `RUNNING`, `WAITING_APPROVAL`, `SUCCESS` — đều có một tiến trình hoặc một
# người đang tác động lên yêu cầu.
#
# `NEEDS_INFORMATION` KHÔNG có mặt ở đây vì nó KHÔNG TỒN TẠI trong
# `WorkflowStatus` (nó là một trạng thái của tầng API, không phải của cột
# `workflows.status`). Muốn cho phép thì phải chứng minh enum ấy có thật và
# viết test riêng — không thêm dựa trên phỏng đoán.
EDITABLE_WORKFLOW_STATUSES = frozenset({WorkflowStatus.CANCELLED.value, WorkflowStatus.FAILED.value})


# Ô nào của tool nào được sửa bằng một bản vá. ALLOWLIST DƯƠNG, viết tay.
#
# Cố ý KHÔNG suy ra từ "có parser + không nằm trong blacklist". Hai luật đó trả
# lời hai câu khác nhau: `FIELD_PARSERS` nói "đọc được từ văn bản không", bảng
# này nói "được phép đổi không". Gộp chúng nghĩa là thêm một bộ đọc — một việc
# thuần kỹ thuật — sẽ tự động MỞ QUYỀN sửa một ô nghiệp vụ, trong im lặng.
#
# Vắng mặt có chủ ý:
#   - `register_resident`, `pay_fee`: danh tính và tiền. Không sửa bằng patch.
#   - `consent`: một lời đồng ý phải được nói lại, không phải vá lại.
#   - `resident_id`/`vehicle_id`/`booking_id`/`viewing_id`: con trỏ nội bộ.
#   - `wants_shuttle`: xem `SHAPE_FIELDS` — nó đổi hình dạng kế hoạch.
PATCHABLE_FIELDS_BY_TOOL: dict[str, frozenset[str]] = {
    "schedule_property_viewing": frozenset({"project_id", "viewing_date", "viewing_time"}),
    "book_parking": frozenset({"booking_date", "parking_zone"}),
    "register_vehicle": frozenset({"plate_number", "vehicle_type"}),
    "book_shuttle": frozenset({"tour_date", "passenger_count"}),
    "create_maintenance_request": frozenset(
        {"issue_type", "description", "location", "preferred_date", "preferred_time"}
    ),
    "schedule_move": frozenset({"move_date", "move_time", "needs_elevator", "needs_loading_support", "move_vehicle"}),
    "register_property_interest": frozenset({"project_id", "interest_type", "preferred_contact_time"}),
    "pay_fee": frozenset(),
}

# `search_properties` và `register_resident` KHÔNG có mặt, và không phải vì
# quên: cả hai nằm ngoài `PLANNER_ALLOWED_TOOLS`, nên chúng không bao giờ xuất
# hiện trong một kế hoạch của Agent. Liệt kê chúng ở đây là mở sẵn một cửa cho
# căn phòng không tồn tại — và cửa ấy sẽ mở thật nếu sau này có người cho
# Planner dùng lại chúng mà quên rằng bảng này đã chuẩn bị sẵn.


# Ô quyết định một task CÓ TỒN TẠI hay không.
#
# `wants_shuttle` ứng với việc plan có `book_shuttle` hay không (xem
# `wants_shuttle_in_plan`). Đổi nó không phải đổi một giá trị — nó thêm hoặc bỏ
# một bước và đổi cả đồ thị phụ thuộc. Đây là luật về HẬU QUẢ: nó đúng bất kể
# người dùng gõ câu gì và bất kể model nói gì.
SHAPE_FIELDS = frozenset({"wants_shuttle", "needs_shuttle"})

_INTERNAL_POINTERS = frozenset({"resident_id", "vehicle_id", "booking_id", "viewing_id"})


class PatchOutcome(StrEnum):
    PATCH_ACCEPTED = "PATCH_ACCEPTED"
    # Đổi hình dạng kế hoạch → Planner. KHÔNG tự thêm/xoá task.
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    # Bước đã chạy xong đã tạo cam kết thật. KHÔNG phải REPLAN_REQUIRED: một
    # lượt lập kế hoạch chung có thể dựng lại đúng tool đã chạy và đặt lần hai.
    # Việc này cần một hành động nghiệp vụ riêng (RESCHEDULE_VIEWING,
    # CHANGE_BOOKING...) — Phase 1 chỉ KẾT LUẬN, chưa triển khai.
    BUSINESS_ACTION_REQUIRED = "BUSINESS_ACTION_REQUIRED"
    # Không áp dụng được → CLARIFICATION. Không bao giờ tới Planner: rơi về
    # Planner từ một đề xuất hỏng là cách một câu mơ hồ dựng lại một việc người
    # dùng vừa dừng.
    PATCH_REJECTED = "PATCH_REJECTED"
    # Không phải việc của tầng này (NEW_GOAL, QUESTION, APPROVE...).
    NOT_A_PATCH = "NOT_A_PATCH"


@dataclass(frozen=True)
class FieldSite:
    """Một lần xuất hiện của một ô trong kế hoạch."""

    task_id: str
    tool: str
    value: Any


@dataclass(frozen=True)
class EditablePlan:
    """Ảnh chụp ĐỌC-CHỈ của một yêu cầu, đủ để kết luận mà không đoán."""

    workflow_id: str
    goal: str
    owner_user_id: str | None
    workflow_status: str
    # Ô → MỌI lần nó xuất hiện. Danh sách, không phải một giá trị: cùng một tên
    # ô có thể nằm ở hai task (ví dụ `project_id` ở cả `schedule_property_viewing`
    # lẫn `register_property_interest`), và chọn hộ cái đầu tiên là sửa một
    # việc người dùng không nhắc tới.
    sites: dict[str, list[FieldSite]]
    task_status: dict[str, str]
    task_tool: dict[str, str]
    task_depends_on: dict[str, list[str]]
    task_input: dict[str, dict[str, Any]]
    # Task đang chờ MỘT AI ĐÓ quyết định — đơn vị cung cấp hoặc chính người dùng.
    under_review: frozenset[str]
    # Workflow có bất kỳ hồ sơ duyệt nào đang chờ, kể cả không gắn với task.
    has_open_approval: bool
    plan_version: str
    source: str
    provider_submission_known: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PatchDecision:
    outcome: PatchOutcome
    workflow_id: str
    plan_version: str
    accepted: dict[str, Any] = field(default_factory=dict)
    # Task mà mỗi ô được chấp nhận thuộc về. Phase 2 cần nó để vá đúng chỗ.
    targets: dict[str, str] = field(default_factory=dict)
    reason_code: str | None = None
    needs_clarification: bool = False


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [str(item) for item in raw] if isinstance(raw, list) else []


async def _open_approvals(pool, workflow_id: str) -> list[tuple[str, str, str]]:
    """Mọi hồ sơ duyệt của workflow: `(nguồn, task_id, status)`.

    Đọc CẢ HAI hàng đợi. `service_approvals` mang cả lịch tham quan —
    `viewing_approvals` là VIEW trên chính bảng đó. `payment_approvals` là bảng
    RIÊNG, và thiếu nó thì một yêu cầu đang chờ chính người dùng duyệt tiền vẫn
    vá được.
    """
    rows: list[tuple[str, str, str]] = []
    async with pool.acquire() as conn:
        for source, table in (("service", "service_approvals"), ("payment", "payment_approvals")):
            found = await conn.fetch(
                f"SELECT task_id, status FROM {table} WHERE workflow_id = $1",  # noqa: S608 - tên bảng là hằng số
                UUID(workflow_id),
            )
            rows.extend((source, str(r["task_id"]), str(r["status"])) for r in found)
    return rows


async def load_editable_plan(workflow_id: str) -> EditablePlan | None:
    """Ảnh chụp đọc-chỉ của yêu cầu. `None` nếu không đọc được. KHÔNG ghi gì.

    Nguồn đọc, sau khi trace hết call site:

      - `workflows.task_plan` là KẾ HOẠCH BAN ĐẦU đã qua Validator.
        `persist_full_plan()` ghi plan ĐẦY ĐỦ trước khi boundary cắt prefix, và
        `create_workflow()` dùng `COALESCE(NULLIF(...,'null'), EXCLUDED)` để giữ
        bản đầu tiên — nên nó KHÔNG bị prefix ghi đè. Nhưng nó có thể CŨ: một
        lần repair hoặc amend đổi `workflow_tasks.input_data` mà không đụng cột
        này.
      - `workflow_tasks` là HÌNH CHIẾU VẬN HÀNH hiện tại: `input_data` được
        update cho task chưa terminal. Nó là nơi duy nhất phản ánh giá trị đang
        thật sự sẽ được gửi đi — nên đọc nó ở đây là đúng. Nó KHÔNG phải nhật
        ký append-only, nên nó cũng không phải audit trail.

    Phase 1 vì vậy đọc `workflow_tasks` và ghi rõ `source`. Muốn có lịch sử sửa
    đổi đúng nghĩa thì Phase 2 phải thêm `workflow_plan_revisions` append-only.
    """
    pool = None
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        try:
            record = await repository.get_workflow(workflow_id)
        except ValueError:
            return None
        if record is None:
            return None
        approvals = await _open_approvals(pool, workflow_id)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("không đọc được kế hoạch để thẩm định bản vá (%s)", type(exc).__name__)
        return None
    finally:
        if pool is not None:
            await pool.close()

    rows = record.get("tasks") or []
    sites: dict[str, list[FieldSite]] = {}
    task_status: dict[str, str] = {}
    task_tool: dict[str, str] = {}
    task_depends_on: dict[str, list[str]] = {}
    task_input: dict[str, dict[str, Any]] = {}
    for row in rows:
        task_id = str(row.get("task_id"))
        tool = str(row.get("tool"))
        task_status[task_id] = str(row.get("status"))
        task_tool[task_id] = tool
        task_depends_on[task_id] = _as_list(row.get("depends_on"))
        inputs = _as_dict(row.get("input_data"))
        task_input[task_id] = inputs
        for name, value in inputs.items():
            if name in _INTERNAL_POINTERS or isinstance(value, dict):
                # `dict` là `InputRef` — con trỏ tới kết quả bước trước, không
                # phải một giá trị người dùng chọn.
                continue
            sites.setdefault(name, []).append(FieldSite(task_id=task_id, tool=tool, value=value))

    open_now = [item for item in approvals if item[2] == "AWAITING"]
    workflow = record.get("workflow") or {}
    owner = workflow.get("owner_user_id")
    return EditablePlan(
        workflow_id=str(workflow.get("workflow_id") or workflow_id),
        goal=str(workflow.get("goal") or ""),
        owner_user_id=str(owner) if owner else None,
        workflow_status=str(workflow.get("status")),
        sites=sites,
        task_status=task_status,
        task_tool=task_tool,
        task_depends_on=task_depends_on,
        task_input=task_input,
        under_review=frozenset(item[1] for item in open_now),
        has_open_approval=bool(open_now),
        plan_version=plan_version_of(rows, approvals),
        source="EXECUTION_SNAPSHOT",
        # Không có `external_request_id` ở bất kỳ bảng nào. Nói thẳng là KHÔNG
        # BIẾT, thay vì suy từ hàng đợi duyệt hay từ cột trạng thái.
        provider_submission_known=False,
        notes=[
            "Phase 2 phải tạo workflow_plan_revisions append-only trước khi ghi.",
            "Phase 2 phải SELECT ... FOR UPDATE và so lại plan_version trong cùng transaction.",
        ],
    )


def _candidate_plan_is_valid(editable: EditablePlan, accepted: dict[str, Any], targets: dict[str, str]) -> bool:
    """Dựng kế hoạch ỨNG VIÊN trong bộ nhớ và cho Validator kiểm.

    Từng ô hợp lệ không có nghĩa kế hoạch hợp lệ. Validator giữ những luật
    KHÔNG nằm ở mức một ô: đủ trường bắt buộc, khung giờ theo tool, enum theo
    tool, trần thời gian, phụ thuộc. Bỏ qua nó nghĩa là một bản vá "từng ô đều
    đẹp" vẫn dựng ra một kế hoạch provider chắc chắn từ chối.

    KHÔNG ghi gì xuống database. Kế hoạch này chỉ tồn tại trong lời gọi này.
    """
    from src.agents.validator import TaskPlanValidator
    from src.common.task_plan import InputRef, Task, TaskPlan

    tasks = []
    for task_id, tool in editable.task_tool.items():
        merged = dict(editable.task_input.get(task_id, {}))
        for name, value in accepted.items():
            if targets.get(name) == task_id:
                merged[name] = value
        rebuilt: dict[str, Any] = {}
        for name, value in merged.items():
            # `InputRef` được lưu thành dict; dựng lại đúng kiểu để Validator
            # nhìn thấy một kế hoạch giống hệt kế hoạch thật.
            if isinstance(value, dict) and "from_task" in value:
                try:
                    rebuilt[name] = InputRef(**value)
                except (TypeError, ValueError):
                    return False
            else:
                rebuilt[name] = value
        try:
            tasks.append(
                Task(
                    task_id=task_id,
                    tool=tool,
                    depends_on=editable.task_depends_on.get(task_id, []),
                    input=rebuilt,
                )
            )
        except Exception:  # noqa: BLE001 - plan dựng không nổi là plan không hợp lệ
            return False
    if not tasks:
        return False
    try:
        TaskPlanValidator.validate(TaskPlan(goal=editable.goal or "sửa yêu cầu", tasks=tasks))
    except Exception:  # noqa: BLE001 - mọi vi phạm đều là "không dùng được"
        return False
    return True


async def validate_patch(
    proposal: Any,
    editable: EditablePlan | None,
    *,
    requester_user_id: str,
) -> PatchDecision:
    """Thay đổi nào được áp dụng. Không ghi gì, không chạy gì.

    Fail-closed: chặn ở điều kiện chặt nhất trước, và bất kỳ điều gì không chắc
    đều thành `PATCH_REJECTED` + `needs_clarification` — không thành một lượt
    lập kế hoạch mới.
    """
    from src.agents.intent_resolver import Intent  # cục bộ: tránh vòng import

    if editable is None:
        return PatchDecision(
            outcome=PatchOutcome.PATCH_REJECTED,
            workflow_id="",
            plan_version="",
            reason_code="NO_EDITABLE_REQUEST",
            needs_clarification=True,
        )

    def decide(
        outcome: PatchOutcome,
        *,
        reason: str | None = None,
        accepted: dict | None = None,
        targets: dict | None = None,
    ) -> PatchDecision:
        return PatchDecision(
            outcome=outcome,
            workflow_id=editable.workflow_id,
            plan_version=editable.plan_version,
            accepted=accepted or {},
            targets=targets or {},
            reason_code=reason,
            needs_clarification=outcome is PatchOutcome.PATCH_REJECTED,
        )

    # 1. QUYỀN, đọc từ PostgreSQL — không từ câu người dùng nói, không từ model,
    #    không từ body request.
    if editable.owner_user_id is None or str(requester_user_id) != editable.owner_user_id:
        return decide(PatchOutcome.PATCH_REJECTED, reason="NOT_OWNER")

    # 2. Ý định. UNKNOWN đi tới clarification; chỉ NEW_GOAL mới được lập kế
    #    hoạch mới, và việc định tuyến đó là của tầng gọi.
    intent = getattr(proposal, "intent", None)
    if intent is not Intent.MODIFY_EXISTING:
        if intent is Intent.UNKNOWN or intent is None:
            return decide(PatchOutcome.PATCH_REJECTED, reason="INTENT_UNKNOWN")
        return decide(PatchOutcome.NOT_A_PATCH, reason=str(intent))

    # 3. TRẠNG THÁI WORKFLOW. Allowlist, fail-closed.
    if editable.workflow_status not in EDITABLE_WORKFLOW_STATUSES:
        return decide(PatchOutcome.PATCH_REJECTED, reason="WORKFLOW_NOT_EDITABLE")

    # 4. Bất kỳ hồ sơ duyệt nào đang mở — dịch vụ HOẶC thanh toán. Có người
    #    đang phải quyết định; sửa dưới tay họ nghĩa là họ duyệt một đằng, hệ
    #    thống chạy một nẻo. Đây là TRẠNG THÁI hàng đợi, KHÔNG phải bằng chứng
    #    provider đã nhận request.
    if editable.has_open_approval:
        return decide(PatchOutcome.PATCH_REJECTED, reason="UNDER_REVIEW")

    changes = list(getattr(proposal, "changes", None) or [])

    # 5. HÌNH DẠNG kế hoạch — và chỉ chấp nhận BẰNG CHỨNG DETERMINISTIC.
    #
    # `scope_change` do model sinh ra. Nếu nó một mình đủ để trả
    # `REPLAN_REQUIRED` thì một model dao động — hoặc một câu người dùng gõ
    # nhằm điều khiển model — kích hoạt được một lượt lập kế hoạch mới chỉ bằng
    # một boolean. Đó đúng là thứ tầng này sinh ra để chặn, và nó vi phạm chính
    # nguyên tắc: model đề xuất, code quyết định hậu quả.
    #
    # Bằng chứng duy nhất được công nhận ở Phase 1 là allowlist ĐÓNG
    # `SHAPE_FIELDS`. (Chỗ này cũng là nơi một `action_id` do UI/backend sinh sẽ
    # cắm vào, khi có — nó là dữ liệu của HỆ THỐNG, không phải lời model nói.)
    shape_named = [
        change for change in changes if isinstance(getattr(change, "field", None), str) and change.field in SHAPE_FIELDS
    ]
    if shape_named:
        return decide(PatchOutcome.REPLAN_REQUIRED, reason="SHAPE_FIELD")

    if getattr(proposal, "scope_change", False):
        # Model tin là phạm vi đổi, code không thấy bằng chứng nào. Không vá
        # (bản vá có thể sai hướng), cũng không replan (không ai xác nhận).
        # Hỏi lại người dùng.
        return decide(PatchOutcome.PATCH_REJECTED, reason="UNVERIFIED_SCOPE_CHANGE")

    if not changes:
        return decide(PatchOutcome.PATCH_REJECTED, reason="NO_CHANGES")

    accepted: dict[str, Any] = {}
    targets: dict[str, str] = {}
    for change in changes:
        name = getattr(change, "field", None)
        said = getattr(change, "value", None)
        if not isinstance(name, str) or not isinstance(said, str):
            return decide(PatchOutcome.PATCH_REJECTED, reason="MALFORMED_CHANGE")

        occurrences = editable.sites.get(name) or []
        if not occurrences:
            # Ô không có trong kế hoạch. KHÔNG replan: để model bịa một tên ô
            # rồi kích hoạt Planner là mở lại đúng đường mà tầng này sinh ra để
            # đóng. Hỏi lại người dùng.
            return decide(PatchOutcome.PATCH_REJECTED, reason="UNKNOWN_FIELD")
        if len(occurrences) > 1:
            # Cùng một tên ô ở nhiều task. Chọn hộ cái đầu tiên là sửa một việc
            # người dùng không nhắc tới.
            return decide(PatchOutcome.PATCH_REJECTED, reason="AMBIGUOUS_FIELD")

        site = occurrences[0]
        # ALLOWLIST DƯƠNG theo tool. Có bộ đọc KHÔNG đồng nghĩa được phép sửa.
        if name not in PATCHABLE_FIELDS_BY_TOOL.get(site.tool, frozenset()):
            return decide(PatchOutcome.PATCH_REJECTED, reason="FIELD_NOT_PATCHABLE")
        if name not in FIELD_PARSERS:
            return decide(PatchOutcome.PATCH_REJECTED, reason="NO_PARSER")

        value = parse_field(name, said)
        if value is None:
            return decide(PatchOutcome.PATCH_REJECTED, reason="UNPARSABLE")

        status = editable.task_status.get(site.task_id, "")
        if status == TaskStatus.SUCCESS.value:
            # Bước đã xong đã tạo cam kết thật ở phía đơn vị cung cấp. KHÔNG
            # đưa vào Planner chung: một lượt lập kế hoạch có thể dựng lại đúng
            # tool ấy và đặt lần hai.
            return decide(PatchOutcome.BUSINESS_ACTION_REQUIRED, reason="TASK_COMPLETED")
        if status == TaskStatus.RUNNING.value:
            return decide(PatchOutcome.PATCH_REJECTED, reason="TASK_RUNNING")
        if site.task_id in editable.under_review:
            return decide(PatchOutcome.PATCH_REJECTED, reason="UNDER_REVIEW")

        if value == site.value:
            continue  # không đổi gì thì không phải một bản vá
        accepted[name] = value
        targets[name] = site.task_id

    if not accepted:
        return decide(PatchOutcome.PATCH_REJECTED, reason="NO_EFFECTIVE_CHANGE")

    # 5. Kế hoạch ỨNG VIÊN phải qua Validator ĐẦY ĐỦ. Từng ô hợp lệ không có
    #    nghĩa kế hoạch hợp lệ.
    if not _candidate_plan_is_valid(editable, accepted, targets):
        return decide(PatchOutcome.PATCH_REJECTED, reason="CANDIDATE_PLAN_INVALID")

    return decide(PatchOutcome.PATCH_ACCEPTED, accepted=accepted, targets=targets)
