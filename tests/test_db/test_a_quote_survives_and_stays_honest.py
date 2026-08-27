"""Báo giá persist, đọc lại đúng, và không dùng lại được cho việc khác.

Bước A khoá quyền sở hữu, nhưng đơn vị vẫn đến từ một bảng cứng trong mã. Bước
B đổi nguồn danh tính ấy sang một CHỨNG TỪ: đơn vị nào, giá nào, cho yêu cầu
nào, còn hạn tới bao giờ.

Chứng từ chỉ có giá trị nếu nó không sửa được từ phía người tiêu thụ. Nên bảy
điều kiện của `kiem_bao_gia` phải đúng ĐỒNG THỜI, và mỗi điều kiện có một ca
kiểm riêng dựng đúng tình huống nó tồn tại để chặn.

Chạy qua PostgreSQL THẬT, không phải một dict trong RAM: tính bền vững là thứ
duy nhất không kiểm được bằng một object giả. "Báo giá sống qua việc dựng
repository mới" chỉ có nghĩa khi có một database ở giữa.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.db.quote_repository import (
    bao_gia_dang_song,
    doc_bao_gia,
    luu_bao_gia,
    thay_the_bao_gia_cu,
    xac_nhan_bao_gia,
)
from src.orchestration.quote import (
    QuoteInvalidError,
    kiem_bao_gia,
    van_tay_yeu_cau,
)

YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}
VAN_TAY = van_tay_yeu_cau(YEU_CAU)
DICH_VU = "schedule_move"


def _sau(phut: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=phut)


async def _mot_workflow(pool) -> str:
    """Workflow thật, vì `service_quotes.workflow_id` là UUID và test đọc lại theo nó."""
    wid = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid, 'chuyển nhà', 'PENDING')",
        wid,
    )
    return wid


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


def _tieu_thu(bao_gia, **ghi_de):
    """Bộ tham số "đúng hết" để tiêu thụ một báo giá; test chỉ ghi đè phần nó phá."""
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

    Đây là điều một bản mock trong RAM không bao giờ kiểm được. Nếu báo giá chỉ
    sống trong một biến của tiến trình thì mọi restart, mọi worker thứ hai, mọi
    lượt deploy đều xoá sạch — và khách quay lại thấy giá đã đổi mà không ai
    làm gì.
    """
    wid = await _mot_workflow(db_pool)
    goc = await _bao_gia(db_pool, wid)

    # Không giữ lại object nào: đọc lại CHỈ bằng mã, qua một lời gọi mới.
    doc_lai = await doc_bao_gia(db_pool, goc.quote_id)

    assert doc_lai is not None, "báo giá không đọc lại được"
    assert (doc_lai.external_quote_id, doc_lai.service_provider_id, doc_lai.amount) == (
        goc.external_quote_id,
        goc.service_provider_id,
        goc.amount,
    )
    assert doc_lai.request_fingerprint == VAN_TAY
    assert doc_lai.status == "ACTIVE"
    assert doc_lai.created_at is not None and doc_lai.confirmed_at is None


@pytest.mark.asyncio
async def test_two_different_moves_get_two_different_quotes(db_pool):
    """Hai cấu hình khác nhau → giá và vân tay khác nhau, và không lẫn vào nhau."""
    wid = await _mot_workflow(db_pool)
    van_tay_xe_tai = van_tay_yeu_cau({**YEU_CAU, "move_vehicle": "truck"})
    a = await _bao_gia(db_pool, wid, gia=470_000, van_tay=VAN_TAY)
    b = await _bao_gia(db_pool, wid, gia=680_000, van_tay=van_tay_xe_tai)

    assert a.request_fingerprint != b.request_fingerprint
    chi_xe_van = await bao_gia_dang_song(db_pool, workflow_id=wid, task_id="T1", request_fingerprint=VAN_TAY)
    assert [q.quote_id for q in chi_xe_van] == [a.quote_id]


# ------------------------------------------------------------------ hết hạn
@pytest.mark.asyncio
async def test_an_expired_quote_cannot_be_used(db_pool):
    """Hết hạn thì không confirm được và không ghim approval được.

    Còn hiệu lực nghĩa là đơn vị CÒN GIỮ CHỖ. Dùng một báo giá quá hạn là đem
    một lời hứa đã hết đi thu tiền — đơn vị có quyền từ chối, và người chịu là
    khách.
    """
    wid = await _mot_workflow(db_pool)
    het_han = await _bao_gia(db_pool, wid, han_phut=-1)

    assert het_han.het_han
    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(het_han, **_tieu_thu(het_han))
    assert loi.value.ma == "QUOTE_EXPIRED"


