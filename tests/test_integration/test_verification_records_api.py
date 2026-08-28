"""Integration test — xác thực căn hộ/xe có ẢNH qua main app (Path B).

Khác `tests/test_mock/test_verification_records.py` (chỉ gọi thẳng provider):
ở đây toàn bộ stack chạy qua main app thật, không fake tầng nào:

  multipart (FormData + ảnh)
    → POST /api/v1/verification-records
      → OwnershipConnector (client inject, in-process)
        → Mock Ownership Provider (ASGITransport)
          → PostgreSQL test DB

Và quyết định duyệt được MATERIALIZE vào hệ thống thật:
  - approve apartment → tạo resident + user_resident_links VERIFIED
  - approve vehicle   → xe vào bảng `vehicles` (qua Transport provider thật)

`get_user_repository` được override để trỏ vào repository THẬT trên `e2e_pool`
(ASGITransport không fire lifespan → `app.state.runtime` None). `_ownership_connector`
và `_transport_connector` được override bằng connector thật với client in-process.
Ảnh test ghi ra `tmp_path`, không chạm `./data/uploads`.

Seed `apartment_owners` (schema.sql) có: A1201 / Vinhomes Ocean Park / Lâm Thành Bảo.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api import verification_routes
from src.api.auth import create_access_token, hash_password
from src.api.deps import get_user_repository
from src.api.verification_routes import _ownership_connector, _transport_connector
from src.connectors.ownership import OwnershipConnector
from src.connectors.transport import TransportConnector
from src.db.link_request_repository import materialize_resident_link
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.main import app
from src.orchestration.runtime_provider import (
    SharedPool,
    clear_repository_provider,
    set_repository_provider,
)
from src.services.mock.apartment_ownership import apartment_ownership_app
from src.services.mock.transport import transport_app
from tests._otp_registration import dang_ky_qua_duong_that

# Chủ sở hữu thật trong `apartment_owners` — provider trả ownership_match=True.
OWNER_CLAIM = {
    "apartment_code": "A1201",
    "residential_area": "Vinhomes Ocean Park",
    "full_name": "Lâm Thành Bảo",
}

_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _ready(repo):
    """Provider phải là async callable (xem runtime_provider.acquire_repository)."""
    return repo


@pytest_asyncio.fixture
async def verif_env(e2e_pool, tmp_path):
    """Repo thật + connector in-process tới provider thật + upload ra tmp."""
    repo = PostgreSQLWorkflowStateRepository(e2e_pool)
    repo._pool = SharedPool(repo._pool)  # noqa: SLF001 - route close() = no-op
    set_repository_provider(lambda: _ready(repo))

    ownership_client = AsyncClient(transport=ASGITransport(app=apartment_ownership_app), base_url="http://ownership")
    transport_client = AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://transport")

    app.dependency_overrides[get_user_repository] = lambda: repo.users
    app.dependency_overrides[_ownership_connector] = lambda: OwnershipConnector(
        base_url="http://ownership", client=ownership_client
    )
    app.dependency_overrides[_transport_connector] = lambda: TransportConnector(
        base_url="http://transport", client=transport_client
    )

    orig_upload_root = verification_routes.UPLOAD_ROOT
    verification_routes.UPLOAD_ROOT = tmp_path / "uploads"

    yield repo

    verification_routes.UPLOAD_ROOT = orig_upload_root
    app.dependency_overrides.clear()
    clear_repository_provider()
    await ownership_client.aclose()
    await transport_client.aclose()


@pytest_asyncio.fixture
async def verif_client():
    """Client tới main app thật (ASGITransport)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _headers(user: dict) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user)}"}


async def _register_customer(client) -> dict:
    """Khách mới, tạo qua ĐÚNG đường sản phẩm — gồm cả bước OTP.

    Phần cơ học nằm ở `tests/_otp_registration`: cùng một việc cũng cần cho
    `tests/test_db`, và hai bản sao của một luồng đăng ký là hai chỗ để lệch
    nhau khi hợp đồng đổi. Lần này nó đã đổi thật — bước OTP được thêm — và
    đó là lý do file này từng đỏ 9 bài.
    """
    data = await dang_ky_qua_duong_that(client, _unique("customer"), password="matkhau123")
    assert data is not None, "tên đăng ký bị trùng — `_unique` không còn duy nhất"
    return {"id": data["id"], "username": data["username"], "role": data["role"]}


