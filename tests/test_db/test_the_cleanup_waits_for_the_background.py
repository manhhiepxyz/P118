"""Dọn bảng dưới chân một việc đang chạy là lỗi của phép dọn, không phải flaky.

Triệu chứng
-----------
Chạy full suite, khoảng 1/3 số lượt có thêm MỘT `ERROR at teardown` — mỗi lượt
một test DB khác nhau. Nó trông như flaky: không tái hiện được theo thứ tự,
chạy riêng thì xanh, và test "có lỗi" không liên quan gì tới nhau.

Nguyên nhân
-----------
`request_fresh_answer` và đường chạy workflow đều `create_task` rồi trả về NGAY.
Đúng — người dùng không nên chờ một câu trả lời họ chưa hỏi. Nhưng "không chặn"
nghĩa là chúng còn sống sau khi request đã xong, và chúng đang cầm connection
cùng khoá dòng.

`TRUNCATE` của teardown lấy khoá theo thứ tự LIỆT KÊ; tác vụ nền lấy theo thứ tự
NGHIỆP VỤ của nó. Hai thứ tự ngược nhau là công thức của một deadlock:

    asyncpg.exceptions.DeadlockDetectedError: deadlock detected
    Process A waits for AccessExclusiveLock on relation ...; blocked by B.
    Process B waits for RowShareLock on relation ...; blocked by A.

PostgreSQL giết một bên. Bên thua đổi theo tải máy — nên nó hiện ra như "một
test khác nhau mỗi lượt". Không có gì ngẫu nhiên trong đó cả.

Bài kiểm này dựng lại đúng công thức ấy. Nếu ai đó bỏ lượt chờ trong
`clean_tables`, nó đỏ ngay — thay vì để cả suite lại thành 1/3-lượt-đỏ.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from src.api.routes import _DEMO_TASKS, _keep_demo_task, drain_demo_tasks


@pytest.mark.asyncio
async def test_the_teardown_waits_instead_of_deadlocking(db_pool):
    """Khoá NGƯỢC thứ tự `TRUNCATE` — và teardown vẫn phải chạy trót lọt."""
    wid = uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','SUCCESS')", wid)
    await db_pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status)"
        " VALUES ($1,'T1','book_parking','x','{}'::jsonb,'AWAITING')",
        wid,
    )

    async def nen() -> None:
        # `TRUNCATE` khoá `service_approvals` TRƯỚC `workflows`. Task này đi
        # ngược lại, và giữ khoá qua khỏi lúc test kết thúc.
        async with db_pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT * FROM workflows WHERE workflow_id=$1 FOR UPDATE", wid)
            await asyncio.sleep(0.4)
            await conn.execute("SELECT * FROM service_approvals WHERE workflow_id=$1 FOR UPDATE", wid)
            await asyncio.sleep(0.4)

    _keep_demo_task(asyncio.create_task(nen()))
    await asyncio.sleep(0.1)
    # Không await ở đây: đó CHÍNH là hình dạng đã gây ra lỗi. `clean_tables`
    # phải tự lo — nếu không, teardown của test này nổ `DeadlockDetectedError`.


@pytest.mark.asyncio
async def test_nothing_is_left_running_for_the_next_test(db_pool):
    """Test trước để lại một tác vụ nền; tới lượt này nó phải xong hẳn."""
    assert [t for t in _DEMO_TASKS if not t.done()] == []


@pytest.mark.asyncio
async def test_draining_an_empty_registry_costs_nothing():
    assert await drain_demo_tasks() == 0


@pytest.mark.asyncio
async def test_a_hung_task_is_cancelled_not_waited_on_forever():
    """Một tác vụ treo không được giữ cả tiến trình.

    Người gọi đang DỌN DẸP, không đang phục vụ ai — hết giờ thì huỷ và đi tiếp.
    """

    async def treo() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(treo())
    _keep_demo_task(task)

    da_cho = await drain_demo_tasks(timeout=0.2)

    assert da_cho == 1
    assert task.cancelled() or task.done()


def test_shutdown_lets_the_background_finish_before_closing_the_pool():
    """Thứ tự tắt: chờ việc đang chạy → dừng vòng lặp nền → ĐÓNG POOL.

    Thứ tự cũ đóng pool trước rồi mới dọn, nên một lượt deploy giữa chừng giật
    connection khỏi tay `_attach_answer`: câu chốt không bao giờ được ghi, và
    workflow nằm lại đúng trạng thái nó đang dở.

    Kiểm bằng VỊ TRÍ trong mã nguồn: hành vi này chỉ xảy ra lúc tiến trình tắt,
    và dựng lại một lifespan thật trong test tốn hơn nhiều so với thứ nó chứng
    minh. Bài kiểm thô, nhưng nó chặn đúng cách đã hỏng — đảo thứ tự.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "src" / "main.py").read_text(encoding="utf-8")
    than = src[src.index("    clear_repository_provider()") :]

    cho = than.index("await drain_demo_tasks()")
    dung_vong_lap = than.index("task.cancel()")
    dong_pool = than.index("._inner.close()")

    assert cho < dung_vong_lap < dong_pool, "pool đóng trước khi việc đang chạy kịp xong"
