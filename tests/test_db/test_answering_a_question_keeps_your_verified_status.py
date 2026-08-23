"""Trả lời một câu hỏi không được làm mất quyền cư dân đã xác minh.

Chuỗi thật đã đo được
---------------------
Cư dân `thanhbao` (`user_resident_links.verification_status = VERIFIED` từ
18/08) chạy một yêu cầu ba dịch vụ. Workflow CHA chạy trót lọt:

    T2 register_vehicle  SUCCESS      ← hai tool CHỈ dành cho cư dân
    T3 book_parking      SUCCESS
    T1 schedule_property_viewing CANCELLED   ← đơn vị từ chối, mở câu hỏi

Họ trả lời câu hỏi. Workflow CON, tạo một phút sau, cùng tài khoản, cùng phiên:

    status=FAILED  error_code=ACTION_DENIED  0 bước
    "Bạn chưa đủ điều kiện dùng dịch vụ này vì chưa xác minh căn hộ."

Nguyên nhân
-----------
`ResidentAccessBoundary` đọc quyền từ NGỮ CẢNH:

    if needs_resident and self._context.get("resident_verification_status") != "VERIFIED":
        raise ResidentAccessRequiredError

`/start` dựng ngữ cảnh ấy bằng `_trusted_account_context(user)` — đọc thẳng
`user_resident_links`, nguồn có thẩm quyền. `/continue` thì KHÔNG: nó dựng lại
từ `workflow_clarifications.existing_context`, và dòng ấy trong database là:

    existing_context = {}

Rỗng. Nên mọi workflow con đều chạy như một khách chưa xác minh — và câu trả
lời họ nhận được là lời mời đi xác minh một căn hộ họ đã xác minh từ năm ngày
trước.

Quyền phải đọc từ TOKEN + database ở MỌI lượt, không thừa hưởng từ một bản chép
đã ghim. Bản chép có thể rỗng, có thể cũ, và không bao giờ là nguồn sự thật cho
một câu hỏi về quyền.
"""

from __future__ import annotations

import uuid

import pytest

GOAL = "Đăng ký phương tiện và giữ chỗ đỗ xe Khu A ngày 2029-12-20, biển số 51H-99999, ô tô."


async def _cu_dan_da_xac_minh(client, db_pool) -> tuple[str, str, str]:
    """(token, user_id, resident_id) — đúng hình dạng của một tài khoản thật."""
    from tests.test_db.conftest import _register_and_login

    username = f"cudan_{uuid.uuid4().hex[:8]}"
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id::text FROM users WHERE username=$1", username)
    rid = f"RES-{uuid.uuid4().hex[:8].upper()}"
    await db_pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
        " VALUES ($1,'Nguyen Van A','A1201','Vinhomes Ocean Park')",
        rid,
    )
    await db_pool.execute(
        "INSERT INTO user_resident_links (user_id, resident_id, verification_status, verified_at)"
        " VALUES ($1::uuid,$2,'VERIFIED',NOW())",
        uid,
        rid,
    )
    return token, uid, rid


async def _cau_hoi_dang_treo(db_pool, uid: str) -> tuple[str, str]:
    """Workflow cha đang hỏi lại, với `existing_context` RỖNG — đúng như đã đo."""
    wid, sid = uuid.uuid4(), str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO sessions (session_id, user_id, account_state) VALUES ($1::uuid,$2,'resident')", sid, uid
    )
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, session_id)"
        " VALUES ($1,$2,'FAILED',$3,$4::uuid)",
        wid,
        GOAL,
        uid,
        sid,
    )
    from src.orchestration.runtime_provider import acquire_repository

    repository = await acquire_repository()
    await repository.save_clarification(
        str(wid),
        session_id=sid,
        parent_workflow_id=None,
        goal=GOAL,
        missing_fields=["parking_zone"],
        question="Khu A đã hết chỗ. Bạn chọn khu khác giúp mình nhé.",
        existing_context={},
    )
    return str(wid), sid