async def _make_provider(repo) -> dict:
    user = await repo.users.create_user(_unique("provider"), hash_password("matkhau123"), role="provider")
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


async def _create_record(client, user, record_type: str, claim: dict, files: int = 1):
    data = {"record_type": record_type, "claimed_data": json.dumps(claim)}
    file_parts = [("files", ("giay.jpg", _JPEG, "image/jpeg")) for _ in range(files)]
    return await client.post(
        "/api/v1/verification-records",
        headers=_headers(user),
        data=data,
        files=file_parts,
    )


async def _decide(client, reviewer: dict, record_id: str, decision: str, reason: str | None = None):
    body: dict = {"decision": decision}
    if reason is not None:
        body["reject_reason"] = reason
    return await client.post(
        f"/api/v1/verification-records/{record_id}/decide",
        headers=_headers(reviewer),
        json=body,
    )


# ---------------------------------------------------------------------------
# Khách hàng: gửi đơn + ảnh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_apartment_pending_via_multipart(verif_env, verif_client):
    customer = await _register_customer(verif_client)

    res = await _create_record(verif_client, customer, "apartment", OWNER_CLAIM, files=2)

    assert res.status_code == 201, res.text
    item = res.json()["item"]
    assert item["record_type"] == "apartment"
    assert item["status"] == "PENDING"
    # applicant_user_id do BACKEND đặt từ JWT — client không gửi.
    assert item["applicant_user_id"] == customer["id"]
    # Provider tính ownership_match cho căn hộ (không lộ owner_name).
    assert item["ownership_match"] is True
    # Ảnh đã lưu đĩa và trả URL ổn định theo record_id.
    assert len(item["proof_image_urls"]) == 2
    assert all(u.startswith(f"/uploads/{item['record_id']}/") for u in item["proof_image_urls"])
    assert "owner_name" not in item


@pytest.mark.asyncio
async def test_vehicle_create_requires_verified_apartment(verif_env, verif_client):
    customer = await _register_customer(verif_client)

    res = await _create_record(
        verif_client,
        customer,
        "vehicle",
        {"plate_number": "51F-88999", "vehicle_type": "car"},
    )

    # Fail-closed: người chưa xác minh căn hộ không được mở đơn xe.
    assert res.status_code == 403
    assert "xác minh căn hộ" in res.json()["detail"]


@pytest.mark.asyncio
async def test_customer_cannot_list_or_decide(verif_env, verif_client):
    customer = await _register_customer(verif_client)
    await _create_record(verif_client, customer, "apartment", OWNER_CLAIM)

    # Danh sách hồ sơ cho người duyệt — customer bị chặn 403.
    listed = await verif_client.get("/api/v1/verification-records", headers=_headers(customer))
    assert listed.status_code == 403

    # Duyệt — customer bị chặn 403.
    records = await verif_client.get("/api/v1/verification-records/my", headers=_headers(customer))
    record_id = records.json()["items"][0]["record_id"]
    decided = await _decide(verif_client, customer, record_id, "approve")
    assert decided.status_code == 403


@pytest.mark.asyncio
async def test_reject_without_reason_422(verif_env, verif_client):
    customer = await _register_customer(verif_client)
    provider = await _make_provider(verif_env)

    created = await _create_record(verif_client, customer, "apartment", OWNER_CLAIM)
    record_id = created.json()["item"]["record_id"]

    res = await _decide(verif_client, provider, record_id, "reject")

    assert res.status_code == 422
    assert "lý do" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_my_records_isolated_per_user(verif_env, verif_client):
    alice = await _register_customer(verif_client)
    bob = await _register_customer(verif_client)
    await _create_record(verif_client, alice, "apartment", OWNER_CLAIM)

    alice_mine = await verif_client.get("/api/v1/verification-records/my", headers=_headers(alice))
    bob_mine = await verif_client.get("/api/v1/verification-records/my", headers=_headers(bob))

    # Không dò được đơn của người khác — đây là danh sách theo JWT, không nhận user_id.
    assert len(alice_mine.json()["items"]) == 1
    assert bob_mine.json()["items"] == []


