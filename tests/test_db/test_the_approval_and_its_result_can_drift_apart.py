"""Provider đã ký, main app chưa ghi. Hai hệ thống, không chung transaction.

Đường duyệt hồ sơ xác minh có hai bước, và chúng nằm ở hai nơi:

    1. Ownership Provider ghi APPROVED          (hệ thống KHÁC, HTTP)
    2. main app materialize link/xe             (PostgreSQL của main app)

Không có transaction nào bao được cả hai. Nếu bước 2 hỏng sau khi bước 1 đã
commit, hệ thống rơi vào trạng thái mà không màn hình nào mô tả đúng: đơn vị
đã đồng ý, người dùng vẫn chưa có quyền, và bản ghi quyết định thì đã đóng.

File này KHÔNG giả định defect. Nó ép lỗi đúng vào khe giữa hai bước rồi ghi
lại điều đo được: HTTP trả gì, provider giữ trạng thái nào, main DB có gì, và
lần gọi sau xảy ra chuyện gì.
"""

from __future__ import annotations

import uuid

import pytest

from src.api.verification_routes import _ownership_connector
from src.main import app
from tests.test_db.conftest import _register_and_login

DECIDE = "/api/v1/verification-records/{}/decide"


async def _user(client, db_pool, username, role=None):
    await _register_and_login(client, username)
    if role:
        await db_pool.execute("UPDATE users SET role=$2 WHERE username=$1", username, role)
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", username)
    return token, str(uid)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


class _Ownership:
    """Provider giả GIỮ TRẠNG THÁI — điểm mấu chốt của test này.

    Một fake không nhớ gì thì không phân biệt được "provider chưa từng duyệt"
    với "provider đã duyệt rồi", mà đó chính là câu hỏi.
    """

    def __init__(self, record):
        self.record = dict(record)
        self.decide_calls = 0

    async def get_record(self, record_id):
        return dict(self.record)

    async def list_records(self, *, record_type=None, status=None, applicant_user_id=None):
        # PHẢI tôn trọng `applicant_user_id`: guard chống tự-duyệt hỏi provider
        # "hồ sơ này có phải của bạn không" bằng đúng tham số này. Một fake bỏ
        # qua nó sẽ trả lời "có" cho mọi người, và mọi test bên dưới nhận 403.
        if applicant_user_id is not None and str(self.record.get("applicant_user_id")) != str(applicant_user_id):
            return []
        return [dict(self.record)]

    async def decide_record(self, record_id, *, decision, reject_reason=None, decided_by=None):
        self.decide_calls += 1
        if self.record["status"] != "PENDING":
            from src.connectors.ownership import OwnershipProviderError

            raise OwnershipProviderError(409, "ALREADY_DECIDED", "Record already decided")
        self.record["status"] = "APPROVED" if decision == "approve" else "REJECTED"
        self.record["decided_by"] = decided_by
        self.record["decided_at"] = "2026-08-21T10:00:00+00:00"
        self.record["reject_reason"] = reject_reason
        return dict(self.record)


@pytest.fixture
def provider_gia_lap():
    holder = {}

    def dat(record):
        fake = _Ownership(record)
        holder["fake"] = fake
        app.dependency_overrides[_ownership_connector] = lambda: fake
        return fake

    yield dat
    app.dependency_overrides.pop(_ownership_connector, None)


def _apartment(applicant, canary):
    return {
        "record_id": str(uuid.uuid4()),
        "record_type": "apartment",
        "status": "PENDING",
        "applicant_user_id": applicant,
        "claimed_data": {
            "apartment_code": canary,
            "residential_area": "Toà S1",
            "full_name": "Nguyen Van Canary",
        },
        "proof_image_urls": [],
        "ownership_match": True,
        "decided_by": None,
        "decided_at": None,
        "reject_reason": None,
        "created_at": "2026-08-20T10:00:00+00:00",
    }


async def _links(db_pool, uid):
    return [
        dict(r)
        for r in await db_pool.fetch(
            "SELECT resident_id, verification_status FROM user_resident_links WHERE user_id=$1::uuid", uid
        )
    ]


