"""Integration test — duyệt lịch tham quan qua main app thật (path /review).

Cùng thiết kế với `test_verification_records_api.py`: repository THẬT trên
`e2e_pool`, auth THẬT (JWT), route THẬT trên main app qua ASGITransport. Khác ở
chỗ nơi bị fake:

  - verification: fake là chính provider (connector in-process tới provider thật).
  - viewing: `schedule_property_viewing` và `book_shuttle` là TASK trong DAG
    workflow, nên phần bị fake là các tầng gọi provider bên trong
    `_materialize_and_run_remaining`:
      `demo_service.TourConnector`  → fake trả lịch tour
      `demo_service.build_connectors` → `[]` (không tạo connector thật)
      `demo_service.Executor`       → fake ghi nhận seed_statuses/seed_results

Còn lại (đọc AWAITING, ghi APPROVED/REJECTED, đánh FAILED workflow, chống
double-decide) chạy trên PostgreSQL thật — đây là phần lỗi nghiêm trọng nhất
nếu mô phỏng: nó chính là thứ giữ quyết định duyệt sau restart backend.

Seed: workflow WAITING_APPROVAL + task T1 (schedule_property_viewing,
WAITING_APPROVAL) + task T2 (book_shuttle, PENDING, depends_on T1) +
`viewing_approvals` AWAITING. T2.input.viewing_id là InputRef dạng DICT (đúng
như JSONB đọc về) — test xác minh `_plan_from_task_rows` coerce về InputRef
object trước khi đưa cho Executor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.auth import create_access_token, hash_password
from src.api.deps import get_user_repository
from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.results import StandardResult
from src.common.task_plan import InputRef
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.main import app
from src.orchestration import demo_service
from src.orchestration.provider_directory import don_vi_mac_dinh
from src.orchestration.runtime_provider import (
    SharedPool,
    clear_repository_provider,
    set_repository_provider,
)
from tests._otp_registration import dang_ky_qua_duong_that

# Ngày trong tương lai — provider từ chối ngày quá khứ, và ngày cứng sẽ biến
# test thành quả bom hẹn giờ.
FUTURE = (date.today() + timedelta(days=30)).isoformat()

_VIEWING_RESULT = {
    "viewing_id": "VIEW-001",
    "project_id": "PRJ-001",
    "project_name": "Vinhomes Ocean Park",
    "viewing_date": FUTURE,
    "viewing_time": "09:30",
    "viewing_status": "SCHEDULED",
    "contact_name": "Lâm Thành Bảo",
    "contact_phone": "0912345678",
    "receptionist_name": "Chị Mai",
    "receptionist_phone": "0911111111",
    "reception_area": "Sảnh chờ Khu A",
    "reception_time": "09:30",
}

_SHUTTLE_RESULT = {
    "shuttle_id": "SHUTTLE-001",
    "viewing_id": "VIEW-001",
    "tour_date": FUTURE,
    "passenger_count": 4,
    "driver_name": "Anh Tuấn",
    "license_plate": "29A-456.78",
    "vehicle_type": "Ô tô 7 chỗ",
    "pickup_time": "07:30",
}


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _ready(repo):
    """Provider phải là async callable (xem runtime_provider.acquire_repository)."""
    return repo


class _FakeTour:
    """Thay `TourConnector`: materialize lịch tham quan (bước duyệt).

    Mặc định thành công; test set `fail_with` để ép nhánh materialize thất bại.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.fail_with: ErrorCode | None = None
        self.last_input: dict | None = None

    async def execute(self, tool_name: str, input_data: dict, *, context=None) -> StandardResult:
        self.last_input = input_data
        if self.fail_with is not None:
            return StandardResult.fail(error_code=self.fail_with, message="hết chỗ")
        return StandardResult.ok(data=dict(_VIEWING_RESULT))


