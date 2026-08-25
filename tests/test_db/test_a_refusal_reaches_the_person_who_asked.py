"""Lý do đơn vị từ chối phải đi tới người dùng, không chết trong bảng duyệt.

Owner: Thành Bảo (Decision layer)
File: tests/test_db/test_a_refusal_reaches_the_person_who_asked.py

NGUYÊN VĂN ca đã báo, workflow 4f76cbfe trên stack demo:

    màn hình:            "Yêu cầu bảo trì — Bước này đã được huỷ trước khi hoàn tất"
    service_approvals:   REJECTED | NO_AVAILABILITY
                         "Không có nhân viên rảnh vào giờ này"
    workflow_tasks T4:   error_code = NULL, result_data = NULL
    repair_hints:        0 dòng

Đơn vị nói rõ vì sao. Câu đó nằm trong database và không có đường nào ra màn
hình. Người dùng đọc "đã được huỷ" rồi không biết làm gì tiếp.

`_facts_for` là chỗ mã nguồn đã dành sẵn: "Mọi dữ kiện backend tra được cho
tình huống này. MỘT chỗ gom… để nguồn thứ ba không mọc thành một đường riêng
nữa." Nên lý do từ chối vào đây, không mọc đường mới.

Cùng chỗ ấy mang luôn snapshot (`src/orchestration/snapshot.py`) — danh mục
dịch vụ theo quyền tài khoản, bảy dự án thật, và các bước với giá trị của
chúng. Đó là cách chữa gốc cho lỗi cùng họ:

    "có những dự án nào" → Response Agent bịa "Khu A, Khu B, Khu C"

Response Agent được giao việc trả lời mà không được đưa dữ liệu, nên nó lấy
thứ gần nhất trong vốn từ của mình — tên các khu ĐỖ XE.
"""

from __future__ import annotations

import json
import uuid

import pytest

import src.api.routes as routes
from src.api.routes import _facts_for
from src.models.schemas import DemoDetailItem, DemoTaskResult, DemoWorkflowResponse


async def _a_user(pool) -> tuple[str, str]:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, password_hash) VALUES ($1,$2,'x')",
            user_id,
            f"nguoi-{user_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO sessions (session_id, user_id, account_state) VALUES ($1,$2,'resident')",
            str(session_id),
            user_id,
        )
    return str(user_id), str(session_id)


async def _seed_refusal(pool, *, owner_user_id: str) -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
            "VALUES ($1,'đặt lịch và báo bảo trì','SUCCESS',$2)",
            wid,
            uuid.UUID(owner_user_id),
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T4','create_maintenance_request','CANCELLED',$2::jsonb)",
            wid,
            json.dumps({"issue_type": "air_conditioning"}),
        )
        await conn.execute(
            "INSERT INTO service_approvals "
            "(workflow_id, task_id, tool, service_label, details, status, reject_code, reject_reason) "
            "VALUES ($1,'T4','create_maintenance_request','Yêu cầu bảo trì',$2::jsonb,"
            "'REJECTED','NO_AVAILABILITY','Không có nhân viên rảnh vào giờ này')",
            wid,
            json.dumps({}),
        )
    return str(wid)


