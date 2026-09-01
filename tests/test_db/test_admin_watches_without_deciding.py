"""Admin NHÌN THẤY mọi yêu cầu, và không có đường nào từ đó tới một quyết định.

Hai quyền hay bị gộp làm một vì chúng cùng đọc một hàng đợi:

    quyền BIẾT      yêu cầu nào đang tồn tại, của ai, kẹt ở đâu   → admin
    quyền QUYẾT     nhận hay từ chối việc ấy                      → provider

Gộp chúng thành một boolean "reviewer" là chuyện đã xảy ra thật ở backend này:
sáu endpoint duyệt từng cấp quyền cho cả hai vai. File này khoá chiều ngược
lại — admin có đủ dữ kiện để giám sát mà vẫn không ký được gì.

`/admin/requests` cố ý KHÔNG dùng lại `/service-approvals`: một màn giám sát
đọc thẳng endpoint của người duyệt thì đã có sẵn mọi thứ để mọc một nút Duyệt,
và cái nút ấy chỉ cách một dòng JSX.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from tests.test_db.conftest import _register_and_login

LIST = "/api/v1/admin/requests"


async def _user(client, db_pool, username: str, role: str | None = None) -> tuple[str, str]:
    await _register_and_login(client, username)
    if role:
        await db_pool.execute("UPDATE users SET role=$2 WHERE username=$1", username, role)
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", username)
    return token, str(uid)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _workflow(db_pool, owner, *, goal="Đăng ký xe và chỗ đỗ", status="WAITING_APPROVAL"):
    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) VALUES ($1::uuid,$2,$3,$4::uuid)",
        wid,
        goal,
        status,
        owner,
    )
    return wid


async def _task(db_pool, wid, task_id, tool, status, **kw):
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, error_message) "
        "VALUES ($1::uuid,$2,$3,$4,'[]'::jsonb,$5)",
        wid,
        task_id,
        tool,
        status,
        kw.get("error_message"),
    )


async def _service_approval(db_pool, wid, task_id, tool, status="AWAITING", owner=None, decided_by=None):
    await db_pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, "
        "applicant_user_id, decided_by, decided_at) "
        "VALUES ($1::uuid,$2,$3,$4,'{}'::jsonb,$5,$6::uuid,$7,$8)",
        wid,
        task_id,
        tool,
        tool,
        status,
        owner,
        decided_by,
        datetime.now(UTC) if decided_by else None,
    )


# --- ai được vào ------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_anonymous_caller_sees_nothing(client, db_pool):
    assert (await client.get(LIST)).status_code == 401


@pytest.mark.asyncio
async def test_a_customer_cannot_watch_other_peoples_requests(client, db_pool):
    token, _ = await _user(client, db_pool, "giam_sat_khach")
    assert (await client.get(LIST, headers=_auth(token))).status_code == 403


@pytest.mark.asyncio
async def test_a_provider_does_not_get_the_monitoring_surface_either(client, db_pool):
    """Provider quyết định ở `/review`. Màn giám sát toàn hệ thống không phải của họ."""
    token, _ = await _user(client, db_pool, "giam_sat_don_vi", role="provider")
    assert (await client.get(LIST, headers=_auth(token))).status_code == 403


# --- admin thấy đủ thứ cần thấy ---------------------------------------------


@pytest.mark.asyncio
async def test_an_admin_sees_who_asked_for_what_and_who_is_holding_it_up(client, db_pool):
    admin, _ = await _user(client, db_pool, "giam_sat_admin", role="admin")
    _, owner = await _user(client, db_pool, "giam_sat_chu_xe")
    wid = await _workflow(db_pool, owner)
    await _task(db_pool, wid, "T1", "register_vehicle", "WAITING_APPROVAL")
    await _task(db_pool, wid, "T2", "book_parking", "PENDING")
    await _service_approval(db_pool, wid, "T1", "register_vehicle", owner=owner)

    response = await client.get(LIST, headers=_auth(admin))
    assert response.status_code == 200, response.text
    item = next(i for i in response.json()["items"] if i["workflow_id"] == wid)

    assert item["account"]["username"] == "giam_sat_chu_xe"
    assert item["account"]["user_id"] == owner
    assert item["goal"] == "Đăng ký xe và chỗ đỗ"
    # Nhãn nghiệp vụ, không phải tên tool: màn giám sát nói cùng ngôn ngữ với
    # màn duyệt, nếu không hai người mô tả một việc bằng hai tên.
    assert item["service_names"] == ["Đăng ký phương tiện", "Giữ chỗ đỗ xe"], item["service_names"]
    assert item["workflow_status"] == "WAITING_APPROVAL"
    assert item["waiting_for"] == "PROVIDER"
    assert item["provider_decision_status"] == "AWAITING"
    assert item["payment_decision_status"] == "NONE"
    assert item["current_step"] == "Đăng ký phương tiện"
    assert item["created_at"] and item["updated_at"]


@pytest.mark.asyncio
async def test_waiting_for_the_unit_and_waiting_for_the_customer_are_not_the_same_row(client, db_pool):
    """`workflows.status` nói "đang chờ" cho cả hai. Người giám sát cần biết chờ AI."""
    admin, _ = await _user(client, db_pool, "giam_sat_admin_2", role="admin")
    _, owner = await _user(client, db_pool, "giam_sat_chu_2")

    cho_don_vi = await _workflow(db_pool, owner)
    await _task(db_pool, cho_don_vi, "T1", "create_maintenance_request", "WAITING_APPROVAL")
    await _service_approval(db_pool, cho_don_vi, "T1", "create_maintenance_request", owner=owner)

    cho_khach = await _workflow(db_pool, owner)
    await _task(db_pool, cho_khach, "T1", "pay_fee", "WAITING_APPROVAL")
    await db_pool.execute(
        "INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status) "
        "VALUES ($1::uuid,'T1','BOOK-X',100000,'VND','AWAITING')",
        cho_khach,
    )

    items = {i["workflow_id"]: i for i in (await client.get(LIST, headers=_auth(admin))).json()["items"]}
    assert items[cho_don_vi]["waiting_for"] == "PROVIDER"
    assert items[cho_khach]["waiting_for"] == "CUSTOMER_PAYMENT"


@pytest.mark.asyncio
async def test_the_detail_page_names_the_unit_that_signed_and_when(client, db_pool):
    admin, _ = await _user(client, db_pool, "giam_sat_admin_3", role="admin")
    _, owner = await _user(client, db_pool, "giam_sat_chu_3")
    wid = await _workflow(db_pool, owner, status="FAILED")
    await _task(db_pool, wid, "T1", "create_maintenance_request", "CANCELLED")
    await _service_approval(
        db_pool, wid, "T1", "create_maintenance_request", status="REJECTED", owner=owner, decided_by="don-vi-bao-tri"
    )

    response = await client.get(f"{LIST}/{wid}", headers=_auth(admin))
    assert response.status_code == 200, response.text
    body = response.json()

    step = body["steps"][0]
    assert step["service_name"] == "Yêu cầu bảo trì"
    assert step["status"] == "CANCELLED"
    assert step["approval_status"] == "REJECTED"
    assert step["decided_by"]["username"] == "don-vi-bao-tri", "không nói được ĐƠN VỊ nào đã quyết định"
    assert step["decided_at"], "quyết định không có mốc thời gian"
    assert body["workflow_status"] == "FAILED"


@pytest.mark.asyncio
async def test_a_request_that_does_not_exist_is_404_not_a_lookup_oracle(client, db_pool):
    admin, _ = await _user(client, db_pool, "giam_sat_admin_4", role="admin")
    response = await client.get(f"{LIST}/{uuid.uuid4()}", headers=_auth(admin))
    assert response.status_code == 404
    assert "không tìm thấy" in response.json()["detail"].lower()


# --- không rò bí mật --------------------------------------------------------


@pytest.mark.asyncio
async def test_secrets_never_leave_through_the_monitoring_surface(client, db_pool):
    """Log giám sát là nơi bí mật sống lâu nhất — nó bị dán vào issue và CI.

    Lọc theo HÌNH DẠNG chứ không theo tên trường: lọc theo danh sách trường thì
    mỗi lần thêm một trường mới là một lần có thể quên.
    """
    admin, _ = await _user(client, db_pool, "giam_sat_admin_5", role="admin")
    _, owner = await _user(client, db_pool, "giam_sat_chu_5")
    doc = "postgresql://p118:matkhau@postgres:5432/p118_db"
    khoa = "api_key=sk-abcdefghijklmnopqrstuvwx"  # secret-fixture
    wid = await _workflow(db_pool, owner, goal=f"Kết nối {doc} rồi bảo trì", status="FAILED")
    await _task(db_pool, wid, "T1", "create_maintenance_request", "FAILED", error_message=f"provider trả lỗi {khoa}")

    raw_list = (await client.get(LIST, headers=_auth(admin))).text
    raw_detail = (await client.get(f"{LIST}/{wid}", headers=_auth(admin))).text

    for blob in (raw_list, raw_detail):
        assert "matkhau" not in blob
        assert "postgresql://" not in blob
        assert "sk-abcdefghijklmnopqrstuvwx" not in blob  # secret-fixture
        assert "[đã ẩn]" in blob


# --- không có động từ ghi ----------------------------------------------------


@pytest.mark.parametrize("method", ["post", "patch", "put", "delete"])
@pytest.mark.asyncio
async def test_the_monitoring_surface_has_no_write_verb(client, db_pool, method):
    """Admin không sửa yêu cầu, không duyệt, không chạy tiếp, không trả tiền hộ."""
    admin, _ = await _user(client, db_pool, f"giam_sat_ghi_{method}", role="admin")
    response = await client.request(method.upper(), LIST, headers=_auth(admin))
    assert response.status_code == 405, f"{method.upper()} {LIST} → {response.status_code}"


@pytest.mark.asyncio
async def test_the_admin_still_cannot_decide_through_the_provider_gate(client, db_pool):
    """Kiểm chéo: mở màn giám sát KHÔNG được mở lại cổng quyết định."""
    admin, _ = await _user(client, db_pool, "giam_sat_admin_6", role="admin")
    _, owner = await _user(client, db_pool, "giam_sat_chu_6")
    wid = await _workflow(db_pool, owner)
    await _task(db_pool, wid, "T1", "create_maintenance_request", "WAITING_APPROVAL")
    await _service_approval(db_pool, wid, "T1", "create_maintenance_request", owner=owner)

    assert (await client.get(f"{LIST}/{wid}", headers=_auth(admin))).status_code == 200
    decide = await client.post(
        f"/api/v1/service-approvals/{wid}/T1/decide", json={"decision": "approve"}, headers=_auth(admin)
    )
    assert decide.status_code == 403
    assert (
        await db_pool.fetchval("SELECT status FROM service_approvals WHERE workflow_id=$1::uuid AND task_id='T1'", wid)
        == "AWAITING"
    )


# --- bảy trạng thái, ba trường -----------------------------------------------


async def _state(db_pool, client, admin, *, wf, task, sa=None, pa=None):
    _, owner = await _user(client, db_pool, f"tt_chu_{uuid.uuid4().hex[:8]}")
    wid = await _workflow(db_pool, owner, status=wf)
    await _task(db_pool, wid, "T1", "create_maintenance_request", task)
    if sa:
        await _service_approval(
            db_pool,
            wid,
            "T1",
            "create_maintenance_request",
            status=sa,
            owner=owner,
            decided_by=None if sa == "AWAITING" else "don-vi",
        )
    if pa:
        await db_pool.execute(
            "INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status) "
            "VALUES ($1::uuid,'T1','BOOK-S',100000,'VND',$2)",
            wid,
            pa,
        )
    body = (await client.get(f"{LIST}/{wid}", headers=_auth(admin))).json()
    row = next(i for i in (await client.get(LIST, headers=_auth(admin))).json()["items"] if i["workflow_id"] == wid)
    return body, row


_TRANG_THAI = [
    (
        "chờ đơn vị",
        dict(wf="WAITING_APPROVAL", task="WAITING_APPROVAL", sa="AWAITING"),
        ("PROVIDER", "AWAITING", "NONE"),
    ),
    ("đơn vị đã duyệt", dict(wf="RUNNING", task="RUNNING", sa="APPROVED"), ("NONE", "APPROVED", "NONE")),
    ("đơn vị từ chối", dict(wf="FAILED", task="CANCELLED", sa="REJECTED"), ("NONE", "REJECTED", "NONE")),
    (
        "chờ khách trả tiền",
        dict(wf="WAITING_APPROVAL", task="WAITING_APPROVAL", sa="APPROVED", pa="AWAITING"),
        ("CUSTOMER_PAYMENT", "APPROVED", "AWAITING"),
    ),
    (
        "khách đã xác nhận",
        dict(wf="SUCCESS", task="SUCCESS", sa="APPROVED", pa="APPROVED"),
        ("NONE", "APPROVED", "APPROVED"),
    ),
    (
        "khách từ chối trả",
        dict(wf="CANCELLED", task="CANCELLED", sa="APPROVED", pa="REJECTED"),
        ("NONE", "APPROVED", "REJECTED"),
    ),
    ("hoàn tất, không duyệt", dict(wf="SUCCESS", task="SUCCESS"), ("NONE", "NONE", "NONE")),
]


@pytest.mark.parametrize("ten,seed,mong_doi", _TRANG_THAI, ids=[t[0].replace(" ", "-") for t in _TRANG_THAI])
@pytest.mark.asyncio
async def test_the_three_status_fields_stay_independent(client, db_pool, ten, seed, mong_doi):
    """Ba câu hỏi khác nhau, ba trường khác nhau.

    Bản đầu dùng chung một `approval_status`, và nó về `NONE` ngay sau khi đơn
    vị DUYỆT — không phân biệt được với một yêu cầu chưa ai đụng tới. Lịch sử
    quyết định biến mất khỏi màn danh sách đúng lúc nó có ý nghĩa nhất.
    """
    admin, _ = await _user(client, db_pool, f"tt_admin_{abs(hash(ten)) % 100000}", role="admin")
    chi_tiet, danh_sach = await _state(db_pool, client, admin, **seed)

    doc = (
        danh_sach["waiting_for"],
        danh_sach["provider_decision_status"],
        danh_sach["payment_decision_status"],
    )
    assert doc == mong_doi, f"{ten}: {doc}"
    # Danh sách và chi tiết phải kể CÙNG một câu chuyện.
    assert (
        chi_tiet["waiting_for"],
        chi_tiet["provider_decision_status"],
        chi_tiet["payment_decision_status"],
    ) == mong_doi


# --- tool lạ không được lộ ---------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_tool_never_leaks_its_internal_name(client, db_pool):
    """Bảng nhãn thiếu một mục là chuyện sẽ xảy ra. Lúc đó API phải nói "chưa
    xác định", không phơi tên hàm nội bộ ra ảnh chụp màn hình."""
    admin, _ = await _user(client, db_pool, "tool_la_admin", role="admin")
    _, owner = await _user(client, db_pool, "tool_la_chu")
    wid = await _workflow(db_pool, owner)
    await _task(db_pool, wid, "T1", "register_vehicle", "SUCCESS")
    # Tool chưa có trong bảng nhãn — chèn thẳng để không phụ thuộc enum ứng dụng.
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
        "VALUES ($1::uuid,'T2','noi_bo_bi_mat_v2','PENDING','[]'::jsonb)",
        wid,
    )

    danh_sach = await client.get(LIST, headers=_auth(admin))
    chi_tiet = await client.get(f"{LIST}/{wid}", headers=_auth(admin))

    for response in (danh_sach, chi_tiet):
        assert response.status_code == 200, response.text
        assert "noi_bo_bi_mat_v2" not in response.text, "tên tool nội bộ rò ra màn giám sát"

    item = next(i for i in danh_sach.json()["items"] if i["workflow_id"] == wid)
    assert "Dịch vụ chưa xác định" in item["service_names"]
    assert item["current_step"] == "Dịch vụ chưa xác định"
    # Tool canonical vẫn phải có nhãn tiếng Việt đúng.
    assert "Đăng ký phương tiện" in item["service_names"]
    buoc = {s["task_id"]: s["service_name"] for s in chi_tiet.json()["steps"]}
    assert buoc == {"T1": "Đăng ký phương tiện", "T2": "Dịch vụ chưa xác định"}