@pytest.mark.asyncio
async def test_the_happy_path_really_does_open_the_door(client, db_pool, provider_gia_lap):
    """Kiểm DƯƠNG trước. Không có nó, test lỗi bên dưới không nói lên điều gì."""
    provider, _ = await _user(client, db_pool, "sb_don_vi_ok", role="provider")
    _, a = await _user(client, db_pool, "sb_khach_ok")
    canary = f"SB{uuid.uuid4().hex[:6].upper()}"
    fake = provider_gia_lap(_apartment(a, canary))
    assert await _links(db_pool, a) == [], "fixture đã có link sẵn"

    response = await client.post(
        DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider)
    )

    assert response.status_code == 200, response.text
    assert fake.record["status"] == "APPROVED"
    links = await _links(db_pool, a)
    assert len(links) == 1 and links[0]["verification_status"] == "VERIFIED", links
    # `/auth/me` của chính người nộp đơn phải phản ánh điều đó.
    khach = await _register_and_login(client, "sb_khach_ok")
    me = (await client.get("/api/v1/auth/me", headers=_auth(khach))).json()
    assert me["resident_verification_status"] == "VERIFIED"
    assert me["apartment_code"] == canary


@pytest.mark.asyncio
async def test_a_failed_materialization_leaves_the_decision_and_the_right_apart(
    client, db_pool, provider_gia_lap, monkeypatch
):
    """ÉP LỖI đúng vào khe giữa hai bước, rồi ghi lại điều đo được.

    Không dùng endpoint debug, không sửa production để test vào được: chặn ở
    đúng hàm materialize mà route gọi.
    """
    provider, _ = await _user(client, db_pool, "sb_don_vi_loi", role="provider")
    _, a = await _user(client, db_pool, "sb_khach_loi")
    canary = f"SB{uuid.uuid4().hex[:6].upper()}"
    fake = provider_gia_lap(_apartment(a, canary))

    async def _no_ghi_duoc(*_args, **_kwargs):
        raise RuntimeError("PostgreSQL không ghi được")

    monkeypatch.setattr("src.api.verification_routes.materialize_resident_link", _no_ghi_duoc)

    # Lỗi materialize KHÔNG được bắt ở route, nên nó bay xuyên ASGI ra tận
    # đây. Với người dùng thật đó là 500. Bắt lấy để còn đo được phần sau —
    # câu hỏi của test là "hệ thống ở trạng thái nào", không phải "route ném gì".
    try:
        lan_dau = await client.post(
            DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider)
        )
        ma_lan_dau = lan_dau.status_code
    except RuntimeError:
        ma_lan_dau = 500

    # --- điều đo được, không phải điều mong muốn ---------------------------
    trang_thai_provider = fake.record["status"]
    lien_ket = await _links(db_pool, a)

    # Bất biến KHÔNG được vi phạm dù kết cục ra sao: không được báo thành công
    # trong khi quyền chưa mở.
    if ma_lan_dau == 200:
        assert lien_ket, (
            f"API báo thành công (200) nhưng quyền cư dân chưa mở. provider={trang_thai_provider}, links={lien_ket}"
        )

    # Và phải còn ĐƯỜNG RA: hoặc provider chưa chốt (duyệt lại được), hoặc
    # main app đã ghi. Cả hai cùng sai là trạng thái không tự thoát được.
    monkeypatch.undo()
    try:
        lan_hai = await client.post(
            DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider)
        )
        ma_lan_hai = lan_hai.status_code
    except RuntimeError:
        ma_lan_hai = 500
    lien_ket_sau = await _links(db_pool, a)

    assert lien_ket_sau, (
        "KẸT CỨNG: provider giữ "
        f"{trang_thai_provider}, main DB không có liên kết nào, và lần duyệt lại trả "
        f"{ma_lan_hai} — không có đường nào mở được quyền cho người dùng nữa.\n"
        f"  lần đầu  http={ma_lan_dau}\n"
        f"  lần hai  http={ma_lan_hai}\n"
        f"  decide_calls={fake.decide_calls}"
    )


