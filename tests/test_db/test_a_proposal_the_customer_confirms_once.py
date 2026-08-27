"""Đề xuất sống qua restart, chỉ chủ của nó xác nhận được, và chỉ MỘT LẦN.

Bước C chọn được một đơn vị nhưng chỉ ĐỌC. Giữa lúc P-118 nói "mình đề xuất Đại
Tín, 470.000" và lúc khách bấm đồng ý có một khoảng thời gian thật — họ đọc, họ
hỏi người nhà, họ đóng tab rồi mở lại. Bước D là khoảng ấy.

Lượt xác nhận là chỗ ba bảng phải đổi CÙNG NHAU: chứng từ chốt, đề xuất chốt,
một dòng vào hàng đợi đơn vị. Ba lệnh rời nhau nghĩa là có ba lúc hệ thống ở
giữa chừng — và lúc giữa chừng tệ nhất là chứng từ đã chốt, tiền đã có chủ, mà
không ai bên kia nhận được việc. Nên file này kiểm hai thứ mà chỉ PostgreSQL
thật kiểm được: tính bền vững, và tính nguyên tử.

Điều KHÔNG được lưu cũng quan trọng ngang điều được lưu: đề xuất không mang
provider/giá/tiền tệ (chúng ở trên chứng từ), và không mang `approval_actor`
(nó được suy ra lúc đọc). Hai bản sao là hai chỗ để lệch nhau, và chúng lệch
đúng vào lúc con số cũ trông vẫn hợp lệ.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.db.proposal_repository import (
    DeXuatBatNhatQuanError,
    KhongGhimDuocDeXuatError,
    de_xuat_dang_cho,
    doc_de_xuat,
    ghim_de_xuat,
    trang_thai_hieu_luc,
    xac_nhan_de_xuat,
)
from src.db.quote_repository import don_bao_gia_va_de_xuat, luu_bao_gia
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


async def _khach(db_pool, ten: str) -> str:
    uid = uuid.uuid4()
    await db_pool.execute(
        "INSERT INTO users (id, username, password_hash, role, full_name, phone) "
        "VALUES ($1::uuid, $2, 'x', 'customer', $3, '0900000000')",
        uid,
        ten,
        f"Khách {ten}",
    )
    return str(uid)


async def _workflow(db_pool, chu: str, *, tasks=(("T1", DICH_VU),)) -> str:
    wid = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1::uuid, 'chuyển nhà', 'PENDING', $2::uuid)",
        wid,
        chu,
    )
    for task, tool in tasks:
        await db_pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
            "VALUES ($1::uuid, $2, $3, 'PENDING', '[]'::jsonb, $4::jsonb)",
            wid,
            task,
            tool,
            '{"move_date":"2026-09-30","move_time":"08:00","move_vehicle":"van",'
            '"needs_elevator":false,"needs_loading_support":false,"max_price":450000}',
        )
    return wid


async def _bao_gia(db_pool, wid, *, don_vi="MOV-02", gia=470_000, task="T1", han_phut=30):
    return await luu_bao_gia(
        db_pool,
        external_quote_id=f"Q-{uuid.uuid4().hex[:10]}",
        service_provider_id=don_vi,
        service_type=DICH_VU,
        amount=gia,
        currency="VND",
        request_fingerprint=VAN_TAY,
        valid_until=datetime.now(UTC) + timedelta(minutes=han_phut),
        workflow_id=wid,
        task_id=task,
    )


async def _het_han(db_pool, quote_id: str) -> None:
    """Mô phỏng THỜI GIAN TRÔI QUA — `luu_bao_gia` không cho ghi chứng từ đã chết."""
    await db_pool.execute(
        "UPDATE service_quotes SET valid_until = NOW() - INTERVAL '1 minute' WHERE quote_id = $1::uuid",
        quote_id,
    )


async def _duyet(db_pool, wid):
    return await db_pool.fetch(
        "SELECT task_id, status, service_provider_id, details, applicant_user_id "
        "FROM service_approvals WHERE workflow_id = $1::uuid ORDER BY task_id",
        uuid.UUID(wid),
    )


# ---------------------------------------------------------------- 1. bền vững
@pytest.mark.asyncio
async def test_a_proposal_outlives_the_objects_that_made_it(db_pool):
    """Đọc lại và xác nhận được bằng CHỈ mã, qua những lời gọi mới.

    Đây là điều một biến trong bộ nhớ không bao giờ làm được. Nếu đề xuất chỉ
    sống trong tiến trình thì mọi restart, mọi worker thứ hai, mọi lượt deploy
    đều xoá nó — và khách quay lại thấy màn hình trống cho một việc họ đang chờ.
    """
    chu = await _khach(db_pool, "kh_ben_vung")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)

    goc = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    doc_lai = await doc_de_xuat(db_pool, goc.proposal_id)

    assert doc_lai is not None
    assert (doc_lai.workflow_id, doc_lai.task_id, doc_lai.quote_id) == (wid, "T1", bao_gia.quote_id)
    assert doc_lai.status == "PROPOSED" and doc_lai.confirmed_at is None

    ket_qua = await xac_nhan_de_xuat(db_pool, goc.proposal_id, owner_user_id=chu)
    assert ket_qua.thanh_cong, ket_qua.ket_qua
    assert (await doc_de_xuat(db_pool, goc.proposal_id)).status == "CONFIRMED"


@pytest.mark.asyncio
async def test_a_proposal_never_copies_the_price_or_the_provider(db_pool):
    """Chứng từ là nguồn DUY NHẤT cho đơn vị, giá và tiền tệ.

    Chép sang đề xuất là tạo nguồn thứ hai, và hai nguồn thì lệch — lệch đúng
    vào lúc báo giá bị thay thế hoặc hết hạn, tức đúng lúc con số cũ trông vẫn
    hợp lệ. Bài kiểm này khoá điều đó ở tầng SCHEMA, nơi không ai "tiện tay"
    thêm một cột được.
    """
    cot = {
        r["column_name"]
        for r in await db_pool.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'service_provider_proposals'"
        )
    }
    thua = cot & {"service_provider_id", "amount", "currency", "approval_actor", "provider_name"}
    assert not thua, f"đề xuất đang giữ bản sao của: {sorted(thua)}"


@pytest.mark.asyncio
async def test_a_proposal_must_name_a_live_quote_of_the_same_step(db_pool):
    """Neo bắt buộc: chứng từ phải đang sống VÀ thuộc đúng bước này."""
    chu = await _khach(db_pool, "kh_neo")
    wid = await _workflow(db_pool, chu, tasks=(("T1", DICH_VU), ("T5", DICH_VU)))
    cua_t5 = await _bao_gia(db_pool, wid, task="T5")
    da_chet = await _bao_gia(db_pool, wid, don_vi="MOV-01", task="T1")
    await _het_han(db_pool, da_chet.quote_id)

    with pytest.raises(KhongGhimDuocDeXuatError):
        await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=cua_t5.quote_id)
    with pytest.raises(KhongGhimDuocDeXuatError):
        await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=da_chet.quote_id)
    with pytest.raises(KhongGhimDuocDeXuatError):
        await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=str(uuid.uuid4()))
    assert await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1") is None


@pytest.mark.asyncio
async def test_pinning_a_proposal_puts_the_step_into_waiting(db_pool):
    """Bước và workflow chuyển sang chờ duyệt CÙNG transaction với đề xuất.

    Tách ra thì có một khoảnh khắc đề xuất đã tồn tại mà trạng thái vẫn nói
    "đang chạy", và lượt poll rơi đúng vào đó sẽ dựng một màn hình không mời
    khách bấm gì.
    """
    chu = await _khach(db_pool, "kh_trang_thai")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)

    await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    assert (
        await db_pool.fetchval(
            "SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", uuid.UUID(wid)
        )
        == "WAITING_APPROVAL"
    )
    assert (
        await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", uuid.UUID(wid))
        == "WAITING_APPROVAL"
    )


# ------------------------------------------------------------- 2. quyền sở hữu
@pytest.mark.asyncio
async def test_another_customer_cannot_confirm_someone_elses_proposal(db_pool):
    """Người khác bấm → `NOT_FOUND`, và KHÔNG gì đổi.

    404 chứ không 403: 403 xác nhận với người đang dò rằng `proposal_id` ấy có
    thật, và đó là một mẩu thông tin miễn phí.
    """
    chu = await _khach(db_pool, "kh_chu_that")
    nguoi_khac = await _khach(db_pool, "kh_nguoi_la")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    ket_qua = await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=nguoi_khac)

    assert ket_qua.ket_qua == "NOT_FOUND"
    assert ket_qua.de_xuat is None, "lời từ chối vẫn trả về nội dung đề xuất"
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED"
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(bao_gia.quote_id))
        == "ACTIVE"
    )
    assert list(await _duyet(db_pool, wid)) == []


@pytest.mark.asyncio
async def test_a_made_up_proposal_id_is_the_same_answer_as_someone_elses(db_pool):
    """ "Không có" và "không phải của bạn" phải KHÔNG phân biệt được từ bên ngoài."""
    nguoi_la = await _khach(db_pool, "kh_do_ma")
    assert (await xac_nhan_de_xuat(db_pool, str(uuid.uuid4()), owner_user_id=nguoi_la)).ket_qua == "NOT_FOUND"
    assert (await xac_nhan_de_xuat(db_pool, "khong-phai-uuid", owner_user_id=nguoi_la)).ket_qua == "NOT_FOUND"


@pytest.mark.asyncio
async def test_a_workflow_with_no_owner_can_never_be_confirmed(db_pool):
    """Workflow không có chủ thì không ai là chủ — kể cả người đầu tiên hỏi.

    `owner_user_id IS NULL` so với bất kỳ ai đều ra `NULL` trong SQL, và một
    điều kiện `NULL` không phải `TRUE`. Nhưng luật ấy phải là một khẳng định có
    ý thức, không phải một hệ quả tình cờ của SQL ba trạng thái.
    """
    chu = await _khach(db_pool, "kh_mo_coi")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    await db_pool.execute("UPDATE workflows SET owner_user_id = NULL WHERE workflow_id = $1::uuid", uuid.UUID(wid))

    assert (await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)).ket_qua == "NOT_FOUND"


# ------------------------------------------------------------------ 3. hết hạn
@pytest.mark.asyncio
async def test_a_quote_that_expired_while_waiting_kills_the_proposal(db_pool):
    """Chứng từ hết hạn → KHÔNG confirm, đề xuất EXPIRED, KHÔNG dòng duyệt nào.

    Để đề xuất nằm lại ở `PROPOSED` nghĩa là màn hình vẫn mời khách bấm đồng ý
    cho một cái giá không còn tồn tại — và lần bấm sau cũng hỏng, mãi mãi.
    Chuyển nó sang `EXPIRED` là cách nói "cái này chết rồi, xin giá mới đi".
    """
    chu = await _khach(db_pool, "kh_het_han")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    await _het_han(db_pool, bao_gia.quote_id)

    ket_qua = await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    assert ket_qua.ket_qua == "QUOTE_EXPIRED"
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "EXPIRED"
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(bao_gia.quote_id))
        == "ACTIVE"
    ), "chứng từ hết hạn vẫn bị chốt"
    assert list(await _duyet(db_pool, wid)) == [], "hết hạn mà vẫn mở hàng đợi đơn vị"


@pytest.mark.asyncio
async def test_an_expired_proposal_cannot_be_pressed_again(db_pool):
    """Bấm lần hai sau khi hết hạn → `ALREADY_DECIDED`, không phải `QUOTE_EXPIRED` lặp lại.

    Hai mã, hai câu chuyện: lần đầu là "vừa hết hạn", lần sau là "cái này đã
    được xử lý xong rồi". Trả cùng một mã thì tầng trên không phân biệt được
    một lượt bấm mới với một lượt bấm lại.
    """
    chu = await _khach(db_pool, "kh_bam_lai")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    await _het_han(db_pool, bao_gia.quote_id)
    await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    lan_hai = await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    assert lan_hai.ket_qua == "ALREADY_DECIDED"


# --------------------------------------------------------- 4. xác nhận đồng thời
@pytest.mark.asyncio
async def test_two_simultaneous_confirms_leave_exactly_one_winner_and_one_approval(db_pool):
    """Hai lượt bấm cùng lúc → đúng MỘT thắng, và đúng MỘT dòng duyệt.

    Không phải chuyện lý thuyết: hai tab, một lần bấm đúp, một lượt retry của
    client sau timeout. Nếu cả hai đi lọt thì đơn vị nhận hai đơn cho cùng một
    việc, và khách trả tiền hai lần.

    `FOR UPDATE` trên workflow là thứ xếp hàng chúng lại; mệnh đề
    `status = 'PROPOSED'` là thứ để lượt thua nhận ra mình thua.
    """
    chu = await _khach(db_pool, "kh_dong_thoi")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    ket_qua = await asyncio.gather(
        xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu),
        xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu),
    )

    thang = [r for r in ket_qua if r.thanh_cong]
    assert len(thang) == 1, f"{len(thang)}/2 lượt cùng thắng"
    assert [r.ket_qua for r in ket_qua if not r.thanh_cong] == ["ALREADY_DECIDED"]
    dong = await _duyet(db_pool, wid)
    assert len(dong) == 1, f"{len(dong)} dòng duyệt cho một lượt xác nhận"
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "CONFIRMED"
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(bao_gia.quote_id))
        == "CONFIRMED"
    )


@pytest.mark.asyncio
async def test_a_confirm_that_arrives_late_never_rewrites_the_winner(db_pool):
    """Lượt đến MUỘN phải nhận "đã xử lý rồi", không phải "hết hạn".

    Đây là ca mà khoá dòng tồn tại để chặn, và nó phải được dựng bằng tay vì
    `asyncio.gather` không đảm bảo hai lượt thật sự chồng lên nhau — bài kiểm
    đồng thời ở trên xanh CẢ KHI bỏ hết khoá, nên nó chưa chứng minh được gì
    về khoá. Ở đây kết nối A giữ khoá và làm xong việc nhưng CHƯA commit, rồi
    lượt B mới bắt đầu.

    Hai hậu quả nếu B không bị chặn:

      1. B đọc đề xuất còn `PROPOSED` (ảnh chụp cũ), rồi thấy lệnh chốt chứng
         từ trả 0 dòng, và kết luận "hết hạn" — khách nhận "xin giá mới đi" cho
         một lượt bấm ĐÃ THÀNH CÔNG, và họ sẽ đi đặt lần thứ hai.
      2. Tệ hơn: B ghi đè đề xuất `CONFIRMED` thành `EXPIRED`. Hàng đợi đơn vị
         đã mở, chứng từ đã chốt, mà đề xuất nói việc này đã chết. Mọi lệnh đều
         thành công, và không log nào bất thường.
    """
    chu = await _khach(db_pool, "kh_den_muon")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    async with db_pool.acquire() as conn_a:
        tx = conn_a.transaction()
        await tx.start()
        # A cầm khoá và làm xong phần của mình — nhưng chưa commit.
        await conn_a.fetchrow(
            "SELECT workflow_id FROM workflows WHERE workflow_id = $1::uuid FOR UPDATE", uuid.UUID(wid)
        )
        await conn_a.execute(
            "UPDATE service_quotes SET status = 'CONFIRMED', confirmed_at = NOW() WHERE quote_id = $1::uuid",
            uuid.UUID(bao_gia.quote_id),
        )
        await conn_a.execute(
            "UPDATE service_provider_proposals SET status = 'CONFIRMED', confirmed_at = NOW() "
            "WHERE proposal_id = $1::uuid",
            uuid.UUID(de_xuat.proposal_id),
        )

        den_muon = asyncio.create_task(xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu))
        await asyncio.sleep(0.15)
        assert not den_muon.done(), "lượt đến muộn không bị chặn — khoá không có tác dụng"

        await tx.commit()

    ket_qua = await asyncio.wait_for(den_muon, timeout=5)

    assert ket_qua.ket_qua == "ALREADY_DECIDED", f"lượt đến muộn báo {ket_qua.ket_qua}"
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "CONFIRMED", (
        "lượt đến muộn đã ghi đè lên quyết định của lượt thắng"
    )


# ----------------------------------------- 1. vòng đời quote ↔ proposal
@pytest.mark.asyncio
async def test_superseding_a_quote_supersedes_its_proposal_in_one_transaction(db_pool):
    """Chứng từ chết thì đề xuất chết THEO, trong CÙNG một transaction.

    Trước đây đây là hai hàm rời nhau, và đề xuất thì không hàm nào đụng tới.
    Giữa hai lượt ghi có một khe mà chứng từ đã `SUPERSEDED` trong khi đề xuất
    vẫn `PROPOSED` — và trong khe ấy màn hình còn nguyên nút "đồng ý" cho một
    cái giá không còn tồn tại. Khe nhỏ, nhưng một lượt poll sẽ rơi vào.
    """
    chu = await _khach(db_pool, "kh_vong_doi_st")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    da_don = await don_bao_gia_va_de_xuat(
        db_pool, workflow_id=wid, task_id="T1", van_tay_moi="mot van tay hoan toan khac"
    )

    assert da_don == {"thay_the": 1, "het_han": 0, "de_xuat": 1}
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(bao_gia.quote_id))
        == "SUPERSEDED"
    )
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "SUPERSEDED"


@pytest.mark.asyncio
async def test_expiring_a_quote_expires_its_proposal_in_one_transaction(db_pool):
    """Hết hạn cũng vậy, và trạng thái đi theo phải ĐÚNG loại.

    `SUPERSEDED` và `EXPIRED` nói hai chuyện: khách đổi ý, và thời gian trôi.
    Đề xuất phải mang đúng loại của chứng từ nó trỏ vào, nếu không thì lúc có
    sự cố không phân biệt được "đơn vị báo giá quá ngắn" với "khách đổi ba lần".
    """
    chu = await _khach(db_pool, "kh_vong_doi_hh")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    await _het_han(db_pool, bao_gia.quote_id)

    da_don = await don_bao_gia_va_de_xuat(db_pool, workflow_id=wid, task_id="T1")

    assert da_don == {"thay_the": 0, "het_han": 1, "de_xuat": 1}
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(bao_gia.quote_id))
        == "EXPIRED"
    )
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "EXPIRED"


@pytest.mark.asyncio
async def test_cleanup_never_touches_a_confirmed_proposal(db_pool):
    """Đã xác nhận là quyết định ĐÃ XẢY RA, và nó đã sinh ra một dòng hàng đợi."""
    chu = await _khach(db_pool, "kh_don_khong_dung")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    await don_bao_gia_va_de_xuat(db_pool, workflow_id=wid, task_id="T1", van_tay_moi="khac han")

    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "CONFIRMED"
    assert len(await _duyet(db_pool, wid)) == 1


@pytest.mark.asyncio
async def test_cleanup_never_downgrades_a_confirmed_proposal_even_if_its_quote_dies(db_pool):
    """Hàng rào THỨ HAI của phép dọn, kiểm độc lập với hàng rào thứ nhất.

    Bài kiểm trên đi qua đường thật, và ở đó chứng từ của một đề xuất đã xác
    nhận luôn là `CONFIRMED` — nên mệnh đề `q.status IN ('SUPERSEDED','EXPIRED')`
    đã chặn sẵn, và guard `p.status = 'PROPOSED'` không mang trách nhiệm nào.
    Đo được bằng mutation: bỏ guard ấy thì không bài kiểm nào đỏ.

    Ở đây trạng thái được gieo THẲNG — chứng từ `SUPERSEDED` trong khi đề xuất
    đã `CONFIRMED` — để guard phải tự đứng. Nó quan trọng vì hạ một đề xuất đã
    xác nhận xuống `SUPERSEDED` nghĩa là hàng đợi đơn vị đã mở mà chứng từ nói
    việc này chưa từng được đồng ý.
    """
    chu = await _khach(db_pool, "kh_don_khong_ha_cap")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)
    await db_pool.execute(
        "UPDATE service_quotes SET status = 'SUPERSEDED', confirmed_at = NULL WHERE quote_id = $1::uuid",
        uuid.UUID(bao_gia.quote_id),
    )

    da_don = await don_bao_gia_va_de_xuat(db_pool, workflow_id=wid, task_id="T1")

    assert da_don["de_xuat"] == 0, "phép dọn đã hạ cấp một đề xuất đã xác nhận"
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "CONFIRMED"


@pytest.mark.asyncio
async def test_cleanup_stays_inside_its_own_step(db_pool):
    """Dọn bước T1 không được đụng chứng từ hay đề xuất của T5."""
    chu = await _khach(db_pool, "kh_don_dung_buoc")
    wid = await _workflow(db_pool, chu, tasks=(("T1", DICH_VU), ("T5", DICH_VU)))
    cua_t5 = await _bao_gia(db_pool, wid, task="T5")
    de_xuat_t5 = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T5", quote_id=cua_t5.quote_id)
    await _bao_gia(db_pool, wid, don_vi="MOV-01", task="T1")

    await don_bao_gia_va_de_xuat(db_pool, workflow_id=wid, task_id="T1", van_tay_moi="khac han")

    assert (await doc_de_xuat(db_pool, de_xuat_t5.proposal_id)).status == "PROPOSED"
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(cua_t5.quote_id))
        == "ACTIVE"
    )


# ------------------------------------ 2. ghim hỏng không được cướp chỗ cái cũ
@pytest.mark.asyncio
async def test_a_failed_new_proposal_leaves_the_old_one_still_waiting(db_pool):
    """Chứng từ mới không dùng được → đề xuất CŨ vẫn `PROPOSED`.

    Bản đầu làm ngược thứ tự: thay thế cái cũ, rồi `INSERT ... SELECT` cái mới,
    rồi ném ở NGOÀI transaction nếu không có dòng nào. Transaction thoát bình
    thường và COMMIT — đề xuất cũ đã mất `PROPOSED` mà không có gì thay chỗ.
    Khách mất nút "đồng ý" cho một đề xuất vẫn còn hiệu lực, vì một lượt ghim
    MỚI đã hỏng.

    Ba cách chứng từ mới hỏng, cả ba phải để cái cũ nguyên vẹn.
    """
    chu = await _khach(db_pool, "kh_ghim_hong")
    wid = await _workflow(db_pool, chu, tasks=(("T1", DICH_VU), ("T5", DICH_VU)))
    con_song = await _bao_gia(db_pool, wid, don_vi="MOV-02")
    cu = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=con_song.quote_id)

    da_chet = await _bao_gia(db_pool, wid, don_vi="MOV-01")
    await _het_han(db_pool, da_chet.quote_id)
    cua_buoc_khac = await _bao_gia(db_pool, wid, task="T5")

    for quote_id, ten in (
        (da_chet.quote_id, "chứng từ đã hết hạn"),
        (cua_buoc_khac.quote_id, "chứng từ của bước khác"),
        (str(uuid.uuid4()), "mã bịa ra"),
    ):
        with pytest.raises(KhongGhimDuocDeXuatError):
            await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=quote_id)
        con_lai = await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")
        assert con_lai is not None, f"{ten}: đề xuất cũ đã mất PROPOSED"
        assert con_lai.proposal_id == cu.proposal_id, f"{ten}: đề xuất cũ bị thay bởi hư vô"


# ------------------------------------------------------------------- 5. rollback
@pytest.mark.asyncio
async def test_a_failed_approval_write_rolls_back_the_confirmations(db_pool, monkeypatch):
    """Ghim hàng đợi hỏng → chứng từ VÀ đề xuất quay lại như cũ.

    Đây là split-brain tệ nhất của bước D: chứng từ nói "đã chốt", đề xuất nói
    "đã chốt", và không ai bên kia nhận được việc. Khách thấy màn hình chờ đơn
    vị; đơn vị không có gì trong hàng đợi. Không có ai để hỏi, và không có gì
    để thử lại — vì bấm lần nữa sẽ nhận `ALREADY_DECIDED`.

    Ép lỗi bằng cách phá RÀNG BUỘC ở database chứ không vá hàm: một `tool`
    không có trong danh sách của `service_approvals` làm lệnh `INSERT` nổ ở
    đúng chỗ nó có thể nổ thật.
    """
    chu = await _khach(db_pool, "kh_rollback")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    import src.db.proposal_repository as kho

    # Nhãn dài hơn cột `service_label` → `StringDataRightTruncationError` ngay
    # tại lệnh INSERT cuối cùng, sau khi hai lệnh UPDATE đã chạy.
    monkeypatch.setitem(kho.SERVICE_LABELS, DICH_VU, "X" * 300)

    with pytest.raises(asyncpg.PostgresError):
        await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED", "đề xuất chốt mà không có việc"
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(bao_gia.quote_id))
        == "ACTIVE"
    ), "chứng từ chốt mà không có việc"
    assert list(await _duyet(db_pool, wid)) == []


@pytest.mark.asyncio
async def test_a_quote_superseded_while_waiting_cannot_be_confirmed(db_pool):
    """Chứng từ bị THAY THẾ trong lúc chờ → `QUOTE_NOT_USABLE`, không phải hết hạn.

    Ca thật và có thật: khách đổi ngày, `don_bao_gia_va_de_xuat` đẩy chứng từ đời
    cũ sang SUPERSEDED, nhưng tab đang mở vẫn còn nút "đồng ý" cho đề xuất cũ.
    Bấm nó nghĩa là chốt một cái giá cho một yêu cầu khách không còn hỏi.

    Mã khác `QUOTE_EXPIRED` vì hành động tiếp theo khác: hết hạn thì xin giá
    mới cho CÙNG yêu cầu; bị thay thế thì yêu cầu đã đổi, và đề xuất mới đã
    được dựng ở đâu đó rồi.
    """
    chu = await _khach(db_pool, "kh_bi_thay")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    await db_pool.execute(
        "UPDATE service_quotes SET status = 'SUPERSEDED' WHERE quote_id = $1::uuid",
        uuid.UUID(bao_gia.quote_id),
    )

    ket_qua = await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    assert ket_qua.ket_qua == "QUOTE_NOT_USABLE"
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED", (
        "bị thay thế không phải hết hạn — đề xuất không được tự chuyển EXPIRED"
    )
    assert list(await _duyet(db_pool, wid)) == []


@pytest.mark.asyncio
async def test_postgres_refuses_a_proposal_pointing_at_another_steps_quote(db_pool):
    """Neo lệch bị chặn ở DATABASE, không chỉ ở tầng ứng dụng.

    `ghim_de_xuat` đã từ chối, và `xac_nhan_de_xuat` cũng kiểm lại. Nhưng một
    luật chỉ sống ở tầng ứng dụng là một luật mà MỌI đường ghi mới phải nhớ lại
    từ đầu — và một lượt sửa dữ liệu bằng tay thì không nhớ gì cả.

    Khoá ngoại tổng hợp `(quote_id, workflow_id, task_id)` làm trạng thái ấy
    không tồn tại được. Khoá ngoại đơn trên `quote_id` không đủ: nó chỉ nói
    "chứng từ này có thật", không nói "nó thuộc bước này".
    """
    chu = await _khach(db_pool, "kh_neo_lech")
    wid = await _workflow(db_pool, chu, tasks=(("T1", DICH_VU), ("T5", DICH_VU)))
    cua_t5 = await _bao_gia(db_pool, wid, task="T5")

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db_pool.execute(
            "INSERT INTO service_provider_proposals (proposal_id, workflow_id, task_id, quote_id, status) "
            "VALUES (gen_random_uuid(), $1::uuid, 'T1', $2::uuid, 'PROPOSED')",
            uuid.UUID(wid),
            uuid.UUID(cua_t5.quote_id),
        )
    assert await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1") is None


@pytest.mark.asyncio
async def test_a_failure_after_the_business_updates_rolls_everything_back(db_pool, monkeypatch):
    """Bất biến vỡ SAU khi chứng từ đã chốt → NÉM, và cả ba quay về như cũ.

    Đây là luật quan trọng nhất của lượt xác nhận: từ lệnh ghi đầu tiên trở đi
    không còn `return` nào cho nhánh hỏng. Một `return` ở đó COMMIT trạng thái
    nửa chừng — chứng từ đã chốt, tiền đã có chủ, và không ai bên kia nhận được
    việc. Khách thấy màn hình chờ đơn vị; đơn vị không có gì trong hàng đợi.

    Ép vỡ bằng cách làm lệnh ghim hàng đợi đụng 0 dòng — cùng hình dạng với một
    ràng buộc `WHERE` bị sửa nhầm, và nó xảy ra SAU cả hai lệnh `UPDATE`.
    """
    chu = await _khach(db_pool, "kh_vo_sau_ghi")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    import src.db.proposal_repository as kho

    goc = kho._so_dong

    def dem_gia(command_tag):
        # Chỉ nói dối về lệnh INSERT — hai lệnh UPDATE phải chạy thật để bài
        # kiểm chứng minh được chúng bị CUỐN THEO khi rollback.
        if str(command_tag).startswith("INSERT"):
            return 0
        return goc(command_tag)

    monkeypatch.setattr(kho, "_so_dong", dem_gia)

    with pytest.raises(DeXuatBatNhatQuanError):
        await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED"
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(bao_gia.quote_id))
        == "ACTIVE"
    )
    assert list(await _duyet(db_pool, wid)) == []


@pytest.mark.asyncio
async def test_a_proposal_update_that_touches_no_row_rolls_back_the_quote(db_pool, monkeypatch):
    """Mốc thứ hai: lệnh chốt đề xuất đụng 0 dòng → chứng từ cũng quay lại."""
    chu = await _khach(db_pool, "kh_vo_giua_ghi")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    import src.db.proposal_repository as kho

    goc = kho._so_dong
    da_thay_update = {"n": 0}

    def dem_gia(command_tag):
        if str(command_tag).startswith("UPDATE"):
            da_thay_update["n"] += 1
            # Lệnh UPDATE thứ hai là lệnh chốt đề xuất.
            if da_thay_update["n"] == 2:
                return 0
        return goc(command_tag)

    monkeypatch.setattr(kho, "_so_dong", dem_gia)

    with pytest.raises(DeXuatBatNhatQuanError):
        await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED"
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(bao_gia.quote_id))
        == "ACTIVE"
    )
    assert list(await _duyet(db_pool, wid)) == []


@pytest.mark.asyncio
async def test_a_step_whose_tool_no_longer_matches_the_quote_is_refused(db_pool):
    """Bước đổi `tool` sau khi chứng từ được phát → từ chối, và KHÔNG ghi gì.

    `luu_bao_gia` buộc `tool` của bước trùng `service_type` của chứng từ lúc
    ghi, nên trạng thái này không sinh ra được qua đường thật. Nó sinh ra được
    qua một lượt SỬA: vòng sửa lỗi viết lại bước, một migration dữ liệu, một
    lệnh tay lúc trực sự cố.

    Kiểm này phải nằm TRƯỚC lượt ghi đầu tiên — nó là kiểm cuối cùng còn được
    phép `return`. Sau đó thì chỉ còn đường ném, vì chứng từ đã chốt.
    """
    chu = await _khach(db_pool, "kh_tool_lech")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    await db_pool.execute(
        "UPDATE workflow_tasks SET tool = 'create_maintenance_request' WHERE workflow_id = $1::uuid AND task_id = 'T1'",
        uuid.UUID(wid),
    )

    ket_qua = await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    assert ket_qua.ket_qua == "QUOTE_NOT_USABLE"
    assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED"
    assert (
        await db_pool.fetchval("SELECT status FROM service_quotes WHERE quote_id=$1::uuid", uuid.UUID(bao_gia.quote_id))
        == "ACTIVE"
    )
    assert list(await _duyet(db_pool, wid)) == [], "ghim một dòng duyệt không biết mình là việc gì"


# ---------------------------------------- 5. đọc phải fail-CLOSED với đề xuất cũ
@pytest.mark.asyncio
async def test_a_live_proposal_says_it_can_be_confirmed(db_pool):
    chu = await _khach(db_pool, "kh_con_bam_duoc")
    wid = await _workflow(db_pool, chu)
    de_xuat = await ghim_de_xuat(
        db_pool, workflow_id=wid, task_id="T1", quote_id=(await _bao_gia(db_pool, wid)).quote_id
    )
    assert await trang_thai_hieu_luc(db_pool, de_xuat) == ("PROPOSED", True)


@pytest.mark.asyncio
async def test_a_proposal_whose_quote_died_says_it_cannot_be_confirmed(db_pool):
    """Cột nói `PROPOSED`, sự thật nói khác — và người đọc phải nghe sự thật.

    Tin `proposal.status` một mình là fail-OPEN: chứng từ có thể vừa hết hạn
    hoặc vừa bị thay thế trong khi lượt dọn CHƯA CHẠY TỚI, và lúc ấy màn hình
    vẫn dựng nút "đồng ý" cho một cái giá không còn tồn tại. Khách bấm, nhận
    lỗi, bấm lại, nhận lỗi.

    Ba cách chứng từ chết, và trạng thái hiệu lực phải nói ĐÚNG loại — vì hết
    hạn thì xin giá mới, còn bị thay thế thì yêu cầu đã đổi.
    """
    for ten, sql, mong_doi in (
        ("hết hạn", "UPDATE service_quotes SET valid_until = NOW() - INTERVAL '1 min'", "EXPIRED"),
        ("đánh dấu EXPIRED", "UPDATE service_quotes SET status = 'EXPIRED'", "EXPIRED"),
        ("bị thay thế", "UPDATE service_quotes SET status = 'SUPERSEDED'", "SUPERSEDED"),
    ):
        chu = await _khach(db_pool, f"kh_chet_{mong_doi}_{ten[:4]}")
        wid = await _workflow(db_pool, chu)
        bao_gia = await _bao_gia(db_pool, wid)
        de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
        await db_pool.execute(f"{sql} WHERE quote_id = $1::uuid", uuid.UUID(bao_gia.quote_id))

        hieu_luc, con_bam = await trang_thai_hieu_luc(db_pool, de_xuat)

        assert (hieu_luc, con_bam) == (mong_doi, False), f"{ten}: {hieu_luc}/{con_bam}"
        assert (await doc_de_xuat(db_pool, de_xuat.proposal_id)).status == "PROPOSED", (
            f"{ten}: một lượt ĐỌC đã đổi dữ liệu"
        )


@pytest.mark.asyncio
async def test_reading_the_effective_status_never_writes(db_pool):
    """Một `GET` chữa dữ liệu là một `GET` đổi trạng thái — từ một tab đang mở,
    từ một lượt poll, từ một con bot quét link. Việc dọn thuộc về đường dọn."""
    chu = await _khach(db_pool, "kh_doc_khong_ghi")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)
    await _het_han(db_pool, bao_gia.quote_id)

    truoc = await db_pool.fetchrow(
        "SELECT (SELECT status FROM service_quotes WHERE quote_id=$1::uuid) q, "
        "       (SELECT status FROM service_provider_proposals WHERE proposal_id=$2::uuid) p",
        uuid.UUID(bao_gia.quote_id),
        uuid.UUID(de_xuat.proposal_id),
    )
    for _ in range(3):
        await trang_thai_hieu_luc(db_pool, de_xuat)
    sau = await db_pool.fetchrow(
        "SELECT (SELECT status FROM service_quotes WHERE quote_id=$1::uuid) q, "
        "       (SELECT status FROM service_provider_proposals WHERE proposal_id=$2::uuid) p",
        uuid.UUID(bao_gia.quote_id),
        uuid.UUID(de_xuat.proposal_id),
    )
    assert dict(truoc) == dict(sau)


@pytest.mark.asyncio
async def test_a_decided_proposal_can_never_be_confirmed_again(db_pool):
    chu = await _khach(db_pool, "kh_da_quyet")
    wid = await _workflow(db_pool, chu)
    de_xuat = await ghim_de_xuat(
        db_pool, workflow_id=wid, task_id="T1", quote_id=(await _bao_gia(db_pool, wid)).quote_id
    )
    await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    da_chot = await doc_de_xuat(db_pool, de_xuat.proposal_id)
    assert await trang_thai_hieu_luc(db_pool, da_chot) == ("CONFIRMED", False)


# ------------------------------------------------------ 6/7. thay thế, không mở lại
@pytest.mark.asyncio
async def test_a_new_proposal_supersedes_the_one_still_waiting(db_pool):
    """Đúng MỘT đề xuất đang sống mỗi bước. Cái mới đẩy cái cũ sang SUPERSEDED.

    Không có luật này thì hai dòng `PROPOSED` cùng tồn tại, và khách xác nhận
    một cái trong khi màn hình đang hiển thị cái kia.
    """
    chu = await _khach(db_pool, "kh_thay_the")
    wid = await _workflow(db_pool, chu)
    cu = await ghim_de_xuat(
        db_pool, workflow_id=wid, task_id="T1", quote_id=(await _bao_gia(db_pool, wid, don_vi="MOV-02")).quote_id
    )
    moi = await ghim_de_xuat(
        db_pool, workflow_id=wid, task_id="T1", quote_id=(await _bao_gia(db_pool, wid, don_vi="MOV-01")).quote_id
    )

    assert (await doc_de_xuat(db_pool, cu.proposal_id)).status == "SUPERSEDED"
    assert (await doc_de_xuat(db_pool, moi.proposal_id)).status == "PROPOSED"
    assert (await de_xuat_dang_cho(db_pool, workflow_id=wid, task_id="T1")).proposal_id == moi.proposal_id


@pytest.mark.asyncio
async def test_a_superseded_proposal_cannot_be_confirmed(db_pool):
    """Khách còn mở tab cũ và bấm đề xuất đã bị thay → `ALREADY_DECIDED`."""
    chu = await _khach(db_pool, "kh_tab_cu")
    wid = await _workflow(db_pool, chu)
    cu = await ghim_de_xuat(
        db_pool, workflow_id=wid, task_id="T1", quote_id=(await _bao_gia(db_pool, wid, don_vi="MOV-02")).quote_id
    )
    await ghim_de_xuat(
        db_pool, workflow_id=wid, task_id="T1", quote_id=(await _bao_gia(db_pool, wid, don_vi="MOV-01")).quote_id
    )

    ket_qua = await xac_nhan_de_xuat(db_pool, cu.proposal_id, owner_user_id=chu)

    assert ket_qua.ket_qua == "ALREADY_DECIDED"
    assert list(await _duyet(db_pool, wid)) == []


@pytest.mark.asyncio
async def test_a_confirmed_proposal_is_never_reopened(db_pool):
    """Đã xác nhận là một quyết định ĐÃ XẢY RA — và nó đã sinh ra một dòng
    trong hàng đợi của đơn vị. Một lượt đề xuất mới không được viết đè lên nó."""
    chu = await _khach(db_pool, "kh_da_chot")
    wid = await _workflow(db_pool, chu)
    da_chot = await ghim_de_xuat(
        db_pool, workflow_id=wid, task_id="T1", quote_id=(await _bao_gia(db_pool, wid, don_vi="MOV-02")).quote_id
    )
    await xac_nhan_de_xuat(db_pool, da_chot.proposal_id, owner_user_id=chu)

    await ghim_de_xuat(
        db_pool, workflow_id=wid, task_id="T1", quote_id=(await _bao_gia(db_pool, wid, don_vi="MOV-01")).quote_id
    )
    await don_bao_gia_va_de_xuat(db_pool, workflow_id=wid, task_id="T1", van_tay_moi="mot van tay khac")

    assert (await doc_de_xuat(db_pool, da_chot.proposal_id)).status == "CONFIRMED"
    assert (await doc_de_xuat(db_pool, da_chot.proposal_id)).confirmed_at is not None


# ------------------------------------------------------- 8. chủ sở hữu từ chứng từ
@pytest.mark.asyncio
async def test_the_approval_owner_comes_from_the_quote(db_pool):
    """`service_approvals.service_provider_id` lấy từ CHỨNG TỪ đã persist.

    Không từ model, không từ task, không từ body. Đó là toàn bộ lý do bước B
    tồn tại: thứ quyết định ai nhận việc và ai được trả tiền phải là thứ có
    chứng từ đối chiếu được.

    Kiểm bằng một chứng từ của MOV-01 trong khi bước cũng có một chứng từ khác
    — nếu chủ sở hữu bị lấy từ đâu khác, nó sẽ không khớp đúng cái được đề xuất.
    """
    chu = await _khach(db_pool, "kh_chu_so_huu")
    wid = await _workflow(db_pool, chu)
    await _bao_gia(db_pool, wid, don_vi="MOV-03", gia=420_000)
    duoc_chon = await _bao_gia(db_pool, wid, don_vi="MOV-01", gia=430_000)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=duoc_chon.quote_id)

    await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    dong = await _duyet(db_pool, wid)
    assert len(dong) == 1
    assert dong[0]["service_provider_id"] == "MOV-01", "chủ sở hữu không đến từ chứng từ được đề xuất"
    assert dong[0]["status"] == "AWAITING"
    assert str(dong[0]["applicant_user_id"]) == chu, "người yêu cầu không đến từ chủ workflow"


@pytest.mark.asyncio
async def test_the_customers_budget_never_reaches_the_provider(db_pool):
    """`max_price` KHÔNG được có mặt trong dữ kiện đơn vị nhìn thấy.

    Bước B dựng hai hàng rào để ngân sách không rời khỏi P-118 ở đường xin báo
    giá. Để nó rò qua đường ghim hàng đợi là chặn một nửa: đơn vị vẫn đọc được
    túi tiền của khách, chỉ chậm hơn một bước.

    Bước ở fixture cố ý mang `max_price` trong `input_data`.
    """
    chu = await _khach(db_pool, "kh_ngan_sach")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    chi_tiet = (await _duyet(db_pool, wid))[0]["details"]
    import json as _json

    chi_tiet = _json.loads(chi_tiet) if isinstance(chi_tiet, str) else chi_tiet
    assert "max_price" not in chi_tiet, f"ngân sách của khách lọt sang đơn vị: {chi_tiet}"
    assert chi_tiet.get("move_date") == "2026-09-30", "dữ kiện cần thiết bị lọc mất"


@pytest.mark.asyncio
async def test_confirming_does_not_move_the_step_out_of_waiting(db_pool):
    """Bước vẫn `WAITING_APPROVAL` sau khi khách bấm — chỉ NGƯỜI CHỜ đổi.

    Trước: chờ khách. Sau: chờ đơn vị. Không cột nào ghi điều đó, và đó là cố
    ý — `approval_actor` được suy ra lúc dựng câu trả lời. Lưu nó nghĩa là có
    hai chỗ nói "đang chờ ai", và chỗ thứ hai sẽ đứng im đúng lúc việc đổi tay.
    """
    chu = await _khach(db_pool, "kh_van_cho")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    assert (
        await db_pool.fetchval(
            "SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", uuid.UUID(wid)
        )
        == "WAITING_APPROVAL"
    )
    assert (
        await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", uuid.UUID(wid))
        == "WAITING_APPROVAL"
    )


@pytest.mark.asyncio
async def test_confirming_does_not_call_any_provider(db_pool):
    """Lượt xác nhận chỉ MỞ hàng đợi. Yêu cầu đi ra ngoài khi ĐƠN VỊ bấm duyệt.

    Bằng chứng ở trạng thái gửi provider của bước: `NOT_SUBMITTED` và không có
    `external_request_id`. Nếu lượt xác nhận gọi thật thì hai trường ấy đổi —
    và lúc đơn vị từ chối, yêu cầu đã nằm bên họ rồi.
    """
    chu = await _khach(db_pool, "kh_khong_goi")
    wid = await _workflow(db_pool, chu)
    bao_gia = await _bao_gia(db_pool, wid)
    de_xuat = await ghim_de_xuat(db_pool, workflow_id=wid, task_id="T1", quote_id=bao_gia.quote_id)

    await xac_nhan_de_xuat(db_pool, de_xuat.proposal_id, owner_user_id=chu)

    row = await db_pool.fetchrow(
        "SELECT provider_submission_status, external_request_id FROM workflow_tasks "
        "WHERE workflow_id=$1::uuid AND task_id='T1'",
        uuid.UUID(wid),
    )
    assert row["provider_submission_status"] == "NOT_SUBMITTED"
    assert row["external_request_id"] is None
