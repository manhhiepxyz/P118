"""Cột `kind` phải xuống được một database ĐANG CÓ DỮ LIỆU, không chỉ database rỗng.

Bộ test chạy trên một database dựng mới mỗi lượt, nên nó chứng minh
`schema.sql` đúng — và im lặng về `schema_migrations.sql`, thứ thật sự chạy
trên database của người dùng lúc backend khởi động.

Hai câu hỏi mà chỉ một database có sẵn dữ liệu mới trả lời được:

    dòng CŨ nhận giá trị gì      NULL là không được: `resume_after_service_decision`
                                 lọc theo `kind`, và một dòng NULL sẽ rơi khỏi
                                 cả hai nhánh — bước có thật thành vô hình
    chạy LẦN HAI có sao không    migration chạy mỗi lần khởi động; lần thứ hai
                                 ném lỗi nghĩa là backend không lên được nữa
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.db.migrations import run_migrations


@pytest.mark.asyncio
async def test_rows_written_before_the_column_existed_are_treated_as_steps(db_pool):
    """Mọi dòng có TRƯỚC cột này đều là một BƯỚC — mặc định phải nói đúng điều đó."""
    wid = uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','WAITING_APPROVAL')", wid)
    # Ghi KHÔNG nêu `kind`, đúng như mọi lệnh ghi viết trước khi cột ra đời.
    await db_pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status)"
        " VALUES ($1,'T1','book_parking','Giữ chỗ đỗ xe',$2::jsonb,'AWAITING')",
        wid,
        json.dumps({}),
    )

    kind = await db_pool.fetchval(
        "SELECT kind FROM service_approvals WHERE workflow_id=$1::uuid AND task_id='T1'", str(wid)
    )

    assert kind == "TASK", f"dòng cũ nhận {kind!r} — lượt resume sẽ không còn thấy nó là một bước"


@pytest.mark.asyncio
async def test_running_the_migration_again_changes_nothing(db_pool):
    """Migration chạy mỗi lần backend khởi động. Lần thứ hai ném là không lên được."""
    wid = uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','WAITING_APPROVAL')", wid)
    await db_pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, kind)"
        " VALUES ($1,'YC1','schedule_property_viewing','Xin huỷ lịch','{}'::jsonb,'AWAITING','REQUEST')",
        wid,
    )

    await run_migrations(db_pool)
    await run_migrations(db_pool)

    assert (
        await db_pool.fetchval(
            "SELECT kind FROM service_approvals WHERE workflow_id=$1::uuid AND task_id='YC1'", str(wid)
        )
        == "REQUEST"
    ), "chạy lại migration ghi đè phân loại của một hồ sơ đang chờ"


@pytest.mark.asyncio
async def test_only_the_two_known_kinds_are_accepted(db_pool):
    """Ràng buộc phải sống ở DATABASE, không chỉ trong Python.

    Một giá trị thứ ba lọt xuống bảng nghĩa là dòng ấy không phải bước cũng
    không phải hồ sơ, và mọi vòng lọc đều bỏ qua nó — im lặng.
    """
    import asyncpg

    wid = uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','WAITING_APPROVAL')", wid)

    with pytest.raises(asyncpg.CheckViolationError):
        await db_pool.execute(
            "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, kind)"
            " VALUES ($1,'T1','book_parking','x','{}'::jsonb,'AWAITING','SOMETHING_ELSE')",
            wid,
        )
