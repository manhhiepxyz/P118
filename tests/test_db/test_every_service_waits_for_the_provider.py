"""Mọi dịch vụ đều phải được ĐƠN VỊ CUNG CẤP duyệt trước khi chạy.

Trước đây chỉ `schedule_property_viewing` có cổng ấy. Sáu dịch vụ còn lại —
đăng ký xe, chỗ đỗ, bảo trì, chuyển nhà, xe đưa đón, đăng ký tư vấn — chạy
thẳng tới provider, và khách nhận kết quả trước khi có ai bên kia đồng ý.

Kiểm bằng đường TẤT ĐỊNH: gọi thẳng boundary với một kế hoạch dựng sẵn. Đi qua
Planner thì kết quả phụ thuộc mô hình đoán đúng, và một cổng an toàn không nên
được kiểm bằng thứ không lặp lại được.
"""

from __future__ import annotations

import inspect
import uuid

import pytest

from src.common.enums import TaskStatus
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration.service_approval import (
    PROVIDER_TOOLS,
    SERVICE_LABELS,
    ServiceApprovalBoundary,
    ServiceApprovalRequiredError,
    pending_for_workflow,
    record_service_decision,
)


class _Runtime:
    """Tầng thực thi giả — ghi lại nó ĐƯỢC PHÉP chạy những bước nào."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    async def execute(self, plan, workflow_id=None, **_kw):
        self.ran.extend(task.task_id for task in plan.tasks)
        return workflow_id, {t.task_id: StandardResult.ok({}) for t in plan.tasks}


def _plan(tool: str) -> TaskPlan:
    return TaskPlan(
        goal="kiểm cổng",
        tasks=[
            Task(task_id="T1", tool="search_properties", depends_on=[], input={"residential_area": "Ocean Park"}),
            Task(task_id="T2", tool=tool, depends_on=[], input={"note": "x", "resident_id": "RES-001"}),
        ],
    )


async def _seed_workflow(pool) -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'kiểm cổng','RUNNING')", wid
        )
        for task_id, tool in (("T1", "search_properties"), ("T2", "book_parking")):
            await conn.execute(
                "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status) VALUES ($1,$2,$3,'PENDING')",
                wid, task_id, tool,
            )
    return str(wid)


@pytest.mark.parametrize("tool", sorted(PROVIDER_TOOLS))
def test_every_provider_service_has_a_human_readable_label(tool: str) -> None:
    """Đơn vị nhìn hàng đợi, không nhìn tên tool."""
    assert SERVICE_LABELS.get(tool), f"{tool} không có tên dịch vụ cho người duyệt"


def test_the_gate_covers_every_service_that_commits_a_provider() -> None:
    """Danh sách phải ĐỦ. Thiếu một tool là một dịch vụ chạy thẳng."""
    from src.api.routes import _TOOL_PRESENTATION

    # Ba tool KHÔNG qua cổng này, mỗi cái một lý do khác nhau.
    khong_can = {
        "search_properties",            # chỉ đọc, không tạo cam kết
        "register_resident",            # định danh của chính người dùng
        "pay_fee",                      # tiền là quyết định của NGƯỜI DÙNG
        "schedule_property_viewing",    # đã có cổng riêng đang chạy
    }
    thieu = set(_TOOL_PRESENTATION) - khong_can - set(PROVIDER_TOOLS)
    assert not thieu, f"dịch vụ chạy thẳng, không ai duyệt: {sorted(thieu)}"


@pytest.mark.asyncio
async def test_a_gated_service_does_not_run_before_approval(db_pool) -> None:
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed_workflow(db_pool)
    runtime = _Runtime()
    boundary = ServiceApprovalBoundary(runtime, approved=False, repository=repository)

    with pytest.raises(ServiceApprovalRequiredError):
        await boundary.execute(_plan("book_parking"), workflow_id)

    assert "T2" not in runtime.ran, "bước cần duyệt ĐÃ CHẠY trước khi có ai đồng ý"
    assert "T1" in runtime.ran, "bước không cần duyệt bị chặn oan"

    rows = {row["task_id"]: row for row in await pending_for_workflow(db_pool, workflow_id)}
    assert rows["T2"]["status"] == "AWAITING"
    assert rows["T2"]["service_label"] == SERVICE_LABELS["book_parking"]
    assert "resident_id" not in rows["T2"]["details"], "lộ định danh nội bộ cho người duyệt"

    tasks = {row["task_id"]: row["status"] for row in await repository.list_tasks(workflow_id)}
    assert tasks["T2"] == TaskStatus.WAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_it_runs_once_the_provider_has_approved(db_pool) -> None:
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed_workflow(db_pool)
    runtime = _Runtime()

    with pytest.raises(ServiceApprovalRequiredError):
        await ServiceApprovalBoundary(runtime, approved=False, repository=repository).execute(
            _plan("book_parking"), workflow_id
        )

    assert await record_service_decision(
        db_pool, workflow_id, "T2", "APPROVED", decided_by="provider"
    ) is True

    after = _Runtime()
    await ServiceApprovalBoundary(after, approved=True, repository=repository).execute(
        _plan("book_parking"), workflow_id
    )
    assert "T2" in after.ran, "đã duyệt rồi mà bước vẫn không chạy"


@pytest.mark.asyncio
async def test_a_decision_is_recorded_with_who_made_it(db_pool) -> None:
    """Không ghi ai duyệt thì lúc có sự cố chỉ còn cách suy luận."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed_workflow(db_pool)
    with pytest.raises(ServiceApprovalRequiredError):
        await ServiceApprovalBoundary(_Runtime(), approved=False, repository=repository).execute(
            _plan("book_parking"), workflow_id
        )

    await record_service_decision(db_pool, workflow_id, "T2", "APPROVED", decided_by="don_vi_A")
    async with db_pool.acquire() as conn:
        who = await conn.fetchval(
            "SELECT decided_by FROM service_approvals WHERE workflow_id=$1 AND task_id='T2'",
            uuid.UUID(workflow_id),
        )
    assert who == "don_vi_A"