class _FakeExecutor:
    """Thay `Executor`: ghi nhận seed rồi trả kết quả đặt xe 4 thông tin tài xế.

    Bản giả phải cập nhật TRẠNG THÁI TASK giống Executor thật, vì phía gọi đọc
    lại `workflow_tasks` để quyết định workflow SUCCESS hay FAILED. Bản trước
    chỉ emulate `finalize=True` bằng cách đẩy thẳng workflow về SUCCESS; khi
    caller chuyển sang `finalize=False` và tự chốt trạng thái từ task rows, một
    bản giả không ghi task row sẽ làm mọi task trông như còn PENDING và workflow
    thành FAILED — một thất bại của ĐỒ GIẢ, không phải của sản phẩm.
    """

    instances: list[_FakeExecutor] = []

    def __init__(self, connectors, repository, on_failure=None) -> None:
        self.connectors = connectors
        self.repository = repository
        # `on_failure` là đường DUY NHẤT sinh repair hint. Bản giả từng không
        # nhận tham số này, và khi caller bắt đầu truyền nó thì route trả 502 —
        # một thất bại của ĐỒ GIẢ trông y hệt một thất bại của sản phẩm.
        self.on_failure = on_failure
        self.calls: list[dict] = []
        _FakeExecutor.instances.append(self)

    async def execute(
        self,
        plan,
        workflow_id: str,
        *,
        finalize: bool = True,
        # Hợp đồng của `Executor.execute` có CẢ HAI tham số này, và
        # `ValidatedExecutionBoundary` chuyển tiếp chúng xuống. Đồ giả hẹp hơn
        # đồ thật thì mọi lớp bọc mới đều làm test đỏ vì `TypeError` — một thất
        # bại của ĐỒ GIẢ trông y hệt thất bại của sản phẩm.
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
        seed_statuses: dict | None = None,
        seed_results: dict | None = None,
    ):
        self.calls.append(
            {
                "plan": plan,
                "workflow_id": workflow_id,
                "finalize": finalize,
                "seed_statuses": seed_statuses,
                "seed_results": seed_results,
            }
        )
        # Executor thật đánh SUCCESS cho từng task nó chạy xong.
        for task in getattr(plan, "tasks", []):
            await self.repository.update_task_status(workflow_id, task.task_id, TaskStatus.SUCCESS)
        if finalize:
            await self.repository.update_workflow_status(workflow_id, WorkflowStatus.SUCCESS)
        return workflow_id, {"T2": StandardResult.ok(data=dict(_SHUTTLE_RESULT))}


def _executor_call() -> dict:
    assert _FakeExecutor.instances, "Executor chưa được gọi trong lượt resume"
    return _FakeExecutor.instances[-1].calls[0]


@dataclass
class _Harness:
    repo: PostgreSQLWorkflowStateRepository
    fake_tour: _FakeTour


@pytest_asyncio.fixture
async def viewing_env(e2e_pool, monkeypatch):
    """Repo + auth thật; chỉ ba tầng gọi provider trong resume bị fake."""
    _FakeExecutor.instances = []
    repo = PostgreSQLWorkflowStateRepository(e2e_pool)
    repo._pool = SharedPool(repo._pool)  # noqa: SLF001 - route close() = no-op
    set_repository_provider(lambda: _ready(repo))
    app.dependency_overrides[get_user_repository] = lambda: repo.users

    fake_tour = _FakeTour(base_url="http://tour")
    monkeypatch.setattr(demo_service, "TourConnector", lambda base_url: fake_tour)
    monkeypatch.setattr(demo_service, "build_connectors", lambda **kwargs: [])
    monkeypatch.setattr(demo_service, "Executor", _FakeExecutor)

    yield _Harness(repo=repo, fake_tour=fake_tour)

    app.dependency_overrides.clear()
    clear_repository_provider()


