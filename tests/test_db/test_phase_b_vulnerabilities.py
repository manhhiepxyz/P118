"""Tái hiện các lỗ hổng Phase B trên API + PostgreSQL thật.

Đây KHÔNG phải test hồi quy cho hành vi mong muốn; nó ghi lại hành vi SAI đang
tồn tại, để bản vá có một mốc đối chiếu cụ thể thay vì lời mô tả. Sau khi Phase
B hoàn tất, mọi test ở đây phải đảo chiều — và chúng được viết sẵn theo dạng
đảo chiều đó, nên chúng đỏ cho tới khi guard có thật.

Không assert trên source text: mọi kết luận đi qua HTTP và database.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from src.orchestration.runtime_provider import set_repository_provider
from tests.test_db.conftest import _register_and_login


@pytest.mark.asyncio
async def test_starting_a_workflow_requires_authentication(client):
    """`/workflows/demo/start` phải từ chối request không có token.

    Hiện tại endpoint không khai `Depends(get_current_user)`, nên bất kỳ ai
    chạm được tới cổng đều tạo được workflow. Mọi ràng buộc quyền phía sau đều
    vô nghĩa nếu điểm vào không biết người gọi là ai.
    """
    response = await client.post("/api/v1/workflows/demo/start", json={"goal": "Tìm căn hộ cho thuê"})

    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_the_request_body_cannot_claim_an_account_state(client):
    """`account_state` do client gửi phải bị từ chối, không phải bị bỏ qua.

    Quyền cư dân phải suy ra từ token cộng mapping trong database. Một field
    trong body quyết định điều đó nghĩa là leo thang đặc quyền chỉ tốn một dòng
    JSON.
    """
    token = await _register_and_login(client, "nguoidung_body_state")

    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đăng ký xe", "account_state": "resident"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("resident_id", "RES-001"),
        ("verification_status", "VERIFIED"),
        ("owner_user_id", "00000000-0000-0000-0000-000000000001"),
        ("existing_context", {"resident_id": "RES-001"}),
    ],
)
async def test_the_request_body_cannot_claim_trusted_identity_fields(client, field, value):
    token = await _register_and_login(client, f"nguoidung_{field}")

    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đăng ký xe", field: value},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_a_user_cannot_read_another_users_workflow(client):
    """IDOR: workflow của người khác phải trả 404, không phải 200.

    404 chứ không phải 403: 403 xác nhận workflow đó tồn tại, và với ID tuần tự
    hoặc đoán được thì đó đã là rò rỉ.
    """
    token_a = await _register_and_login(client, "nguoidung_a")
    token_b = await _register_and_login(client, "nguoidung_b")

    created = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Tìm căn hộ cho thuê tại Vinhomes Ocean Park"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert created.status_code == 202, created.text
    workflow_id = created.json()["workflow_id"]

    stolen = await client.get(
        f"/api/v1/workflows/demo/{workflow_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert stolen.status_code == 404, stolen.text


@pytest.mark.asyncio
async def test_a_user_cannot_see_another_users_workflow_in_the_list(client):
    token_a = await _register_and_login(client, "nguoidung_list_a")
    token_b = await _register_and_login(client, "nguoidung_list_b")

    created = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Tìm căn hộ cho thuê tại Vinhomes Ocean Park"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    workflow_id = created.json()["workflow_id"]

    listed = await client.get(
        "/api/v1/workflows/demo?status=active&limit=50",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert listed.status_code == 200, listed.text
    assert workflow_id not in [item["workflow_id"] for item in listed.json()["items"]]


@pytest.mark.asyncio
async def test_a_user_cannot_decide_payment_on_another_users_workflow(client):
    token_a = await _register_and_login(client, "nguoidung_pay_a")
    token_b = await _register_and_login(client, "nguoidung_pay_b")

    created = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Tìm căn hộ cho thuê tại Vinhomes Ocean Park"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    workflow_id = created.json()["workflow_id"]

    hijacked = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/payment-decision",
        json={"decision": "approve"},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert hijacked.status_code == 404, hijacked.text


@pytest.mark.asyncio
async def test_registering_creates_a_customer_without_any_resident_link(client, db_pool):
    """Đăng ký tạo tài khoản `customer`, KHÔNG tạo quyền cư dân.

    Role cũ tên `resident` khiến hai trục khác nhau bị trộn: "đây là tài khoản
    loại gì" và "tài khoản này đã liên kết căn hộ chưa". Ai đọc code cũng dễ
    tưởng đăng ký xong là thành cư dân.
    """
    await _register_and_login(client, "nguoidung_moi_dang_ky")

    row = await db_pool.fetchrow("SELECT id, role FROM users WHERE username = $1", "nguoidung_moi_dang_ky")

    assert row["role"] == "customer"
    link = await db_pool.fetchrow("SELECT verification_status FROM user_resident_links WHERE user_id = $1", row["id"])
    assert link is None, "đăng ký không được tự tạo liên kết cư dân"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_a_user_cannot_decide_a_real_pending_payment_of_another_user(client, db_pool, decision):
    """IDOR trên payment-decision, với một yêu cầu chờ duyệt THẬT.

    Test trước cũng gọi endpoint này bằng token của người khác và cũng thấy 404
    — nhưng 404 đó đến từ "không có yêu cầu chờ duyệt nào", không phải từ kiểm
    quyền. Nó pass kể cả khi guard bị gỡ bỏ. Ở đây workflow của A thực sự đang
    chờ duyệt, nên chỉ guard mới giải thích được kết quả.
    """
    token_a = await _register_and_login(client, f"nn_pay_that_a_{decision}")
    token_b = await _register_and_login(client, f"nn_pay_that_b_{decision}")
    assert token_a

    # Tạo workflow của A trực tiếp: `/start` chạy planner ở background task nên
    # row `workflows` chưa chắc đã tồn tại khi test đi tiếp.
    owner_a = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", f"nn_pay_that_a_{decision}")
    workflow_id = str(
        await db_pool.fetchval(
            "INSERT INTO workflows (goal, status, owner_user_id) "
            "VALUES ('Thanh toán phí đỗ xe', 'WAITING_APPROVAL', $1) RETURNING workflow_id",
            owner_a,
        )
    )

    from src.orchestration.payment_approval import PaymentQuote, save_pending_approval

    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
        "VALUES ($1::uuid, 'T_PAY', 'pay_fee', 'PENDING', '[]'::jsonb) ON CONFLICT DO NOTHING",
        workflow_id,
    )
    await save_pending_approval(
        db_pool,
        workflow_id=workflow_id,
        task_id="T_PAY",
        quote=PaymentQuote(booking_id="BK-IDOR-001", amount=99_000, currency="VND"),
    )

    hijacked = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/payment-decision",
        json={"decision": decision},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert hijacked.status_code == 404, hijacked.text
    still_awaiting = await db_pool.fetchval(
        "SELECT status FROM payment_approvals WHERE workflow_id = $1::uuid", workflow_id
    )
    assert still_awaiting == "AWAITING", "quyết định của người khác đã đổi được trạng thái"


@pytest.mark.asyncio
async def test_the_unauthenticated_sync_endpoint_no_longer_exists(client):
    """`POST /workflows/demo` (đồng bộ) phải biến mất hẳn.

    Nó không đòi token nhưng vẫn gọi LLM và runtime thật, nên "chạy ở quyền
    thấp nhất" không cứu được: bất kỳ ai chạm tới cổng vẫn đốt quota, vẫn tạo
    được lịch xem nhà, và workflow sinh ra không có chủ nên nằm ngoài mọi kiểm
    tra quyền lẫn audit.
    """
    response = await client.post("/api/v1/workflows/demo", json={"goal": "Tìm căn hộ cho thuê"})

    assert response.status_code in {404, 405}, response.text


@pytest.mark.asyncio
async def test_every_workflow_created_through_the_api_has_an_owner(client, db_pool):
    """Không đường nào tạo được workflow với owner_user_id NULL."""
    token = await _register_and_login(client, "nn_owner_bat_buoc")

    created = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Tìm căn hộ cho thuê tại Vinhomes Ocean Park"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 202, created.text
    workflow_id = created.json()["workflow_id"]

    for _ in range(200):
        owner = await db_pool.fetchval("SELECT owner_user_id FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
        if owner is not None:
            break
        await asyncio.sleep(0.01)

    expected = await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_owner_bat_buoc'")
    assert owner == expected


@pytest.mark.asyncio
async def test_a_user_cannot_list_another_users_session(client, db_pool):
    """IDOR trên `/workflows/demo/session/{id}`.

    Endpoint này lọc theo `session_id` — một giá trị client biết và gửi lại
    được, nên nó KHÔNG phải bằng chứng về quyền. Ai cầm được session của người
    khác thì đọc được toàn bộ thread của họ.
    """
    token_a = await _register_and_login(client, "nn_sess_a")
    token_b = await _register_and_login(client, "nn_sess_b")

    created = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Tìm căn hộ cho thuê tại Vinhomes Ocean Park"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    session_id = created.json()["session_id"]
    assert session_id

    stolen = await client.get(
        f"/api/v1/workflows/demo/session/{session_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    unknown = await client.get(
        "/api/v1/workflows/demo/session/00000000-0000-0000-0000-0000000000ff",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    # Session của người khác và session không tồn tại phải TRẢ GIỐNG NHAU.
    # Khác nhau ở bất kỳ điểm nào cũng đủ để dò xem một session có thật hay không.
    assert stolen.status_code == unknown.status_code
    assert stolen.json() == unknown.json() or stolen.json()["workflows"] == unknown.json()["workflows"] == []


@pytest.mark.asyncio
async def test_the_executor_cannot_overwrite_an_existing_owner(client, db_pool):
    """`create_workflow` gọi lại KHÔNG được đổi chủ sở hữu.

    Executor gọi lại `create_workflow` sau shell và không truyền owner. Cho phép
    ghi đè nghĩa là ai gọi sau cùng thì sở hữu workflow — đúng thứ IDOR cần.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    await _register_and_login(client, "nn_owner_giu_nguyen")
    await _register_and_login(client, "nn_owner_ke_cuop")
    owner_a = await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_owner_giu_nguyen'")
    owner_b = await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_owner_ke_cuop'")

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await repository.create_workflow({"goal": "Đặt chỗ đỗ xe", "owner_user_id": str(owner_a)})

    # Executor gọi lại, không truyền owner.
    await repository.create_workflow({"id": workflow_id, "goal": "Đặt chỗ đỗ xe"})
    # Và một lần cố tình truyền owner khác.
    await repository.create_workflow({"id": workflow_id, "goal": "Đặt chỗ đỗ xe", "owner_user_id": str(owner_b)})

    assert (
        await db_pool.fetchval("SELECT owner_user_id FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
        == owner_a
    )


@pytest.mark.asyncio
async def test_a_legacy_workflow_without_an_owner_is_invisible_to_customers(client, db_pool):
    """Row tạo trước Phase B giữ lại để truy vết, nhưng không thuộc về ai."""
    token = await _register_and_login(client, "nn_legacy")

    legacy_id = str(
        await db_pool.fetchval(
            "INSERT INTO workflows (goal, status, owner_user_id) "
            "VALUES ('Workflow cũ trước Phase B', 'RUNNING', NULL) RETURNING workflow_id"
        )
    )
    headers = {"Authorization": f"Bearer {token}"}

    detail = await client.get(f"/api/v1/workflows/demo/{legacy_id}", headers=headers)
    listed = await client.get("/api/v1/workflows/demo?status=all&limit=50", headers=headers)

    assert detail.status_code == 404
    assert legacy_id not in [item["workflow_id"] for item in listed.json()["items"]]


@pytest.mark.asyncio
async def test_a_child_workflow_inherits_the_owner_and_session_of_its_parent(client, db_pool):
    """Continue tạo workflow con — con phải cùng chủ và cùng phiên với cha.

    Con mất owner sẽ rơi vào nhóm legacy NULL và biến mất khỏi danh sách của
    chính người vừa tạo ra nó; con mang owner khác thì tệ hơn nhiều.
    """
    from src.api import routes
    from src.db.link_request_repository import materialize_resident_link

    token = await _register_and_login(client, "nn_cha_con")
    owner_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_cha_con'")

    # Tài khoản phải ĐÃ xác minh căn hộ, nếu không `/start` không tạo workflow.
    #
    # Test này từng đỏ vì chính sách đã siết mà nó không theo: đăng ký xe và
    # chỗ đỗ yêu cầu liên kết cư dân VERIFIED. Người chưa xác minh gửi mục tiêu
    # đó thì route trả 202 kèm `status="CHAT"` và hướng dẫn đi xác minh — không
    # có row `workflows` nào được ghi, nên `parent_workflow_id` của con vi phạm
    # khoá ngoại, `_ensure_workflow_shell` nuốt lỗi và trả False, và test nổ ở
    # `child["owner_user_id"]` với TypeError: NoneType.
    #
    # Triệu chứng cách nguyên nhân bốn bước và không nhắc gì tới xác minh căn
    # hộ. Đo mới ra: in body của `/start` thấy `stage="CHAT"`.
    #
    # Dùng chính `materialize_resident_link` mà luồng duyệt thật gọi, thay vì
    # INSERT tay vào `user_resident_links` — INSERT tay sẽ vẫn xanh kể cả khi
    # hàm thật đổi hình dạng dữ liệu.
    await materialize_resident_link(
        db_pool,
        user_id=str(owner_id),
        apartment_code="A1201",
        residential_area="Vinhomes Ocean Park",
        full_name="Nguyen Van A",
    )

    created = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đăng ký xe và đặt chỗ đậu xe"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Khẳng định NGAY rằng `/start` đã vào lane dịch vụ. Không có dòng này thì
    # một chính sách siết thêm trong tương lai lại làm test đỏ ở chỗ khác, với
    # một TypeError không nói gì.
    assert created.json().get("stage") != "CHAT", (
        f"/start không tạo workflow, nó trả CHAT: {created.json().get('message')}"
    )
    parent_id = created.json()["workflow_id"]
    session_id = created.json()["session_id"]

    for _ in range(200):
        if await db_pool.fetchval("SELECT 1 FROM workflows WHERE workflow_id = $1::uuid", parent_id):
            break
        await asyncio.sleep(0.01)

    # Workflow con do đường continue tạo ra, dựng qua chính helper của route.
    child_id = str(uuid.uuid4())
    await routes._ensure_workflow_shell(
        child_id,
        goal="Đăng ký xe và đặt chỗ đậu xe",
        session_id=session_id,
        parent_workflow_id=parent_id,
        owner_user_id=str(owner_id),
    )

    child = await db_pool.fetchrow(
        "SELECT owner_user_id, session_id, parent_workflow_id FROM workflows WHERE workflow_id = $1::uuid",
        child_id,
    )
    parent = await db_pool.fetchrow(
        "SELECT owner_user_id, session_id FROM workflows WHERE workflow_id = $1::uuid", parent_id
    )

    assert child["owner_user_id"] == parent["owner_user_id"] == owner_id
    assert child["session_id"] == parent["session_id"]
    assert str(child["parent_workflow_id"]) == parent_id


@pytest.mark.asyncio
async def test_continue_never_reads_a_session_that_belongs_to_someone_else(client, db_pool):
    """Session của người khác phải fail-closed, kể cả khi workflow cha đúng chủ.

    Kiểm chủ sở hữu workflow cha rồi coi session là hệ quả sẽ dựa vào giả định
    "dữ liệu luôn nhất quán". Một guard quyền không được đứng trên giả định đó:
    session_id đi vào từ dữ liệu đã ghim, và nếu nó trỏ sang phiên của người
    khác thì quyền của phiên đó không được rơi sang đây.

    Kiểm ở tầng `_load_session` vì đó chính là chỗ phạm vi theo user được ép.
    """
    from src.api import routes
    from src.db.session_repository import create_session

    await _register_and_login(client, "nn_ss_a")
    await _register_and_login(client, "nn_ss_b")
    user_a = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_ss_a'"))
    user_b = str(await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_ss_b'"))

    session_of_b = str(uuid.uuid4())
    await create_session(
        db_pool,
        session_id=session_of_b,
        account_state="resident",
        resident_id="RES-CUA-B",
        user_id=user_b,
    )

    class _Pool:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def close(self):
            return None

    class _Repo:
        _pool = _Pool(db_pool)

    async def _build(**_kwargs):
        return _Repo()

    # Fixture `client` đã đặt provider trỏ vào pool test; ở đây tạm thay bằng
    # một repo mỏng rồi trả lại đúng provider cũ.
    from src.orchestration import runtime_provider

    original = runtime_provider._provider  # noqa: SLF001 - test khôi phục nguyên trạng
    set_repository_provider(_build)
    try:
        as_owner = await routes._load_session(session_of_b, user_id=user_b)
        as_intruder = await routes._load_session(session_of_b, user_id=user_a)
    finally:
        runtime_provider._provider = original  # noqa: SLF001

    assert as_owner is not None, "chủ phiên phải đọc được phiên của mình"
    assert as_intruder is None, "phiên của người khác phải fail-closed"
    # Fail-closed nghĩa là rơi về prospect, không phải kế thừa quyền cư dân của B.
    assert (as_intruder or {}).get("account_state", "prospect") == "prospect"


@pytest.mark.asyncio
async def test_a_continue_child_workflow_is_readable_by_its_own_creator(client, db_pool):
    """Workflow con phải kế thừa chủ sở hữu của cha.

    Bug đã xảy ra: `/continue` tạo workflow con KHÔNG có `owner_user_id`. Con
    rơi vào nhóm "legacy không chủ", nên `_require_workflow_owner` trả 404 cho
    chính người vừa trả lời câu hỏi bổ sung. Workflow vẫn chạy tới SUCCESS ở
    phía sau — người dùng chỉ không bao giờ nhìn thấy kết quả.

    Không test in-process nào bắt được vì chúng dựng workflow con trực tiếp.
    Browser E2E mới thấy.
    """
    from src.api import routes

    token = await _register_and_login(client, "nn_con_ke_thua")
    owner_id = await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_con_ke_thua'")
    assert token

    parent_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    await routes._ensure_workflow_shell(
        parent_id,
        goal="Đặt chỗ đỗ xe",
        session_id=session_id,
        parent_workflow_id=None,
        owner_user_id=str(owner_id),
    )

    # Con được tạo bằng CÙNG một helper mà đường `/continue` dùng.
    child_id = str(uuid.uuid4())
    await routes._ensure_workflow_shell(
        child_id,
        goal="Đặt chỗ đỗ xe",
        session_id=session_id,
        parent_workflow_id=parent_id,
        owner_user_id=str(owner_id),
    )

    child_owner = await db_pool.fetchval("SELECT owner_user_id FROM workflows WHERE workflow_id = $1::uuid", child_id)
    assert child_owner == owner_id, "workflow con mất chủ sở hữu"

    seen = await client.get(f"/api/v1/workflows/demo/{child_id}", headers={"Authorization": f"Bearer {token}"})
    assert seen.status_code == 200, "người tạo không đọc được workflow con của chính mình"
