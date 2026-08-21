"""Số liệu vận hành phải là của TOÀN hệ thống, và chỉ admin đọc được.

Sự cố đã xảy ra: `AdminDashboardPage` gọi `GET /workflows/demo` — endpoint lọc
theo `owner_user_id` — rồi hiển thị kết quả dưới tiêu đề "Giám sát toàn bộ
workflow". Tài khoản admin không sở hữu workflow nào nên màn hình luôn hiện 0,
trong khi database có 92 workflow.

Hai bất biến ở đây, và cái thứ hai quan trọng hơn: endpoint này KHÔNG được để
lộ nội dung yêu cầu của cư dân. Giám sát vận hành cần biết có bao nhiêu việc
đang hỏng, không cần biết ai yêu cầu gì.
"""

from __future__ import annotations

import pytest

from tests.test_db.conftest import _register_and_login


@pytest.mark.asyncio
async def test_metrics_count_every_workflow_not_just_the_callers(client, db_pool):
    """Admin không sở hữu workflow nào mà vẫn phải thấy số của cả hệ thống."""
    customer_token = await _register_and_login(client, "nn_metrics_customer")
    await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {customer_token}"},
        json={"goal": "Đặt chỗ đỗ xe khu A"},
    )

    admin_token = await _register_and_login(client, "nn_metrics_admin")
    await db_pool.execute("UPDATE users SET role = 'admin' WHERE username = $1", "nn_metrics_admin")

    response = await client.get("/api/v1/admin/metrics", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200, response.text
    body = response.json()

    persisted = await db_pool.fetchval("SELECT count(*) FROM workflows WHERE archived_at IS NULL")
    assert body["total"] == persisted, "đếm theo chủ sở hữu thay vì toàn hệ thống"
    assert body["total"] > 0, "admin vẫn thấy 0 — đúng triệu chứng của lỗi cũ"

    # Mọi trạng thái phải rơi vào đúng một ô. Bản đầu lọc theo tên `ErrorCode`
    # thay vì `WorkflowStatus`, và bỏ sót CANCELLED — một bộ lọc sai tên không
    # báo lỗi, nó chỉ đếm ra 0. Bất biến này bắt được cả hai.
    buckets = body["running"] + body["waiting_approval"] + body["success"] + body["failed"] + body["cancelled"]
    assert buckets == body["total"], f"có trạng thái không thuộc ô nào: {body}"


@pytest.mark.asyncio
async def test_every_status_lands_in_exactly_one_bucket(client, db_pool):
    """Gieo đủ CẢ SÁU trạng thái — nếu không, bất biến trên chẳng thử được gì.

    Bản đầu lọc theo tên `ErrorCode` ('EXECUTION_ERROR', 'PLANNING_ERROR')
    thay vì `WorkflowStatus`, và bỏ sót CANCELLED. Một bộ lọc sai tên trạng
    thái không báo lỗi — nó chỉ đếm ra 0. Chỉ dữ liệu có đủ mọi trạng thái mới
    phát hiện được.
    """
    token = await _register_and_login(client, "nn_metrics_buckets")
    await db_pool.execute("UPDATE users SET role = 'admin' WHERE username = $1", "nn_metrics_buckets")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_metrics_buckets")

    statuses = ["PENDING", "RUNNING", "WAITING_APPROVAL", "SUCCESS", "FAILED", "CANCELLED"]
    for status in statuses:
        await db_pool.execute(
            "INSERT INTO workflows (goal, status, owner_user_id) VALUES ($1, $2, $3)",
            f"bucket {status}",
            status,
            owner,
        )

    body = (
        await client.get("/api/v1/admin/metrics", headers={"Authorization": f"Bearer {token}"})
    ).json()

    buckets = body["running"] + body["waiting_approval"] + body["success"] + body["failed"] + body["cancelled"]
    assert buckets == body["total"], f"trạng thái rơi ra ngoài mọi ô: {body}"
    for key in ("success", "failed", "cancelled", "waiting_approval"):
        assert body[key] >= 1, f"ô {key!r} không đếm được hàng vừa gieo: {body}"


@pytest.mark.asyncio
async def test_metrics_never_leak_what_residents_asked_for(client, db_pool):
    """Chỉ SỐ ĐẾM. Một dashboard tiện tay hiện goal là rò rỉ được cấp phép sẵn."""
    admin_token = await _register_and_login(client, "nn_metrics_privacy")
    await db_pool.execute("UPDATE users SET role = 'admin' WHERE username = $1", "nn_metrics_privacy")

    body = (
        await client.get("/api/v1/admin/metrics", headers={"Authorization": f"Bearer {admin_token}"})
    ).json()

    # SỐ, không phải chữ. `total_cost` và `avg_latency_ms` là float (tiền và
    # mili-giây trung bình không tròn được); phần còn lại là số đếm nguyên.
    # Điều test này canh là KHÔNG có chuỗi nào lọt vào — chuỗi mới là chỗ nội
    # dung yêu cầu của cư dân có thể đi nhờ ra ngoài.
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in body.values()), body
    for leaked in ("goal", "owner_user_id", "username", "workflow_id", "items"):
        assert leaked not in body, f"số liệu vận hành mang theo {leaked!r}"


