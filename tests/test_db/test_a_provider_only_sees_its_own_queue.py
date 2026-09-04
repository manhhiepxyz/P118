"""Đơn vị cung cấp chỉ thấy và chỉ quyết định được việc CỦA MÌNH.

Vì sao luật này đổi
-------------------
`test_only_the_provider_decides.py` ghi rõ giả định cũ:

    "Kiểm ROLE, không phải quyền sở hữu: người duyệt được quyết định TOÀN BỘ
     hàng đợi chứ không riêng phần 'của mình', nên đây không phải IDOR."

Giả định ấy ĐÚNG khi hệ thống chỉ có một đơn vị cung cấp — mọi tài khoản
`provider` đều nhân danh cùng một tổ chức, nên "toàn bộ hàng đợi" chính là
"phần của mình".

Nó HẾT đúng từ lúc có nhiều đơn vị. Khi P-118 đề xuất "đội Đại Tín" cho một
yêu cầu chuyển nhà mà bất kỳ tài khoản provider nào cũng bấm duyệt được, thì
việc chọn đơn vị chỉ tồn tại trên dữ liệu — **không tồn tại trong nghiệp vụ**.
Và lúc đó nó ĐÚNG là IDOR: một tổ chức đọc và quyết định trên đơn hàng của tổ
chức khác.

Bốn luật được khoá ở đây
------------------------
1. Danh sách lọc theo đơn vị của tài khoản đang đăng nhập.
2. `decide` kiểm quyền sở hữu ĐỘC LẬP — không tin rằng "nó xuất hiện trong
   danh sách nên chắc là của mình". Hai đường đọc khác nhau thì sớm muộn lệch.
3. Đơn vị KHÔNG BAO GIỜ đến từ request. Nó đến từ tài khoản và từ bản ghi.
4. Dòng cũ chưa có đơn vị (`service_provider_id IS NULL`) là FAIL-CLOSED:
   không provider nào thấy. Mặc định "ai cũng thấy" biến mọi dòng lịch sử
   thành một lỗ hổng ngay khi migration chạy.

Một tài khoản quản lý được NHIỀU đơn vị, và một đơn vị có NHIỀU nhân viên —
nên quan hệ nằm ở bảng liên kết riêng, không phải một cột trên `users`. Dùng
role làm danh tính đơn vị thì thêm nhân viên thứ hai là phải đổi schema.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from src.orchestration.provider_directory import don_vi_mac_dinh
from src.orchestration.service_approval import save_pending_service_approvals
from tests.test_db.conftest import _register_and_login

SERVICE = "/api/v1/service-approvals"

MOV_01 = "MOV-01"
MOV_02 = "MOV-02"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _tai_khoan(client, db_pool, username: str, role: str | None = None) -> tuple[str, str]:
    """Token cấp SAU khi role đã đổi — `require_roles` đọc role qua JWT."""
    await _register_and_login(client, username)
    if role is not None:
        await db_pool.execute("UPDATE users SET role = $2 WHERE username = $1", username, role)
    token = await _register_and_login(client, username)
    user_id = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    return token, str(user_id)


async def _gan_don_vi(db_pool, user_id: str, service_provider_id: str) -> None:
    await db_pool.execute(
        "INSERT INTO service_provider_accounts (user_id, service_provider_id) "
        "VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING",
        user_id,
        service_provider_id,
    )


async def _yeu_cau(db_pool, owner_user_id: str, service_provider_id: str | None) -> str:
    """Một dòng chờ duyệt. `None` = dòng cũ có trước khi có khái niệm đơn vị."""
    wid = str(uuid.uuid4())
    # Gieo ĐỦ như đường thật: workflow + task_plan + workflow_tasks.
    #
    # Bản đầu chỉ gieo dòng `service_approvals`, nên khi quyền sở hữu cho đi
    # tiếp thì lượt resume vỡ ở chỗ dựng lại TaskPlan từ `workflow_tasks` —
    # một bài kiểm hỏng vì thiếu dữ liệu, trông y hệt một sản phẩm hỏng.
    ke_hoach = {
        "goal": "chuyển nhà",
        "tasks": [
            {
                "task_id": "T1",
                "tool": "schedule_move",
                "depends_on": [],
                "input": {
                    "move_date": "2026-09-30",
                    "move_time": "08:00",
                    # Ba ô BẮT BUỘC từ khi giá tính theo quãng đường và khối
                    # lượng. Thiếu chúng thì Validator từ chối cả kế hoạch, và
                    # bài kiểm đỏ ở một chỗ không liên quan tới quyền sở hữu.
                    "move_origin_id": "MOVE-Q7-A1",
                    "move_destination_id": "MOVE-Q7-B1",
                    "move_size": "medium",
                    "move_vehicle": "van",
                    "needs_elevator": False,
                    "needs_loading_support": False,
                },
            }
        ],
    }
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, task_plan) "
        "VALUES ($1::uuid, 'chuyển nhà', 'WAITING_APPROVAL', $2::uuid, $3::jsonb)",
        wid,
        owner_user_id,
        json.dumps(ke_hoach),
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', 'schedule_move', 'WAITING_APPROVAL', '[]'::jsonb, $2::jsonb)",
        wid,
        json.dumps(ke_hoach["tasks"][0]["input"]),
    )
    await db_pool.execute(
        "INSERT INTO service_approvals "
        "(workflow_id, task_id, tool, service_label, status, service_provider_id) "
        "VALUES ($1::uuid, 'T1', 'schedule_move', 'Chuyển nhà', 'AWAITING', $2)",
        wid,
        service_provider_id,
    )
    return wid


def _ma_workflow(body: dict) -> set[str]:
    muc = body.get("items") or body.get("approvals") or []
    return {m["workflow_id"] for m in muc}


# ---------------------------------------------------------------- danh sách
@pytest.mark.asyncio
async def test_each_provider_sees_only_its_own_queue(client, db_pool):
    khach, khach_id = await _tai_khoan(client, db_pool, "chu_yeu_cau")
    tok_a, id_a = await _tai_khoan(client, db_pool, "donvi_a", role="provider")
    tok_b, id_b = await _tai_khoan(client, db_pool, "donvi_b", role="provider")
    await _gan_don_vi(db_pool, id_a, MOV_01)
    await _gan_don_vi(db_pool, id_b, MOV_02)

    cua_a = await _yeu_cau(db_pool, khach_id, MOV_01)
    cua_b = await _yeu_cau(db_pool, khach_id, MOV_02)

    thay_a = _ma_workflow((await client.get(SERVICE, headers=_auth(tok_a))).json())
    thay_b = _ma_workflow((await client.get(SERVICE, headers=_auth(tok_b))).json())

    assert cua_a in thay_a and cua_b not in thay_a, f"đơn vị A thấy {thay_a}"
    assert cua_b in thay_b and cua_a not in thay_b, f"đơn vị B thấy {thay_b}"


@pytest.mark.asyncio
async def test_a_provider_with_no_mapping_sees_an_empty_queue(client, db_pool):
    """Chưa gắn đơn vị nào thì KHÔNG thấy gì — fail-closed, không phải thấy hết."""
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_2")
    tok, _ = await _tai_khoan(client, db_pool, "donvi_chua_gan", role="provider")
    await _yeu_cau(db_pool, khach_id, MOV_01)

    assert _ma_workflow((await client.get(SERVICE, headers=_auth(tok))).json()) == set()


@pytest.mark.asyncio
async def test_a_legacy_row_without_a_provider_is_hidden_from_every_provider(client, db_pool):
    """Dòng cũ `service_provider_id IS NULL` không thuộc về ai.

    Mặc định "ai cũng thấy" biến mọi dòng lịch sử thành lỗ hổng ngay khi
    migration chạy — và đó là loại lỗi không ai để ý vì màn hình trông vẫn đúng.
    """
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_3")
    tok, id_a = await _tai_khoan(client, db_pool, "donvi_cu", role="provider")
    await _gan_don_vi(db_pool, id_a, MOV_01)
    cu = await _yeu_cau(db_pool, khach_id, None)

    assert cu not in _ma_workflow((await client.get(SERVICE, headers=_auth(tok))).json())


@pytest.mark.asyncio
async def test_one_account_can_hold_several_providers(client, db_pool):
    """Bảng liên kết, không phải một cột — nên nhiều đơn vị trên một tài khoản."""
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_4")
    tok, uid = await _tai_khoan(client, db_pool, "donvi_kiem_nhiem", role="provider")
    await _gan_don_vi(db_pool, uid, MOV_01)
    await _gan_don_vi(db_pool, uid, MOV_02)

    a = await _yeu_cau(db_pool, khach_id, MOV_01)
    b = await _yeu_cau(db_pool, khach_id, MOV_02)
    thay = _ma_workflow((await client.get(SERVICE, headers=_auth(tok))).json())
    assert {a, b} <= thay


@pytest.mark.asyncio
async def test_the_queue_says_which_unit_holds_each_row(client, db_pool):
    """Mỗi dòng mang mã VÀ tên đơn vị giữ nó.

    Một tài khoản kiêm nhiệm nhiều đơn vị đọc một hàng đợi trộn lẫn. Không có
    hai trường này thì màn hình không chia được theo đơn vị, và người duyệt bấm
    Duyệt mà không biết mình đang quyết định nhân danh đội nào.

    Tên tính ở BACKEND. Để giao diện tự map mã → tên là dựng một bảng tên thứ
    hai, và bảng thứ hai là bảng sẽ lệch khi danh mục đơn vị đổi.
    """
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_ten")
    tok, uid = await _tai_khoan(client, db_pool, "donvi_doc_ten", role="provider")
    await _gan_don_vi(db_pool, uid, MOV_01)
    await _gan_don_vi(db_pool, uid, MOV_02)
    a = await _yeu_cau(db_pool, khach_id, MOV_01)
    b = await _yeu_cau(db_pool, khach_id, MOV_02)

    muc = {m["workflow_id"]: m for m in (await client.get(SERVICE, headers=_auth(tok))).json()["items"]}

    assert muc[a]["service_provider_id"] == MOV_01
    assert muc[b]["service_provider_id"] == MOV_02
    # Tên THẬT, không phải chính cái mã in lại.
    assert muc[a]["service_provider_name"] not in (None, "", MOV_01), muc[a]["service_provider_name"]
    assert muc[a]["service_provider_name"] != muc[b]["service_provider_name"]


@pytest.mark.asyncio
async def test_the_total_counts_only_this_accounts_units(client, db_pool):
    """`total` đếm phần của TÀI KHOẢN NÀY, không phải cả bảng.

    Trước bản sửa, câu đếm không có mệnh đề đơn vị: một đơn vị có 2 việc đọc
    được "2 / 290". Con số ấy vừa vô nghĩa vừa nguy hiểm — nó nói rằng còn 288
    việc người duyệt chưa nhìn thấy, và không có chỗ nào để bấm xem.
    """
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_tong")
    tok, uid = await _tai_khoan(client, db_pool, "donvi_dem_tong", role="provider")
    await _gan_don_vi(db_pool, uid, MOV_01)
    cua_toi = await _yeu_cau(db_pool, khach_id, MOV_01)
    await _yeu_cau(db_pool, khach_id, MOV_02)  # của đơn vị khác
    await _yeu_cau(db_pool, khach_id, None)  # dòng cũ không chủ

    body = (await client.get(SERVICE, headers=_auth(tok))).json()

    assert body["total"] == len(body["items"]), f"total={body['total']} items={len(body['items'])}"
    assert _ma_workflow(body) == {cua_toi}


@pytest.mark.asyncio
async def test_a_provider_with_no_mapping_is_told_zero_not_everything(client, db_pool):
    """Chưa gắn đơn vị: hàng đợi rỗng VÀ tổng bằng 0.

    Tách khỏi bài trên vì đây là nhánh fail-closed. Một `total` khác 0 trên một
    danh sách rỗng nói rằng có việc đang bị giấu — và người duyệt sẽ đi tìm.
    """
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_tong_0")
    tok, _ = await _tai_khoan(client, db_pool, "donvi_tong_0", role="provider")
    await _yeu_cau(db_pool, khach_id, MOV_01)

    body = (await client.get(SERVICE, headers=_auth(tok))).json()
    assert body["items"] == [] and body["total"] == 0, body["total"]


@pytest.mark.asyncio
async def test_an_admin_does_not_get_a_queue_of_its_own(client, db_pool):
    """Admin KHÔNG vào hàng đợi của đơn vị — kể cả chỉ để xem.

    Bản đầu của bài kiểm này khẳng định ngược lại ("admin vẫn thấy toàn bộ"),
    và nó sai theo một cách đáng ghi lại. Quyền duyệt là quyền NHÂN DANH một
    đơn vị nhận việc; admin không có mặt bằng, không có đội bảo trì, không có
    xe. Cho họ đọc hàng đợi là đặt sẵn dữ liệu để một nút Duyệt mọc lên — nó
    chỉ cách một dòng JSX.

    Nó cũng phá chính công cụ giám sát: nếu người giám sát tự tay giải quyết
    được hàng đợi thì con số "đang chờ đơn vị" không còn đo cái gì.

    Tầm nhìn toàn cục của admin nằm ở `/admin/requests` — bài kiểm ngay dưới.
    """
    tok_admin, _ = await _tai_khoan(client, db_pool, "quan_tri", role="admin")
    assert (await client.get(SERVICE, headers=_auth(tok_admin))).status_code == 403


@pytest.mark.asyncio
async def test_an_admin_sees_which_unit_is_holding_each_step(client, db_pool):
    """Bù lại: `/admin/requests/{id}` nói rõ bước nào đang nằm ở đơn vị nào.

    Không có vế này thì bài kiểm 403 phía trên chỉ là cắt tầm nhìn. "Đang chờ
    ai" là câu hỏi chính của màn giám sát, và sau khi hàng đợi đóng lại thì đây
    là chỗ DUY NHẤT trả lời được nó.
    """
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_5")
    tok_admin, _ = await _tai_khoan(client, db_pool, "quan_tri_xem", role="admin")
    wid = await _yeu_cau(db_pool, khach_id, MOV_02)

    chi_tiet = await client.get(f"/api/v1/admin/requests/{wid}", headers=_auth(tok_admin))
    assert chi_tiet.status_code == 200, chi_tiet.text
    buoc = [b for b in chi_tiet.json()["steps"] if b.get("service_provider")]
    assert buoc, "màn giám sát không nói được bước nào đang nằm ở đơn vị nào"
    assert buoc[0]["service_provider"]["id"] == MOV_02
    # Mã ĐI KÈM tên: mã để đối chiếu log, tên để gọi điện. Trả mỗi mã thì admin
    # phải tra bảng trong đầu.
    assert buoc[0]["service_provider"]["name"] == "Vận tải Đại Tín"


@pytest.mark.asyncio
async def test_an_admin_can_tell_an_ownerless_row_from_a_hidden_one(client, db_pool):
    """Dòng legacy (chưa có đơn vị) phải NHÌN RA ĐƯỢC là chưa có đơn vị.

    Nó vô hình với mọi provider — đúng ý fail-closed — nên nếu màn giám sát
    cũng vẽ nó y hệt một dòng bình thường thì không ai phát hiện ra rằng có một
    yêu cầu không ai duyệt được. `None` ở đây là một câu trả lời, và nó phải
    khác với "có đơn vị".
    """
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_legacy")
    tok_admin, _ = await _tai_khoan(client, db_pool, "quan_tri_legacy", role="admin")
    wid = await _yeu_cau(db_pool, khach_id, None)

    chi_tiet = await client.get(f"/api/v1/admin/requests/{wid}", headers=_auth(tok_admin))
    assert chi_tiet.status_code == 200, chi_tiet.text
    assert all(b["service_provider"] is None for b in chi_tiet.json()["steps"])


@pytest.mark.asyncio
async def test_a_customer_never_reaches_the_queue(client, db_pool):
    tok, _ = await _tai_khoan(client, db_pool, "cu_dan_thuong")
    assert (await client.get(SERVICE, headers=_auth(tok))).status_code in (401, 403)


# ---------------------------------------------------------------- quyết định
@pytest.mark.asyncio
async def test_a_provider_cannot_decide_another_providers_request(client, db_pool):
    """Kiểm quyền sở hữu ĐỘC LẬP ở `decide`.

    Không được tin "nó không có trong danh sách nên chắc không quyết định
    được": danh sách và quyết định là hai đường đọc khác nhau, và hai đường
    khác nhau thì sớm muộn lệch. Kẻ tấn công không đi qua danh sách.
    """
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_6")
    tok_a, id_a = await _tai_khoan(client, db_pool, "donvi_a2", role="provider")
    _, id_b = await _tai_khoan(client, db_pool, "donvi_b2", role="provider")
    await _gan_don_vi(db_pool, id_a, MOV_01)
    await _gan_don_vi(db_pool, id_b, MOV_02)

    cua_b = await _yeu_cau(db_pool, khach_id, MOV_02)
    res = await client.post(f"{SERVICE}/{cua_b}/T1/decide", json={"decision": "approve"}, headers=_auth(tok_a))
    assert res.status_code in (403, 404), f"đơn vị A duyệt được việc của B: {res.status_code}"

    trang_thai = await db_pool.fetchval(
        "SELECT status FROM service_approvals WHERE workflow_id = $1::uuid AND task_id = 'T1'", cua_b
    )
    assert trang_thai == "AWAITING", f"trạng thái đã bị đổi thành {trang_thai}"


@pytest.mark.asyncio
async def test_a_provider_with_no_mapping_cannot_decide_anything(client, db_pool):
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_7")
    tok, _ = await _tai_khoan(client, db_pool, "donvi_chua_gan_2", role="provider")
    wid = await _yeu_cau(db_pool, khach_id, MOV_01)

    res = await client.post(f"{SERVICE}/{wid}/T1/decide", json={"decision": "approve"}, headers=_auth(tok))
    assert res.status_code in (403, 404)


@pytest.mark.asyncio
async def test_a_legacy_row_cannot_be_decided_by_a_provider(client, db_pool):
    """Fail-closed cũng phải đúng ở đường quyết định, không riêng đường đọc."""
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_8")
    tok, uid = await _tai_khoan(client, db_pool, "donvi_a3", role="provider")
    await _gan_don_vi(db_pool, uid, MOV_01)
    cu = await _yeu_cau(db_pool, khach_id, None)

    res = await client.post(f"{SERVICE}/{cu}/T1/decide", json={"decision": "approve"}, headers=_auth(tok))
    assert res.status_code in (403, 404)


@pytest.mark.asyncio
async def test_re_pinning_a_step_gives_it_an_owner_again(client, db_pool):
    """Ghim LẠI là một yêu cầu MỚI, nên nó phải mang chủ sở hữu MỚI.

    Ca thật đằng sau bài kiểm này: một dòng legacy (chưa có đơn vị) bị khách
    sửa và chạy lại. Nếu `ON CONFLICT` giữ nguyên `service_provider_id` cũ thì
    yêu cầu vừa sửa vẫn vô chủ — vô hình với mọi provider, không ai duyệt được,
    và lần này thì KHÔNG phải dữ liệu cũ nữa mà là một việc khách vừa nhờ.

    Cùng mệnh đề ấy còn là chỗ bước B móc vào: đổi ngày có thể đổi sang đơn vị
    khác còn lịch, và giữ chủ cũ nghĩa là bắt đơn vị A quyết định một việc đã
    chuyển sang cho B.
    """
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_ghim_lai")
    wid = await _yeu_cau(db_pool, khach_id, None)

    await save_pending_service_approvals(
        db_pool,
        workflow_id=wid,
        rows=[{"task_id": "T1", "tool": "schedule_move", "service_label": "Chuyển nhà", "details": {}}],
    )

    chu = await db_pool.fetchval(
        "SELECT service_provider_id FROM service_approvals WHERE workflow_id=$1::uuid AND task_id='T1'",
        wid,
    )
    assert chu == don_vi_mac_dinh("schedule_move"), f"ghim lại xong vẫn là {chu!r}"


@pytest.mark.asyncio
async def test_the_provider_identity_never_comes_from_the_request(client, db_pool):
    """Gửi kèm `service_provider_id` trong body không mở được cửa nào.

    Danh tính đơn vị đến từ tài khoản và từ bản ghi approval. Nhận nó từ
    request là để người gọi tự khai mình là ai.
    """
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_9")
    tok_a, id_a = await _tai_khoan(client, db_pool, "donvi_a4", role="provider")
    await _gan_don_vi(db_pool, id_a, MOV_01)
    cua_b = await _yeu_cau(db_pool, khach_id, MOV_02)

    res = await client.post(
        f"{SERVICE}/{cua_b}/T1/decide",
        json={"decision": "approve", "service_provider_id": MOV_02},
        headers=_auth(tok_a),
    )
    assert res.status_code in (403, 404, 422), f"body tự khai đơn vị mà vẫn qua: {res.status_code}"
    assert (
        await db_pool.fetchval(
            "SELECT status FROM service_approvals WHERE workflow_id = $1::uuid AND task_id = 'T1'",
            cua_b,
        )
        == "AWAITING"
    )


@pytest.mark.asyncio
async def test_the_owner_can_still_decide_its_own_request(client, db_pool):
    """Đừng khoá nhầm đường đúng — đơn vị sở hữu vẫn phải quyết định được."""
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_10")
    tok, uid = await _tai_khoan(client, db_pool, "donvi_so_huu", role="provider")
    await _gan_don_vi(db_pool, uid, MOV_01)
    wid = await _yeu_cau(db_pool, khach_id, MOV_01)

    res = await client.post(f"{SERVICE}/{wid}/T1/decide", json={"decision": "approve"}, headers=_auth(tok))
    assert res.status_code < 400, f"chủ sở hữu bị chặn: {res.status_code} {res.text}"
    assert (
        await db_pool.fetchval(
            "SELECT status FROM service_approvals WHERE workflow_id = $1::uuid AND task_id = 'T1'",
            wid,
        )
        == "APPROVED"
    )


# ---------------------------------------------------------------- bền vững
@pytest.mark.asyncio
async def test_the_mapping_lives_in_postgresql_not_in_memory(client, db_pool):
    """Restart không được làm mất quyền sở hữu.

    Kiểm bằng cách đọc thẳng database: nếu mapping nằm trong RAM của tiến
    trình thì nó không có ở đây, và một lần khởi động lại là mọi đơn vị mất
    hàng đợi của mình.
    """
    _, uid = await _tai_khoan(client, db_pool, "donvi_ben_vung", role="provider")
    await _gan_don_vi(db_pool, uid, MOV_01)
    assert await db_pool.fetchval("SELECT count(*) FROM service_provider_accounts WHERE user_id = $1::uuid", uid) == 1


@pytest.mark.asyncio
async def test_two_simultaneous_decisions_leave_exactly_one_winner(client, db_pool):
    """Hai nhân viên của CÙNG đơn vị bấm cùng lúc — chỉ một lệnh được ăn.

    Quyền sở hữu không được làm mất luật một-lượt-một-quyết-định đang có
    (`WHERE status='AWAITING'` ở tầng SQL).
    """
    _, khach_id = await _tai_khoan(client, db_pool, "chu_yc_11")
    tok1, uid1 = await _tai_khoan(client, db_pool, "nhan_vien_1", role="provider")
    tok2, uid2 = await _tai_khoan(client, db_pool, "nhan_vien_2", role="provider")
    await _gan_don_vi(db_pool, uid1, MOV_01)
    await _gan_don_vi(db_pool, uid2, MOV_01)
    wid = await _yeu_cau(db_pool, khach_id, MOV_01)

    res1, res2 = await asyncio.gather(
        client.post(f"{SERVICE}/{wid}/T1/decide", json={"decision": "approve"}, headers=_auth(tok1)),
        client.post(
            f"{SERVICE}/{wid}/T1/decide",
            json={"decision": "reject", "reject_code": "NO_AVAILABILITY", "reject_reason": "bận"},
            headers=_auth(tok2),
        ),
        return_exceptions=True,
    )
    ma = [r.status_code for r in (res1, res2) if hasattr(r, "status_code")]
    assert sum(1 for m in ma if m < 400) == 1, f"số lệnh thành công phải là 1, nhận {ma}"