@pytest.mark.asyncio
async def test_the_child_workflow_is_not_denied(client, db_pool, monkeypatch):
    """Đây là lỗi được báo."""
    from src.api import routes

    ke_hoach: list[dict] = []

    async def _bat_lai(workflow_id, goal, approve, urls, account_state, **kwargs):
        # Ngữ cảnh mà lượt chạy sẽ dùng nằm trong `_DEMO_JOBS`; chữ ký thật của
        # `_run_demo_job` không nhận `job` như một tham số.
        ke_hoach.append({**routes._DEMO_JOBS.get(workflow_id, {}), "account_state": account_state})

    monkeypatch.setattr(routes, "_run_demo_job", _bat_lai)

    token, uid, _rid = await _cu_dan_da_xac_minh(client, db_pool)
    wid, _sid = await _cau_hoi_dang_treo(db_pool, uid)

    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"fields": {"parking_zone": "ZONE_B"}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 202, res.text
    assert ke_hoach, "không lượt chạy nào được xếp cho câu trả lời"
    ngu_canh = ke_hoach[0].get("existing_context") or {}
    assert ngu_canh.get("resident_verification_status") == "VERIFIED", (
        f"quyền cư dân biến mất khi trả lời câu hỏi: {ngu_canh}"
    )
    assert ke_hoach[0].get("account_state") == "resident", ke_hoach[0].get("account_state")


@pytest.mark.asyncio
async def test_an_unverified_account_is_still_refused(client, db_pool, monkeypatch):
    """Nạp lại quyền KHÔNG phải nới quyền.

    Tài khoản chưa xác minh vẫn phải bị từ chối — nếu không, bản vá này biến
    một lỗi mất quyền thành một lỗ hổng cấp quyền.
    """
    from src.api import routes
    from tests.test_db.conftest import _register_and_login

    ke_hoach: list[dict] = []

    async def _bat_lai(workflow_id, goal, approve, urls, account_state, **kwargs):
        # Ngữ cảnh mà lượt chạy sẽ dùng nằm trong `_DEMO_JOBS`; chữ ký thật của
        # `_run_demo_job` không nhận `job` như một tham số.
        ke_hoach.append({**routes._DEMO_JOBS.get(workflow_id, {}), "account_state": account_state})

    monkeypatch.setattr(routes, "_run_demo_job", _bat_lai)

    username = f"khach_{uuid.uuid4().hex[:8]}"
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id::text FROM users WHERE username=$1", username)
    wid, _sid = await _cau_hoi_dang_treo(db_pool, uid)

    await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"fields": {"parking_zone": "ZONE_B"}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert ke_hoach, "không lượt chạy nào được xếp"
    ngu_canh = ke_hoach[0].get("existing_context") or {}
    assert ngu_canh.get("resident_verification_status") != "VERIFIED", (
        f"cấp quyền cho tài khoản chưa xác minh: {ngu_canh}"
    )
    assert ke_hoach[0].get("account_state") == "prospect"


@pytest.mark.asyncio
async def test_the_answer_itself_is_not_overwritten(client, db_pool, monkeypatch):
    """Nạp lại quyền không được đè lên chính câu trả lời của khách."""
    from src.api import routes

    ke_hoach: list[dict] = []

    async def _bat_lai(workflow_id, goal, approve, urls, account_state, **kwargs):
        # Ngữ cảnh mà lượt chạy sẽ dùng nằm trong `_DEMO_JOBS`; chữ ký thật của
        # `_run_demo_job` không nhận `job` như một tham số.
        ke_hoach.append({**routes._DEMO_JOBS.get(workflow_id, {}), "account_state": account_state})

    monkeypatch.setattr(routes, "_run_demo_job", _bat_lai)

    token, uid, _rid = await _cu_dan_da_xac_minh(client, db_pool)
    wid, _sid = await _cau_hoi_dang_treo(db_pool, uid)

    await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"fields": {"parking_zone": "ZONE_B"}},
        headers={"Authorization": f"Bearer {token}"},
    )

    ngu_canh = ke_hoach[0].get("existing_context") or {}
    assert ngu_canh.get("parking_zone") == "ZONE_B", f"câu trả lời bị ngữ cảnh tin cậy đè mất: {ngu_canh}"