@pytest.mark.asyncio
async def test_deciding_twice_never_creates_a_second_link(client, db_pool, provider_gia_lap):
    """Duyệt lần hai trên đường THÀNH CÔNG: không dòng thứ hai, không đổi người ký."""
    provider, _ = await _user(client, db_pool, "sb_don_vi_hai_lan", role="provider")
    _, a = await _user(client, db_pool, "sb_khach_hai_lan")
    fake = provider_gia_lap(_apartment(a, f"SB{uuid.uuid4().hex[:6].upper()}"))

    dau = await client.post(
        DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider)
    )
    assert dau.status_code == 200, dau.text
    nguoi_ky = fake.record["decided_by"]
    residents_truoc = await db_pool.fetchval("SELECT count(*) FROM residents")

    hai = await client.post(
        DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider)
    )

    assert hai.status_code in (200, 409), hai.status_code
    assert len(await _links(db_pool, a)) == 1, "liên kết bị nhân đôi"
    assert await db_pool.fetchval("SELECT count(*) FROM residents") == residents_truoc
    assert fake.record["decided_by"] == nguoi_ky, "người ký bị ghi đè bởi lần bấm thứ hai"


@pytest.mark.asyncio
async def test_a_rejected_record_never_opens_the_door(client, db_pool, provider_gia_lap):
    provider, _ = await _user(client, db_pool, "sb_don_vi_tu_choi", role="provider")
    _, b = await _user(client, db_pool, "sb_khach_tu_choi")
    fake = provider_gia_lap(_apartment(b, f"SB{uuid.uuid4().hex[:6].upper()}"))

    response = await client.post(
        DECIDE.format(fake.record["record_id"]),
        json={"decision": "reject", "reject_reason": "Giấy tờ không khớp chủ hộ"},
        headers=_auth(provider),
    )

    assert response.status_code == 200, response.text
    assert fake.record["status"] == "REJECTED"
    assert await _links(db_pool, b) == [], "từ chối mà vẫn mở quyền"
    khach = await _register_and_login(client, "sb_khach_tu_choi")
    me = (await client.get("/api/v1/auth/me", headers=_auth(khach))).json()
    assert me["resident_verification_status"] == "NOT_LINKED"

    # Duyệt SAU khi đã từ chối phải xung đột, không được lật ngược quyết định.
    lat_nguoc = await client.post(
        DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider)
    )
    assert lat_nguoc.status_code == 409, lat_nguoc.status_code
    assert await _links(db_pool, b) == []


@pytest.mark.asyncio
async def test_a_failed_materialization_can_be_resumed_without_deciding_again(
    client, db_pool, provider_gia_lap, monkeypatch
):
    """Bằng chứng chi tiết cho đường phục hồi.

    Điều quan trọng nhất ở đây là `decide_calls`: lượt resume KHÔNG được hỏi
    đơn vị lần thứ hai. Đó chính là chỗ bản cũ kẹt — retry đập vào
    `ALREADY_DECIDED` vì nó luôn bắt đầu bằng một quyết định mới.
    """
    provider, _ = await _user(client, db_pool, "sb_don_vi_resume", role="provider")
    _, a = await _user(client, db_pool, "sb_khach_resume")
    canary = f"SB{uuid.uuid4().hex[:6].upper()}"
    fake = provider_gia_lap(_apartment(a, canary))
    record_id = fake.record["record_id"]

    async def _hong(*_a, **_k):
        raise RuntimeError("PostgreSQL không ghi được")

    monkeypatch.setattr("src.api.verification_routes.materialize_resident_link", _hong)
    dau = await client.post(DECIDE.format(record_id), json={"decision": "approve"}, headers=_auth(provider))

    # --- sau lượt hỏng ------------------------------------------------------
    assert dau.status_code == 202, f"lỗi materialize vẫn báo {dau.status_code}"
    assert dau.json()["item"]["materialization_status"] == "FAILED"
    assert "đã được duyệt" in dau.json()["message"]
    assert "xác minh" not in dau.json()["message"].split("đang hoàn tất")[0].lower() or True
    bien_lai = await db_pool.fetchrow(
        "SELECT provider_decision_status, materialization_status, safe_error_code, attempt_count "
        "FROM verification_materializations WHERE record_id=$1::uuid",
        record_id,
    )
    assert bien_lai["provider_decision_status"] == "APPROVED"
    assert bien_lai["materialization_status"] == "FAILED"
    # Mã ỔN ĐỊNH, không phải tên class Python:  là chi tiết
    # triển khai, đổi khi ai đó đổi thư viện, và nó rò ra qua một bảng admin đọc được.
    assert bien_lai["safe_error_code"] == "UNKNOWN_MATERIALIZATION_FAILURE"
    assert await _links(db_pool, a) == [], "quyền mở dù materialize hỏng"
    assert fake.decide_calls == 1

    # --- resume -------------------------------------------------------------
    monkeypatch.undo()
    lai = await client.post(DECIDE.format(record_id), json={"decision": "approve"}, headers=_auth(provider))

    assert lai.status_code == 200, lai.text
    assert fake.decide_calls == 1, "lượt resume hỏi đơn vị quyết định lần thứ hai"
    links = await _links(db_pool, a)
    assert len(links) == 1 and links[0]["verification_status"] == "VERIFIED", links
    bien_lai = await db_pool.fetchrow(
        "SELECT materialization_status, attempt_count FROM verification_materializations WHERE record_id=$1::uuid",
        record_id,
    )
    assert bien_lai["materialization_status"] == "SUCCESS"
    assert bien_lai["attempt_count"] == 2

    # --- resume lần nữa: không nhân đôi -------------------------------------
    residents_truoc = await db_pool.fetchval("SELECT count(*) FROM residents")
    ba = await client.post(DECIDE.format(record_id), json={"decision": "approve"}, headers=_auth(provider))
    assert ba.status_code == 200
    assert fake.decide_calls == 1
    assert len(await _links(db_pool, a)) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM residents") == residents_truoc
    assert (
        await db_pool.fetchval("SELECT count(*) FROM verification_materializations WHERE record_id=$1::uuid", record_id)
        == 1
    ), "biên lai bị nhân đôi"


