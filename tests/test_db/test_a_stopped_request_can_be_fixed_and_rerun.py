"""Sửa vài ô của một yêu cầu ĐÃ DỪNG rồi chạy lại chính nó.

Vì sao không đi qua hội thoại: giá trị cũ nằm trong `workflow_tasks` — một kế
hoạch đã qua Validator — chứ không nằm trong ký ức trò chuyện. Dựng lại từ ký
ức thì Planner phải đoán, và `_fields_taken_from_recall` buộc hỏi lại TỪNG ô
(đúng như thiết kế: giá trị nhớ được phải được xác nhận). Đo được:

    planner: 6 field lấy từ nho_lai → hỏi lại

Đọc thẳng từ kế hoạch đã lưu thì không có gì để đoán, và không guard nào bị nới.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest

from src.common.enums import WorkflowStatus
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration.demo_service import AMENDABLE_STATUSES, NotAmendable, amend_and_rerun


async def _seed(pool, status: str) -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'giữ chỗ đỗ xe',$2)", wid, status
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1','book_parking',$2,"
            '\'{"vehicle_id":"VEH-1","parking_zone":"ZONE_A","booking_date":"2029-01-15"}\'::jsonb)',
            wid,
            status if status != "CANCELLED" else "CANCELLED",
        )
    return str(wid)


@pytest.mark.asyncio
async def test_a_stopped_step_is_reopened_not_left_cancelled(client, db_pool):
    """`update_task_status` cố ý TỪ CHỐI đưa một task rời khỏi CANCELLED.

    Nếu không có thao tác mở lại riêng, "sửa và chạy lại" sẽ chạy trên một kế
    hoạch mà mọi bước đều đã huỷ — tức không chạy gì cả, trong im lặng.
    """
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed(db_pool, "CANCELLED")

    from src.common.enums import TaskStatus

    # Đường thường KHÔNG hồi sinh được: câu UPDATE loại trừ CANCELLED, nên
    # không dòng nào đổi và repository báo lỗi thay vì im lặng.
    from src.db.workflow_repository import TaskNotFoundError

    with pytest.raises(TaskNotFoundError):
        await repository.update_task_status(workflow_id, "T1", TaskStatus.PENDING)
    still = await db_pool.fetchval(
        "SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", uuid.UUID(workflow_id)
    )
    assert still == "CANCELLED"

    moved = await repository.reopen_cancelled_tasks(workflow_id)
    assert moved == 1
    now = await db_pool.fetchval(
        "SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", uuid.UUID(workflow_id)
    )
    assert now == "PENDING"


@pytest.mark.asyncio
async def test_a_finished_step_is_never_rerun(client, db_pool):
    """Bước đã SUCCESS đã tạo cam kết thật ở phía đơn vị. Chạy lại là đặt hai lần."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','CANCELLED')", wid)
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1','register_vehicle','SUCCESS','{}'::jsonb), "
            "($1,'T2','book_parking','CANCELLED','{}'::jsonb)",
            wid,
        )
    moved = await repository.reopen_cancelled_tasks(str(wid))
    assert moved == 1, "chỉ bước chưa xong được mở lại"
    done = await db_pool.fetchval("SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", wid)
    assert done == "SUCCESS"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["SUCCESS", "RUNNING", "PENDING", "WAITING_APPROVAL"])
async def test_only_a_stopped_request_may_be_amended(client, db_pool, status: str):
    """Sửa đè lên một yêu cầu đã hoàn tất sẽ xoá bản ghi của việc THẬT SỰ đã xảy ra.

    Và sửa kế hoạch dưới chân một yêu cầu đang chạy là đua với chính mình.
    """
    workflow_id = await _seed(db_pool, status)
    with pytest.raises(NotAmendable) as err:
        await amend_and_rerun(workflow_id, {"parking_zone": "ZONE_B"})
    assert err.value.code == "NOT_AMENDABLE"
    assert "mới" in str(err.value) or "dừng" in str(err.value), str(err.value)


def test_the_amendable_list_is_short_and_justified():
    """Danh sách này phải NGẮN và có lý do — thêm một trạng thái là một quyết định."""
    assert AMENDABLE_STATUSES == frozenset({WorkflowStatus.CANCELLED.value, WorkflowStatus.FAILED.value})


@pytest.mark.asyncio
async def test_an_unknown_request_is_refused_without_confirming_it_exists(client, db_pool):
    with pytest.raises(NotAmendable) as err:
        await amend_and_rerun(str(uuid.uuid4()), {"parking_zone": "ZONE_B"})
    assert err.value.code == "NOT_FOUND"