@pytest.mark.asyncio
async def test_a_second_decision_is_refused(db_pool) -> None:
    """`WHERE status='AWAITING'` là khoá chống hai lệnh duyệt đồng thời."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed_workflow(db_pool)
    with pytest.raises(ServiceApprovalRequiredError):
        await ServiceApprovalBoundary(_Runtime(), approved=False, repository=repository).execute(
            _plan("book_parking"), workflow_id
        )

    assert await record_service_decision(db_pool, workflow_id, "T2", "APPROVED", decided_by="a") is True
    assert await record_service_decision(db_pool, workflow_id, "T2", "REJECTED", decided_by="b") is False

    async with db_pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM service_approvals WHERE workflow_id=$1 AND task_id='T2'",
            uuid.UUID(workflow_id),
        )
    assert status == "APPROVED", "quyết định thứ hai ghi đè quyết định thứ nhất"


@pytest.mark.asyncio
async def test_there_is_only_one_approval_queue(db_pool) -> None:
    """MỘT bảng vật lý. Hai bảng là hai chỗ để lệch nhau.

    `viewing_approvals` giờ là KHUNG NHÌN trên `service_approvals`, nên 108 chỗ
    đọc trong mã nguồn không phải sửa dòng nào, mà người duyệt vẫn chỉ nhìn một
    hàng đợi.
    """
    async with db_pool.acquire() as conn:
        kind = await conn.fetchval(
            "SELECT table_type FROM information_schema.tables WHERE table_name='viewing_approvals'"
        )
        base = await conn.fetchval(
            "SELECT table_type FROM information_schema.tables WHERE table_name='service_approvals'"
        )
    assert kind == "VIEW", f"viewing_approvals vẫn là {kind} — hai hàng đợi song song"
    assert base == "BASE TABLE"


@pytest.mark.asyncio
async def test_the_old_view_is_still_readable_and_writable(db_pool) -> None:
    """Gộp không được làm hỏng đường cũ.

    PostgreSQL từ chối `INSERT` vào view có cột dẫn xuất, nên khung nhìn có
    trigger `INSTEAD OF`. Đo được: thiếu nó thì 12 test đỏ ngay, tất cả vì
    chúng seed bằng `INSERT INTO viewing_approvals` — và mã cũ, script vận
    hành, test chưa viết đều có thể ghi vào đây.
    """
    workflow_id = await _seed_workflow(db_pool)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO viewing_approvals (workflow_id, task_id, project_id, viewing_date, viewing_time) "
            "VALUES ($1,'T3','PRJ-001',CURRENT_DATE + 30,'09:00')",
            uuid.UUID(workflow_id),
        )
        row = await conn.fetchrow(
            "SELECT status, project_id, viewing_time FROM viewing_approvals "
            "WHERE workflow_id=$1 AND task_id='T3'",
            uuid.UUID(workflow_id),
        )
        tool = await conn.fetchval(
            "SELECT tool FROM service_approvals WHERE workflow_id=$1 AND task_id='T3'",
            uuid.UUID(workflow_id),
        )
    assert row["status"] == "AWAITING"
    assert row["project_id"] == "PRJ-001"
    assert row["viewing_time"] == "09:00"
    assert tool == "schedule_property_viewing", "ghi qua khung nhìn không xuống bảng gộp"


@pytest.mark.asyncio
async def test_one_provider_decision_does_not_decide_another_providers_part(db_pool) -> None:
    """Bảng cũ khoá theo `workflow_id`; bảng gộp khoá theo `(workflow_id, task_id)`.

    Một yêu cầu có thể chứa cả lịch tham quan lẫn chỗ đỗ xe của hai đơn vị khác
    nhau. Các lệnh ghi của luồng tham quan vẫn dùng `WHERE workflow_id = $1`,
    nên thiếu giới hạn theo `tool` thì đơn vị tour bấm duyệt là duyệt luôn phần
    của đơn vị kia.
    """
    from src.orchestration.viewing_approval import record_viewing_decision

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed_workflow(db_pool)

    # Chỗ đỗ xe chờ đơn vị A.
    with pytest.raises(ServiceApprovalRequiredError):
        await ServiceApprovalBoundary(_Runtime(), approved=False, repository=repository).execute(
            _plan("book_parking"), workflow_id
        )
    # Lịch tham quan chờ đơn vị B.
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO viewing_approvals (workflow_id, task_id, project_id, viewing_date, viewing_time) "
            "VALUES ($1,'T5','PRJ-001',CURRENT_DATE + 30,'09:00')",
            uuid.UUID(workflow_id),
        )

    await record_viewing_decision(db_pool, workflow_id, "APPROVED", "don_vi_tour")

    rows = {r["task_id"]: r["status"] for r in await pending_for_workflow(db_pool, workflow_id)}
    assert rows["T5"] == "APPROVED", "quyết định của đơn vị tour không được ghi"
    assert rows["T2"] == "AWAITING", (
        "đơn vị tour duyệt lịch mà chỗ đỗ xe cũng thành APPROVED — một đơn vị "
        "vừa quyết định thay cho đơn vị khác"
    )


def test_the_review_page_shows_one_queue_for_every_service() -> None:
    """Người duyệt nhìn MỘT chỗ.

    Trước đây tab "Tham quan" đọc riêng một hàng đợi, còn sáu dịch vụ kia không
    có tab nào. Hai danh sách nghĩa là bắt người duyệt nhớ phải nhìn hai chỗ,
    và chỗ họ quên là chỗ khách chờ mãi.
    """
    from pathlib import Path

    page = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "ProviderReviewPage.tsx"
    source = page.read_text(encoding="utf-8")

    assert "listServiceApprovals(" in source, "trang duyệt không đọc hàng đợi gộp"
    assert "label: 'Dịch vụ'" in source, "tab vẫn chỉ nói về tham quan"
    assert "listViewingApprovals" not in source, "còn đọc hàng đợi cũ — hai danh sách song song"

    # Quyết định định tuyến theo LOẠI: tham quan có đường chạy tiếp riêng
    # (materialize qua Tour provider rồi đặt xe đưa đón). Gộp hàng đợi không
    # có nghĩa là gộp cách chạy tiếp.
    assert "record.tool === 'schedule_property_viewing'" in source, (
        "mọi dịch vụ đi chung một đường chạy tiếp — lịch tham quan sẽ mất bước "
        "materialize và xe đưa đón"
    )
    assert "decideServiceApproval(" in source


def test_the_review_page_does_not_hardcode_one_service_shape() -> None:
    """Thêm một dịch vụ mới không được kéo theo một lần sửa màn duyệt."""
    from pathlib import Path

    page = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "ProviderReviewPage.tsx"
    source = page.read_text(encoding="utf-8")
    assert "Object.entries(record.details)" in source, (
        "dữ kiện vẽ cứng theo tham quan; dịch vụ khác sẽ hiện thiếu"
    )
    assert "DETAIL_LABELS" in source, "hiện khoá thô — đó là từ vựng nội bộ"


def test_the_queue_is_split_by_service_type() -> None:
    """Một danh sách trộn bảy loại là bắt đơn vị cuộn tìm phần của mình.

    Đo được trước khi tách: 24 lịch tham quan và 1 yêu cầu bảo trì nằm chung
    một cột — đơn vị bảo trì phải cuộn qua 24 dòng không thuộc về họ.
    """
    from pathlib import Path

    page = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "ProviderReviewPage.tsx"
    source = page.read_text(encoding="utf-8")

    assert "SERVICE_TAB_LABELS" in source, "tab con không có nhãn tiếng Việt"
    assert 'role="tab"' in source, "không có thanh tab con cho từng loại dịch vụ"
    assert "SERVICE_ORDER" in source, (
        "thứ tự tab theo dữ liệu về — tab nhảy chỗ giữa hai lần tải, và người "
        "duyệt phải tìm lại mỗi lần"
    )
    assert "({count})" in source, "tab không hiện số việc; không thấy chỗ nào đang dồn"


def test_a_decided_item_leaves_the_list_immediately() -> None:
    """Bấm Duyệt mà con số không đổi thì người duyệt kết luận nút không ăn.

    Danh sách giới hạn 50 mục. Hàng đợi dài hơn thế thì lượt tải lại trả về
    đúng 50 như cũ. Đo được: quyết định ĐÃ ghi (`APPROVED` kèm `decided_by`)
    trong khi màn hình vẫn 50/50 — nút hoạt động, màn hình nói ngược lại.
    """
    from pathlib import Path

    page = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "ProviderReviewPage.tsx"
    source = page.read_text(encoding="utf-8")
    body = source[source.index("async function decideService(") :]
    body = body[: body.index("\n  /**", 1)]
    assert "setServiceItems((current) =>" in body, (
        "mục vừa quyết định không bị bỏ khỏi danh sách ngay — người duyệt không "
        "thấy thao tác của mình có tác dụng"
    )
    assert "item.task_id === record.task_id" in body, (
        "lọc theo mình workflow_id — một yêu cầu có nhiều bước, và bỏ nhầm bước "
        "của đơn vị khác"
    )


def test_a_decided_item_moves_to_history_not_limbo() -> None:
    """Duyệt xong thì rời hàng đợi, nhưng phải xem lại được.

    Hàng đợi chỉ có `AWAITING`, nên mục đã quyết định biến mất đúng như mong
    đợi. Vấn đề là nó biến mất HẲN: không có chỗ nào tra lại ai đã duyệt, lúc
    nào, và vì sao từ chối.
    """
    from src.orchestration import service_approval

    assert hasattr(service_approval, "list_by_status"), "không có đường đọc lịch sử"
    source = inspect.getsource(service_approval.list_by_status)
    assert "decided_by" in source and "decided_at" in source, (
        "lịch sử không mang người quyết định và thời điểm — tra lại thành suy luận"
    )
    assert "reject_reason" in source, "không xem lại được lý do từ chối"


def test_the_two_lists_are_ordered_for_their_own_purpose() -> None:
    """Thứ tự không phải chuyện thẩm mỹ.

    đang chờ → cũ nhất trước, để người chờ lâu nhất được phục vụ trước
    đã quyết → mới nhất trước, vì cái vừa làm là cái người ta muốn xem lại
    """
    from src.orchestration import service_approval

    awaiting = inspect.getsource(service_approval.list_awaiting)
    assert "newest_first=False" in awaiting, "hàng đợi xếp mới trước — người chờ lâu nhất chờ mãi"


def test_the_queue_reports_its_true_size() -> None:
    """Một hàng đợi dài hơn `limit` trông y hệt một hàng đợi vừa đủ.

    Đo được: yêu cầu vào hàng đợi lúc 18:44:41 nằm ở vị trí ~62 trong khi danh
    sách cắt ở 50 và xếp cũ-nhất-trước. Người duyệt không thấy, khách chờ rồi
    huỷ — và cả hai phía đều tin hệ thống hỏng.
    """
    from pathlib import Path

    route = Path(__file__).resolve().parents[2] / "src" / "api" / "service_approval_routes.py"
    source = route.read_text(encoding="utf-8")
    assert '"total"' in source, "response không nói tổng số, chỉ nói số đang hiện"
    assert "limit: int = 200" in source, "giới hạn cũ cắt mất phần MỚI NHẤT của hàng đợi"