def _view() -> DemoWorkflowResponse:
    return DemoWorkflowResponse(
        status="SUCCESS",
        tasks=[
            DemoTaskResult(
                task_id="T4",
                tool="create_maintenance_request",
                status="CANCELLED",
                title="Yêu cầu bảo trì",
                message="Bước này đã được huỷ trước khi hoàn tất.",
                details=[],
            ),
            DemoTaskResult(
                task_id="T1",
                tool="schedule_property_viewing",
                status="SUCCESS",
                title="Đặt lịch tham quan",
                message="Đã xác nhận.",
                details=[DemoDetailItem(label="Thời gian", value="2026-08-24 09:30")],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_the_reason_the_provider_gave_is_handed_to_the_answer(client, db_pool):
    """Câu của đơn vị phải có mặt trong dữ kiện mà tầng nói được đưa."""
    user_id, _ = await _a_user(db_pool)
    workflow_id = await _seed_refusal(db_pool, owner_user_id=user_id)

    facts = await _facts_for(
        workflow_id, goal="báo bảo trì", response=_view(), owner_user_id=user_id
    )
    assert facts is not None
    phang = json.dumps(facts, ensure_ascii=False)
    assert "Không có nhân viên rảnh vào giờ này" in phang
    assert "NO_AVAILABILITY" in phang


@pytest.mark.asyncio
async def test_the_answer_layer_is_given_the_real_project_list(client, db_pool):
    """"Khu A/B/C" là khu ĐỖ XE. Nó không bao giờ được là một dự án."""
    user_id, _ = await _a_user(db_pool)
    workflow_id = await _seed_refusal(db_pool, owner_user_id=user_id)

    facts = await _facts_for(
        workflow_id, goal="có những dự án nào", response=_view(), owner_user_id=user_id
    )
    phang = json.dumps(facts, ensure_ascii=False)
    assert "Vinhomes Pearl Bay" in phang
    assert "Khu C" not in phang


@pytest.mark.asyncio
async def test_the_steps_and_their_values_are_handed_over(client, db_pool):
    """"xong chưa" / "đã đổi ngày chưa" trả lời được vì dữ kiện mang giá trị thật."""
    user_id, _ = await _a_user(db_pool)
    workflow_id = await _seed_refusal(db_pool, owner_user_id=user_id)

    facts = await _facts_for(
        workflow_id, goal="xong chưa", response=_view(), owner_user_id=user_id
    )
    phang = json.dumps(facts, ensure_ascii=False)
    assert "2026-08-24 09:30" in phang
    assert "Đặt lịch tham quan" in phang


# HÀNG RÀO QUYỀN. Dữ kiện đi thẳng vào prompt; nói một dịch vụ đang MỞ cho tài
# khoản chưa xác minh căn hộ nghĩa là model sẽ mời họ dùng, họ gõ theo, và bị
# từ chối ở tầng dưới.
@pytest.mark.asyncio
async def test_an_unverified_account_is_never_told_a_resident_service_is_open(client, db_pool):
    user_id, _ = await _a_user(db_pool)
    workflow_id = await _seed_refusal(db_pool, owner_user_id=user_id)

    facts = await _facts_for(
        workflow_id, goal="tôi dùng được gì", response=_view(), owner_user_id=user_id
    )
    phang = json.dumps(facts, ensure_ascii=False)
    assert "Đăng ký phương tiện và chỗ đỗ xe" in phang, "dịch vụ khoá vẫn phải được nêu tên"
    assert "KHOÁ" in phang or "khoá" in phang


async def _verify(pool, user_id: str) -> None:
    """Liên kết căn hộ ĐÃ DUYỆT — nguồn có thẩm quyền, không phải một cờ trong session."""
    resident_id = f"RES-{uuid.uuid4().hex[:8].upper()}"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO residents (resident_id, apartment_code, residential_area, full_name) "
            "VALUES ($1,'P-101','Khu A','Người Thử') ON CONFLICT DO NOTHING",
            resident_id,
        )
        await conn.execute(
            "INSERT INTO user_resident_links (user_id, resident_id, verification_status) "
            "VALUES ($1,$2,'VERIFIED')",
            uuid.UUID(user_id),
            resident_id,
        )


# QUYỀN SUY TỪ NGUỒN THẬT, KHÔNG NHẬN THEO THAM SỐ.
#
# Mã nguồn đã ghi bài học này: "một dòng JSON `account_state: resident` từng đủ
# để mở toàn bộ dịch vụ cư dân". Nếu `_facts_for` nhận `account_state` như một
# tham số thì một caller cầm bản chép cũ sẽ nói với cư dân rằng dịch vụ của họ
# bị khoá — hoặc tệ hơn, nói ngược lại.
@pytest.mark.asyncio
async def test_a_verified_resident_is_never_told_their_service_is_locked(client, db_pool):
    user_id, _ = await _a_user(db_pool)
    await _verify(db_pool, user_id)
    workflow_id = await _seed_refusal(db_pool, owner_user_id=user_id)

    facts = await _facts_for(
        workflow_id, goal="tôi dùng được gì", response=_view(), owner_user_id=user_id
    )
    phang = json.dumps(facts, ensure_ascii=False)
    assert "Đăng ký phương tiện và chỗ đỗ xe (dùng được)" in phang
    assert "KHOÁ" not in phang


@pytest.mark.asyncio
async def test_an_unreadable_permission_falls_back_to_the_narrower_answer(client, db_pool):
    """Không đọc được quyền thì coi như CHƯA xác minh.

    Hướng ngược lại — đoán là cư dân — sẽ mời họ dùng một dịch vụ rồi để tầng
    dưới từ chối, đúng lỗi mà toàn bộ tầng này sinh ra để tránh.
    """
    workflow_id = await _seed_refusal(db_pool, owner_user_id=(await _a_user(db_pool))[0])
    facts = await _facts_for(
        workflow_id, goal="tôi dùng được gì", response=_view(), owner_user_id=None
    )
    phang = json.dumps(facts, ensure_ascii=False)
    assert "KHOÁ" in phang


@pytest.mark.asyncio
async def test_a_failed_permission_read_is_never_guessed_as_verified(client, db_pool, monkeypatch):
    """Database hỏng thì trả lời HẸP hơn, không rộng hơn.

    Hai hướng sai lệch nhau hoàn toàn khi không đọc được quyền:

        đoán là cư dân   → mời họ dùng dịch vụ, tầng dưới từ chối sau khi họ
                           đã gõ xong toàn bộ yêu cầu
        đoán là khách    → nói ít hơn sự thật, họ hỏi lại một câu

    Mutation đổi nhánh lỗi từ "prospect" thành "resident" KHÔNG bị test nào bắt
    trước khi có ca này — và đó chính là hướng biến một sự cố database thành
    một lời mời sai gửi thẳng cho người dùng.
    """
    user_id, _ = await _a_user(db_pool)
    await _verify(db_pool, user_id)
    workflow_id = await _seed_refusal(db_pool, owner_user_id=user_id)

    that = routes.acquire_repository
    goi = {"n": 0}

    async def _hong_lan_dau(*args, **kwargs):
        # Chỉ làm hỏng lượt đọc QUYỀN; các lượt đọc khác vẫn chạy để test không
        # xanh vì một lý do khác.
        goi["n"] += 1
        if goi["n"] == 1:
            raise RuntimeError("database không đọc được")
        return await that(*args, **kwargs)

    monkeypatch.setattr(routes, "acquire_repository", _hong_lan_dau)
    facts = await _facts_for(
        workflow_id, goal="tôi dùng được gì", response=_view(), owner_user_id=user_id
    )
    phang = json.dumps(facts, ensure_ascii=False)
    assert "KHOÁ" in phang, "lỗi đọc quyền bị đoán thành đã xác minh"