def test_internal_ids_are_never_offered_as_editable():
    """`vehicle_id`, `booking_id`, `viewing_id` là mã nội bộ — người dùng không sửa được.

    `InputRef` cũng bị bỏ: nó là con trỏ tới kết quả bước trước, không phải một
    giá trị người dùng chọn.
    """
    from src.api.routes import _amendable_fields

    record = {
        "tasks": [
            {
                "input_data": {
                    "resident_id": "RES-1",
                    "vehicle_id": {"from_task": "T1", "field": "vehicle_id"},
                    "booking_id": "BOOK-1",
                    "viewing_id": "VIEW-1",
                    "parking_zone": "ZONE_A",
                }
            }
        ]
    }
    names = {f["name"] for f in _amendable_fields(record)}
    assert names == {"parking_zone"}, names


@pytest.mark.asyncio
async def test_a_request_already_sent_to_the_provider_is_not_amendable(client, db_pool):
    """ "Đã gửi đi chưa" đọc từ HÀNG ĐỢI DUYỆT, không từ cột `status`.

    Cột đó lệch được: đo được một workflow ghi `CANCELLED` trong khi hai bước
    của nó nằm `WAITING_APPROVAL` và `service_approvals` có hồ sơ AWAITING. Tin
    cột trạng thái nghĩa là khách sửa được thứ đơn vị đang xem xét — họ duyệt
    một đằng, hệ thống chạy một nẻo.
    """
    from src.orchestration.service_approval import save_pending_service_approvals

    workflow_id = await _seed(db_pool, "CANCELLED")
    await save_pending_service_approvals(
        db_pool,
        workflow_id=workflow_id,
        rows=[{"task_id": "T1", "tool": "book_parking", "service_label": "Giữ chỗ đỗ xe", "details": {}}],
    )

    with pytest.raises(NotAmendable) as err:
        await amend_and_rerun(workflow_id, {"parking_zone": "ZONE_B"})
    assert err.value.code == "ALREADY_SENT"
    assert "huỷ" in str(err.value), str(err.value)


@pytest.mark.asyncio
async def test_a_decided_queue_no_longer_blocks_amending(client, db_pool):
    """Đơn vị đã quyết xong thì hàng đợi không còn giữ gì — không chặn nữa."""
    from src.orchestration.service_approval import save_pending_service_approvals

    workflow_id = await _seed(db_pool, "CANCELLED")
    await save_pending_service_approvals(
        db_pool,
        workflow_id=workflow_id,
        rows=[{"task_id": "T1", "tool": "book_parking", "service_label": "Giữ chỗ đỗ xe", "details": {}}],
    )
    await db_pool.execute(
        "UPDATE service_approvals SET status='REJECTED' WHERE workflow_id=$1::uuid", uuid.UUID(workflow_id)
    )
    # Không còn AWAITING → qua được cổng này (dừng ở bước sau vì thiếu connector).
    try:
        await amend_and_rerun(workflow_id, {"parking_zone": "ZONE_B"})
    except NotAmendable as exc:  # pragma: no cover - chỉ để thông báo rõ khi hỏng
        pytest.fail(f"bị chặn oan: {exc.code}")
    except Exception:
        pass


@pytest.mark.asyncio
async def test_the_request_itself_is_reopened_not_only_its_steps(client, db_pool):
    """Cùng hàng rào tồn tại ở CẢ HAI bảng, và bản ở `workflows` từng bị bỏ sót.

    `update_workflow_status` từ chối đưa một workflow rời khỏi `CANCELLED`, nên
    gọi nó trong "Sửa và chạy lại" là một lệnh KHÔNG LÀM GÌ — đúng ở trường hợp
    duy nhất mà đường ấy tồn tại để phục vụ. Đo được trên 09430928, sau khi đổi
    ngày tham quan bằng lời:

        workflow_tasks.T1  WAITING_APPROVAL   viewing_date 2026-09-30
        workflows.status   CANCELLED

    Kế hoạch chạy thật, còn màn hình đọc cột kia và nói "đã dừng".
    """
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed(db_pool, "CANCELLED")

    # Đường thường KHÔNG mở lại được, và nó thất bại TRONG IM LẶNG: câu UPDATE
    # không khớp dòng nào, không lỗi nào được nêu.
    await repository.update_workflow_status(workflow_id, WorkflowStatus.RUNNING)
    still = await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", uuid.UUID(workflow_id))
    assert still == "CANCELLED"

    assert await repository.reopen_cancelled_workflow(workflow_id) is True
    now = await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", uuid.UUID(workflow_id))
    assert now == "RUNNING"