@pytest.mark.asyncio
async def test_a_customer_cannot_read_system_metrics(client, db_pool):
    """Ranh giới quyền: đây là lý do endpoint này tách khỏi `/workflows/demo`."""
    token = await _register_and_login(client, "nn_metrics_intruder")

    response = await client.get("/api/v1/admin/metrics", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code in {401, 403}, response.status_code


@pytest.mark.asyncio
async def test_a_workflow_waiting_for_the_user_is_not_counted_as_orphaned(client, db_pool):
    """Chờ người dùng trả lời KHÔNG phải mồ côi.

    Bản đầu có một ô "Kẹt quá 5 phút" đếm mọi PENDING/RUNNING không nhúc nhích.
    Đo trên dữ liệu thật: cả 17 workflow nó đếm đều đang chờ người dùng bổ sung
    thông tin — tức hệ thống chạy ĐÚNG. Ô đó báo động giả 100%.

    Một chỉ số vận hành báo động giả thì tệ hơn không có: người trực học cách
    phớt lờ nó, rồi phớt lờ luôn lần nó nói thật.

    `awaiting_user` phải dùng chính điều kiện sweeper dùng để THA — có
    clarification chưa giải quyết — nếu không màn hình sẽ mâu thuẫn với hành vi
    thật của hệ thống.
    """
    token = await _register_and_login(client, "nn_metrics_awaiting")
    await db_pool.execute("UPDATE users SET role = 'admin' WHERE username = $1", "nn_metrics_awaiting")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_metrics_awaiting")

    # Workflow cũ hơn TTL của sweeper, nhưng đang chờ người dùng trả lời.
    workflow_id = await db_pool.fetchval(
        "INSERT INTO workflows (goal, status, owner_user_id, updated_at) "
        "VALUES ($1, 'PENDING', $2, NOW() - INTERVAL '3 hours') RETURNING workflow_id",
        "Đặt lịch nhưng thiếu ngày",
        owner,
    )
    await db_pool.execute(
        "INSERT INTO workflow_clarifications (workflow_id, goal, missing_fields, resolved_at) "
        "VALUES ($1, $2, $3::jsonb, NULL)",
        workflow_id,
        'Đặt lịch nhưng thiếu ngày',
        '["ngay_gio"]',
    )

    body = (
        await client.get("/api/v1/admin/metrics", headers={"Authorization": f"Bearer {token}"})
    ).json()

    assert body["awaiting_user"] >= 1, f"không đếm được workflow đang chờ người dùng: {body}"
    assert body["orphaned"] == 0, (
        f"workflow đang chờ người dùng bị đếm là mồ côi — đúng lỗi báo động giả cũ: {body}"
    )


@pytest.mark.asyncio
async def test_a_workflow_nobody_is_working_on_is_counted_as_orphaned(client, db_pool):
    """Chốt ngược: mồ côi thật thì PHẢI đếm.

    Siết `orphaned` thành "luôn bằng 0" cũng làm ô này vô dụng — chỉ theo hướng
    ngược lại. Khác 0 nghĩa là vòng quét zombie đang không chạy.
    """
    token = await _register_and_login(client, "nn_metrics_orphan")
    await db_pool.execute("UPDATE users SET role = 'admin' WHERE username = $1", "nn_metrics_orphan")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_metrics_orphan")

    await db_pool.execute(
        "INSERT INTO workflows (goal, status, owner_user_id, updated_at) "
        "VALUES ($1, 'RUNNING', $2, NOW() - INTERVAL '3 hours')",
        "Không ai đang làm việc này",
        owner,
    )

    body = (
        await client.get("/api/v1/admin/metrics", headers={"Authorization": f"Bearer {token}"})
    ).json()

    assert body["orphaned"] >= 1, f"mồ côi thật mà không đếm: {body}"