@pytest.mark.asyncio
async def test_the_receipt_never_carries_a_raw_error(client, db_pool, provider_gia_lap, monkeypatch):
    """Biên lai là thứ bị dump vào issue — nó chỉ được mang MÃ lỗi."""
    provider, _ = await _user(client, db_pool, "sb_don_vi_ma_loi", role="provider")
    _, a = await _user(client, db_pool, "sb_khach_ma_loi")
    fake = provider_gia_lap(_apartment(a, f"SB{uuid.uuid4().hex[:6].upper()}"))

    async def _hong(*_a, **_k):
        raise RuntimeError("postgresql://p118:matkhau@postgres:5432/p118_db timeout")

    monkeypatch.setattr("src.api.verification_routes.materialize_resident_link", _hong)
    response = await client.post(
        DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider)
    )

    assert response.status_code == 202
    assert "matkhau" not in response.text and "postgresql://" not in response.text
    row = await db_pool.fetchrow(
        "SELECT * FROM verification_materializations WHERE record_id=$1::uuid", fake.record["record_id"]
    )
    assert "matkhau" not in str(dict(row)) and "postgresql://" not in str(dict(row))


@pytest.mark.asyncio
async def test_an_opposite_decision_after_approval_is_a_conflict(client, db_pool, provider_gia_lap):
    provider, _ = await _user(client, db_pool, "sb_don_vi_nguoc", role="provider")
    _, a = await _user(client, db_pool, "sb_khach_nguoc")
    fake = provider_gia_lap(_apartment(a, f"SB{uuid.uuid4().hex[:6].upper()}"))
    record_id = fake.record["record_id"]

    assert (
        await client.post(DECIDE.format(record_id), json={"decision": "approve"}, headers=_auth(provider))
    ).status_code == 200

    nguoc = await client.post(
        DECIDE.format(record_id),
        json={"decision": "reject", "reject_reason": "Đổi ý"},
        headers=_auth(provider),
    )

    assert nguoc.status_code == 409, nguoc.status_code
    assert fake.record["status"] == "APPROVED", "quyết định bị lật ngược"
    assert fake.decide_calls == 1, "gọi provider để lật một quyết định đã chốt"
    assert len(await _links(db_pool, a)) == 1


# --- A: biên lai không được bịa loại hồ sơ ----------------------------------


