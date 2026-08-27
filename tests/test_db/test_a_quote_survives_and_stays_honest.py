"""Báo giá persist, đọc lại đúng, và không dùng lại được cho việc khác.

Bước A khoá quyền sở hữu, nhưng đơn vị vẫn đến từ một bảng cứng trong mã. Bước
B đổi nguồn danh tính ấy sang một CHỨNG TỪ: đơn vị nào, giá nào, cho yêu cầu
nào, neo vào bước nào, còn hạn tới bao giờ.

Chứng từ chỉ có giá trị nếu nó không sửa được từ phía người tiêu thụ, và nếu
không có đường nào tạo ra một chứng từ nửa vời. Nên:

  * `workflow_id`/`task_id` là NOT NULL, có khoá ngoại tổng hợp tới
    `workflow_tasks`, và `kiem_bao_gia()` LUÔN kiểm chúng — không phải "kiểm
    nếu caller chịu truyền". Một điều kiện tuỳ chọn là một điều kiện không tồn
    tại.
  * Bước được neo phải có `tool` trùng `service_type`: báo giá chuyển nhà
    không neo được vào một bước tra cứu.
  * HẠN được thực thi ở ĐỒNG HỒ CỦA DATABASE, trong cùng lệnh ghi. Kiểm ở tầng
    ứng dụng rồi mới `UPDATE` để hở một cửa sổ, và cửa sổ ấy mở đúng lúc hệ
    thống bận nhất.

Chạy qua PostgreSQL THẬT: tính bền vững và tính nguyên tử là hai thứ không kiểm
được bằng một object giả.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.db.quote_repository import (
    bao_gia_dang_song,
    doc_bao_gia,
    het_han_bao_gia_qua_han,
    luu_bao_gia,
    thay_the_bao_gia_cu,
    xac_nhan_bao_gia,
)
from src.orchestration.quote import QuoteInvalidError, kiem_bao_gia, van_tay_yeu_cau

YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}
VAN_TAY = van_tay_yeu_cau(YEU_CAU)
DICH_VU = "schedule_move"
# Bước TRA CỨU nhà cung cấp — không tiêu thụ gì, nên một chứng từ neo ở đó sẽ
# không bao giờ được đối chiếu.
TRA_CUU = "search_properties"


def _sau(phut: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=phut)


async def _mot_workflow(pool, *, task="T1", tool=DICH_VU) -> str:
    """Workflow VÀ bước thật — khoá ngoại tổng hợp không cho neo vào hư vô."""
    wid = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid, 'chuyển nhà', 'PENDING')",
        wid,
    )
    await _them_buoc(pool, wid, task, tool)
    return wid


async def _them_buoc(pool, wid, task, tool=DICH_VU) -> None:
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
        "VALUES ($1::uuid, $2, $3, 'PENDING', '[]'::jsonb)",
        wid,
        task,
        tool,
    )


async def _bao_gia(pool, wid, *, don_vi="MOV-02", gia=470_000, han_phut=30, van_tay=VAN_TAY, task="T1"):
    return await luu_bao_gia(
        pool,
        external_quote_id=f"QMOV-{uuid.uuid4().hex[:8]}",
        service_provider_id=don_vi,
        service_type=DICH_VU,
        amount=gia,
        currency="VND",
        request_fingerprint=van_tay,
        valid_until=_sau(han_phut),
        workflow_id=wid,
        task_id=task,
    )


async def _lam_cho_het_han(pool, quote_id: str) -> None:
    """Đẩy hạn về quá khứ — mô phỏng THỜI GIAN TRÔI QUA, không phải gieo dữ liệu xấu.

    `luu_bao_gia()` từ chối ghi một báo giá đã hết hạn, nên đây là cách duy
    nhất và cũng là cách trung thực: chứng từ được phát ra hợp lệ, rồi hết hạn.
    """
    await pool.execute(
        "UPDATE service_quotes SET valid_until = NOW() - INTERVAL '1 minute' WHERE quote_id = $1::uuid",
        quote_id,
    )


def _tieu_thu(bao_gia, **ghi_de):
    """Bộ tham số "đúng hết" để tiêu thụ; test chỉ ghi đè phần nó phá."""
    tham_so = {
        "service_type": bao_gia.service_type,
        "service_provider_id": bao_gia.service_provider_id,
        "request_fingerprint": bao_gia.request_fingerprint,
        "amount": bao_gia.amount,
        "currency": bao_gia.currency,
        "workflow_id": bao_gia.workflow_id,
        "task_id": bao_gia.task_id,
    }
    tham_so.update(ghi_de)
    return tham_so


# ------------------------------------------------------------------ bền vững
@pytest.mark.asyncio
async def test_a_quote_outlives_the_objects_that_made_it(db_pool):
    """Báo giá sống qua việc dựng repository/service object mới.

    Điều một bản mock trong RAM không bao giờ kiểm được. Nếu báo giá chỉ sống
    trong một biến của tiến trình thì mọi restart, mọi worker thứ hai, mọi lượt
    deploy đều xoá sạch — và khách quay lại thấy giá đã đổi mà không ai làm gì.
    """
    wid = await _mot_workflow(db_pool)
    goc = await _bao_gia(db_pool, wid)

    doc_lai = await doc_bao_gia(db_pool, goc.quote_id)

    assert doc_lai is not None, "báo giá không đọc lại được"
    assert (doc_lai.external_quote_id, doc_lai.service_provider_id, doc_lai.amount) == (
        goc.external_quote_id,
        goc.service_provider_id,
        goc.amount,
    )
    assert (doc_lai.workflow_id, doc_lai.task_id) == (wid, "T1")
    assert doc_lai.request_fingerprint == VAN_TAY
    assert doc_lai.status == "ACTIVE"
    assert doc_lai.created_at is not None and doc_lai.confirmed_at is None


@pytest.mark.asyncio
async def test_two_different_moves_get_two_different_quotes(db_pool):
    wid = await _mot_workflow(db_pool)
    van_tay_xe_tai = van_tay_yeu_cau({**YEU_CAU, "move_vehicle": "truck"})
    a = await _bao_gia(db_pool, wid, gia=470_000, van_tay=VAN_TAY)
    b = await _bao_gia(db_pool, wid, gia=680_000, van_tay=van_tay_xe_tai)

    assert a.request_fingerprint != b.request_fingerprint
    chi_xe_van = await bao_gia_dang_song(
        db_pool, workflow_id=wid, task_id="T1", request_fingerprint=VAN_TAY
    )
    assert [q.quote_id for q in chi_xe_van] == [a.quote_id]


# --------------------------------------------------------------- NEO bắt buộc
@pytest.mark.asyncio
async def test_a_quote_cannot_be_persisted_without_a_workflow_or_a_step(db_pool):
    """Không có đường nào tạo ra một chứng từ không thuộc ai.

    Bản đầu để hai tham số này mặc định `None`, nên một call site mới chỉ cần
    QUÊN là đủ sinh ra một báo giá vô chủ — và nó trông y hệt báo giá thật cho
    tới lúc ai đó cố tiêu thụ.
    """
    wid = await _mot_workflow(db_pool)
    for thieu in ({"workflow_id": ""}, {"task_id": ""}):
        with pytest.raises((ValueError, TypeError)):
            await luu_bao_gia(
                db_pool,
                external_quote_id=f"QMOV-{uuid.uuid4().hex[:8]}",
                service_provider_id="MOV-02",
                service_type=DICH_VU,
                amount=470_000,
                currency="VND",
                request_fingerprint=VAN_TAY,
                valid_until=_sau(30),
                **{"workflow_id": wid, "task_id": "T1", **thieu},
            )
    assert await bao_gia_dang_song(db_pool, workflow_id=wid, task_id="T1") == []


@pytest.mark.asyncio
async def test_a_quote_cannot_be_persisted_for_a_step_that_does_not_exist(db_pool):
    """Neo vào một `task_id` không có thật cũng là neo vào hư vô.

    Khoá ngoại chỉ tới `workflows` là chưa đủ: workflow có thật, bước thì không,
    và chứng từ vẫn ghi được.
    """
    wid = await _mot_workflow(db_pool)
    with pytest.raises(ValueError):
        await _bao_gia(db_pool, wid, task="T99")
    assert await db_pool.fetchval(
        "SELECT count(*) FROM service_quotes WHERE workflow_id = $1::uuid", uuid.UUID(wid)
    ) == 0


@pytest.mark.asyncio
async def test_a_quote_cannot_be_anchored_to_a_lookup_step(db_pool):
    """Báo giá chuyển nhà phải neo vào bước TIÊU THỤ, không vào bước tra cứu.

    Bước tra cứu không tiêu thụ gì, nên chứng từ neo ở đó không bao giờ được
    đối chiếu — và cũng không bao giờ hết hạn theo cách ai đó nhìn thấy. Nó
    nằm im, hợp lệ về hình thức, và vô dụng.
    """
    wid = await _mot_workflow(db_pool, task="T1", tool=DICH_VU)
    await _them_buoc(db_pool, wid, "T0", TRA_CUU)

    with pytest.raises(ValueError, match="schedule_move"):
        await _bao_gia(db_pool, wid, task="T0")

    hop_le = await _bao_gia(db_pool, wid, task="T1")
    assert hop_le.task_id == "T1"


@pytest.mark.asyncio
async def test_a_quote_of_a_lookup_step_cannot_be_used_by_the_scheduling_step(db_pool):
    """Và nếu bằng cách nào đó nó tồn tại, cổng vẫn chặn ở đường tiêu thụ.

    Hai hàng rào cho cùng một luật: một ở lượt ghi, một ở lượt đọc. Ở đây chứng
    từ được gieo THẲNG vào database, vòng qua `luu_bao_gia()`, để kiểm rằng
    hàng rào thứ hai đứng độc lập chứ không dựa vào hàng rào thứ nhất.
    """
    wid = await _mot_workflow(db_pool, task="T1", tool=DICH_VU)
    await _them_buoc(db_pool, wid, "T0", TRA_CUU)
    quote_id = uuid.uuid4()
    await db_pool.execute(
        "INSERT INTO service_quotes (quote_id, external_quote_id, service_provider_id, service_type, "
        "amount, currency, request_fingerprint, valid_until, workflow_id, task_id) "
        "VALUES ($1::uuid, $2, 'MOV-02', $3, 470000, 'VND', $4, NOW() + INTERVAL '30 min', $5::uuid, 'T0')",
        quote_id,
        f"QMOV-{uuid.uuid4().hex[:8]}",
        DICH_VU,
        VAN_TAY,
        wid,
    )
    cua_buoc_tra_cuu = await doc_bao_gia(db_pool, str(quote_id))

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(cua_buoc_tra_cuu, **_tieu_thu(cua_buoc_tra_cuu, task_id="T1"))
    assert loi.value.ma == "QUOTE_WRONG_TASK"


@pytest.mark.asyncio
async def test_the_ownership_check_is_not_optional(db_pool):
    """`kiem_bao_gia()` KHÔNG gọi được nếu thiếu neo.

    Bản đầu cho phép bỏ qua hai tham số này, và khi bỏ qua thì hai điều kiện
    tương ứng cũng bị bỏ qua. Bài kiểm này khoá chính chữ ký hàm: quên truyền
    là `TypeError` ngay lúc gọi, không phải một lượt kiểm im lặng thiếu hai vế.
    """
    wid = await _mot_workflow(db_pool)
    bao_gia = await _bao_gia(db_pool, wid)
    day_du = _tieu_thu(bao_gia)
    for bo in ("workflow_id", "task_id"):
        thieu = {k: v for k, v in day_du.items() if k != bo}
        with pytest.raises(TypeError):
            kiem_bao_gia(bao_gia, **thieu)


# ------------------------------------------------------------------ hết hạn
@pytest.mark.asyncio
async def test_a_provider_quote_that_is_already_expired_is_never_stored(db_pool):
    """Đơn vị trả một báo giá đã quá hạn → không ghi.

    Ghi nó vào rồi chờ một bước quét chuyển sang EXPIRED là tạo rác kèm một
    khoảng thời gian nó trông như còn sống — và trong khoảng ấy nó là một đề
    xuất hợp lệ trên màn hình.
    """
    wid = await _mot_workflow(db_pool)
    with pytest.raises(ValueError):
        await _bao_gia(db_pool, wid, han_phut=-1)
    assert await bao_gia_dang_song(db_pool, workflow_id=wid, task_id="T1") == []


@pytest.mark.asyncio
async def test_an_expired_quote_never_reaches_the_recommendation(db_pool):
    """Hết hạn thì biến mất khỏi danh sách "đang sống" — trước cả bước lọc giá.

    Bản đầu cố ý không lọc hạn ở đường đọc, và rồi không tầng nào lọc: một báo
    giá quá hạn vẫn thành đề xuất, và chỉ bị chặn tận lúc tiêu thụ — tức sau
    khi khách đã thấy nó như một lựa chọn có thật.
    """
    wid = await _mot_workflow(db_pool)
    het = await _bao_gia(db_pool, wid, don_vi="MOV-01", gia=100_000)
    con = await _bao_gia(db_pool, wid, don_vi="MOV-02", gia=470_000)
    await _lam_cho_het_han(db_pool, het.quote_id)

    dang_song = await bao_gia_dang_song(db_pool, workflow_id=wid, task_id="T1")

    assert [q.quote_id for q in dang_song] == [con.quote_id], "báo giá hết hạn vẫn được đem ra chọn"


@pytest.mark.asyncio
async def test_an_expired_quote_cannot_be_used(db_pool):
    """Còn hiệu lực nghĩa là đơn vị CÒN GIỮ CHỖ. Dùng một báo giá quá hạn là
    đem một lời hứa đã hết đi thu tiền — đơn vị có quyền từ chối, và người chịu
    là khách."""
    wid = await _mot_workflow(db_pool)
    bao_gia = await _bao_gia(db_pool, wid)
    await _lam_cho_het_han(db_pool, bao_gia.quote_id)
    het_han = await doc_bao_gia(db_pool, bao_gia.quote_id)

    assert het_han.het_han
    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(het_han, **_tieu_thu(het_han))
    assert loi.value.ma == "QUOTE_EXPIRED"


@pytest.mark.asyncio
async def test_expiry_is_checked_before_the_details(db_pool):
    """Thứ tự kiểm: hết hạn được báo TRƯỚC những sai lệch nhỏ hơn.

    Nếu một báo giá vừa hết hạn vừa sai giá mà thông điệp nói "số tiền không
    khớp", người xử lý sẽ đi tìm lỗi ở chỗ không có.
    """
    wid = await _mot_workflow(db_pool)
    bao_gia = await _bao_gia(db_pool, wid)
    await _lam_cho_het_han(db_pool, bao_gia.quote_id)
    het_han = await doc_bao_gia(db_pool, bao_gia.quote_id)

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(het_han, **_tieu_thu(het_han, amount=1))
    assert loi.value.ma == "QUOTE_EXPIRED"


@pytest.mark.asyncio
async def test_an_expired_quote_cannot_be_confirmed_even_bypassing_the_gate(db_pool):
    """Lệnh xác nhận tự kiểm hạn — KHÔNG dựa vào ai đó gọi cổng trước.

    Bản đầu chỉ có `WHERE quote_id = $1 AND status = 'ACTIVE'`, nên một báo giá
    quá hạn vẫn chuyển được sang CONFIRMED. Ở đây cổng bị BỎ QUA cố ý: điều
    đang kiểm là mệnh đề `WHERE`, không phải kỷ luật của call site.
    """
    wid = await _mot_workflow(db_pool)
    bao_gia = await _bao_gia(db_pool, wid)
    await _lam_cho_het_han(db_pool, bao_gia.quote_id)

    ket_qua = await xac_nhan_bao_gia(db_pool, bao_gia.quote_id, **_tieu_thu(bao_gia))

    assert ket_qua is None, "báo giá hết hạn vẫn xác nhận được"
    assert (await doc_bao_gia(db_pool, bao_gia.quote_id)).status == "ACTIVE"


@pytest.mark.asyncio
async def test_confirming_at_the_expiry_boundary_has_exactly_one_valid_outcome(db_pool):
    """Ngay tại biên hết hạn: hoặc CONFIRMED, hoặc không đổi gì. Không có ở giữa.

    Cửa sổ giữa "kiểm thấy còn hạn" và "lệnh ghi chạy" là chỗ một chứng từ có
    thể chết. Vì cả hai vế nằm trong MỘT lệnh, kết quả chỉ có hai khả năng — và
    trạng thái luôn nhất quán với việc lệnh có trả về dòng nào hay không.
    """
    wid = await _mot_workflow(db_pool)
    bao_gia = await _bao_gia(db_pool, wid)
    # Hạn đúng "ngay bây giờ": lệnh chạy sau đó vài micro giây nên nó đã qua.
    await db_pool.execute(
        "UPDATE service_quotes SET valid_until = NOW() WHERE quote_id = $1::uuid", bao_gia.quote_id
    )

    ket_qua = await xac_nhan_bao_gia(db_pool, bao_gia.quote_id, **_tieu_thu(bao_gia))
    sau = await doc_bao_gia(db_pool, bao_gia.quote_id)

    assert (ket_qua is not None) == (sau.status == "CONFIRMED"), (
        "lệnh nói một đằng, trạng thái một nẻo"
    )
    assert (sau.confirmed_at is not None) == (sau.status == "CONFIRMED")


@pytest.mark.asyncio
async def test_an_expired_row_does_not_block_a_new_quote_forever(db_pool):
    """Hết hạn rồi hỏi lại: dòng cũ EXPIRED, dòng mới ACTIVE.

    Ràng buộc `UNIQUE ... WHERE status = 'ACTIVE'` chỉ nhìn `status`, và thời
    gian trôi qua KHÔNG tự đổi `status`. Không có bước quét thì một dòng quá
    hạn vẫn mang `ACTIVE` và chặn VĨNH VIỄN mọi lượt hỏi lại của cùng đơn vị
    cho cùng yêu cầu — ràng buộc dựng lên để chống trùng lại thành cái khoá cửa.
    """
    wid = await _mot_workflow(db_pool)
    cu = await _bao_gia(db_pool, wid, don_vi="MOV-02", gia=470_000)
    await _lam_cho_het_han(db_pool, cu.quote_id)

    da_quet = await het_han_bao_gia_qua_han(db_pool, workflow_id=wid, task_id="T1")
    moi = await _bao_gia(db_pool, wid, don_vi="MOV-02", gia=520_000)

    assert da_quet == 1
    assert (await doc_bao_gia(db_pool, cu.quote_id)).status == "EXPIRED"
    assert moi.status == "ACTIVE"
    dang_song = await bao_gia_dang_song(db_pool, workflow_id=wid, task_id="T1")
    assert [q.quote_id for q in dang_song] == [moi.quote_id]


@pytest.mark.asyncio
async def test_the_sweep_never_touches_a_quote_that_is_still_alive(db_pool):
    """Quét hạn là phép dọn, không phải phép xoá sổ."""
    wid = await _mot_workflow(db_pool)
    con_han = await _bao_gia(db_pool, wid, don_vi="MOV-01")
    het = await _bao_gia(db_pool, wid, don_vi="MOV-02")
    await _lam_cho_het_han(db_pool, het.quote_id)

    assert await het_han_bao_gia_qua_han(db_pool, workflow_id=wid, task_id="T1") == 1
    assert (await doc_bao_gia(db_pool, con_han.quote_id)).status == "ACTIVE"


# ------------------------------------------------------- yêu cầu đã đổi
@pytest.mark.asyncio
async def test_an_old_quote_is_refused_after_the_request_changes(db_pool):
    """Ca thật: xin giá cho xe van, thấy rẻ, rồi sửa thành xe tải và bấm xác
    nhận. Không có vế này thì đơn vị nhận một việc họ chưa bao giờ báo giá."""
    wid = await _mot_workflow(db_pool)
    cu = await _bao_gia(db_pool, wid)
    van_tay_moi = van_tay_yeu_cau({**YEU_CAU, "move_vehicle": "truck"})

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(cu, **_tieu_thu(cu, request_fingerprint=van_tay_moi))
    assert loi.value.ma == "QUOTE_STALE_REQUEST"


@pytest.mark.asyncio
async def test_a_stale_quote_cannot_be_confirmed_even_bypassing_the_gate(db_pool):
    """Vân tay cũng nằm trong mệnh đề `WHERE`, không chỉ ở cổng."""
    wid = await _mot_workflow(db_pool)
    cu = await _bao_gia(db_pool, wid)
    van_tay_moi = van_tay_yeu_cau({**YEU_CAU, "move_vehicle": "truck"})

    ket_qua = await xac_nhan_bao_gia(db_pool, cu.quote_id, **_tieu_thu(cu, request_fingerprint=van_tay_moi))

    assert ket_qua is None
    assert (await doc_bao_gia(db_pool, cu.quote_id)).status == "ACTIVE"


@pytest.mark.asyncio
async def test_changing_the_request_supersedes_the_old_quotes(db_pool):
    """SUPERSEDED chứ không xoá, và không phải EXPIRED: hết hạn là thời gian
    trôi, bị thay thế là khách đổi ý. Gộp chúng thì lúc có sự cố không phân
    biệt được "đơn vị báo giá quá ngắn" với "khách đổi ngày ba lần"."""
    wid = await _mot_workflow(db_pool)
    cu = await _bao_gia(db_pool, wid)
    van_tay_moi = van_tay_yeu_cau({**YEU_CAU, "move_date": "2026-10-05"})

    da_doi = await thay_the_bao_gia_cu(db_pool, workflow_id=wid, task_id="T1", van_tay_moi=van_tay_moi)

    assert da_doi == 1
    assert (await doc_bao_gia(db_pool, cu.quote_id)).status == "SUPERSEDED"
    assert await bao_gia_dang_song(db_pool, workflow_id=wid, task_id="T1") == []


@pytest.mark.asyncio
async def test_a_confirmed_quote_is_never_rewritten_by_a_later_change(db_pool):
    """Đã CONFIRMED là một cam kết ĐÃ XẢY RA. Lượt sửa sau không xoá được nó."""
    wid = await _mot_workflow(db_pool)
    da_chot = await _bao_gia(db_pool, wid)
    await xac_nhan_bao_gia(db_pool, da_chot.quote_id, **_tieu_thu(da_chot))

    await thay_the_bao_gia_cu(
        db_pool, workflow_id=wid, task_id="T1", van_tay_moi=van_tay_yeu_cau({**YEU_CAU, "move_time": "15:00"})
    )
    await het_han_bao_gia_qua_han(db_pool, workflow_id=wid, task_id="T1")

    assert (await doc_bao_gia(db_pool, da_chot.quote_id)).status == "CONFIRMED"


# ------------------------------------------------------- sửa từ phía tiêu thụ
@pytest.mark.asyncio
async def test_editing_the_amount_in_the_task_does_not_edit_the_quote(db_pool):
    """`kiem_bao_gia` nhận `amount` caller ĐỊNH DÙNG rồi đối chiếu, chứ không
    đọc ra. Nếu chỉ đọc ra thì một con số bị sửa sẽ lặng lẽ bị thay thế và
    không ai biết đã có một lần thử."""
    wid = await _mot_workflow(db_pool)
    that = await _bao_gia(db_pool, wid, gia=470_000)

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(that, **_tieu_thu(that, amount=1_000))
    assert loi.value.ma == "QUOTE_AMOUNT_MISMATCH"
    assert (await doc_bao_gia(db_pool, that.quote_id)).amount == 470_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("o", "gia_tri_gia"),
    [
        ("amount", 1_000),
        ("currency", "USD"),
        ("service_provider_id", "MOV-03"),
        ("service_type", "create_maintenance_request"),
        ("task_id", "T9"),
    ],
)
async def test_confirm_refuses_every_mismatched_field_on_its_own(db_pool, o, gia_tri_gia):
    """Từng điều kiện của lệnh xác nhận đứng ĐỘC LẬP.

    Kiểm chúng một lượt cùng nhau thì một điều kiện bị xoá vẫn xanh nhờ các
    điều kiện còn lại — và mutation "bỏ một vế" sống sót. Mỗi ca ở đây phá đúng
    MỘT vế, nên mỗi vế có một bài kiểm nói tên nó.
    """
    wid = await _mot_workflow(db_pool)
    bao_gia = await _bao_gia(db_pool, wid)

    ket_qua = await xac_nhan_bao_gia(db_pool, bao_gia.quote_id, **_tieu_thu(bao_gia, **{o: gia_tri_gia}))

    assert ket_qua is None, f"xác nhận đi qua dù {o} không khớp"
    assert (await doc_bao_gia(db_pool, bao_gia.quote_id)).status == "ACTIVE"


@pytest.mark.asyncio
async def test_confirm_refuses_a_quote_belonging_to_another_workflow(db_pool):
    """Vế `workflow_id` của lệnh xác nhận, tách riêng.

    Đặt riêng khỏi bài parametrize trên vì nó cần một workflow THẬT thứ hai —
    truyền một UUID bịa ra sẽ đúng vì lý do khác (không dòng nào khớp), và bài
    kiểm sẽ vẫn xanh kể cả khi vế ấy bị xoá khỏi mệnh đề.
    """
    cua_toi = await _mot_workflow(db_pool)
    cua_nguoi_khac = await _mot_workflow(db_pool)
    bao_gia = await _bao_gia(db_pool, cua_nguoi_khac)

    ket_qua = await xac_nhan_bao_gia(db_pool, bao_gia.quote_id, **_tieu_thu(bao_gia, workflow_id=cua_toi))

    assert ket_qua is None
    assert (await doc_bao_gia(db_pool, bao_gia.quote_id)).status == "ACTIVE"


@pytest.mark.asyncio
async def test_editing_the_provider_in_the_task_does_not_move_the_quote(db_pool):
    wid = await _mot_workflow(db_pool)
    cua_mov02 = await _bao_gia(db_pool, wid, don_vi="MOV-02")

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(cua_mov02, **_tieu_thu(cua_mov02, service_provider_id="MOV-03"))
    assert loi.value.ma == "QUOTE_WRONG_PROVIDER"


@pytest.mark.asyncio
async def test_a_quote_for_another_service_is_refused(db_pool):
    wid = await _mot_workflow(db_pool)
    cua_chuyen_nha = await _bao_gia(db_pool, wid)

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(cua_chuyen_nha, **_tieu_thu(cua_chuyen_nha, service_type="create_maintenance_request"))
    assert loi.value.ma == "QUOTE_WRONG_SERVICE"


# ------------------------------------------------------- báo giá của người khác
@pytest.mark.asyncio
async def test_a_quote_from_another_workflow_cannot_be_reused(db_pool):
    """Vân tay tính từ input, nên hai người xin CÙNG một việc có CÙNG vân tay.

    Đây chính là ca mà bảy điều kiện kia không bắt được: dịch vụ đúng, đơn vị
    đúng, vân tay đúng, giá đúng, còn hạn, đang ACTIVE.
    """
    cua_toi = await _mot_workflow(db_pool)
    cua_nguoi_khac = await _mot_workflow(db_pool)
    bao_gia = await _bao_gia(db_pool, cua_nguoi_khac)

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(bao_gia, **_tieu_thu(bao_gia, workflow_id=cua_toi))
    assert loi.value.ma == "QUOTE_WRONG_WORKFLOW"


@pytest.mark.asyncio
async def test_a_quote_for_another_step_cannot_be_reused(db_pool):
    """Một yêu cầu có thể gồm hai lần chuyển nhà (hai kho, hai ngày). Dùng
    chứng từ của bước này cho bước kia là trả một lần cho hai việc."""
    wid = await _mot_workflow(db_pool, task="T1")
    await _them_buoc(db_pool, wid, "T5")
    cua_t1 = await _bao_gia(db_pool, wid, task="T1")

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(cua_t1, **_tieu_thu(cua_t1, task_id="T5"))
    assert loi.value.ma == "QUOTE_WRONG_TASK"


@pytest.mark.asyncio
async def test_a_quote_that_does_not_exist_is_not_an_active_one(db_pool):
    assert await doc_bao_gia(db_pool, str(uuid.uuid4())) is None
    assert await doc_bao_gia(db_pool, "khong-phai-uuid") is None
    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(
            None,
            service_type=DICH_VU,
            service_provider_id="MOV-02",
            request_fingerprint=VAN_TAY,
            amount=1,
            currency="VND",
            workflow_id=str(uuid.uuid4()),
            task_id="T1",
        )
    assert loi.value.ma == "QUOTE_NOT_FOUND"


# ------------------------------------------------------------------ xác nhận
@pytest.mark.asyncio
async def test_two_simultaneous_confirms_leave_exactly_one_winner(db_pool):
    """Đọc-rồi-ghi ở tầng ứng dụng thì cả hai lượt đọc thấy ACTIVE và cả hai
    ghi CONFIRMED — hai lần xác nhận cho một chứng từ, và bên cung cấp nhận hai
    đơn. Mệnh đề `WHERE` biến nó thành phép so-sánh-rồi-đổi nguyên tử ở
    database, nơi duy nhất có thể phân xử."""
    wid = await _mot_workflow(db_pool)
    bao_gia = await _bao_gia(db_pool, wid)
    tham_so = _tieu_thu(bao_gia)

    ket_qua = await asyncio.gather(
        xac_nhan_bao_gia(db_pool, bao_gia.quote_id, **tham_so),
        xac_nhan_bao_gia(db_pool, bao_gia.quote_id, **tham_so),
    )

    thang = [r for r in ket_qua if r is not None]
    assert len(thang) == 1, f"{len(thang)} lượt cùng xác nhận một báo giá"
    assert thang[0].status == "CONFIRMED" and thang[0].confirmed_at is not None
    assert (await doc_bao_gia(db_pool, bao_gia.quote_id)).status == "CONFIRMED"


@pytest.mark.asyncio
async def test_a_superseded_quote_is_not_active_anymore(db_pool):
    wid = await _mot_workflow(db_pool)
    cu = await _bao_gia(db_pool, wid)
    await thay_the_bao_gia_cu(
        db_pool, workflow_id=wid, task_id="T1", van_tay_moi=van_tay_yeu_cau({**YEU_CAU, "move_time": "15:00"})
    )

    sau = await doc_bao_gia(db_pool, cu.quote_id)
    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(sau, **_tieu_thu(sau))
    assert loi.value.ma == "QUOTE_NOT_ACTIVE"


# --------------------------------------------------------- ràng buộc dữ liệu
@pytest.mark.asyncio
async def test_the_same_provider_cannot_quote_twice_for_the_same_request(db_pool):
    """Retry sau timeout, hai tab, hai lượt poll — nếu mỗi lượt ghi một dòng
    thì luật chọn sẽ lấy dòng rẻ hơn, tức hệ thống tự thưởng cho mình mỗi lần
    mạng chập chờn."""
    wid = await _mot_workflow(db_pool)
    await _bao_gia(db_pool, wid, don_vi="MOV-02", gia=470_000)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _bao_gia(db_pool, wid, don_vi="MOV-02", gia=310_000)


@pytest.mark.asyncio
async def test_one_provider_cannot_reuse_one_external_quote_id(db_pool):
    """Trong MỘT đơn vị, mã báo giá phải là danh tính.

    Trùng mã nghĩa là lúc tranh chấp, câu "chúng tôi đã xác nhận Q-001" trỏ tới
    hai con số khác nhau và không ai phân xử được. Ở đây hai chứng từ nằm ở hai
    workflow khác nhau, nên ràng buộc theo bước KHÔNG bắt được — chỉ ràng buộc
    `(provider, external_quote_id)` mới bắt.
    """
    a = await _mot_workflow(db_pool)
    b = await _mot_workflow(db_pool)
    trung = "QMOV-TRUNG-01"
    chung = dict(
        service_provider_id="MOV-02",
        service_type=DICH_VU,
        amount=470_000,
        currency="VND",
        request_fingerprint=VAN_TAY,
        valid_until=_sau(30),
        task_id="T1",
    )
    await luu_bao_gia(db_pool, external_quote_id=trung, workflow_id=a, **chung)
    with pytest.raises(asyncpg.UniqueViolationError):
        await luu_bao_gia(db_pool, external_quote_id=trung, workflow_id=b, **chung)


@pytest.mark.asyncio
async def test_two_providers_may_use_the_same_internal_numbering(db_pool):
    """Nhưng KHÔNG unique toàn cục.

    Hai đơn vị khác nhau hoàn toàn có thể cùng đánh số `Q-001`, và ép chúng
    phải khác nhau là áp một luật của P-118 lên hệ thống đánh mã nội bộ của
    người khác — rồi từ chối một báo giá hợp lệ vì lý do không phải của họ.
    """
    wid = await _mot_workflow(db_pool)
    chung = dict(
        service_type=DICH_VU,
        amount=470_000,
        currency="VND",
        request_fingerprint=VAN_TAY,
        valid_until=_sau(30),
        workflow_id=wid,
        task_id="T1",
    )
    await luu_bao_gia(db_pool, external_quote_id="Q-001", service_provider_id="MOV-01", **chung)
    b = await luu_bao_gia(db_pool, external_quote_id="Q-001", service_provider_id="MOV-02", **chung)
    assert b.status == "ACTIVE"


@pytest.mark.asyncio
async def test_a_replacement_quote_is_allowed_after_the_old_one_is_superseded(db_pool):
    """Ràng buộc chỉ áp cho ACTIVE — không được chặn lượt xin báo giá mới."""
    wid = await _mot_workflow(db_pool)
    await _bao_gia(db_pool, wid, don_vi="MOV-02")
    van_tay_moi = van_tay_yeu_cau({**YEU_CAU, "move_date": "2026-10-05"})
    await thay_the_bao_gia_cu(db_pool, workflow_id=wid, task_id="T1", van_tay_moi=van_tay_moi)

    moi = await _bao_gia(db_pool, wid, don_vi="MOV-02", van_tay=van_tay_moi)
    assert moi.status == "ACTIVE"


@pytest.mark.asyncio
async def test_an_amount_that_is_not_a_positive_integer_never_persists(db_pool):
    """VND không có phần lẻ, và số thực làm hai lần cộng cùng một hoá đơn ra
    hai kết quả. Một báo giá 0 đồng thì không phải báo giá."""
    wid = await _mot_workflow(db_pool)
    for gia in (0, -1, 470_000.5, "470000", True):
        with pytest.raises(ValueError):
            await _bao_gia(db_pool, wid, gia=gia)
    with pytest.raises(ValueError, match="currency"):
        await luu_bao_gia(
            db_pool,
            external_quote_id="QMOV-x",
            service_provider_id="MOV-02",
            service_type=DICH_VU,
            amount=470_000,
            currency="USD",
            request_fingerprint=VAN_TAY,
            valid_until=_sau(30),
            workflow_id=wid,
            task_id="T1",
        )
    assert await bao_gia_dang_song(db_pool, workflow_id=wid, task_id="T1") == []