# ---------------------------------------------------------------------------
# Materialize — duyệt ghi vào hệ thống thật
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_apartment_opens_resident_services(verif_env, verif_client):
    customer = await _register_customer(verif_client)
    provider = await _make_provider(verif_env)

    created = await _create_record(verif_client, customer, "apartment", OWNER_CLAIM)
    record_id = created.json()["item"]["record_id"]

    res = await _decide(verif_client, provider, record_id, "approve")

    assert res.status_code == 200, res.text
    assert res.json()["item"]["status"] == "APPROVED"
    assert res.json()["item"]["decided_by"] == provider["username"]

    # /me phản ánh quyền cư dân ĐÃ MỞ — nguồn sự thật là DB.
    me = await verif_client.get("/api/v1/auth/me", headers=_headers(customer))
    me_data = me.json()
    assert me_data["resident_verification_status"] == "VERIFIED"
    assert me_data["apartment_code"] == "A1201"

    # Kiểm thật trong DB: resident + link VERIFIED tồn tại.
    async with verif_env._pool.acquire() as conn:  # noqa: SLF001 - test dựng state
        link = await conn.fetchrow(
            "SELECT 1 FROM user_resident_links WHERE user_id = $1 AND verification_status = 'VERIFIED'",
            customer["id"],
        )
        assert link is not None
        resident = await conn.fetchrow("SELECT resident_id FROM residents WHERE apartment_code = 'A1201'")
        assert resident is not None


@pytest.mark.asyncio
async def test_approve_vehicle_creates_vehicle(verif_env, verif_client):
    customer = await _register_customer(verif_client)
    provider = await _make_provider(verif_env)

    # Cư dân đã VERIFIED — dựng qua đúng helper materialize của hệ thống.
    resident_id = await materialize_resident_link(
        verif_env._pool,  # noqa: SLF001 - test dùng pool thật
        user_id=customer["id"],
        apartment_code="A1201",
        residential_area="Vinhomes Ocean Park",
        full_name="Lâm Thành Bảo",
    )

    plate = f"51F-{uuid.uuid4().int % 100000:05d}"
    created = await _create_record(
        verif_client,
        customer,
        "vehicle",
        {"plate_number": plate, "vehicle_type": "car"},
    )
    assert created.status_code == 201, created.text
    record_id = created.json()["item"]["record_id"]

    res = await _decide(verif_client, provider, record_id, "approve")

    assert res.status_code == 200, res.text
    item = res.json()["item"]
    assert item["status"] == "APPROVED"
    assert item.get("materialized", {}).get("vehicle_id")

    # Xe đã vào bảng `vehicles`, treo đúng resident_id của người nộp đơn
    # (resident_id TRA từ liên kết VERIFIED, không lấy từ body/người duyệt).
    async with verif_env._pool.acquire() as conn:  # noqa: SLF001
        vehicle = await conn.fetchrow(
            "SELECT vehicle_id, resident_id, plate_number FROM vehicles WHERE plate_number = $1",
            plate,
        )
        assert vehicle is not None
        assert str(vehicle["resident_id"]) == str(resident_id)
        assert str(vehicle["vehicle_id"]) == str(item["materialized"]["vehicle_id"])


@pytest.mark.asyncio
async def test_approve_vehicle_fails_when_link_revoked(verif_env, verif_client):
    """Applicant không còn VERIFIED lúc duyệt → 409, fail-closed.

    Tình huống thật: user nộp đơn xe, giữa chừng bị thu hồi liên kết căn hộ.
    Materialize phải KHÔNG tạo xe dựa trên thông tin đã cũ.
    """
    customer = await _register_customer(verif_client)
    provider = await _make_provider(verif_env)

    await materialize_resident_link(
        verif_env._pool,  # noqa: SLF001
        user_id=customer["id"],
        apartment_code="A1201",
        residential_area="Vinhomes Ocean Park",
        full_name="Lâm Thành Bảo",
    )
    created = await _create_record(
        verif_client,
        customer,
        "vehicle",
        {"plate_number": f"51F-{uuid.uuid4().int % 100000:05d}", "vehicle_type": "car"},
    )
    record_id = created.json()["item"]["record_id"]

    # Thu hồi liên kết giữa chừng.
    async with verif_env._pool.acquire() as conn:  # noqa: SLF001
        await conn.execute(
            "DELETE FROM user_resident_links WHERE user_id = $1",
            customer["id"],
        )

    res = await _decide(verif_client, provider, record_id, "approve")

    assert res.status_code == 409
    assert "không còn liên kết" in res.json()["detail"]


# ---------------------------------------------------------------------------
# Không rò owner_name — qua đúng đường main app
# ---------------------------------------------------------------------------


def _has_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