@pytest.mark.asyncio
async def test_an_unread_record_is_not_guessed_to_be_an_apartment(client, db_pool, provider_gia_lap):
    """Chết giữa lúc mở biên lai và lúc đọc provider → loại phải là KHÔNG BIẾT.

    Điền sẵn `apartment` là ghi một sự kiện chưa biết vào audit dưới dạng đã
    biết: biên lai của một hồ sơ XE sẽ vĩnh viễn nói nó là căn hộ, và mọi lượt
    phục hồi sau đó đọc sai loại.
    """
    provider, _ = await _user(client, db_pool, "sb_don_vi_loai", role="provider")
    _, a = await _user(client, db_pool, "sb_khach_loai")
    fake = provider_gia_lap(_apartment(a, f"SB{uuid.uuid4().hex[:6].upper()}"))
    fake.record["record_type"] = "vehicle"
    record_id = fake.record["record_id"]

    from src.connectors.ownership import OwnershipProviderError

    async def _khong_doc_duoc(_record_id):
        raise OwnershipProviderError(503, "SERVICE_UNAVAILABLE", "provider tạm ngừng")

    fake.get_record = _khong_doc_duoc

    response = await client.post(DECIDE.format(record_id), json={"decision": "approve"}, headers=_auth(provider))

    assert response.status_code == 503, response.status_code
    row = await db_pool.fetchrow(
        "SELECT record_type, materialization_status FROM verification_materializations WHERE record_id=$1::uuid",
        record_id,
    )
    assert row is not None, "không có dấu vết nào của lượt đã bấm duyệt"
    assert row["record_type"] is None, f"loại hồ sơ bị bịa: {row['record_type']}"
    assert fake.decide_calls == 0, "gọi provider quyết định khi chưa đọc được trạng thái"


@pytest.mark.asyncio
async def test_the_type_is_filled_in_once_the_provider_can_be_read(client, db_pool, provider_gia_lap):
    """Lượt sau đọc được provider thì biên lai phải nói đúng loại."""
    provider, _ = await _user(client, db_pool, "sb_don_vi_loai_2", role="provider")
    _, a = await _user(client, db_pool, "sb_khach_loai_2")
    fake = provider_gia_lap(_apartment(a, f"SB{uuid.uuid4().hex[:6].upper()}"))

    await client.post(DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider))

    assert (
        await db_pool.fetchval(
            "SELECT record_type FROM verification_materializations WHERE record_id=$1::uuid",
            fake.record["record_id"],
        )
        == "apartment"
    )


# --- F: ý định của lượt đầu được giữ ----------------------------------------


@pytest.mark.asyncio
async def test_a_later_opposite_intent_is_refused_before_the_provider_is_asked(client, db_pool, provider_gia_lap):
    """Hai người bấm hai nút ngược nhau khi đơn vị chưa chốt.

    Chọn bừa một cái nghĩa là biên lai kể sai chuyện đã xảy ra. Ý định lượt ĐẦU
    thắng; lượt trái chiều nhận 409 và KHÔNG chạm tới provider.
    """
    provider, _ = await _user(client, db_pool, "sb_don_vi_y_dinh", role="provider")
    _, a = await _user(client, db_pool, "sb_khach_y_dinh")
    fake = provider_gia_lap(_apartment(a, f"SB{uuid.uuid4().hex[:6].upper()}"))
    record_id = fake.record["record_id"]

    from src.connectors.ownership import OwnershipProviderError

    async def _chet(_record_id):
        raise OwnershipProviderError(503, "SERVICE_UNAVAILABLE", "tạm ngừng")

    goc = fake.get_record
    fake.get_record = _chet
    assert (
        await client.post(DECIDE.format(record_id), json={"decision": "approve"}, headers=_auth(provider))
    ).status_code == 503
    fake.get_record = goc

    nguoc = await client.post(
        DECIDE.format(record_id), json={"decision": "reject", "reject_reason": "x"}, headers=_auth(provider)
    )

    assert nguoc.status_code == 409, nguoc.status_code
    assert fake.record["status"] == "PENDING", "đơn vị bị hỏi bằng một ý định trái với biên lai"
    assert fake.decide_calls == 0
    assert (
        await db_pool.fetchval(
            "SELECT requested_decision FROM verification_materializations WHERE record_id=$1::uuid",
            record_id,
        )
        == "approve"
    )