@pytest.mark.asyncio
async def test_expiry_is_checked_before_the_details(db_pool):
    """Thứ tự kiểm: hết hạn được báo TRƯỚC những sai lệch nhỏ hơn.

    Không phải chuyện thẩm mỹ. Nếu một báo giá vừa hết hạn vừa sai giá mà thông
    điệp nói "số tiền không khớp", người xử lý sẽ đi tìm lỗi ở chỗ không có.
    """
    wid = await _mot_workflow(db_pool)
    het_han = await _bao_gia(db_pool, wid, han_phut=-1)
    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(het_han, **_tieu_thu(het_han, amount=1))
    assert loi.value.ma == "QUOTE_EXPIRED"


# ------------------------------------------------------- yêu cầu đã đổi
@pytest.mark.asyncio
async def test_an_old_quote_is_refused_after_the_request_changes(db_pool):
    """Đổi input nhưng dùng báo giá cũ → từ chối.

    Ca thật: xin giá cho xe van, thấy rẻ, rồi sửa thành xe tải và bấm xác nhận.
    Không có vế này thì đơn vị nhận một việc họ chưa bao giờ báo giá.
    """
    wid = await _mot_workflow(db_pool)
    cu = await _bao_gia(db_pool, wid)
    van_tay_moi = van_tay_yeu_cau({**YEU_CAU, "move_vehicle": "truck"})

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(cu, **_tieu_thu(cu, request_fingerprint=van_tay_moi))
    assert loi.value.ma == "QUOTE_STALE_REQUEST"


@pytest.mark.asyncio
async def test_changing_the_request_supersedes_the_old_quotes(db_pool):
    """Vân tay mới → báo giá đời cũ thành SUPERSEDED, không phải EXPIRED.

    Ba trạng thái nói ba điều: hết hạn là thời gian trôi, bị thay thế là khách
    đổi ý, đã xác nhận là cam kết đã xảy ra. Gộp chúng thì lúc có sự cố không
    phân biệt được "đơn vị báo giá quá ngắn" với "khách đổi ngày ba lần".
    """
    wid = await _mot_workflow(db_pool)
    cu = await _bao_gia(db_pool, wid)
    van_tay_moi = van_tay_yeu_cau({**YEU_CAU, "move_date": "2026-10-05"})

    da_doi = await thay_the_bao_gia_cu(db_pool, workflow_id=wid, task_id="T1", van_tay_moi=van_tay_moi)

    assert da_doi == 1
    sau = await doc_bao_gia(db_pool, cu.quote_id)
    assert sau.status == "SUPERSEDED"
    assert await bao_gia_dang_song(db_pool, workflow_id=wid, task_id="T1") == []


@pytest.mark.asyncio
async def test_a_confirmed_quote_is_never_rewritten_by_a_later_change(db_pool):
    """Đã CONFIRMED là một cam kết ĐÃ XẢY RA. Lượt sửa sau không xoá được nó."""
    wid = await _mot_workflow(db_pool)
    da_chot = await _bao_gia(db_pool, wid)
    await xac_nhan_bao_gia(db_pool, da_chot.quote_id)

    await thay_the_bao_gia_cu(
        db_pool, workflow_id=wid, task_id="T1", van_tay_moi=van_tay_yeu_cau({**YEU_CAU, "move_time": "15:00"})
    )

    assert (await doc_bao_gia(db_pool, da_chot.quote_id)).status == "CONFIRMED"


# ------------------------------------------------------- sửa từ phía tiêu thụ
@pytest.mark.asyncio
async def test_editing_the_amount_in_the_task_does_not_edit_the_quote(db_pool):
    """Sửa `amount` ở task không thay được chứng từ đã persist.

    `kiem_bao_gia` nhận `amount` caller ĐỊNH DÙNG rồi đối chiếu, chứ không đọc
    ra từ báo giá. Nếu chỉ đọc ra thì một con số bị sửa sẽ lặng lẽ bị thay thế
    và không ai biết đã có một lần thử.
    """
    wid = await _mot_workflow(db_pool)
    that = await _bao_gia(db_pool, wid, gia=470_000)

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(that, **_tieu_thu(that, amount=1_000))
    assert loi.value.ma == "QUOTE_AMOUNT_MISMATCH"
    assert (await doc_bao_gia(db_pool, that.quote_id)).amount == 470_000