@pytest.mark.asyncio
async def test_provider_list_never_leaks_owner_name(verif_env, verif_client):
    alice = await _register_customer(verif_client)
    bob = await _register_customer(verif_client)
    provider = await _make_provider(verif_env)

    await _create_record(verif_client, alice, "apartment", OWNER_CLAIM)
    # Một đơn khớp và một đơn KHÔNG khớp chủ hộ (tên tự khai sai) — để chắc
    # rằng cả hai trạng thái ownership_match đều không kéo theo owner_name.
    await _create_record(
        verif_client,
        bob,
        "apartment",
        {
            "apartment_code": "B2202",
            "residential_area": "Vinhomes Ocean Park",
            "full_name": "Người Khác",
        },
    )

    res = await verif_client.get(
        "/api/v1/verification-records",
        params={"record_type": "apartment", "status": "PENDING"},
        headers=_headers(provider),
    )

    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 2
    # List có ownership_match để người duyệt quyết định, nhưng KHÔNG có owner_name.
    assert all("ownership_match" in i for i in items)
    assert not _has_key(res.json(), "owner_name")


# ---------------------------------------------------------------------------
# Không ai duyệt hồ sơ của chính mình
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reviewer_cannot_approve_their_own_application(verif_env, verif_client):
    """Leo thang quyền đã tái hiện được, chạy đến tận cùng:

        provider nộp hồ sơ căn hộ "SELF-9001"  → tạo được
        provider tự duyệt hồ sơ đó              → APPROVED, decided_by=provider
        /auth/me                                → VERIFIED, căn hộ SELF-9001

    Người duyệt tự cấp cho mình tư cách cư dân của một căn hộ KHÔNG có trong
    registry. Toàn bộ giá trị của bước xác thực nằm ở chỗ có người thứ hai nhìn
    vào hồ sơ — người duyệt trùng người nộp thì bước đó bằng không.
    """
    provider = await _make_provider(verif_env)

    created = await _create_record(
        verif_client,
        provider,
        "apartment",
        {"apartment_code": "SELF-9001", "residential_area": "Vinhomes Ocean Park", "full_name": "Toi Tu Khai"},
    )
    assert created.status_code == 201, created.text
    record_id = created.json()["item"]["record_id"]

    decided = await _decide(verif_client, provider, record_id, "approve")
    assert decided.status_code == 403, f"tự duyệt lọt qua: {decided.status_code} {decided.text}"
    assert "chính mình" in decided.json()["detail"]

    # Và hồ sơ phải còn nguyên PENDING — chặn sau khi đã đổi trạng thái thì
    # rollback nghĩa là phải gỡ cả liên kết cư dân đã materialize.
    mine = await verif_client.get("/api/v1/verification-records/my", headers=_headers(provider))
    assert mine.json()["items"][0]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_self_rejection_is_blocked_too(verif_env, verif_client):
    """Từ chối cũng là quyết định.

    Chỉ chặn `approve` thì người duyệt vẫn tự dập được hồ sơ bất lợi của mình —
    và `decide_record` claim bằng UPDATE trên `status='PENDING'`, nên một lần từ
    chối là hồ sơ hết đường được xem xét lại.
    """
    provider = await _make_provider(verif_env)
    created = await _create_record(
        verif_client,
        provider,
        "apartment",
        {"apartment_code": "SELF-9002", "residential_area": "Vinhomes Ocean Park", "full_name": "Toi Tu Khai"},
    )
    record_id = created.json()["item"]["record_id"]

    decided = await _decide(verif_client, provider, record_id, "reject", "tự dập hồ sơ của mình")
    assert decided.status_code == 403, decided.text


@pytest.mark.asyncio
async def test_reviewing_someone_elses_application_still_works(verif_env, verif_client):
    """Chốt phải HẸP. Chặn nhầm cả hồ sơ người khác là làm hỏng luồng duyệt.

    Mutation test sống: siết `_reject_self_review` thành "chặn mọi hồ sơ" thì
    test này đỏ ngay.
    """
    customer = await _register_customer(verif_client)
    provider = await _make_provider(verif_env)

    created = await _create_record(verif_client, customer, "apartment", OWNER_CLAIM)
    assert created.status_code == 201, created.text
    record_id = created.json()["item"]["record_id"]

    decided = await _decide(verif_client, provider, record_id, "reject", "Giấy tờ chưa rõ")
    assert decided.status_code == 200, decided.text
    assert decided.json()["item"]["status"] == "REJECTED"
