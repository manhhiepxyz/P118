"""Card "Cần bạn xác nhận" phải nói ĐÚNG loại việc khách còn phải làm.

Triệu chứng đo được
-------------------
Một workflow `schedule_move` đang chờ khách chọn đơn vị hiện ra như thế này:

    CẦN BẠN XÁC NHẬN
    —                                          ← số tiền không có
    Chỗ đỗ xe đã được giữ. Khoản này chưa       ← không có chỗ đỗ xe nào
    được thanh toán — chỉ thu sau khi bạn đồng ý.
    [ Xem và xác nhận ở workspace ]            ← nút chung cho mọi thứ

Không bước nào trong yêu cầu ấy là `book_parking` hay `pay_fee`.

Nguyên nhân
-----------
Giao diện suy loại việc từ TRẠNG THÁI WORKFLOW:

    waitingPayment = status === 'WAITING_APPROVAL' && !viewing_approval

`WAITING_APPROVAL` là trạng thái dùng chung cho MỌI kiểu chờ — chờ khách trả
tiền, chờ khách chọn đơn vị, chờ khách bổ sung thông tin, chờ đơn vị nhận việc.
Suy một trong bốn thứ ấy từ "không phải tham quan" là đoán, và cái đoán ấy sai
ba lần trên bốn.

`approval_actor` không cứu được: nó trả lời "AI phải làm", không trả lời "làm
VIỆC GÌ". Thanh toán và chọn đơn vị đều là `USER`.

Hợp đồng
--------
Response nói thẳng loại hành động bằng một trường có mã định danh
(`customer_action.kind`), và giao diện chuyển theo mã ấy — không suy từ status,
không suy từ tên tool, không suy từ câu chữ.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.test_db.conftest import _register_and_login

DEMO = "/api/v1/workflows/demo"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _tai_khoan(client, db_pool, username: str) -> tuple[str, str]:
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    return token, str(uid)


async def _workflow_cho_de_xuat(client, db_pool, username: str) -> str:
    """Một `schedule_move` đang chờ KHÁCH chọn đơn vị. Không có bước tiền nào."""
    from src.orchestration.provider_matching import DICH_VU_CHUYEN_NHA  # noqa: F401

    token, uid = await _tai_khoan(client, db_pool, username)
    wid = str(uuid.uuid4())
    vao = {
        "move_date": "2026-12-01",
        "move_time": "08:00",
        "move_vehicle": "van",
        "needs_elevator": False,
        "needs_loading_support": False,
    }
    ke_hoach = {
        "goal": "chuyển nhà",
        "tasks": [{"task_id": "T1", "tool": "schedule_move", "depends_on": [], "input": vao}],
    }
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, task_plan) "
        "VALUES ($1::uuid, 'chuyển nhà', 'WAITING_APPROVAL', $2::uuid, $3::jsonb)",
        wid,
        uid,
        json.dumps(ke_hoach),
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', 'schedule_move', 'WAITING_APPROVAL', '[]'::jsonb, $2::jsonb)",
        wid,
        json.dumps(vao),
    )
    quote_id = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO service_quotes (quote_id, external_quote_id, service_provider_id, service_type, amount, "
        " currency, request_fingerprint, valid_until, workflow_id, task_id, status) "
        "VALUES ($1::uuid, $2, 'MOV-01', 'schedule_move', 430000, 'VND', $3, NOW() + INTERVAL '90 min', "
        "        $4::uuid, 'T1', 'ACTIVE')",
        quote_id,
        f"Q-{quote_id[:8]}",
        f"vt{wid[:8]}",
        wid,
    )
    await db_pool.execute(
        "INSERT INTO service_provider_proposals (proposal_id, workflow_id, task_id, quote_id, status) "
        "VALUES ($1::uuid, $2::uuid, 'T1', $3::uuid, 'PROPOSED')",
        str(uuid.uuid4()),
        wid,
        quote_id,
    )
    return token, wid


# ==================================================== triệu chứng
@pytest.mark.asyncio
async def test_a_move_request_is_never_described_as_a_parking_payment(client, db_pool):
    """Yêu cầu chuyển nhà KHÔNG được mang nội dung chỗ đỗ xe hay thanh toán.

    Đây là bài tái hiện. Nó đọc chính response mà trang chi tiết dựng card từ
    đó, nên nó đỏ vì cùng lý do người dùng thấy sai — không phải vì một thứ
    gần giống.
    """
    token, wid = await _workflow_cho_de_xuat(client, db_pool, "kh_card_move")

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    # Không có bước tiền nào trong yêu cầu này.
    assert not any(t.get("tool") in ("pay_fee", "book_parking") for t in body.get("tasks", []))
    # Nên response không được mang báo giá thanh toán.
    assert body.get("payment_quote") is None, body.get("payment_quote")


@pytest.mark.asyncio
async def test_the_response_names_the_action_instead_of_leaving_it_to_be_guessed(client, db_pool):
    """Response phải mang MÃ loại hành động, không để giao diện suy.

    `approval_actor` trả lời "ai phải làm". Câu còn thiếu là "làm việc gì" —
    và thanh toán với chọn đơn vị đều là `USER`, nên một mình nó không đủ.
    """
    token, wid = await _workflow_cho_de_xuat(client, db_pool, "kh_card_kind")

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert body.get("approval_actor") == "USER", body.get("approval_actor")
    hanh_dong = body.get("customer_action")
    assert hanh_dong is not None, "response không nói loại hành động — giao diện buộc phải đoán"
    assert hanh_dong.get("kind") == "PROVIDER_PROPOSAL", hanh_dong


@pytest.mark.asyncio
async def test_the_action_carries_what_the_card_needs_to_render(client, db_pool):
    """Đủ dữ kiện để vẽ card, không phải để giao diện tự bịa.

    Thiếu một trong số này thì giao diện lại phải dựng một chuỗi mặc định — và
    chuỗi mặc định là chính thứ đang hỏng.
    """
    token, wid = await _workflow_cho_de_xuat(client, db_pool, "kh_card_du")

    hanh_dong = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json().get("customer_action") or {}

    assert hanh_dong.get("title"), hanh_dong
    assert hanh_dong.get("amount") == 430000, hanh_dong
    assert hanh_dong.get("currency") == "VND", hanh_dong
    assert (hanh_dong.get("provider") or {}).get("name"), hanh_dong
    # `can_confirm`, KHÔNG phải `can_act`: mỗi loại hành động giữ đúng tên
    # trường của nó. Giao diện đã chuyển theo `kind` nên nó biết đọc trường nào
    # — gộp tên lại chỉ để "cho đều" là làm mất thông tin ở chỗ khác.
    assert hanh_dong.get("can_confirm") is True, hanh_dong
    # Và KHÔNG mang chữ nào của một dịch vụ khác.
    assert "đỗ xe" not in json.dumps(hanh_dong, ensure_ascii=False)
    assert "thanh toán" not in json.dumps(hanh_dong, ensure_ascii=False).lower()


# ==================================================== ma trận: mỗi dịch vụ một loại card
async def _cho_don_vi(client, db_pool, username: str, tool: str, chi_tiet: dict) -> tuple[str, str]:
    """Một yêu cầu đang chờ ĐƠN VỊ nhận việc — khách không phải làm gì."""
    from src.orchestration.service_approval import SERVICE_LABELS, save_pending_service_approvals

    token, uid = await _tai_khoan(client, db_pool, username)
    wid = str(uuid.uuid4())
    ke_hoach = {"goal": tool, "tasks": [{"task_id": "T1", "tool": tool, "depends_on": [], "input": chi_tiet}]}
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, task_plan) "
        "VALUES ($1::uuid, $2, 'WAITING_APPROVAL', $3::uuid, $4::jsonb)",
        wid,
        tool,
        uid,
        json.dumps(ke_hoach),
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', $2, 'WAITING_APPROVAL', '[]'::jsonb, $3::jsonb)",
        wid,
        tool,
        json.dumps(chi_tiet),
    )
    await save_pending_service_approvals(
        db_pool,
        workflow_id=wid,
        rows=[{"task_id": "T1", "tool": tool, "service_label": SERVICE_LABELS.get(tool, tool), "details": chi_tiet}],
        applicant={"user_id": uid, "name": "Người Thử", "phone": "0900000000"},
    )
    return token, wid


CHO_DON_VI = [
    ("create_maintenance_request", {"issue_type": "plumbing", "description": "Vòi rò", "preferred_date": "2026-12-01"}),
    ("schedule_property_viewing", {"project_id": "PRJ-001", "viewing_date": "2026-12-01", "viewing_time": "09:00"}),
    ("book_shuttle", {"viewing_date": "2026-12-01", "viewing_time": "09:00", "passenger_count": 2}),
]


@pytest.mark.parametrize(("tool", "chi_tiet"), CHO_DON_VI, ids=[t for t, _ in CHO_DON_VI])
@pytest.mark.asyncio
async def test_waiting_for_a_provider_gives_the_customer_no_action_card(client, db_pool, tool, chi_tiet):
    """Chờ ĐƠN VỊ là một trạng thái KHÔNG có việc gì cho khách.

    Đây là nhánh sinh ra lỗi ban đầu: `WAITING_APPROVAL` ở đây nghĩa là "đơn vị
    đang xem", nhưng giao diện đọc nó thành "khách phải trả tiền". Không card
    nào được mọc lên, và đặc biệt không card nào nói về chỗ đỗ xe.
    """
    token, wid = await _cho_don_vi(client, db_pool, f"kh_cho_{tool[:10]}", tool, chi_tiet)

    body = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()

    assert body.get("customer_action") is None, body.get("customer_action")
    assert body.get("payment_quote") is None
    assert "đỗ xe" not in json.dumps(body.get("customer_action"), ensure_ascii=False)


@pytest.mark.asyncio
async def test_a_confirmed_proposal_stops_being_an_action(client, db_pool):
    """Đồng ý xong thì card biến mất — việc đã chuyển sang đơn vị.

    Một hành động đã giải quyết mà vẫn hiện là mời khách bấm lần thứ hai vào
    thứ họ vừa bấm xong.
    """
    token, wid = await _workflow_cho_de_xuat(client, db_pool, "kh_card_xong")
    assert (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()["customer_action"] is not None

    await db_pool.execute(
        "UPDATE service_provider_proposals SET status='CONFIRMED', confirmed_at=NOW() WHERE workflow_id=$1::uuid",
        uuid.UUID(wid),
    )
    await db_pool.execute(
        "UPDATE service_quotes SET status='CONFIRMED', confirmed_at=NOW() WHERE workflow_id=$1::uuid",
        uuid.UUID(wid),
    )

    assert (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()["customer_action"] is None


@pytest.mark.asyncio
async def test_a_cold_read_keeps_the_same_kind(client, db_pool):
    """Xoá cache RAM rồi đọc lại: CÙNG loại card, không rơi về thanh toán.

    Đường đọc lại từ database là một đường dựng response KHÁC. Trước bản vá, hai
    đường ấy cùng im lặng về loại việc, nên giao diện đoán ở cả hai — và đoán
    giống nhau vì cùng sai một kiểu. Bài này ghim rằng chúng nói cùng một câu vì
    cùng ĐÚNG, không phải vì cùng im.
    """
    from src.api.routes import _DEMO_JOBS

    token, wid = await _workflow_cho_de_xuat(client, db_pool, "kh_card_nguoi")
    truoc = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()["customer_action"]

    _DEMO_JOBS.clear()
    sau = (await client.get(f"{DEMO}/{wid}", headers=_auth(token))).json()["customer_action"]

    assert sau["kind"] == truoc["kind"] == "PROVIDER_PROPOSAL"
    assert sau["title"] == truoc["title"]
    assert sau["amount"] == truoc["amount"]


@pytest.mark.asyncio
async def test_one_workflow_never_borrows_another_workflows_action(client, db_pool):
    """Hai workflow của CÙNG một khách, hai loại việc khác nhau.

    Đường đọc có một lớp cache theo `workflow_id`. Một lỗi khoá cache sẽ hiện
    card của yêu cầu này trên yêu cầu kia — và cả hai đều "hợp lệ" nên không có
    gì báo.
    """
    token, wid_de_xuat = await _workflow_cho_de_xuat(client, db_pool, "kh_hai_wf")
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "kh_hai_wf")
    wid_cho = str(uuid.uuid4())
    ct = {"issue_type": "plumbing", "description": "Vòi rò", "preferred_date": "2026-12-01"}
    ke_hoach = {
        "goal": "bảo trì",
        "tasks": [{"task_id": "T1", "tool": "create_maintenance_request", "depends_on": [], "input": ct}],
    }
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, task_plan) "
        "VALUES ($1::uuid, 'bảo trì', 'WAITING_APPROVAL', $2::uuid, $3::jsonb)",
        wid_cho,
        str(uid),
        json.dumps(ke_hoach),
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1::uuid, 'T1', 'create_maintenance_request', 'WAITING_APPROVAL', '[]'::jsonb, $2::jsonb)",
        wid_cho,
        json.dumps(ct),
    )

    a = (await client.get(f"{DEMO}/{wid_de_xuat}", headers=_auth(token))).json()["customer_action"]
    b = (await client.get(f"{DEMO}/{wid_cho}", headers=_auth(token))).json()["customer_action"]

    assert a["kind"] == "PROVIDER_PROPOSAL"
    assert b is None, b


# ==================================================== không bao giờ có tiêu đề rỗng
def test_an_actionable_card_can_never_carry_an_empty_title():
    """Pydantic chặn ở BACKEND, không để giao diện phải dựng `"—"`.

    `"—"` là giá trị dự phòng cũ, và nó xuất hiện đúng lúc dữ liệu hỏng — tức
    là đúng lúc KHÔNG nên có nút nào để bấm.
    """
    import pydantic

    from src.models.schemas import ClarificationAction, PaymentApprovalAction

    with pytest.raises(pydantic.ValidationError):
        PaymentApprovalAction(task_id="T1", title="", body="x", amount=1000, currency="VND", can_act=True)
    with pytest.raises(pydantic.ValidationError):
        ClarificationAction(title="", question="x", can_act=True)
    # Và một câu hỏi không nêu được hỏi gì cũng không phải một việc làm được.
    with pytest.raises(pydantic.ValidationError):
        ClarificationAction(title="Bổ sung thông tin", can_act=True)


def test_the_union_is_discriminated_by_kind_in_the_published_contract():
    """`kind` phải là DISCRIMINATOR thật, không chỉ một trường trùng tên.

    Điều đó thay đổi hai thứ mà bài kiểm hành vi không thấy:

      * OpenAPI mô tả ba hình dạng kèm bản đồ `kind → schema`, nên client sinh
        tự động (TypeScript) thu hẹp được kiểu. Không có nó, client nhận một
        union phẳng và phải tự đoán — đúng thứ bản vá này vừa gỡ bỏ;
      * lỗi validate nêu đúng nhánh. Union không discriminator báo lỗi của CẢ
        BA nhánh cho một payload sai, và người đọc phải tự tìm nhánh nào là ý
        họ.
    """
    from src.models.schemas import DemoWorkflowResponse

    khoi = DemoWorkflowResponse.model_json_schema()["properties"]["customer_action"]
    # `anyOf` vì trường là `CustomerAction | None`; nhánh union nằm bên trong.
    nhanh = khoi.get("discriminator") or next(
        (m.get("discriminator") for m in khoi.get("anyOf", []) if isinstance(m, dict) and m.get("discriminator")),
        None,
    )
    assert nhanh is not None, f"union không có discriminator: {khoi}"
    assert nhanh["propertyName"] == "kind", nhanh
    assert set(nhanh["mapping"]) == {"PAYMENT_APPROVAL", "PROVIDER_PROPOSAL", "CLARIFICATION"}, nhanh


def test_a_payload_whose_kind_does_not_match_its_shape_is_refused():
    """Nói `kind` này mà mang hình dạng kia thì bị từ chối, không được đoán hộ.

    Không có phép kiểm này, một tầng gửi sai `kind` vẫn qua được nhờ union tự
    thử từng nhánh — và giao diện, vốn chuyển theo `kind`, sẽ vẽ nhầm loại.
    """
    import pydantic

    from src.models.schemas import DemoWorkflowResponse

    with pytest.raises(pydantic.ValidationError):
        DemoWorkflowResponse.model_validate(
            {
                "status": "WAITING_APPROVAL",
                "customer_action": {
                    "kind": "CLARIFICATION",
                    "task_id": "T1",
                    "title": "Xác nhận thanh toán",
                    "body": "…",
                    "amount": 1000,
                    "currency": "VND",
                    "can_act": True,
                },
            }
        )


def test_a_listed_but_unusable_proposal_is_not_an_action():
    """Còn trong danh sách nhưng KHÔNG bấm được → không phải việc đang chờ.

    Kiểm ở tầng HỢP ĐỒNG chứ không qua HTTP, và đó là chỗ đúng: trên đường đọc
    hiện nay một đề xuất hết hạn rời hẳn `service_proposals`, nên không request
    nào dựng được tình huống này. Luật vẫn phải có — nó là luật của response,
    và một route MỚI hoàn toàn có thể liệt kê cả đề xuất không bấm được (giao
    diện cần hiện lý do thẻ hết hiệu lực).

    Bài `test_a_confirmed_proposal_stops_being_an_action` không thay được: ở đó
    đề xuất biến mất khỏi danh sách, nên một bản vá bỏ phép kiểm `can_confirm`
    vẫn xanh. Đo được — đột biến "hành động đã giải quyết vẫn hiện" sống sót
    cho tới khi có bài này.
    """
    from src.models.schemas import DemoWorkflowResponse

    het_han = {
        "kind": "PROVIDER_PROPOSAL",
        "title": "Xác nhận đơn vị cung cấp",
        "proposal_id": "p-1",
        "task_id": "T1",
        "provider": {"id": "MOV-01", "name": "Chuyển nhà Minh Phát"},
        "amount": 430000,
        "currency": "VND",
        "reason": "Báo giá 430.000 VND.",
        "valid_until": "2020-01-01T00:00:00+00:00",
        "effective_status": "EXPIRED",
        "can_confirm": False,
    }

    tra_ve = DemoWorkflowResponse.model_validate(
        {"status": "WAITING_APPROVAL", "approval_actor": "USER", "service_proposals": [het_han]}
    )

    assert tra_ve.service_proposals, "đề xuất hết hạn phải vẫn đọc được"
    assert tra_ve.customer_action is None, tra_ve.customer_action
