"""Admin nhìn thấy hồ sơ xác minh, và không chạm được vào chúng.

Sau khi ba route admin legacy bị xoá, admin MÙ hẳn với loại yêu cầu này:
`verification_records` sống ở Ownership Provider, không nằm trong workflow nào,
nên `/admin/requests` không thấy chúng. Đó là một khoảng trống có thật do chính
lượt siết quyền tạo ra, và endpoint này lấp nó — trả lại tầm nhìn mà không trả
lại quyền quyết định.

Connector được override ở tầng dependency. Đây KHÔNG phải Docker E2E và không
giả vờ là: nó kiểm CONTRACT của route — role, làm sạch, filter, xử lý provider
chết — trong khi phần tra tài khoản vẫn đọc PostgreSQL test thật.
"""

from __future__ import annotations

import uuid

import pytest

from src.api.verification_routes import _ownership_connector
from src.connectors.ownership import OwnershipProviderError
from src.main import app
from tests.test_db.conftest import _register_and_login

LIST = "/api/v1/admin/verifications"
BI_MAT = {
    "apartment_code": "A1201",
    "residential_area": "Vinhomes Ocean Park",
    "full_name": "Lâm Thành Bảo",
    "id_number": "001234567890",
}


async def _user(client, db_pool, username, role=None):
    await _register_and_login(client, username)
    if role:
        await db_pool.execute("UPDATE users SET role=$2 WHERE username=$1", username, role)
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", username)
    return token, str(uid)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


class _FakeOwnership:
    """Provider giả — trả đúng HÌNH DẠNG payload thật, kể cả phần nhạy cảm.

    Cố ý mang `claimed_data`, `proof_image_urls` và `resident_id`: một fake
    "sạch sẵn" sẽ khiến test làm-sạch xanh mà không chứng minh gì.
    """

    def __init__(self, records, error=None):
        self.records = records
        self.error = error
        self.calls = []

    async def list_records(self, *, record_type=None, status=None, applicant_user_id=None):
        self.calls.append({"record_type": record_type, "status": status})
        if self.error:
            raise self.error
        out = self.records
        if record_type:
            out = [r for r in out if r["record_type"] == record_type]
        if status:
            out = [r for r in out if r["status"] == status]
        return out


def _record(applicant, *, loai="apartment", status="PENDING", **kw):
    return {
        "record_id": str(uuid.uuid4()),
        "record_type": loai,
        "status": status,
        "applicant_user_id": applicant,
        "claimed_data": dict(BI_MAT),
        "proof_image_urls": ["/uploads/abc/giay-to.png"],
        "resident_id": "RES-BI-MAT",
        "reject_reason": kw.get("reject_reason"),
        "decided_by": kw.get("decided_by"),
        "decided_at": kw.get("decided_at"),
        "created_at": "2026-08-20T10:00:00+00:00",
        "updated_at": kw.get("updated_at"),
    }


@pytest.fixture
def gia_lap():
    """Override dependency; gỡ sạch sau mỗi test."""
    holder = {}

    def dat(records, error=None):
        fake = _FakeOwnership(records, error)
        holder["fake"] = fake
        app.dependency_overrides[_ownership_connector] = lambda: fake
        return fake

    yield dat
    app.dependency_overrides.pop(_ownership_connector, None)


# --- ai được vào ------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_anonymous_caller_sees_nothing(client, db_pool):
    assert (await client.get(LIST)).status_code == 401


@pytest.mark.parametrize("vai", ["customer", "provider"], ids=["khách", "đơn-vị"])
@pytest.mark.asyncio
async def test_only_an_admin_gets_the_monitoring_surface(client, db_pool, vai):
    """Provider cũng 403: họ quyết định ở `/verification-records`, không ở đây."""
    token, _ = await _user(client, db_pool, f"xm_{vai}", role=None if vai == "customer" else vai)
    assert (await client.get(LIST, headers=_auth(token))).status_code == 403


# --- admin thấy đủ, và chỉ đủ -----------------------------------------------


@pytest.mark.asyncio
async def test_an_admin_sees_requests_from_every_account(client, db_pool, gia_lap):
    admin, _ = await _user(client, db_pool, "xm_admin_ds", role="admin")
    _, a = await _user(client, db_pool, "xm_khach_a")
    _, b = await _user(client, db_pool, "xm_khach_b")
    gia_lap([_record(a), _record(b, loai="vehicle", status="APPROVED", decided_by="don-vi-tour")])

    response = await client.get(LIST, headers=_auth(admin))

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {i["account"]["username"] for i in items} == {"xm_khach_a", "xm_khach_b"}
    theo_ten = {i["account"]["username"]: i for i in items}
    assert theo_ten["xm_khach_a"]["request_name"] == "Xác minh căn hộ"
    # Màn giám sát KHÔNG có field `status` gộp: nó tách `provider_status` khỏi
    # `effective_status`, vì "đơn vị đã ký" và "quyền đã mở" là hai chuyện.
    assert theo_ten["xm_khach_a"]["provider_status"] == "PENDING"
    assert theo_ten["xm_khach_a"]["effective_status"] == "WAITING_PROVIDER"
    assert "status" not in theo_ten["xm_khach_a"]
    assert theo_ten["xm_khach_b"]["request_name"] == "Xác minh phương tiện"
    assert theo_ten["xm_khach_b"]["decided_by"] == "don-vi-tour"