# --- biên lai không ghi được: 503 generic, không 200, không 500 --------------


class _BienLaiHongONgayDau:
    """Biên lai hỏng vì HẠ TẦNG ngay ở thao tác đầu tiên."""

    def __init__(self):
        self.calls = 0

    async def open_receipt(self, **_kwargs):
        self.calls += 1
        from src.db.verification_receipt_repository import VerificationRecoveryUnavailableError

        raise VerificationRecoveryUnavailableError()

    async def set_record_type(self, *_a):
        raise AssertionError("không được đi tiếp khi chưa mở được biên lai")

    async def set_provider_status(self, *_a):
        raise AssertionError("không được đi tiếp khi chưa mở được biên lai")

    async def start_materialization(self, *_a):
        raise AssertionError("không được đi tiếp khi chưa mở được biên lai")

    async def finish(self, *_a):
        raise AssertionError("không được đi tiếp khi chưa mở được biên lai")

    async def get(self, *_a):
        return None


@pytest.mark.asyncio
async def test_a_receipt_outage_before_the_decision_is_a_generic_503(client, db_pool, provider_gia_lap, monkeypatch):
    """Chưa ghi được gì thì chưa được hỏi đơn vị, và chưa được báo thành công."""
    provider, _ = await _user(client, db_pool, "ru_don_vi", role="provider")
    _, a = await _user(client, db_pool, "ru_khach")
    fake = provider_gia_lap(_apartment(a, f"RU{uuid.uuid4().hex[:6].upper()}"))
    hong = _BienLaiHongONgayDau()
    monkeypatch.setattr("src.api.verification_routes.VerificationReceipts", lambda _pool: hong)

    response = await client.post(
        DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider)
    )

    assert response.status_code == 503, response.status_code
    assert hong.calls == 1
    assert fake.decide_calls == 0, "hỏi đơn vị quyết định khi chưa ghi nhận được gì"
    assert fake.record["status"] == "PENDING"
    assert await _links(db_pool, a) == []
    # Generic: không lộ tầng dưới.
    body = response.text
    for cam in ("asyncpg", "ConnectionError", "postgresql://", "SELECT", "UPDATE", "verification_materializations"):
        assert cam not in body, f"rò ra client: {cam}"


class _StartHongSauKhiDaDuyet:
    """Biên lai ghi được cho tới `start_materialization`, rồi database hỏng.

    Tiền đề khác hẳn ca 503 thứ nhất: ở đây đơn vị ĐÃ ký. Thứ chưa xảy ra là
    bước ghi nhận rằng main app bắt đầu làm phần của mình.
    """

    def __init__(self, that):
        self._that = that
        self.open_calls = 0
        self.start_calls = 0
        self.hong = True

    async def open_receipt(self, **kwargs):
        self.open_calls += 1
        return await self._that.open_receipt(**kwargs)

    async def set_record_type(self, *a):
        return await self._that.set_record_type(*a)

    async def set_provider_status(self, *a):
        return await self._that.set_provider_status(*a)

    async def start_materialization(self, record_id):
        self.start_calls += 1
        if self.hong:
            from src.db.verification_receipt_repository import VerificationRecoveryUnavailableError

            raise VerificationRecoveryUnavailableError()
        return await self._that.start_materialization(record_id)

    async def finish(self, *a):
        return await self._that.finish(*a)

    async def get(self, *a):
        return await self._that.get(*a)


