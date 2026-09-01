"""Đường chọn đọc từ `service_quotes`, và chỉ từ đó — không ghi gì.

Bước B khoá việc "thứ được đề xuất phải có chứng từ". Bước C không được phá nó:
hàm chọn nhận báo giá ĐÃ PERSIST, không tính giá tại chỗ, không gọi provider,
không dùng bảng giá trong mã.

Và bước C CHỈ ĐỌC. Sau một lượt chọn, database phải y nguyên: không báo giá nào
chuyển sang CONFIRMED, không dòng nào vào hàng đợi duyệt, không bước nào đổi
trạng thái. Đề xuất chưa phải cam kết — đó là bước D.

Chạy qua PostgreSQL thật vì đó là chỗ duy nhất kiểm được cả hai vế: đọc đúng
chứng từ nào, và không để lại dấu vết nào.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.db.quote_repository import luu_bao_gia
from src.orchestration.provider_selection import chon_don_vi_cho_buoc
from src.orchestration.quote import van_tay_yeu_cau

DICH_VU = "schedule_move"
YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}
VAN_TAY = van_tay_yeu_cau(YEU_CAU)


async def _workflow(pool, *, tasks=("T1",)) -> str:
    wid = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid, 'chuyển nhà', 'PENDING')", wid
    )
    for task in tasks:
        await pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
            "VALUES ($1::uuid, $2, 'schedule_move', 'PENDING', '[]'::jsonb)",
            wid,
            task,
        )
    return wid


async def _bao_gia(pool, wid, don_vi, gia, *, task="T1", van_tay=VAN_TAY, han_phut=30):
    return await luu_bao_gia(
        pool,
        external_quote_id=f"Q-{uuid.uuid4().hex[:10]}",
        service_provider_id=don_vi,
        service_type=DICH_VU,
        amount=gia,
        currency="VND",
        request_fingerprint=van_tay,
        valid_until=datetime.now(UTC) + timedelta(minutes=han_phut),
        workflow_id=wid,
        task_id=task,
    )


async def _anh_chup(pool, wid):
    """Toàn bộ dấu vết một lượt chọn có thể để lại, nếu nó không thật sự chỉ đọc."""
    return {
        "trang_thai_bao_gia": sorted(
            (r["status"], r["service_provider_id"])
            for r in await pool.fetch(
                "SELECT status, service_provider_id FROM service_quotes WHERE workflow_id = $1::uuid", wid
            )
        ),
        "hang_doi_duyet": await pool.fetchval(
            "SELECT count(*) FROM service_approvals WHERE workflow_id = $1::uuid", wid
        ),
        "trang_thai_buoc": sorted(
            (r["task_id"], r["status"])
            for r in await pool.fetch("SELECT task_id, status FROM workflow_tasks WHERE workflow_id = $1::uuid", wid)
        ),
    }


@pytest.mark.asyncio
async def test_the_recommendation_is_a_row_that_exists(db_pool):
    """Thứ được chọn phải đọc lại được bằng mã của nó."""
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-01", 430_000)
    re_nhat = await _bao_gia(db_pool, wid, "MOV-03", 420_000)

    ket_qua = await chon_don_vi_cho_buoc(
        db_pool, workflow_id=wid, task_id="T1", service_type=DICH_VU, request_fingerprint=VAN_TAY
    )

    assert ket_qua.ket_qua == "SELECTED"
    assert ket_qua.bao_gia.quote_id == re_nhat.quote_id
    assert ket_qua.bao_gia.external_quote_id, "đề xuất không mang mã của đơn vị"


@pytest.mark.asyncio
async def test_choosing_changes_nothing_in_the_database(db_pool):
    """Sau một lượt chọn, database y nguyên. Đề xuất chưa phải cam kết."""
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-01", 430_000)
    await _bao_gia(db_pool, wid, "MOV-02", 470_000)
    truoc = await _anh_chup(db_pool, wid)

    for tham_so in (
        {},
        {"max_price": 100_000},
        {"ten_don_vi_khach_noi": "Đại Tín"},
        {"ten_don_vi_khach_noi": "Đại Tín", "max_price": 100_000},
        {"ten_don_vi_khach_noi": "Không Có Bên Này"},
        {"ten_don_vi_khach_noi": "MOV"},
    ):
        await chon_don_vi_cho_buoc(
            db_pool,
            workflow_id=wid,
            task_id="T1",
            service_type=DICH_VU,
            request_fingerprint=VAN_TAY,
            **tham_so,
        )

    assert await _anh_chup(db_pool, wid) == truoc, "một lượt chọn đã để lại dấu vết"


@pytest.mark.asyncio
async def test_the_choice_never_reaches_the_previous_generation_of_quotes(db_pool):
    """Lọc theo vân tay là BẮT BUỘC, không phải tối ưu.

    Một bước có thể còn chứng từ của đời yêu cầu trước nếu lượt dọn chưa chạy.
    Chọn trong đó nghĩa là chọn theo một yêu cầu khách không còn hỏi — và vì
    đời cũ thường rẻ hơn (ít yêu cầu hơn), nó sẽ luôn thắng.
    """
    wid = await _workflow(db_pool)
    van_tay_cu = van_tay_yeu_cau({**YEU_CAU, "move_vehicle": "none"})
    await _bao_gia(db_pool, wid, "MOV-01", 200_000, van_tay=van_tay_cu)
    doi_moi = await _bao_gia(db_pool, wid, "MOV-01", 430_000)

    ket_qua = await chon_don_vi_cho_buoc(
        db_pool, workflow_id=wid, task_id="T1", service_type=DICH_VU, request_fingerprint=VAN_TAY
    )

    assert ket_qua.bao_gia.quote_id == doi_moi.quote_id
    assert ket_qua.bao_gia.amount == 430_000, "đang chọn theo yêu cầu khách đã đổi"


@pytest.mark.asyncio
async def test_the_choice_never_reaches_another_step(db_pool):
    wid = await _workflow(db_pool, tasks=("T1", "T5"))
    await _bao_gia(db_pool, wid, "MOV-03", 100_000, task="T5")
    cua_t1 = await _bao_gia(db_pool, wid, "MOV-01", 430_000, task="T1")

    ket_qua = await chon_don_vi_cho_buoc(
        db_pool, workflow_id=wid, task_id="T1", service_type=DICH_VU, request_fingerprint=VAN_TAY
    )

    assert ket_qua.bao_gia.quote_id == cua_t1.quote_id


@pytest.mark.asyncio
async def test_the_choice_never_reaches_another_workflow(db_pool):
    cua_toi = await _workflow(db_pool)
    cua_nguoi_khac = await _workflow(db_pool)
    await _bao_gia(db_pool, cua_nguoi_khac, "MOV-03", 100_000)
    cua_toi_q = await _bao_gia(db_pool, cua_toi, "MOV-01", 430_000)

    ket_qua = await chon_don_vi_cho_buoc(
        db_pool, workflow_id=cua_toi, task_id="T1", service_type=DICH_VU, request_fingerprint=VAN_TAY
    )

    assert ket_qua.bao_gia.quote_id == cua_toi_q.quote_id


@pytest.mark.asyncio
async def test_a_step_with_no_quotes_says_so(db_pool):
    wid = await _workflow(db_pool)
    ket_qua = await chon_don_vi_cho_buoc(
        db_pool, workflow_id=wid, task_id="T1", service_type=DICH_VU, request_fingerprint=VAN_TAY
    )
    assert ket_qua.ket_qua == "NO_AVAILABLE_QUOTE"


@pytest.mark.asyncio
async def test_a_quote_that_expired_in_the_database_is_not_offered(db_pool):
    """Hết hạn ở đồng hồ của database — cùng đồng hồ mà bước quét và lệnh xác
    nhận dùng, nên ba chỗ không thể nói ba điều khác nhau."""
    wid = await _workflow(db_pool)
    re_nhung_chet = await _bao_gia(db_pool, wid, "MOV-03", 100_000)
    await db_pool.execute(
        "UPDATE service_quotes SET valid_until = NOW() - INTERVAL '1 minute' WHERE quote_id = $1::uuid",
        re_nhung_chet.quote_id,
    )
    con_song = await _bao_gia(db_pool, wid, "MOV-01", 430_000)

    ket_qua = await chon_don_vi_cho_buoc(
        db_pool, workflow_id=wid, task_id="T1", service_type=DICH_VU, request_fingerprint=VAN_TAY
    )

    assert ket_qua.bao_gia.quote_id == con_song.quote_id


@pytest.mark.asyncio
async def test_a_named_unit_is_honoured_end_to_end(db_pool):
    """Đường "chỉ đích danh", chạy qua database thật từ tên tới chứng từ."""
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-03", 420_000)
    dai_tin = await _bao_gia(db_pool, wid, "MOV-02", 470_000)

    ket_qua = await chon_don_vi_cho_buoc(
        db_pool,
        workflow_id=wid,
        task_id="T1",
        service_type=DICH_VU,
        request_fingerprint=VAN_TAY,
        ten_don_vi_khach_noi="vận tải đại tín",
    )

    assert ket_qua.bao_gia.quote_id == dai_tin.quote_id, "chọn bên rẻ hơn thay vì bên khách chỉ định"


@pytest.mark.asyncio
async def test_a_quote_of_another_service_in_the_same_step_is_never_chosen(db_pool):
    """Hàng rào dịch vụ đứng ở HÀM CHỌN, nên nó đúng cả khi chứng từ tới từ database.

    Một bước chỉ có `tool` = `schedule_move`, nên `luu_bao_gia` không cho ghi
    chứng từ bảo trì vào đó — hai hàng rào cho cùng một luật. Ở đây chứng từ
    được gieo THẲNG, vòng qua hàng rào thứ nhất, để kiểm hàng rào thứ hai đứng
    độc lập chứ không dựa vào hàng rào thứ nhất.
    """
    wid = await _workflow(db_pool)
    await db_pool.execute(
        "INSERT INTO service_quotes (quote_id, external_quote_id, service_provider_id, service_type, "
        "amount, currency, request_fingerprint, valid_until, workflow_id, task_id) "
        "VALUES (gen_random_uuid(), $1, 'FIX-01', 'create_maintenance_request', 100000, 'VND', $2, "
        "NOW() + INTERVAL '30 min', $3::uuid, 'T1')",
        f"Q-{uuid.uuid4().hex[:10]}",
        VAN_TAY,
        wid,
    )
    cua_chuyen_nha = await _bao_gia(db_pool, wid, "MOV-01", 430_000)

    ket_qua = await chon_don_vi_cho_buoc(
        db_pool, workflow_id=wid, task_id="T1", service_type=DICH_VU, request_fingerprint=VAN_TAY
    )

    assert ket_qua.bao_gia.quote_id == cua_chuyen_nha.quote_id, "chọn phải chứng từ của ngành khác"


@pytest.mark.asyncio
async def test_an_unreadable_budget_stops_before_any_read_is_used(db_pool):
    """`-1` không được thành `OVER_BUDGET`, kể cả trên đường đi qua database."""
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-01", 430_000)

    for hong in (-1, 0, "450000", True):
        ket_qua = await chon_don_vi_cho_buoc(
            db_pool,
            workflow_id=wid,
            task_id="T1",
            service_type=DICH_VU,
            request_fingerprint=VAN_TAY,
            max_price=hong,
        )
        assert ket_qua.ket_qua == "INVALID_BUDGET", f"{hong!r} → {ket_qua.ket_qua}"


@pytest.mark.asyncio
async def test_a_generic_word_never_selects_a_unit_end_to_end(db_pool):
    """Model trích "chuyển nhà" vào ô tên → không có `SELECTED`, trên dữ liệu thật."""
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-01", 430_000)

    for cum in ("chuyển nhà", "vận tải", "dịch vụ"):
        ket_qua = await chon_don_vi_cho_buoc(
            db_pool,
            workflow_id=wid,
            task_id="T1",
            service_type=DICH_VU,
            request_fingerprint=VAN_TAY,
            ten_don_vi_khach_noi=cum,
        )
        assert ket_qua.ket_qua == "UNKNOWN_PROVIDER", f"{cum!r} → {ket_qua.ket_qua}"
        assert ket_qua.bao_gia is None


@pytest.mark.asyncio
async def test_a_named_unit_over_budget_is_a_conflict_end_to_end(db_pool):
    wid = await _workflow(db_pool)
    await _bao_gia(db_pool, wid, "MOV-03", 420_000)
    await _bao_gia(db_pool, wid, "MOV-02", 470_000)

    ket_qua = await chon_don_vi_cho_buoc(
        db_pool,
        workflow_id=wid,
        task_id="T1",
        service_type=DICH_VU,
        request_fingerprint=VAN_TAY,
        ten_don_vi_khach_noi="Đại Tín",
        max_price=450_000,
    )

    assert ket_qua.ket_qua == "OVER_BUDGET"
    assert (ket_qua.provider_id, ket_qua.bao_gia.amount, ket_qua.gia_re_nhat) == ("MOV-02", 470_000, 420_000)