@pytest.mark.asyncio
async def test_the_sensitive_half_of_the_record_never_leaves(client, db_pool, gia_lap):
    """Ảnh giấy tờ, dữ liệu khai và định danh cư dân đều dừng ở backend."""
    admin, _ = await _user(client, db_pool, "xm_admin_sach", role="admin")
    _, a = await _user(client, db_pool, "xm_khach_sach")
    gia_lap([_record(a)])

    raw = (await client.get(LIST, headers=_auth(admin))).text

    for cam in ("claimed_data", "proof_image_urls", "giay-to.png", "RES-BI-MAT", "001234567890", "Lâm Thành Bảo"):
        assert cam not in raw, f"rò ra màn giám sát: {cam}"
    assert "/review" not in raw, "màn giám sát dẫn admin tới cổng duyệt"


@pytest.mark.asyncio
async def test_a_deleted_account_still_shows_its_record(client, db_pool, gia_lap):
    """Hồ sơ không được biến mất khỏi audit chỉ vì tài khoản không còn."""
    admin, _ = await _user(client, db_pool, "xm_admin_mat_tk", role="admin")
    gia_lap([_record(str(uuid.uuid4()))])

    response = await client.get(LIST, headers=_auth(admin))

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["account"]["display_name"] == "Tài khoản không còn hoạt động"
    assert item["account"]["username"] is None


@pytest.mark.parametrize(
    "query,mong_doi",
    [("record_type=apartment", 1), ("record_type=vehicle", 1), ("status=PENDING", 1), ("status=APPROVED", 1)],
)
@pytest.mark.asyncio
async def test_the_filters_reach_the_provider(client, db_pool, gia_lap, query, mong_doi):
    admin, _ = await _user(client, db_pool, f"xm_admin_loc_{abs(hash(query)) % 10000}", role="admin")
    _, a = await _user(client, db_pool, f"xm_khach_loc_{abs(hash(query)) % 10000}")
    gia_lap([_record(a), _record(a, loai="vehicle", status="APPROVED")])

    response = await client.get(f"{LIST}?{query}", headers=_auth(admin))

    assert response.status_code == 200
    assert len(response.json()["items"]) == mong_doi


@pytest.mark.parametrize("query", ["record_type=khong_co", "status=DANG_CHO"])
@pytest.mark.asyncio
async def test_an_unsupported_filter_is_refused_not_ignored(client, db_pool, gia_lap, query):
    """Bỏ qua filter lạ nghĩa là trả một danh sách RỘNG hơn điều người hỏi tưởng."""
    admin, _ = await _user(client, db_pool, f"xm_admin_loc_la_{abs(hash(query)) % 10000}", role="admin")
    gia_lap([])
    assert (await client.get(f"{LIST}?{query}", headers=_auth(admin))).status_code == 422


@pytest.mark.asyncio
async def test_a_dead_provider_is_a_generic_503(client, db_pool, gia_lap):
    """Provider chết thì nói provider chết — không kèm URL, exception hay payload."""
    admin, _ = await _user(client, db_pool, "xm_admin_provider_chet", role="admin")
    gia_lap(
        [], error=OwnershipProviderError(503, "SERVICE_UNAVAILABLE", "connect to http://mock-ownership:8004 failed")
    )

    response = await client.get(LIST, headers=_auth(admin))

    assert response.status_code == 503, response.status_code
    body = response.text
    assert "8004" not in body and "http://" not in body
    assert "OwnershipProviderError" not in body


@pytest.mark.asyncio
async def test_reading_the_monitoring_surface_changes_nothing(client, db_pool, gia_lap):
    """GET là GET: không đụng provider record, không đụng link, không tạo workflow."""
    admin, _ = await _user(client, db_pool, "xm_admin_chi_doc", role="admin")
    _, a = await _user(client, db_pool, "xm_khach_chi_doc")
    fake = gia_lap([_record(a)])
    truoc = {
        "links": await db_pool.fetchval("SELECT count(*) FROM user_resident_links"),
        "workflows": await db_pool.fetchval("SELECT count(*) FROM workflows"),
        "tasks": await db_pool.fetchval("SELECT count(*) FROM workflow_tasks"),
        "records": [dict(r) for r in fake.records],
    }

    assert (await client.get(LIST, headers=_auth(admin))).status_code == 200

    assert await db_pool.fetchval("SELECT count(*) FROM user_resident_links") == truoc["links"]
    assert await db_pool.fetchval("SELECT count(*) FROM workflows") == truoc["workflows"]
    assert await db_pool.fetchval("SELECT count(*) FROM workflow_tasks") == truoc["tasks"]
    assert [dict(r) for r in fake.records] == truoc["records"], "GET sửa dữ liệu của provider"


@pytest.mark.parametrize("method", ["post", "patch", "put", "delete"])
@pytest.mark.asyncio
async def test_the_verification_monitor_has_no_write_verb(client, db_pool, method):
    admin, _ = await _user(client, db_pool, f"xm_admin_ghi_{method}", role="admin")
    response = await client.request(method.upper(), LIST, headers=_auth(admin))
    assert response.status_code == 405, f"{method.upper()} {LIST} → {response.status_code}"