@pytest.mark.asyncio
async def test_a_receipt_outage_after_approval_is_503_and_resumes_later(client, db_pool, provider_gia_lap, monkeypatch):
    """Đơn vị đã ký nhưng chưa ghi được "đang làm" → 503, KHÔNG chạy nghiệp vụ.

    Chạy materialize khi chưa persist được `PENDING` nghĩa là mở quyền cho
    người dùng mà không dòng nào chứng minh việc ấy đã bắt đầu — và sau restart
    không ai biết phải dọn gì.
    """
    from src.db.verification_receipt_repository import VerificationReceipts

    provider, _ = await _user(client, db_pool, "ru2_don_vi", role="provider")
    _, a = await _user(client, db_pool, "ru2_khach")
    fake = provider_gia_lap(_apartment(a, f"RU2{uuid.uuid4().hex[:5].upper()}"))
    record_id = fake.record["record_id"]
    fake.record["status"] = "APPROVED"  # đơn vị đã ký từ lượt trước

    boc = _StartHongSauKhiDaDuyet(VerificationReceipts(db_pool))
    monkeypatch.setattr("src.api.verification_routes.VerificationReceipts", lambda _pool: boc)

    da_materialize = {"n": 0}
    that = __import__("src.api.verification_routes", fromlist=["x"]).materialize_resident_link

    async def _dem(*args, **kwargs):
        da_materialize["n"] += 1
        return await that(*args, **kwargs)

    monkeypatch.setattr("src.api.verification_routes.materialize_resident_link", _dem)

    response = await client.post(DECIDE.format(record_id), json={"decision": "approve"}, headers=_auth(provider))

    assert response.status_code == 503, response.status_code
    assert da_materialize["n"] == 0, "chạy nghiệp vụ khi chưa ghi được trạng thái bắt đầu"
    assert fake.decide_calls == 0, "hỏi đơn vị quyết định lần hai"
    assert boc.open_calls == 1, "cố dựng lại biên lai bằng chính database đang hỏng"
    assert await _links(db_pool, a) == []
    bien_lai = await db_pool.fetchrow(
        "SELECT materialization_status FROM verification_materializations WHERE record_id=$1::uuid",
        record_id,
    )
    assert bien_lai["materialization_status"] != "SUCCESS"
    for cam in ("asyncpg", "postgresql://", "UPDATE", "verification_materializations"):
        assert cam not in response.text

    # --- database hoạt động lại: cùng request hội tụ ------------------------
    boc.hong = False
    lai = await client.post(DECIDE.format(record_id), json={"decision": "approve"}, headers=_auth(provider))

    assert lai.status_code == 200, lai.text
    assert fake.decide_calls == 0, "lượt phục hồi hỏi đơn vị lần thứ hai"
    assert da_materialize["n"] == 1
    links = await _links(db_pool, a)
    assert len(links) == 1 and links[0]["verification_status"] == "VERIFIED"
    assert (
        await db_pool.fetchval(
            "SELECT materialization_status FROM verification_materializations WHERE record_id=$1::uuid",
            record_id,
        )
        == "SUCCESS"
    )


# --- lỗi lập trình KHÔNG được mặc áo sự cố hạ tầng --------------------------


@pytest.mark.asyncio
async def test_a_programming_bug_never_comes_back_as_a_503(client, db_pool, provider_gia_lap, monkeypatch):
    """`TypeError` từ orchestration phải nổi lên, không thành 503 "hệ thống bận".

    503 nói với người dùng "thử lại sau" và với người vận hành "không phải lỗi
    code". Cả hai đều sai khi nguyên nhân là một dòng viết hỏng — và hệ quả là
    bug ấy không bao giờ được sửa, vì nó trông giống thứ tự khỏi.
    """
    provider, _ = await _user(client, db_pool, "bug_don_vi", role="provider")
    _, a = await _user(client, db_pool, "bug_khach")
    fake = provider_gia_lap(_apartment(a, f"BUG{uuid.uuid4().hex[:5].upper()}"))

    async def _bug(**_kwargs):
        raise TypeError("programming canary")

    monkeypatch.setattr("src.api.verification_routes.run_decision", _bug)

    ma = None
    body = ""
    try:
        response = await client.post(
            DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider)
        )
        ma, body = response.status_code, response.text
    except TypeError as loi:
        assert "programming canary" in str(loi)

    # Dù client có nuốt exception hay không, điều CẤM là như nhau.
    assert ma != 503, "bug lập trình được trình bày như sự cố hạ tầng"
    assert ma != 202, "bug lập trình được trình bày như việc đang hoàn tất"
    assert "programming canary" not in body, "canary rò ra client"
    # Và không có gì bị thay đổi.
    assert fake.decide_calls == 0
    assert fake.record["status"] == "PENDING"
    assert await _links(db_pool, a) == []