@pytest.mark.asyncio
async def test_a_finished_request_is_never_reopened(client, db_pool):
    """Chỉ yêu cầu ĐÃ DỪNG hoặc ĐÃ HỎNG mới mở lại được."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    for status in ("SUCCESS", "RUNNING", "WAITING_APPROVAL"):
        workflow_id = await _seed(db_pool, status)
        assert await repository.reopen_cancelled_workflow(workflow_id) is False


@pytest.mark.asyncio
async def test_asking_the_provider_again_re_arms_the_expired_request(client, db_pool):
    """Ghim LẠI một lịch tham quan nghĩa là cần một quyết định MỚI.

    `viewing_approvals` là VIEW trên `service_approvals`, và trigger ghi của nó
    giữ `ON CONFLICT DO NOTHING` — bản sao còn sót của luật cũ, đã sửa ở
    `save_pending_service_approvals` cho các dịch vụ khác. Một luật, hai bản cài
    đặt, và bản cũ mới là bản người dùng chạm vào khi họ đổi lịch.

    Đo được trên 09430928: bước chờ duyệt với ngày 2026-09-30, hồ sơ thì
    `EXPIRED` và vẫn mang 2026-09-10. Không ai được hỏi, và yêu cầu treo mãi.
    """
    wid = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','RUNNING')", wid)

        async def park(day: str) -> None:
            await conn.execute(
                "INSERT INTO viewing_approvals "
                "(workflow_id, task_id, project_id, project_name, viewing_date, viewing_time) "
                "VALUES ($1,'T1','PRJ-005','Vinhomes Green Paradise',$2::date,'09:30')",
                wid,
                date.fromisoformat(day),
            )

        await park("2026-09-10")
        # Người dùng bấm Dừng: hồ sơ hết hạn.
        await conn.execute("UPDATE service_approvals SET status='EXPIRED', decided_at=NOW() WHERE workflow_id=$1", wid)
        # Sửa ngày rồi chạy lại → cổng ghim lại đúng bước ấy.
        await park("2026-09-30")

        row = await conn.fetchrow("SELECT status, details, decided_at FROM service_approvals WHERE workflow_id=$1", wid)
    assert row["status"] == "AWAITING", "đơn vị tour phải được hỏi lại"
    assert json.loads(row["details"])["viewing_date"] == "2026-09-30", "và phải hỏi về ngày MỚI"
    assert row["decided_at"] is None, "quyết định cũ không được mang sang hồ sơ mới"


def test_the_write_rule_for_viewings_lives_in_exactly_one_file():
    """Một luật, một bản cài đặt — và bản cài đặt phải là bản đang chạy.

    `viewing_approvals_write()` từng được chép nguyên si vào cả `schema.sql` lẫn
    `schema_migrations.sql`. File thứ hai chạy SAU, nên bản chép đè lên bản gốc:
    sửa `schema.sql` xong, migration báo chạy xong, mà hàm trong database vẫn
    giữ luật cũ. Không có lỗi nào được nêu ở bất kỳ đâu.

    Chủ sở hữu là `schema_migrations.sql`, không phải `schema.sql`: file đó
    phải chạy được MỘT MÌNH trên một database cũ, nên nó không mượn được
    định nghĩa từ nơi khác.
    """
    from pathlib import Path

    sql = Path(__file__).resolve().parents[2] / "src" / "db"
    bodies = [
        f.name for f in sql.glob("*.sql") if "CREATE OR REPLACE FUNCTION viewing_approvals_write" in f.read_text()
    ]
    assert bodies == ["schema_migrations.sql"], bodies


@pytest.mark.asyncio
async def test_a_cancelled_viewing_is_asked_again_after_the_date_is_fixed(client, db_pool):
    """`EXPIRED` là dấu vết người dùng bấm Dừng, không phải quyết định của đơn vị.

    Đường ghi lúc chạy thật là `save_pending_viewing_approval` — không phải
    trigger của khung nhìn — và nó từng có `WHERE status = 'AWAITING'`. Nên sau
    khi người dùng sửa ngày rồi chạy lại, hồ sơ cũ không bao giờ được vũ trang
    lại. Đo được trên 1fc4b70d:

        workflow_tasks.T1   WAITING_APPROVAL   viewing_date 2026-09-30
        service_approvals   EXPIRED            viewing_date 2026-09-10

    Bước chờ một quyết định về ngày MỚI; hồ sơ thì hết hạn và vẫn mang ngày CŨ.
    """
    from src.orchestration.viewing_approval import save_pending_viewing_approval

    wid = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','RUNNING')", wid)

    async def park(day: str) -> None:
        await save_pending_viewing_approval(
            db_pool,
            workflow_id=str(wid),
            task_id="T1",
            project_id="PRJ-005",
            project_name="Vinhomes Green Paradise",
            viewing_date=day,
            viewing_time="09:30",
            passenger_count=None,
            wants_shuttle=False,
            applicant_user_id=None,
            applicant_name=None,
            applicant_phone=None,
        )

    await park("2026-09-10")
    await db_pool.execute("UPDATE service_approvals SET status='EXPIRED', decided_at=NOW() WHERE workflow_id=$1", wid)
    await park("2026-09-30")

    row = await db_pool.fetchrow(
        "SELECT status, details->>'viewing_date' AS d, decided_at FROM service_approvals WHERE workflow_id=$1",
        wid,
    )
    assert row["status"] == "AWAITING", "đơn vị tour phải được hỏi lại"
    assert row["d"] == "2026-09-30", "và phải hỏi về ngày MỚI"
    assert row["decided_at"] is None


@pytest.mark.asyncio
async def test_a_real_decision_is_still_not_overwritten_silently(client, db_pool):
    """Đường này CHỈ chạy sau khi cổng duyệt kết luận là còn phải duyệt.

    Nên khi nó chạy, lượt chạy ấy không có phê duyệt hợp lệ nào để mất — và hồ
    sơ được vũ trang lại là ĐÚNG. Test này ghim chính điều đó thành hợp đồng,
    để lần sau ai đọc `status = 'AWAITING'` trong câu UPDATE còn biết vì sao.
    """
    from src.orchestration.viewing_approval import save_pending_viewing_approval

    wid = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','RUNNING')", wid)
    await save_pending_viewing_approval(
        db_pool,
        workflow_id=str(wid),
        task_id="T1",
        project_id="PRJ-005",
        project_name="Vinhomes Green Paradise",
        viewing_date="2026-09-10",
        viewing_time="09:30",
        passenger_count=None,
        wants_shuttle=False,
        applicant_user_id=None,
        applicant_name=None,
        applicant_phone=None,
    )
    status = await db_pool.fetchval("SELECT status FROM service_approvals WHERE workflow_id=$1", wid)
    assert status == "AWAITING"


@pytest.mark.asyncio
async def test_the_two_write_paths_agree_on_when_to_re_arm(client, db_pool):
    """Cùng một luật, và hai bản cài đặt phải nói giống nhau.

    Lịch tham quan được ghim qua HAI đường: `save_pending_viewing_approval`
    (đường chạy thật) và trigger của khung nhìn `viewing_approvals` (test, script
    vận hành, mã cũ). Chúng đã ba lần lệch nhau. Test này bắt chúng trả lời
    giống nhau cho cả hai câu hỏi quan trọng.
    """
    from src.orchestration.viewing_approval import save_pending_viewing_approval

    async def through_the_view(wid: uuid.UUID, day: str) -> None:
        await db_pool.execute(
            "INSERT INTO viewing_approvals "
            "(workflow_id, task_id, project_id, project_name, viewing_date, viewing_time) "
            "VALUES ($1,'T1','PRJ-005','Vinhomes Green Paradise',$2::date,'09:30')",
            wid,
            date.fromisoformat(day),
        )

    async def through_the_code(wid: uuid.UUID, day: str) -> None:
        await save_pending_viewing_approval(
            db_pool,
            workflow_id=str(wid),
            task_id="T1",
            project_id="PRJ-005",
            project_name="Vinhomes Green Paradise",
            viewing_date=day,
            viewing_time="09:30",
            passenger_count=None,
            wants_shuttle=False,
            applicant_user_id=None,
            applicant_name=None,
            applicant_phone=None,
        )

    for park in (through_the_view, through_the_code):
        for was, expected in (("EXPIRED", "AWAITING"), ("APPROVED", "APPROVED")):
            wid = uuid.uuid4()
            await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','RUNNING')", wid)
            await park(wid, "2026-09-10")
            await db_pool.execute(
                "UPDATE service_approvals SET status=$2, decided_at=NOW() WHERE workflow_id=$1", wid, was
            )
            await park(wid, "2026-09-30")
            row = await db_pool.fetchrow(
                "SELECT status, details->>'viewing_date' AS d FROM service_approvals WHERE workflow_id=$1",
                wid,
            )
            assert row["status"] == expected, (park.__name__, was)
            assert row["d"] == ("2026-09-30" if expected == "AWAITING" else "2026-09-10"), (park.__name__, was)