@pytest_asyncio.fixture
async def viewing_client():
    """Client tới main app thật (ASGITransport)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _headers(user: dict) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user)}"}


async def _register_customer(client) -> dict:
    """Khách mới, tạo qua ĐÚNG đường sản phẩm — gồm cả bước OTP.

    Phần cơ học nằm ở `tests/_otp_registration`: cùng một việc cũng cần cho
    `tests/test_db`, và hai bản sao của một luồng đăng ký là hai chỗ để lệch
    nhau khi hợp đồng đổi. Lần này nó đã đổi thật — bước OTP được thêm — và
    đó là lý do file này từng đỏ 9 bài.
    """
    data = await dang_ky_qua_duong_that(client, _unique("customer"), password="matkhau123")
    assert data is not None, "tên đăng ký bị trùng — `_unique` không còn duy nhất"
    return {"id": data["id"], "username": data["username"], "role": data["role"]}


async def _make_provider(repo, *, don_vi: str | None = None) -> dict:
    """Tài khoản đơn vị — có role VÀ, mặc định, được gắn đơn vị giữ lịch tham quan.

    Gắn đơn vị không phải chi tiết dựng cảnh. Từ khi cổng tham quan lọc theo
    quyền sở hữu, một tài khoản `provider` chưa gắn đơn vị nào không đọc và
    không quyết định được gì — đó là hành vi ĐÚNG, và mọi bài ở file này nói về
    chuyện khác.

    Mã lấy từ `don_vi_mac_dinh` chứ không gõ tay: bảng ánh xạ tool → đơn vị chỉ
    nên có một bản. Truyền `don_vi` khác để dựng một đơn vị KHÔNG sở hữu.
    """
    user = await repo.users.create_user(_unique("provider"), hash_password("matkhau123"), role="provider")
    ma = don_vi if don_vi is not None else don_vi_mac_dinh("schedule_property_viewing")
    async with repo._pool.acquire() as conn:  # noqa: SLF001 - test dựng state
        await conn.execute(
            "INSERT INTO service_provider_accounts (user_id, service_provider_id) "
            "VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING",
            str(user["id"]),
            ma,
        )
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


async def _seed_awaiting_workflow(harness, *, owner_user_id: str) -> str:
    """Workflow đang chờ duyệt lịch tham quan + task T1/T2 + viewing_approvals.

    `T2.input.viewing_id` là InputRef dạng DICT đúng như JSONB đọc về — đường
    này test coerce dict→object khi dựng lại plan từ `workflow_tasks`.
    """
    repo = harness.repo
    workflow_id = str(uuid.uuid4())
    await repo.create_workflow(
        {
            "id": workflow_id,
            "goal": "Đặt lịch tham quan và đặt xe đưa đón",
            "status": "WAITING_APPROVAL",
            "owner_user_id": owner_user_id,
        }
    )
    await repo.create_task(
        workflow_id,
        {
            "id": "T1",
            "tool": "schedule_property_viewing",
            "depends_on": [],
            "input": {"project_id": "PRJ-001", "viewing_date": FUTURE, "viewing_time": "09:30"},
            "status": "WAITING_APPROVAL",
        },
    )
    await repo.create_task(
        workflow_id,
        {
            "id": "T2",
            "tool": "book_shuttle",
            "depends_on": ["T1"],
            "input": {
                "viewing_id": {"from_task": "T1", "field": "viewing_id"},
                "tour_date": FUTURE,
                "passenger_count": 4,
            },
            "status": "PENDING",
        },
    )
    # asyncpg không tự adapt `str` sang cột DATE — đưa object date thật.
    from datetime import date as _date

    async with repo._pool.acquire() as conn:  # noqa: SLF001 - test dựng state
        await conn.execute(
            """
            INSERT INTO viewing_approvals (
                workflow_id, task_id, status, project_id, project_name,
                viewing_date, viewing_time, passenger_count, wants_shuttle,
                applicant_user_id, applicant_name, applicant_phone
            )
            VALUES ($1, 'T1', 'AWAITING', 'PRJ-001', 'Vinhomes Ocean Park',
                    $2, '09:30', 4, TRUE, $3, 'Lâm Thành Bảo', '0912345678')
            """,
            workflow_id,
            _date.fromisoformat(FUTURE),
            owner_user_id,
        )
        # Gán ĐƠN VỊ giữ hồ sơ. Trigger `INSTEAD OF INSERT` của view
        # `viewing_approvals` KHÔNG đặt `service_provider_id`, nên mọi dòng ghi
        # qua view là dòng VÔ CHỦ — và từ khi cổng tham quan lọc theo quyền sở
        # hữu, dòng vô chủ không đơn vị nào thấy. Đó là fail-closed đúng ý, chứ
        # không phải một lỗi ở đây; nhưng nó có nghĩa là bài kiểm phải nói ra ai
        # sở hữu, y như đường ghi thật (`save_pending_viewing_approval`) đang làm.
        await conn.execute(
            "UPDATE service_approvals SET service_provider_id = $2 WHERE workflow_id = $1 AND task_id = 'T1'",
            workflow_id,
            don_vi_mac_dinh("schedule_property_viewing"),
        )
    return workflow_id


async def _decide(
    client,
    reviewer: dict,
    workflow_id: str,
    decision: str,
    reason: str | None = None,
    code: str | None = None,
):
    body: dict = {"decision": decision}
    if reason is not None:
        body["reject_reason"] = reason
    if code is not None:
        body["reject_code"] = code
    return await client.post(
        f"/api/v1/viewing-approvals/{workflow_id}/decide",
        headers=_headers(reviewer),
        json=body,
    )


# ---------------------------------------------------------------------------
# Quyền — người duyệt là provider/admin, khách không đụng vào
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_cannot_list_or_decide(viewing_env, viewing_client):
    customer = await _register_customer(viewing_client)
    workflow_id = await _seed_awaiting_workflow(viewing_env, owner_user_id=customer["id"])

    listed = await viewing_client.get("/api/v1/viewing-approvals", headers=_headers(customer))
    assert listed.status_code == 403

    decided = await _decide(viewing_client, customer, workflow_id, "approve")
    assert decided.status_code == 403


@pytest.mark.asyncio
async def test_provider_lists_awaiting_with_applicant_pii(viewing_env, viewing_client):
    customer = await _register_customer(viewing_client)
    provider = await _make_provider(viewing_env.repo)
    workflow_id = await _seed_awaiting_workflow(viewing_env, owner_user_id=customer["id"])

    res = await viewing_client.get(
        "/api/v1/viewing-approvals",
        params={"status": "AWAITING"},
        headers=_headers(provider),
    )

    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["workflow_id"] == workflow_id
    assert item["status"] == "AWAITING"
    # Người duyệt cần đủ thông tin để gọi khách — reviewer view có PII.
    assert item["applicant_name"] == "Lâm Thành Bảo"
    assert item["applicant_phone"] == "0912345678"
    assert item["passenger_count"] == 4
    assert item["wants_shuttle"] is True


# ---------------------------------------------------------------------------
# Duyệt → materialize lịch + resume book_shuttle (~30s)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_materializes_and_resumes_with_driver_details(viewing_env, viewing_client):
    customer = await _register_customer(viewing_client)
    provider = await _make_provider(viewing_env.repo)
    workflow_id = await _seed_awaiting_workflow(viewing_env, owner_user_id=customer["id"])

    res = await _decide(viewing_client, provider, workflow_id, "approve")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["decision"] == "approve"
    # Summary đặt xe phải rõ tài xế / biển số / loại xe / giờ đón.
    assert "tài xế Anh Tuấn" in body["summary"]
    assert "biển số 29A-456.78" in body["summary"]
    assert "Ô tô 7 chỗ" in body["summary"]
    assert "giờ đón 07:30" in body["summary"]

    # Resume chạy đúng một lần với seed đầy đủ.
    call = _executor_call()
    assert call["workflow_id"] == workflow_id
    # `finalize=False` là CỐ Ý, và đây là chỗ giữ nó.
    #
    # Executor không được tự chốt SUCCESS ở nhánh này. Nếu nó chốt, workflow
    # chuyển sang SUCCESS trước khi câu trả lời cuối được ghi — và khách đọc
    # được "Đơn vị tour đang xác nhận lịch" cho một việc đã xong hẳn. Đó là lỗi
    # thật đã đo được trong database (`assistant_for_status` còn kẹt ở
    # WAITING_APPROVAL trong khi `status` đã là SUCCESS).
    assert call["finalize"] is False, "Executor không được tự chốt SUCCESS trước khi có câu trả lời cuối"
    assert call["seed_statuses"] == {"T1": TaskStatus.SUCCESS}
    seeded_viewing = call["seed_results"]["T1"]
    assert seeded_viewing.data["viewing_id"] == "VIEW-001"

    # Plan dựng lại từ `workflow_tasks` gồm CẢ hai task — không được mất
    # book_shuttle, và InputRef dict phải được coerce về object.
    plan = call["plan"]
    assert {t.task_id for t in plan.tasks} == {"T1", "T2"}
    shuttle_task = next(t for t in plan.tasks if t.task_id == "T2")
    viewing_id = shuttle_task.input["viewing_id"]
    assert isinstance(viewing_id, InputRef)
    assert viewing_id.from_task == "T1"
    assert viewing_id.field == "viewing_id"

    # Tour được materialize với đúng input từ bảng chờ duyệt.
    assert viewing_env.fake_tour.last_input == {
        "project_id": "PRJ-001",
        "viewing_date": FUTURE,
        "viewing_time": "09:30",
    }

    # DB: quyết định APPROVED + decided_by từ JWT, workflow về SUCCESS.
    async with viewing_env.repo._pool.acquire() as conn:  # noqa: SLF001
        row = await conn.fetchrow(
            "SELECT status, decided_by FROM viewing_approvals WHERE workflow_id = $1",
            workflow_id,
        )
        assert row["status"] == "APPROVED"
        assert row["decided_by"] == provider["username"]
        wf = await conn.fetchrow(
            """
            SELECT status, assistant_for_status, assistant_answer
            FROM workflows WHERE workflow_id = $1
            """,
            workflow_id,
        )
        assert wf["status"] == "SUCCESS"

        # Câu trả lời cuối phải được ghi TRƯỚC khi trạng thái thành SUCCESS.
        #
        # Không có ràng buộc này thì tồn tại một khoảng — dài bằng cả một nhịp
        # poll — mà workflow đã xong còn câu khách đọc vẫn là câu của lúc chờ.
        # Đo được đúng như vậy trong database trước khi sửa:
        #   status = SUCCESS, assistant_for_status = WAITING_APPROVAL
        assert wf["assistant_for_status"] == "SUCCESS", "câu trả lời vẫn thuộc về trạng thái cũ khi workflow đã SUCCESS"
        assert "đang xác nhận" not in (wf["assistant_answer"] or "")


@pytest.mark.asyncio
async def test_approve_second_time_conflicts(viewing_env, viewing_client):
    customer = await _register_customer(viewing_client)
    provider = await _make_provider(viewing_env.repo)
    workflow_id = await _seed_awaiting_workflow(viewing_env, owner_user_id=customer["id"])

    first = await _decide(viewing_client, provider, workflow_id, "approve")
    assert first.status_code == 200, first.text

    # Quyết định đã khoá — `WHERE status='AWAITING'` không cho đổi lần hai.
    second = await _decide(viewing_client, provider, workflow_id, "approve")
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# Materialize thất bại → workflow FAILED, không treo ở WAITING_APPROVAL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_failure_fails_workflow(viewing_env, viewing_client):
    customer = await _register_customer(viewing_client)
    provider = await _make_provider(viewing_env.repo)
    workflow_id = await _seed_awaiting_workflow(viewing_env, owner_user_id=customer["id"])
    viewing_env.fake_tour.fail_with = ErrorCode.NO_AVAILABILITY

    res = await _decide(viewing_client, provider, workflow_id, "approve")

    assert res.status_code == 502
    assert "hết chỗ" in res.json()["detail"]

    # Decision đã khoá nhưng workflow phải FAILED — không để treo mãi.
    async with viewing_env.repo._pool.acquire() as conn:  # noqa: SLF001
        row = await conn.fetchrow("SELECT status FROM viewing_approvals WHERE workflow_id = $1", workflow_id)
        assert row["status"] == "APPROVED"
        wf = await conn.fetchrow("SELECT status FROM workflows WHERE workflow_id = $1", workflow_id)
        assert wf["status"] == "FAILED"
        tasks = await conn.fetch("SELECT status FROM workflow_tasks WHERE workflow_id = $1", workflow_id)
        assert {t["status"] for t in tasks} == {"FAILED"}


# ---------------------------------------------------------------------------
# Từ chối → FAILED chuỗi + lý do ghi lại
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_without_reason_is_422(viewing_env, viewing_client):
    customer = await _register_customer(viewing_client)
    provider = await _make_provider(viewing_env.repo)
    workflow_id = await _seed_awaiting_workflow(viewing_env, owner_user_id=customer["id"])

    res = await _decide(viewing_client, provider, workflow_id, "reject")

    assert res.status_code == 422


@pytest.mark.asyncio
async def test_reject_fails_chain_and_records_reason(viewing_env, viewing_client):
    customer = await _register_customer(viewing_client)
    provider = await _make_provider(viewing_env.repo)
    workflow_id = await _seed_awaiting_workflow(viewing_env, owner_user_id=customer["id"])

    # Từ chối DỨT KHOÁT: không phải hết khung giờ, nên chuỗi hỏng hẳn.
    # `NO_AVAILABILITY` đi đường khác — nó mở một lượt hỏi lại giờ, xem
    # `tests/test_db/test_a_refused_viewing_still_speaks.py`.
    res = await _decide(
        viewing_client,
        provider,
        workflow_id,
        "reject",
        reason="Lịch đã kín giờ tuần này",
        code="INVALID_REQUEST",
    )

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "REJECTED"

    async with viewing_env.repo._pool.acquire() as conn:  # noqa: SLF001
        row = await conn.fetchrow(
            "SELECT status, reject_reason, decided_by FROM viewing_approvals WHERE workflow_id = $1",
            workflow_id,
        )
        assert row["status"] == "REJECTED"
        assert row["reject_reason"] == "Lịch đã kín giờ tuần này"
        assert row["decided_by"] == provider["username"]
        wf = await conn.fetchrow("SELECT status FROM workflows WHERE workflow_id = $1", workflow_id)
        assert wf["status"] == "FAILED"
        tasks = await conn.fetch("SELECT status FROM workflow_tasks WHERE workflow_id = $1", workflow_id)
        # Viewing + shuttle phụ thuộc đều FAILED — không giữ "chỗ đỗ" nào.
        assert {t["status"] for t in tasks} == {"FAILED"}


@pytest.mark.asyncio
async def test_stale_approvals_leave_the_queue(viewing_env, viewing_client):
    """Yêu cầu quá ngày không được nằm mãi trong hàng chờ.

    Người duyệt không có cách nào nhìn ra một yêu cầu đã hết hiệu lực: nó trông
    y hệt yêu cầu hợp lệ. Bấm Duyệt xong mới vỡ ở Tour provider và trả 502 —
    một lỗi không nói được gì cho người đang đứng trước màn hình. Đã gặp đúng
    tình huống này khi chạy e2e: hàng chờ còn một yêu cầu cũ, test bấm nhầm vào
    nó, và bốn kiểm tra đỏ vì lý do không liên quan đến sản phẩm.
    """
    from src.orchestration.viewing_approval import (
        APPROVAL_EXPIRED,
        EXPIRED,
        expire_stale_viewing_approvals,
    )

    customer = await _register_customer(viewing_client)
    workflow_id = await _seed_awaiting_workflow(viewing_env, owner_user_id=customer["id"])

    async with viewing_env.repo._pool.acquire() as conn:  # noqa: SLF001
        await conn.execute(
            "UPDATE viewing_approvals SET viewing_date = CURRENT_DATE - 1 WHERE workflow_id = $1",
            uuid.UUID(workflow_id),
        )
        changed = await expire_stale_viewing_approvals(viewing_env.repo._pool)  # noqa: SLF001
        assert changed == 1
        row = await conn.fetchrow(
            "SELECT status, reject_reason FROM viewing_approvals WHERE workflow_id = $1",
            uuid.UUID(workflow_id),
        )

    assert row["status"] == EXPIRED
    assert row["reject_reason"] == APPROVAL_EXPIRED

    # Không xoá dữ liệu — bằng chứng ai yêu cầu gì vẫn phải còn.
    assert row is not None
