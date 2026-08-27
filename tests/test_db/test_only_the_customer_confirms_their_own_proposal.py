"""Hợp đồng HTTP của lượt xác nhận: hai thứ đi vào, không gì khác.

Client gửi ĐÚNG mã đề xuất (đường dẫn) và quyết định (body). Không provider,
không số tiền, không tên đơn vị. Mọi dữ kiện khác đọc từ database bên trong
transaction — vì mọi thứ nhận từ body là thứ người gọi tự khai, và một trường
được nhận thì sớm muộn sẽ có người tin nó.

Ba vai, một nút:

    customer   bấm đồng ý với đề xuất CỦA MÌNH
    provider   duyệt ở `/service-approvals`, không bấm hộ khách
    admin      giám sát ở `/admin/requests`, không bấm hộ ai

Và `approval_actor` phải SUY RA đúng ở cả hai phía của lượt bấm: `USER` trước,
`PROVIDER` sau. Không cột nào ghi nó — đó là điểm chính, vì một cột thứ hai sẽ
đứng im đúng lúc việc đổi tay.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.db.proposal_repository import doc_de_xuat, ghim_de_xuat
from src.db.quote_repository import luu_bao_gia
from src.orchestration.quote import van_tay_yeu_cau
from tests.test_db.conftest import _register_and_login, dang_nhap_don_vi

DICH_VU = "schedule_move"
YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}
VAN_TAY = van_tay_yeu_cau(YEU_CAU)
GOC = "/api/v1/service-proposals"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _dat_hang(client, db_pool, ten_khach: str):
    """Một khách thật, một workflow thật, một chứng từ thật, một đề xuất đang chờ."""
    token = await _register_and_login(client, ten_khach)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", ten_khach)
    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'chuyển nhà', 'PENDING', $2)",
        wid,
        uid,
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', 'schedule_move', 'PENDING', '[]'::jsonb, $2::jsonb)",
        wid,
        json.dumps({**YEU_CAU, "max_price": 450_000}),
    )
    bao_gia = await luu_bao_gia(
        db_pool,
        external_quote_id=f"Q-{uuid.uuid4().hex[:10]}",
        service_provider_id="MOV-02",
        service_type=DICH_VU,
        amount=470_000,
        currency="VND",
        request_fingerprint=VAN_TAY,
        valid_until=datetime.now(UTC) + timedelta(minutes=30),
        workflow_id=wid,
        task_id="T1",
    )
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    return token, wid, bao_gia, de_xuat


# ------------------------------------------------------------------ đường vào
@pytest.mark.asyncio
async def test_an_anonymous_caller_cannot_confirm(client, db_pool):
    _, _, _, de_xuat = await _dat_hang(client, db_pool, "kh_an_danh")
    res = await client.post(f"{GOC}/{de_xuat.proposal_id}/confirm", json={"decision": "confirm"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_the_owner_confirms_and_the_queue_opens(client, db_pool):
    """Kiểm DƯƠNG. Thiếu nó thì mọi 401/403/404 bên dưới có thể đúng vì route hỏng."""
    token, wid, bao_gia, de_xuat = await _dat_hang(client, db_pool, "kh_chu_that")

    res = await client.post(
        f"{GOC}/{de_xuat.proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(token)
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "CONFIRMED"
    assert body["provider"] == {"id": "MOV-02", "name": "Vận tải Đại Tín"}
    assert body["amount"] == 470_000 and body["currency"] == "VND"
    # Người chờ đã đổi tay, và điều đó được SUY RA — không cột nào ghi nó.
    assert body["approval_actor"] == "PROVIDER"
    dong = await db_pool.fetch(
        "SELECT status, service_provider_id FROM service_approvals WHERE workflow_id = $1::uuid", uuid.UUID(wid)
    )
    assert [(r["status"], r["service_provider_id"]) for r in dong] == [("AWAITING", "MOV-02")]
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(bao_gia.quote_id))
        == "CONFIRMED"
    )


# ------------------------------------------------------------------ ba vai
@pytest.mark.asyncio
async def test_another_customer_gets_404_not_403(client, db_pool):
    """404 chứ không 403: 403 xác nhận rằng mã ấy có thật."""
    _, wid, _, de_xuat = await _dat_hang(client, db_pool, "kh_chu_cua_don")
    khach_khac = await _register_and_login(client, "kh_nguoi_la")

    res = await client.post(
        f"{GOC}/{de_xuat.proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(khach_khac)
    )

    assert res.status_code == 404, res.text
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED"
    assert (
        await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid))
        == 0
    )


@pytest.mark.asyncio
async def test_another_customer_cannot_even_read_it(client, db_pool):
    """Không xem được cũng phải là 404 — cùng lý do, và cùng một câu trả lời."""
    _, _, _, de_xuat = await _dat_hang(client, db_pool, "kh_chu_doc")
    khach_khac = await _register_and_login(client, "kh_nguoi_la_doc")

    assert (await client.get(f"{GOC}/{de_xuat.proposal_id}", headers=_auth(khach_khac))).status_code == 404
    khong_co = await client.get(f"{GOC}/{uuid.uuid4()}", headers=_auth(khach_khac))
    assert khong_co.status_code == 404, "hai tình huống phải không phân biệt được từ bên ngoài"


@pytest.mark.asyncio
async def test_a_provider_does_not_confirm_on_behalf_of_the_customer(client, db_pool):
    """Đơn vị có bề mặt riêng (`/service-approvals`). Khoản tiền là của KHÁCH."""
    _, wid, _, de_xuat = await _dat_hang(client, db_pool, "kh_chu_vs_dv")
    tok_dv, _ = await dang_nhap_don_vi(client, db_pool, "dv_bam_ho")

    res = await client.post(
        f"{GOC}/{de_xuat.proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(tok_dv)
    )

    assert res.status_code == 403, res.text
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED"
    assert (
        await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid))
        == 0
    )


@pytest.mark.asyncio
async def test_an_admin_does_not_confirm_on_behalf_of_anyone(client, db_pool):
    _, _, _, de_xuat = await _dat_hang(client, db_pool, "kh_chu_vs_admin")
    await _register_and_login(client, "qt_bam_ho")
    await db_pool.execute("UPDATE users SET role='admin' WHERE username='qt_bam_ho'")
    tok = await _register_and_login(client, "qt_bam_ho")

    res = await client.post(
        f"{GOC}/{de_xuat.proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(tok)
    )

    assert res.status_code == 403, res.text
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED"


# --------------------------------------------------------- body không quyết gì
@pytest.mark.asyncio
async def test_a_body_that_names_a_provider_or_a_price_is_refused(client, db_pool):
    """Gửi kèm đơn vị hay số tiền → 422, KHÔNG phải "bỏ qua trường thừa".

    Bỏ qua lặng lẽ nghĩa là lần sau ai đó nối chúng vào thì không ai thấy gì
    đổi — và lúc ấy client quyết định được đơn vị nào nhận việc.
    """
    token, wid, _, de_xuat = await _dat_hang(client, db_pool, "kh_body_gia")

    for than in (
        {"decision": "confirm", "service_provider_id": "MOV-03"},
        {"decision": "confirm", "amount": 1_000},
        {"decision": "confirm", "currency": "USD"},
        {"decision": "confirm", "quote_id": str(uuid.uuid4())},
        {"decision": "confirm", "owner_user_id": str(uuid.uuid4())},
    ):
        res = await client.post(f"{GOC}/{de_xuat.proposal_id}/confirm", json=than, headers=_auth(token))
        assert res.status_code == 422, f"{than} → {res.status_code}"

    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED"
    assert (
        await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid))
        == 0
    )


@pytest.mark.asyncio
async def test_a_decision_other_than_confirm_is_refused(client, db_pool):
    """Từ chối một đề xuất là XIN BÁO GIÁ KHÁC, không phải một quyết định ở đây.

    Nhận `decision="reject"` rồi không làm gì là để lại một endpoint trả 200
    cho một việc nó không làm — và người bấm tin là đã từ chối.
    """
    token, _, _, de_xuat = await _dat_hang(client, db_pool, "kh_tu_choi")
    for quyet in ("reject", "cancel", "", "CONFIRM"):
        res = await client.post(
            f"{GOC}/{de_xuat.proposal_id}/confirm", json={"decision": quyet}, headers=_auth(token)
        )
        assert res.status_code == 422, f"{quyet!r} → {res.status_code}"


# ------------------------------------------------------------- lượt bấm thứ hai
@pytest.mark.asyncio
async def test_pressing_twice_gets_409_and_leaves_one_approval(client, db_pool):
    token, wid, _, de_xuat = await _dat_hang(client, db_pool, "kh_bam_hai_lan")

    dau = await client.post(
        f"{GOC}/{de_xuat.proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(token)
    )
    lai = await client.post(
        f"{GOC}/{de_xuat.proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(token)
    )

    assert (dau.status_code, lai.status_code) == (200, 409)
    assert (
        await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid))
        == 1
    )


@pytest.mark.asyncio
async def test_an_expired_quote_is_409_with_a_reason_the_customer_can_act_on(client, db_pool):
    """Hết hạn → 409, đề xuất EXPIRED, và KHÔNG dòng duyệt nào.

    Câu chữ phải mời khách làm việc TIẾP THEO đúng (xin giá mới), chứ không mời
    họ bấm lại — bấm lại bao nhiêu lần cũng hỏng.
    """
    token, wid, bao_gia, de_xuat = await _dat_hang(client, db_pool, "kh_qua_han")
    await db_pool.execute(
        "UPDATE service_quotes SET valid_until = NOW() - INTERVAL '1 min' WHERE quote_id = $1::uuid",
        uuid.UUID(bao_gia.quote_id),
    )

    res = await client.post(
        f"{GOC}/{de_xuat.proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(token)
    )

    assert res.status_code == 409, res.text
    assert "hết hiệu lực" in res.json()["detail"]
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "EXPIRED"
    assert (
        await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid))
        == 0
    )


# ------------------------------------------------ ai đang chờ, trước và sau
@pytest.mark.asyncio
async def test_the_reader_sees_user_before_and_provider_after(client, db_pool):
    """`approval_actor` suy ra đúng ở CẢ HAI phía của lượt bấm.

    Trước: khách còn phải làm gì đó → đề xuất `PROPOSED`, và tầng dựng câu trả
    lời đọc ra `USER`. Sau: hàng đợi đơn vị đã mở → `PROVIDER`.

    Không cột nào ghi điều này. Bài kiểm khoá cả hai đầu để một bản vá sau này
    không "tiện tay" thêm một cột `approval_actor` — bản sao thứ hai sẽ đứng im
    đúng lúc việc đổi tay.
    """
    token, wid, _, de_xuat = await _dat_hang(client, db_pool, "kh_ai_dang_cho")

    truoc = await client.get(f"{GOC}/{de_xuat.proposal_id}", headers=_auth(token))
    assert truoc.status_code == 200, truoc.text
    assert truoc.json()["status"] == "PROPOSED"
    assert (
        await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id=$1::uuid", uuid.UUID(wid))
        == 0
    ), "chưa bấm mà hàng đợi đơn vị đã mở"

    sau = await client.post(
        f"{GOC}/{de_xuat.proposal_id}/confirm", json={"decision": "confirm"}, headers=_auth(token)
    )
    assert sau.json()["approval_actor"] == "PROVIDER"
    assert sau.json()["waiting_for"] == "provider"

    doc_lai = await client.get(f"{GOC}/{de_xuat.proposal_id}", headers=_auth(token))
    assert doc_lai.json()["status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_the_public_shape_reads_the_price_from_the_quote(client, db_pool):
    """Giá và đơn vị ghép lúc ĐỌC, từ chứng từ — không có bản sao nào để lệch."""
    token, _, bao_gia, de_xuat = await _dat_hang(client, db_pool, "kh_doc_gia")

    body = (await client.get(f"{GOC}/{de_xuat.proposal_id}", headers=_auth(token))).json()

    assert body["amount"] == bao_gia.amount
    assert body["provider"]["id"] == bao_gia.service_provider_id
    assert body["valid_until"], "không nói được báo giá sống tới bao giờ"