@pytest.mark.asyncio
async def test_editing_the_provider_in_the_task_does_not_move_the_quote(db_pool):
    """Đổi `service_provider_id` ở phía tiêu thụ không chuyển được chứng từ sang đơn vị khác."""
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

    Không neo vào workflow thì báo giá của người này dùng được cho yêu cầu của
    người kia — đúng nghĩa IDOR, chỉ là trên chứng từ thay vì trên hàng đợi.
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
    """Cùng workflow, khác bước — cũng không.

    Một yêu cầu có thể gồm hai lần chuyển nhà (hai kho, hai ngày). Dùng chứng
    từ của bước này cho bước kia là trả một lần cho hai việc.
    """
    wid = await _mot_workflow(db_pool)
    cua_t1 = await _bao_gia(db_pool, wid, task="T1")

    with pytest.raises(QuoteInvalidError) as loi:
        kiem_bao_gia(cua_t1, **_tieu_thu(cua_t1, task_id="T5"))
    assert loi.value.ma == "QUOTE_WRONG_TASK"


@pytest.mark.asyncio
async def test_a_quote_that_does_not_exist_is_not_an_active_one(db_pool):
    """Mã bịa ra trả `None`, và `None` không đi qua cổng được."""
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
        )
    assert loi.value.ma == "QUOTE_NOT_FOUND"


# ------------------------------------------------------------------ xác nhận
@pytest.mark.asyncio
async def test_two_simultaneous_confirms_leave_exactly_one_winner(db_pool):
    """Hai lượt xác nhận đồng thời → đúng MỘT thành công.

    Đọc-rồi-ghi ở tầng ứng dụng thì cả hai lượt đọc thấy ACTIVE và cả hai ghi
    CONFIRMED — hai lần xác nhận cho một chứng từ, và bên cung cấp nhận hai đơn.
    `WHERE status = 'ACTIVE'` biến nó thành một phép so-sánh-rồi-đổi nguyên tử
    ở database, nơi duy nhất có thể phân xử.
    """
    wid = await _mot_workflow(db_pool)
    bao_gia = await _bao_gia(db_pool, wid)

    ket_qua = await asyncio.gather(
        xac_nhan_bao_gia(db_pool, bao_gia.quote_id),
        xac_nhan_bao_gia(db_pool, bao_gia.quote_id),
    )

    thang = [r for r in ket_qua if r is not None]
    assert len(thang) == 1, f"{len(thang)} lượt cùng xác nhận một báo giá"
    assert thang[0].status == "CONFIRMED" and thang[0].confirmed_at is not None
    assert (await doc_bao_gia(db_pool, bao_gia.quote_id)).status == "CONFIRMED"


@pytest.mark.asyncio
async def test_an_expired_quote_cannot_be_confirmed_through_the_gate(db_pool):
    """Cổng chặn TRƯỚC khi tới lệnh xác nhận — hết hạn thì không có lượt ghi nào."""
    wid = await _mot_workflow(db_pool)
    het_han = await _bao_gia(db_pool, wid, han_phut=-1)

    with pytest.raises(QuoteInvalidError):
        kiem_bao_gia(het_han, **_tieu_thu(het_han))
    assert (await doc_bao_gia(db_pool, het_han.quote_id)).status == "ACTIVE", "cổng đã ném nhưng trạng thái vẫn bị đổi"


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
    """Một lượt xin báo giá chạy hai lần không được để lại hai dòng ACTIVE.

    Retry sau timeout, hai tab, hai lượt poll — nếu mỗi lượt ghi một dòng thì
    luật chọn sẽ lấy dòng rẻ hơn, tức hệ thống tự thưởng cho mình mỗi lần mạng
    chập chờn.
    """
    import asyncpg as _pg

    wid = await _mot_workflow(db_pool)
    await _bao_gia(db_pool, wid, don_vi="MOV-02", gia=470_000)
    with pytest.raises(_pg.UniqueViolationError):
        await _bao_gia(db_pool, wid, don_vi="MOV-02", gia=310_000)


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
    """`amount` phải là số nguyên dương, `currency` phải trong allowlist.

    VND không có phần lẻ, và số thực làm hai lần cộng cùng một hoá đơn ra hai
    kết quả. Một báo giá 0 đồng thì không phải báo giá.
    """
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