@pytest.mark.asyncio
async def test_a_stale_identity_in_the_pinned_copy_never_wins(client, db_pool, monkeypatch):
    """Bản ghim mang `resident_id` CŨ thì bản tra từ database phải thắng.

    Mã ấy quyết định chỗ đỗ xe được đặt cho ai. Một mã của lượt trước — hoặc
    của một liên kết đã đổi — không được đi tiếp chỉ vì nó nằm sẵn trong bản
    chép.
    """
    from src.api import routes

    ke_hoach: list[dict] = []

    async def _bat_lai(workflow_id, goal, approve, urls, account_state, **kwargs):
        ke_hoach.append({**routes._DEMO_JOBS.get(workflow_id, {}), "account_state": account_state})

    monkeypatch.setattr(routes, "_run_demo_job", _bat_lai)

    token, uid, rid = await _cu_dan_da_xac_minh(client, db_pool)
    wid, sid = uuid.uuid4(), str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO sessions (session_id, user_id, account_state) VALUES ($1::uuid,$2,'resident')", sid, uid
    )
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, session_id)"
        " VALUES ($1,$2,'FAILED',$3,$4::uuid)",
        wid,
        GOAL,
        uid,
        sid,
    )
    from src.orchestration.runtime_provider import acquire_repository

    repository = await acquire_repository()
    await repository.save_clarification(
        str(wid),
        session_id=sid,
        parent_workflow_id=None,
        goal=GOAL,
        missing_fields=["parking_zone"],
        question="Khu A đã hết chỗ.",
        existing_context={"resident_id": "RES-CUA-NGUOI-KHAC"},
    )

    await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"fields": {"parking_zone": "ZONE_B"}},
        headers={"Authorization": f"Bearer {token}"},
    )

    ngu_canh = ke_hoach[0].get("existing_context") or {}
    assert ngu_canh.get("resident_id") == rid, f"mã cư dân cũ trong bản ghim vẫn thắng: {ngu_canh.get('resident_id')}"


@pytest.mark.asyncio
async def test_verifying_mid_session_takes_effect_without_a_new_conversation(client, db_pool, monkeypatch):
    """Xác minh xong giữa chừng thì dùng được ngay, không phải mở cuộc mới.

    Persona ghim vào `sessions` tồn tại để chặn một dòng JSON trong body tự
    nhận quyền — nó chặn INPUT CỦA CLIENT. Dùng nó để kìm cả kết quả tra từ
    database thì người vừa xác minh xong vẫn bị coi là khách, và họ không có
    cách nào biết phải mở một cuộc mới.
    """
    from src.api import routes
    from tests.test_db.conftest import _register_and_login

    ke_hoach: list[dict] = []

    async def _bat_lai(workflow_id, goal, approve, urls, account_state, **kwargs):
        ke_hoach.append({**routes._DEMO_JOBS.get(workflow_id, {}), "account_state": account_state})

    monkeypatch.setattr(routes, "_run_demo_job", _bat_lai)

    username = f"vua_xac_minh_{uuid.uuid4().hex[:6]}"
    token = await _register_and_login(client, username)
    uid = await db_pool.fetchval("SELECT id::text FROM users WHERE username=$1", username)

    # Phiên mở lúc còn là KHÁCH.
    wid, sid = uuid.uuid4(), str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO sessions (session_id, user_id, account_state) VALUES ($1::uuid,$2,'prospect')", sid, uid
    )
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, session_id)"
        " VALUES ($1,$2,'FAILED',$3,$4::uuid)",
        wid,
        GOAL,
        uid,
        sid,
    )
    from src.orchestration.runtime_provider import acquire_repository

    repository = await acquire_repository()
    await repository.save_clarification(
        str(wid),
        session_id=sid,
        parent_workflow_id=None,
        goal=GOAL,
        missing_fields=["parking_zone"],
        question="Khu A đã hết chỗ.",
        existing_context={},
    )

    # …rồi họ xác minh căn hộ, GIỮA phiên.
    rid = f"RES-{uuid.uuid4().hex[:8].upper()}"
    await db_pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
        " VALUES ($1,'Nguyen Van B','B0202','Vinhomes Ocean Park')",
        rid,
    )
    await db_pool.execute(
        "INSERT INTO user_resident_links (user_id, resident_id, verification_status, verified_at)"
        " VALUES ($1::uuid,$2,'VERIFIED',NOW())",
        uid,
        rid,
    )

    await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"fields": {"parking_zone": "ZONE_B"}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert ke_hoach[0].get("account_state") == "resident", "phiên cũ kìm quyền của người vừa xác minh xong"


def test_the_permission_is_read_from_the_database_not_the_pinned_copy():
    """Quyền đọc từ `_trusted_account_context`, không từ `existing_context`.

    Bản chép đã ghim có thể rỗng, có thể cũ — và trong ca đã đo, nó rỗng.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "src" / "api" / "routes.py").read_text(encoding="utf-8")
    than = src[src.index("async def continue_demo_workflow") :]
    than = than[: than.index("\n@router.")]

    assert "_trusted_account_context" in than, "`/continue` vẫn tin bản chép đã ghim cho câu hỏi về quyền"