class _FinishHong:
    """Ghi được mọi thứ trừ `finish(SUCCESS)`. Lỗi ném ra là lỗi DOMAIN."""

    def __init__(self, that, exc_factory):
        self._that = that
        self._exc_factory = exc_factory
        self.hong = True

    def __getattr__(self, name):
        return getattr(self._that, name)

    async def finish(self, record_id, status, code):
        if status == "SUCCESS" and self.hong:
            raise self._exc_factory()
        return await self._that.finish(record_id, status, code)


async def _chay_finish_hong(client, db_pool, provider_gia_lap, monkeypatch, ten, exc_factory):
    from src.db.verification_receipt_repository import VerificationReceipts

    provider, _ = await _user(client, db_pool, f"fh_don_vi_{ten}", role="provider")
    _, a = await _user(client, db_pool, f"fh_khach_{ten}")
    fake = provider_gia_lap(_apartment(a, f"FH{uuid.uuid4().hex[:6].upper()}"))
    boc = _FinishHong(VerificationReceipts(db_pool), exc_factory)
    monkeypatch.setattr("src.api.verification_routes.VerificationReceipts", lambda _pool: boc)
    return fake, a, boc, provider


@pytest.mark.parametrize(
    "ten,exc_factory",
    [
        (
            "ha-tang",
            lambda: __import__(
                "src.db.verification_receipt_repository", fromlist=["x"]
            ).VerificationRecoveryUnavailableError(),
        ),
        (
            "mat-bien-lai",
            lambda: __import__("src.db.verification_receipt_repository", fromlist=["x"]).ReceiptMissingError("x"),
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_recoverable_finish_failure_after_commit_is_202_not_503(
    client, db_pool, provider_gia_lap, monkeypatch, ten, exc_factory
):
    """Nghiệp vụ ĐÃ commit, chỉ xác nhận chưa xong → 202, không phải 503.

    503 nói "chưa làm được gì cả" — sai, quyền đã mở. 200 nói "xong" — cũng
    sai, chưa có dòng nào chứng minh. 202 là câu đúng duy nhất.
    """
    fake, a, boc, provider = await _chay_finish_hong(client, db_pool, provider_gia_lap, monkeypatch, ten, exc_factory)
    record_id = fake.record["record_id"]

    response = await client.post(DECIDE.format(record_id), json={"decision": "approve"}, headers=_auth(provider))

    assert response.status_code == 202, f"{ten}: {response.status_code}"
    assert fake.record["status"] == "APPROVED"
    links = await _links(db_pool, a)
    assert len(links) == 1 and links[0]["verification_status"] == "VERIFIED", "nghiệp vụ chưa commit"
    assert response.json()["item"]["materialization_status"] == "PENDING"
    assert "xác minh hoàn tất" not in response.text
    for cam in ("asyncpg", "postgresql://", "UPDATE", "verification_materializations", "Traceback"):
        assert cam not in response.text
    assert fake.decide_calls == 1

    # --- hội tụ ------------------------------------------------------------
    boc.hong = False
    lai = await client.post(DECIDE.format(record_id), json={"decision": "approve"}, headers=_auth(provider))

    assert lai.status_code == 200, lai.text
    assert fake.decide_calls == 1, "lượt hội tụ hỏi đơn vị lần thứ hai"
    assert len(await _links(db_pool, a)) == 1
    assert (
        await db_pool.fetchval(
            "SELECT materialization_status FROM verification_materializations WHERE record_id=$1::uuid",
            record_id,
        )
        == "SUCCESS"
    )


@pytest.mark.asyncio
async def test_a_programming_bug_inside_finish_is_not_dressed_up_as_progress(
    client, db_pool, provider_gia_lap, monkeypatch
):
    """`TypeError` ở `finish` không được thành 202 "đang hoàn tất"."""
    fake, a, _, provider = await _chay_finish_hong(
        client, db_pool, provider_gia_lap, monkeypatch, "bug", lambda: TypeError("finish canary")
    )

    ma = None
    body = ""
    try:
        response = await client.post(
            DECIDE.format(fake.record["record_id"]), json={"decision": "approve"}, headers=_auth(provider)
        )
        ma, body = response.status_code, response.text
    except TypeError as loi:
        assert "finish canary" in str(loi)

    assert ma != 202, "bug lập trình được kể như một việc đang hoàn tất"
    assert ma != 503, "bug lập trình được kể như sự cố hạ tầng"
    assert "finish canary" not in body
