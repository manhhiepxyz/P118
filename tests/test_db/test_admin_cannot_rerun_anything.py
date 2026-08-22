"""Admin không có nút nào làm việc lại. Kể cả một nút tên là "retry".

`POST /admin/workflows/{id}/retry` đặt thẳng `workflows.status = PENDING`. Nghe
như một thao tác cứu hộ, nhưng nó là một mutation không điều kiện lên trạng
thái nghiệp vụ, và nó bỏ qua MỌI hàng rào mà phần còn lại của hệ thống dựng
lên:

  * workflow đã SUCCESS được mở lại — bước đã chạy có thể chạy lần hai, và các
    tool này không idempotent;
  * workflow đang chờ ĐƠN VỊ duyệt bị đẩy về PENDING trong khi hàng đợi vẫn còn
    dòng AWAITING — hai nguồn sự thật nói hai chuyện;
  * workflow đang chờ KHÁCH trả tiền bị đẩy về PENDING, còn thẻ thanh toán thì
    vẫn treo;
  * bằng chứng đã gửi provider (`ACKNOWLEDGED`, `external_request_id`) vẫn nằm
    đó trong khi trạng thái nói là chưa chạy;
  * một khoản đã PAID vẫn PAID, nhưng workflow lại nói chưa làm gì.

Không có checkpoint, không có idempotency, không hỏi policy. Một lệnh recovery
an toàn là một capability riêng — nó cần biết bước nào đã cam kết ra ngoài và
bước nào chưa. Endpoint này không biết gì trong số đó.

Các test dưới đây kiểm HÀNH VI qua HTTP thật: sau khi admin gọi, database phải
không đổi một dòng nào.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_db.conftest import _register_and_login

RETRY = "/api/v1/admin/workflows/{}/retry"
HISTORY = "/api/v1/admin/workflows/history"


async def _admin(client, db_pool, username: str) -> str:
    await _register_and_login(client, username)
    await db_pool.execute("UPDATE users SET role='admin' WHERE username=$1", username)
    return await _register_and_login(client, username)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _seed(db_pool, *, workflow_status, task_status, **kw) -> str:
    """Một workflow ở đúng trạng thái cần kiểm, kèm bằng chứng nếu có."""
    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid,'Đăng ký xe',$2)",
        wid,
        workflow_status,
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, "
        "provider_submission_status, external_request_id) "
        "VALUES ($1::uuid,'T1','register_vehicle',$2,'[]'::jsonb,$3,$4)",
        wid,
        task_status,
        kw.get("submission", "NOT_SUBMITTED"),
        kw.get("external_id"),
    )
    if kw.get("service_approval"):
        await db_pool.execute(
            "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status) "
            "VALUES ($1::uuid,'T1','register_vehicle','Đăng ký phương tiện','{}'::jsonb,$2)",
            wid,
            kw["service_approval"],
        )
    if kw.get("payment_approval"):
        await db_pool.execute(
            "INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status) "
            "VALUES ($1::uuid,'T1','BOOK-RT',100000,'VND',$2)",
            wid,
            kw["payment_approval"],
        )
    return wid


async def _snapshot(db_pool, wid) -> dict:
    """Ảnh chụp MỌI thứ một lệnh retry có thể chạm tới."""
    return {
        "workflow": dict(await db_pool.fetchrow("SELECT status FROM workflows WHERE workflow_id=$1::uuid", wid)),
        "tasks": [
            dict(r)
            for r in await db_pool.fetch(
                # Không có cột đếm lần thử trong `workflow_tasks` — `retryable` và khoá
                # idempotency là hai thứ gần nhất, và cả hai đều là thứ một
                # lệnh mở lại workflow có thể làm sai.
                "SELECT task_id, status, provider_submission_status, external_request_id, "
                "retryable, provider_idempotency_key "
                "FROM workflow_tasks WHERE workflow_id=$1::uuid ORDER BY task_id",
                wid,
            )
        ],
        "service_approvals": [
            dict(r)
            for r in await db_pool.fetch(
                "SELECT task_id, status, decided_by FROM service_approvals WHERE workflow_id=$1::uuid",
                wid,
            )
        ],
        "payment_approvals": [
            dict(r)
            for r in await db_pool.fetch(
                "SELECT task_id, status FROM payment_approvals WHERE workflow_id=$1::uuid", wid
            )
        ],
        "payments": [dict(r) for r in await db_pool.fetch("SELECT payment_id, payment_status FROM payments")],
    }


_CASES = [
    ("workflow đã hoàn tất", {"workflow_status": "SUCCESS", "task_status": "SUCCESS"}),
    ("workflow đã huỷ", {"workflow_status": "CANCELLED", "task_status": "CANCELLED"}),
    (
        "đang chờ đơn vị duyệt",
        {"workflow_status": "WAITING_APPROVAL", "task_status": "WAITING_APPROVAL", "service_approval": "AWAITING"},
    ),
    (
        "đang chờ khách trả tiền",
        {"workflow_status": "WAITING_APPROVAL", "task_status": "WAITING_APPROVAL", "payment_approval": "AWAITING"},
    ),
    (
        "provider đã nhận yêu cầu",
        {
            "workflow_status": "SUCCESS",
            "task_status": "SUCCESS",
            "submission": "ACKNOWLEDGED",
            "external_id": "VEH-ĐÃ-GỬI",
        },
    ),
]


@pytest.mark.parametrize("ten,kwargs", _CASES, ids=[c[0].replace(" ", "-") for c in _CASES])
@pytest.mark.asyncio
async def test_an_admin_retry_changes_nothing(client, db_pool, ten, kwargs):
    token = await _admin(client, db_pool, f"retry_admin_{abs(hash(ten)) % 10000}")
    wid = await _seed(db_pool, **kwargs)
    truoc = await _snapshot(db_pool, wid)

    response = await client.post(RETRY.format(wid), headers=_auth(token))

    assert response.status_code in (403, 404, 405), f"{ten}: retry vẫn đi được ({response.status_code})"
    assert await _snapshot(db_pool, wid) == truoc, f"{ten}: database đổi sau một lệnh bị từ chối"


@pytest.mark.asyncio
async def test_an_admin_retry_does_not_reopen_a_paid_workflow(client, db_pool):
    """Đã thu tiền thật rồi thì không ai được mở lại việc bằng một lệnh."""
    token = await _admin(client, db_pool, "retry_admin_da_tra")
    wid = await _seed(db_pool, workflow_status="SUCCESS", task_status="SUCCESS")
    # Chuỗi FK thật: cư dân → xe → chỗ đỗ → khoản đã trả. Dựng tắt bằng cách
    # chèn thẳng booking sẽ vỡ ở `parking_bookings_vehicle_id_fkey`, và một
    # khoản "đã trả" không gắn với xe nào thì không mô phỏng được điều cần kiểm.
    await db_pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
        "VALUES ('RES-RT','Nguyen Van Retry','RT01','Toà S1')"
    )
    await db_pool.execute(
        "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type) "
        "VALUES ('VEH-RT','RES-RT','51R-99999','car')"
    )
    await db_pool.execute(
        "INSERT INTO parking_bookings (booking_id, vehicle_id, parking_zone, booking_date, amount, currency) "
        "VALUES ('BOOK-RT','VEH-RT','ZONE_B','2030-05-04',100000,'VND')"
    )
    await db_pool.execute(
        "INSERT INTO payments (payment_id, booking_id, amount, currency, payment_status, idempotency_key) "
        "VALUES ('PAY-RT','BOOK-RT',100000,'VND','PAID','wf:x:booking:BOOK-RT')"
    )
    truoc = await _snapshot(db_pool, wid)

    response = await client.post(RETRY.format(wid), headers=_auth(token))

    assert response.status_code in (403, 404, 405)
    assert await _snapshot(db_pool, wid) == truoc
    assert await db_pool.fetchval("SELECT count(*) FROM payments WHERE payment_status='PAID'") == 1


@pytest.mark.asyncio
async def test_the_old_history_endpoint_is_gone(client, db_pool):
    """Hai endpoint cùng trả lời "hệ thống đang có yêu cầu gì" là một endpoint thừa.

    Cái cũ trả `goal` THÔ và `input_data` của bước hỏng — nội dung người dùng gõ,
    chưa qua lọc nào. Giữ nó lại nghĩa là `/admin/requests` không phải nguồn
    canonical, chỉ là một lựa chọn an toàn hơn nằm cạnh một lựa chọn không.
    """
    token = await _admin(client, db_pool, "retry_admin_history")
    response = await client.get(HISTORY, headers=_auth(token))
    assert response.status_code in (404, 405, 410), response.status_code


@pytest.mark.asyncio
async def test_the_canonical_surface_never_returns_raw_task_payloads(client, db_pool):
    """`/admin/requests` không được mang theo input/result thô của bước nào."""
    token = await _admin(client, db_pool, "retry_admin_canonical")
    wid = str(uuid.uuid4())
    bi_mat = "CCCD 001234567890 của khách"
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid,'Đăng ký xe','FAILED')", wid
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data) "
        "VALUES ($1::uuid,'T1','register_vehicle','FAILED','[]'::jsonb,$2::jsonb,$2::jsonb)",
        wid,
        f'{{"ghi_chu": "{bi_mat}"}}',
    )

    danh_sach = await client.get("/api/v1/admin/requests", headers=_auth(token))
    chi_tiet = await client.get(f"/api/v1/admin/requests/{wid}", headers=_auth(token))

    for response in (danh_sach, chi_tiet):
        assert response.status_code == 200, response.text
        assert bi_mat not in response.text
        assert "input_data" not in response.text
        assert "result_data" not in response.text
        assert "failed_task" not in response.text


# --- không còn caller nào của hai endpoint cũ --------------------------------

_REPO = __import__("pathlib").Path(__file__).resolve().parents[2]


def _sources(*globs):
    for pattern in globs:
        for path in _REPO.glob(pattern):
            if "node_modules" in path.parts or not path.is_file():
                continue
            yield path


def test_no_frontend_code_calls_the_removed_admin_endpoints():
    """Xoá endpoint mà để lại caller là đổi một lỗ hổng lấy một màn hình hỏng.

    Kiểm cả TÊN HÀM lẫn URL: một hàm còn tồn tại là một hàm sẽ được gọi lại.
    """
    cam = ("admin/workflows/history", "adminWorkflowsHistory", "AdminWorkflowHistoryItem", "adminRetryWorkflow")
    pham = []
    for path in _sources("frontend/src/**/*.ts", "frontend/src/**/*.tsx"):
        for so, dong in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # Ghi chú được phép NHẮC TÊN chúng — đó là chỗ giải thích vì sao
            # chúng bị xoá, và xoá cả lời giải thích thì người sau sẽ thêm lại.
            goc = dong.strip()
            if goc.startswith(("*", "//", "/*")):
                continue
            for tu in cam:
                if tu in dong:
                    pham.append(f"{path.relative_to(_REPO)}:{so} {tu}")
    assert not pham, pham


def test_the_built_bundle_does_not_ship_the_removed_admin_endpoints():
    """Nếu `frontend/dist` có sẵn thì bundle THẬT phải sạch.

    Kiểm nguồn là chưa đủ: thứ chạy trên máy người dùng là bundle. Bỏ qua khi
    chưa build — không dựng build trong test, và không im lặng coi là đạt.
    """
    dist = list(_sources("frontend/dist/assets/*.js"))
    if not dist:
        __import__("pytest").skip("chưa build frontend/dist")
    for path in dist:
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "admin/workflows/history" not in text
        assert "admin/workflows/${" not in text, "còn dựng URL /admin/workflows/{id}/…"
