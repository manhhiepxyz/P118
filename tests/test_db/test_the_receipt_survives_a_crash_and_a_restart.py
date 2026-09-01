"""Ghi nghiệp vụ xong, ghi biên lai chưa xong — rồi tiến trình chết.

Đây là khe hở còn lại sau khi biên lai ra đời. Nó chỉ dịch xuống một tầng:

    provider APPROVED
    → materialize commit          ← nghiệp vụ ĐÃ xong, quyền ĐÃ mở
    → receipts.finish(SUCCESS)    ← chết ở đây
    → response

Nếu route trả 200 ở trạng thái này thì orchestration tuyên bố xong một việc mà
nó không lưu được bằng chứng nào; sau restart, lượt phục hồi đọc biên lai và
thấy một việc dở dang không có thật. Ngược lại, nếu nó trả 500 thô thì người
dùng đọc "hỏng" cho một việc đã thành công.

Hai gate ở đây:

    Gate 5   nghiệp vụ đã commit, biên lai chưa SUCCESS → không được báo 200,
             và lượt sau phải hội tụ
    Gate 11  BỎ HẲN mọi object của lượt đầu, dựng instance mới, và chứng minh
             lượt phục hồi đọc PostgreSQL + provider chứ không đọc RAM
"""

from __future__ import annotations

import uuid

import pytest

from src.db.verification_receipt_repository import (
    ReceiptMissingError,
    VerificationReceipts,
    VerificationRecoveryUnavailableError,
)
from src.orchestration.verification_recovery import (
    DecisionConflictError,
    ProviderStateUnknownError,
    run_decision,
)
from tests.test_db.conftest import _register_and_login


# ---------------------------------------------------------------------------
# Hệ thống NGOÀI, sống độc lập với mọi instance connector.
#
# Đây là điểm mấu chốt của Gate 11: nếu trạng thái provider nằm trong chính
# object connector của lượt đầu thì "restart" chỉ là đổi tên biến — connector
# mới sẽ mất trí nhớ, và test chứng minh nhầm điều nó muốn chứng minh.
# ---------------------------------------------------------------------------
class ProviderStore:
    """Đứng ngoài tiến trình, giống Ownership Provider thật."""

    def __init__(self):
        self.records: dict[str, dict] = {}
        self.decide_calls = 0
        self.unavailable = False


class OwnershipClient:
    """MỘT instance connector. Không giữ trạng thái nghiệp vụ nào."""

    def __init__(self, store: ProviderStore):
        self._store = store

    async def get_record(self, record_id):
        if self._store.unavailable:
            from src.connectors.ownership import OwnershipProviderError

            raise OwnershipProviderError(503, "SERVICE_UNAVAILABLE", "tạm ngừng")
        return dict(self._store.records[record_id])

    async def decide_record(self, record_id, *, decision, reject_reason=None, decided_by=None):
        self._store.decide_calls += 1
        record = self._store.records[record_id]
        if record["status"] != "PENDING":
            from src.connectors.ownership import OwnershipProviderError

            raise OwnershipProviderError(409, "ALREADY_DECIDED", "Record already decided")
        record["status"] = "APPROVED" if decision == "approve" else "REJECTED"
        record["decided_by"] = decided_by
        record["decided_at"] = "2026-08-21T10:00:00+00:00"
        return dict(record)


async def _account(client, db_pool, username):
    await _register_and_login(client, username)
    return str(await db_pool.fetchval("SELECT id FROM users WHERE username=$1", username))


def _record(applicant, canary, status="APPROVED"):
    return {
        "record_id": str(uuid.uuid4()),
        "record_type": "apartment",
        "status": status,
        "applicant_user_id": applicant,
        "claimed_data": {
            "apartment_code": canary,
            "residential_area": "Toà S1",
            "full_name": "Nguyen Van Restart",
        },
        "decided_by": "don-vi" if status != "PENDING" else None,
        "decided_at": "2026-08-21T10:00:00+00:00" if status != "PENDING" else None,
        "reject_reason": None,
        "created_at": "2026-08-20T10:00:00+00:00",
    }


async def _counts(db_pool, uid):
    return {
        "links": await db_pool.fetchval("SELECT count(*) FROM user_resident_links WHERE user_id=$1::uuid", uid),
        "residents": await db_pool.fetchval("SELECT count(*) FROM residents"),
    }


async def _receipt(db_pool, record_id):
    row = await db_pool.fetchrow("SELECT * FROM verification_materializations WHERE record_id=$1::uuid", record_id)
    return dict(row) if row else None


def _real_materializer(pool):
    """Materialize THẬT — cùng hàm production, ghi thật xuống PostgreSQL.

    Không stub: gate này nói về "nghiệp vụ đã commit", nên nghiệp vụ phải commit
    thật, nếu không cả bài đo là giả.
    """
    from src.db.link_request_repository import materialize_resident_link

    async def _run(record):
        claim = record["claimed_data"]
        await materialize_resident_link(
            pool,
            user_id=record["applicant_user_id"],
            apartment_code=claim["apartment_code"],
            residential_area=claim["residential_area"],
            full_name=claim["full_name"],
        )
        return None

    return _run


# ---------------------------------------------------------------------------
# Gate 5
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_business_data_commits_but_the_receipt_write_fails(client, db_pool):
    """Ghi nghiệp vụ xong, ghi biên lai hỏng. Không được báo hoàn tất."""
    a = await _account(client, db_pool, "gate5_khach")
    canary = f"G5{uuid.uuid4().hex[:6].upper()}"
    store = ProviderStore()
    record = _record(a, canary)
    store.records[record["record_id"]] = record

    receipts = VerificationReceipts(db_pool)
    that_bai = {"n": 0}
    finish_that = receipts.finish

    async def _finish_hong(record_id, status, code):
        if status == "SUCCESS":
            that_bai["n"] += 1
            # Lỗi DOMAIN, không phải `ConnectionError` thô: tầng repository
            # đã có trách nhiệm dịch lỗi hạ tầng, nên thứ orchestration nhìn
            # thấy luôn là lỗi domain. Ném lỗi thô ở đây là mô phỏng một biên
            # giới không tồn tại.
            raise VerificationRecoveryUnavailableError()
        return await finish_that(record_id, status, code)

    receipts.finish = _finish_hong

    outcome = await run_decision(
        record_id=record["record_id"],
        requested_decision="approve",
        decided_by="don-vi",
        reject_reason=None,
        ownership=OwnershipClient(store),
        receipts=receipts,
        materialize=_real_materializer(db_pool),
    )

    # Nghiệp vụ ĐÃ commit — chứng minh, không giả định.
    dem = await _counts(db_pool, a)
    assert dem["links"] == 1, "chưa commit nghiệp vụ thì test này không đo đúng thứ nó nói"
    assert (
        await db_pool.fetchval("SELECT verification_status FROM user_resident_links WHERE user_id=$1::uuid", a)
        == "VERIFIED"
    )
    # Provider vẫn APPROVED, và KHÔNG bị hỏi lại.
    assert store.records[record["record_id"]]["status"] == "APPROVED"
    assert store.decide_calls == 0, "provider đã APPROVED sẵn mà vẫn bị gọi decide"
    # Orchestration KHÔNG tuyên bố xong.
    assert outcome.materialization_status == "PENDING", outcome.materialization_status
    assert not outcome.finished, "báo hoàn tất trong khi biên lai chưa ghi được"
    assert that_bai["n"] == 1
    bien_lai = await _receipt(db_pool, record["record_id"])
    assert bien_lai["materialization_status"] != "SUCCESS"


@pytest.mark.asyncio
async def test_the_retry_converges_without_duplicating_anything(client, db_pool):
    """Lượt sau (biên lai ghi được) phải hội tụ, và không tạo dòng thứ hai."""
    a = await _account(client, db_pool, "gate5_khach_hoi_tu")
    canary = f"G5{uuid.uuid4().hex[:6].upper()}"
    store = ProviderStore()
    record = _record(a, canary)
    store.records[record["record_id"]] = record

    receipts = VerificationReceipts(db_pool)
    finish_that = receipts.finish

    async def _finish_hong(record_id, status, code):
        if status == "SUCCESS":
            # Lỗi DOMAIN, không phải `ConnectionError` thô: tầng repository
            # đã có trách nhiệm dịch lỗi hạ tầng, nên thứ orchestration nhìn
            # thấy luôn là lỗi domain. Ném lỗi thô ở đây là mô phỏng một biên
            # giới không tồn tại.
            raise VerificationRecoveryUnavailableError()
        return await finish_that(record_id, status, code)

    receipts.finish = _finish_hong
    await run_decision(
        record_id=record["record_id"],
        requested_decision="approve",
        decided_by="don-vi",
        reject_reason=None,
        ownership=OwnershipClient(store),
        receipts=receipts,
        materialize=_real_materializer(db_pool),
    )
    truoc = await _counts(db_pool, a)

    receipts.finish = finish_that
    lai = await run_decision(
        record_id=record["record_id"],
        requested_decision="approve",
        decided_by="don-vi",
        reject_reason=None,
        ownership=OwnershipClient(store),
        receipts=receipts,
        materialize=_real_materializer(db_pool),
    )

    assert lai.materialization_status == "SUCCESS"
    assert lai.called_provider_decide is False
    assert store.decide_calls == 0
    assert await _counts(db_pool, a) == truoc, "lượt phục hồi tạo dòng nghiệp vụ thứ hai"
    assert (await _receipt(db_pool, record["record_id"]))["materialization_status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_a_receipt_deleted_after_start_is_rebuilt_by_the_next_request(client, db_pool):
    """Biên lai mất SAU `start_materialization` → lượt request SAU dựng lại.

    Ranh giới quan trọng: ở đây `start_materialization` đã chạy xong rồi biên
    lai mới bị xoá, nên nhánh reconstruct TRONG cùng một lượt gọi KHÔNG được
    chạm tới. Thứ bắt được lỗi là row-count guard ở `finish`, và việc hội tụ
    xảy ra ở lượt gọi kế tiếp.

    Nhánh reconstruct cùng-lượt có test riêng bên dưới
    (`test_a_receipt_missing_before_start_is_rebuilt_in_the_same_request`).
    """
    a = await _account(client, db_pool, "gate5_khach_mat_bien_lai")
    store = ProviderStore()
    record = _record(a, f"G5{uuid.uuid4().hex[:6].upper()}")
    store.records[record["record_id"]] = record
    receipts = VerificationReceipts(db_pool)

    async def _xoa_roi_materialize(rec):
        await db_pool.execute("DELETE FROM verification_materializations WHERE record_id=$1::uuid", rec["record_id"])
        return await _real_materializer(db_pool)(rec)

    outcome = await run_decision(
        record_id=record["record_id"],
        requested_decision="approve",
        decided_by="don-vi",
        reject_reason=None,
        ownership=OwnershipClient(store),
        receipts=receipts,
        materialize=_xoa_roi_materialize,
    )

    # `finish(SUCCESS)` khớp 0 dòng → phải NỔ, không im lặng coi là xong.
    assert outcome.materialization_status == "PENDING", outcome.materialization_status
    assert (await _counts(db_pool, a))["links"] == 1

    lai = await run_decision(
        record_id=record["record_id"],
        requested_decision="approve",
        decided_by="don-vi",
        reject_reason=None,
        ownership=OwnershipClient(store),
        receipts=receipts,
        materialize=_real_materializer(db_pool),
    )
    assert lai.materialization_status == "SUCCESS"
    assert store.decide_calls == 0
    bien_lai = await _receipt(db_pool, record["record_id"])
    assert bien_lai["record_type"] == "apartment", "dựng lại bằng dữ liệu bịa"


@pytest.mark.asyncio
async def test_a_missing_receipt_raises_instead_of_updating_nothing(client, db_pool):
    """`UPDATE ... WHERE record_id=...` khớp 0 dòng KHÔNG báo lỗi ở PostgreSQL."""
    receipts = VerificationReceipts(db_pool)
    khong_co = str(uuid.uuid4())

    for goi in (
        receipts.finish(khong_co, "SUCCESS", None),
        receipts.set_provider_status(khong_co, "APPROVED"),
        receipts.set_record_type(khong_co, "apartment"),
        receipts.start_materialization(khong_co),
    ):
        with pytest.raises(ReceiptMissingError):
            await goi


# ---------------------------------------------------------------------------
# Gate 11 — restart bằng object hoàn toàn mới
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_brand_new_process_resumes_from_postgres_and_the_provider(client, db_pool):
    """Bỏ HẲN mọi object của lượt đầu, dựng instance mới, rồi chạy tiếp.

    Trạng thái provider nằm ở `ProviderStore` — ngoài connector — nên connector
    mới không "nhớ" gì. Thứ duy nhất đi qua ranh giới là `record_id`.
    """
    a = await _account(client, db_pool, "gate11_khach")
    canary = f"G11{uuid.uuid4().hex[:5].upper()}"
    store = ProviderStore()
    record = _record(a, canary, status="PENDING")
    record_id = record["record_id"]
    store.records[record_id] = record

    # ---- lượt 1: instance A ------------------------------------------------
    receipts_a = VerificationReceipts(db_pool)
    finish_that = VerificationReceipts.finish

    async def _finish_hong(self, rid, status, code):
        if status == "SUCCESS":
            raise VerificationRecoveryUnavailableError()
        return await finish_that(self, rid, status, code)

    receipts_a.finish = _finish_hong.__get__(receipts_a, VerificationReceipts)
    ket_qua_a = await run_decision(
        record_id=record_id,
        requested_decision="approve",
        decided_by="don-vi",
        reject_reason=None,
        ownership=OwnershipClient(store),
        receipts=receipts_a,
        materialize=_real_materializer(db_pool),
    )
    assert ket_qua_a.called_provider_decide is True
    assert store.decide_calls == 1
    assert ket_qua_a.materialization_status == "PENDING"
    truoc = await _counts(db_pool, a)
    assert truoc["links"] == 1

    # ---- "restart": bỏ hẳn instance A -------------------------------------
    del receipts_a, ket_qua_a

    # ---- lượt 2: instance B, hoàn toàn mới --------------------------------
    receipts_b = VerificationReceipts(db_pool)
    connector_b = OwnershipClient(store)
    ket_qua_b = await run_decision(
        record_id=record_id,
        requested_decision="approve",
        decided_by="don-vi",
        reject_reason=None,
        ownership=connector_b,
        receipts=receipts_b,
        materialize=_real_materializer(db_pool),
    )

    assert ket_qua_b.called_provider_decide is False, "hỏi đơn vị quyết định lần thứ hai"
    assert store.decide_calls == 1, f"decide bị gọi {store.decide_calls} lần"
    assert ket_qua_b.materialization_status == "SUCCESS"
    assert await _counts(db_pool, a) == truoc, "lượt phục hồi nhân đôi dòng nghiệp vụ"
    assert (await _receipt(db_pool, record_id))["materialization_status"] == "SUCCESS"

    # ---- lượt 3: vẫn không nhân đôi ---------------------------------------
    ket_qua_c = await run_decision(
        record_id=record_id,
        requested_decision="approve",
        decided_by="don-vi",
        reject_reason=None,
        ownership=OwnershipClient(store),
        receipts=VerificationReceipts(db_pool),
        materialize=_real_materializer(db_pool),
    )
    assert ket_qua_c.materialization_status == "SUCCESS"
    assert store.decide_calls == 1
    assert await _counts(db_pool, a) == truoc


# ---------------------------------------------------------------------------
# M20 — provider luôn là nguồn sự thật
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_receipt_that_says_approved_never_overrides_the_provider(client, db_pool):
    """Biên lai nói APPROVED, đơn vị nói REJECTED. Đơn vị thắng."""
    a = await _account(client, db_pool, "m20_khach_tu_choi")
    store = ProviderStore()
    record = _record(a, f"M20{uuid.uuid4().hex[:5].upper()}", status="REJECTED")
    store.records[record["record_id"]] = record
    receipts = VerificationReceipts(db_pool)
    await receipts.open_receipt(
        record_id=record["record_id"],
        record_type="apartment",
        requested_decision="approve",
        idempotency_key=f"verif:{record['record_id']}",
    )
    await receipts.set_provider_status(record["record_id"], "APPROVED")

    with pytest.raises(DecisionConflictError):
        await run_decision(
            record_id=record["record_id"],
            requested_decision="approve",
            decided_by="don-vi",
            reject_reason=None,
            ownership=OwnershipClient(store),
            receipts=receipts,
            materialize=_real_materializer(db_pool),
        )

    assert (await _counts(db_pool, a))["links"] == 0, "materialize dựa trên biên lai, không hỏi đơn vị"


@pytest.mark.asyncio
async def test_an_unreachable_provider_is_never_guessed_from_the_receipt(client, db_pool):
    a = await _account(client, db_pool, "m20_khach_provider_chet")
    store = ProviderStore()
    record = _record(a, f"M20{uuid.uuid4().hex[:5].upper()}")
    store.records[record["record_id"]] = record
    store.unavailable = True
    receipts = VerificationReceipts(db_pool)

    from src.connectors.ownership import OwnershipProviderError

    with pytest.raises((OwnershipProviderError, ProviderStateUnknownError)):
        await run_decision(
            record_id=record["record_id"],
            requested_decision="approve",
            decided_by="don-vi",
            reject_reason=None,
            ownership=OwnershipClient(store),
            receipts=receipts,
            materialize=_real_materializer(db_pool),
        )

    assert (await _counts(db_pool, a))["links"] == 0
    assert store.decide_calls == 0


# ---------------------------------------------------------------------------
# Nhánh reconstruct — biên lai mất TRƯỚC `start_materialization`
# ---------------------------------------------------------------------------


class _MatBienLaiTruocKhiBatDau(VerificationReceipts):
    """Xoá biên lai đúng NGAY TRƯỚC lần `start_materialization` đầu tiên.

    Mọi thao tác SQL khác vẫn gọi implementation thật — chỉ dựng lại đúng một
    tai nạn ở đúng một ranh giới, không thay cả repository bằng một cái giả.
    """

    def __init__(self, pool):
        super().__init__(pool)
        self._pool_raw = pool
        self.da_xoa = False
        self.so_lan_start = 0
        self.so_lan_open = 0

    async def open_receipt(self, **kwargs):
        self.so_lan_open += 1
        return await super().open_receipt(**kwargs)

    async def start_materialization(self, record_id):
        self.so_lan_start += 1
        if not self.da_xoa:
            self.da_xoa = True
            await self._pool_raw.execute(
                "DELETE FROM verification_materializations WHERE record_id=$1::uuid", record_id
            )
        # Implementation THẬT — nó phải tự phát hiện 0 dòng và ném.
        return await super().start_materialization(record_id)


@pytest.mark.asyncio
async def test_a_receipt_missing_before_start_is_rebuilt_in_the_same_request(client, db_pool):
    """Biên lai biến mất trước khi bắt đầu ghi: dựng lại NGAY trong lượt này.

    Dựng lại bằng dữ kiện AUTHORITATIVE vừa đọc — loại hồ sơ từ provider, ý
    định từ chính request — chứ không bịa. Và tuyệt đối không hỏi đơn vị quyết
    định lần thứ hai: họ đã ký rồi.
    """
    a = await _account(client, db_pool, "reconstruct_khach")
    canary = f"RC{uuid.uuid4().hex[:6].upper()}"
    store = ProviderStore()
    record = _record(a, canary)
    record_id = record["record_id"]
    store.records[record_id] = record

    receipts = _MatBienLaiTruocKhiBatDau(db_pool)
    outcome = await run_decision(
        record_id=record_id,
        requested_decision="approve",
        decided_by="don-vi",
        reject_reason=None,
        ownership=OwnershipClient(store),
        receipts=receipts,
        materialize=_real_materializer(db_pool),
    )

    # Nhánh reconstruct THỰC SỰ chạy: start bị gọi hai lần, open bị gọi hai lần.
    assert receipts.da_xoa is True
    assert receipts.so_lan_start == 2, f"nhánh dựng lại không chạy (start={receipts.so_lan_start})"
    assert receipts.so_lan_open == 2, f"biên lai không được dựng lại (open={receipts.so_lan_open})"

    # Đơn vị KHÔNG bị hỏi lần hai.
    assert store.decide_calls == 0, f"decide bị gọi {store.decide_calls} lần"
    assert outcome.called_provider_decide is False

    # Hội tụ, và biên lai mang dữ kiện thật.
    assert outcome.materialization_status == "SUCCESS", outcome.materialization_status
    bien_lai = await _receipt(db_pool, record_id)
    assert bien_lai["record_type"] == "apartment"
    assert bien_lai["requested_decision"] == "approve"
    assert bien_lai["idempotency_key"] == f"verif:{record_id}"
    assert bien_lai["materialization_status"] == "SUCCESS"

    # Đúng một dòng nghiệp vụ.
    assert (await _counts(db_pool, a))["links"] == 1


class _BienLaiKhongGhiDuoc(VerificationReceipts):
    """`start_materialization` hỏng vì HẠ TẦNG, không phải vì mất dòng."""

    def __init__(self, pool):
        super().__init__(pool)
        self.so_lan_open = 0

    async def open_receipt(self, **kwargs):
        self.so_lan_open += 1
        return await super().open_receipt(**kwargs)

    async def start_materialization(self, record_id):
        raise ConnectionError("mất kết nối PostgreSQL")


@pytest.mark.asyncio
async def test_a_receipt_database_outage_is_not_mistaken_for_a_missing_receipt(client, db_pool):
    """Không ghi được ≠ không còn ở đó. Gộp hai thứ này là chạy nghiệp vụ mù.

    Dựng lại biên lai khi database đang hỏng nghĩa là chạy đúng câu lệnh vừa
    hỏng, rồi materialize dựa trên một trạng thái chưa từng được persist — tức
    mở quyền cho người dùng mà không có dòng nào chứng minh.
    """
    a = await _account(client, db_pool, "receipt_db_chet")
    store = ProviderStore()
    record = _record(a, f"RD{uuid.uuid4().hex[:6].upper()}")
    record_id = record["record_id"]
    store.records[record_id] = record

    da_materialize = {"n": 0}

    async def _dem(rec):
        da_materialize["n"] += 1
        return await _real_materializer(db_pool)(rec)

    receipts = _BienLaiKhongGhiDuoc(db_pool)
    with pytest.raises(ConnectionError):
        await run_decision(
            record_id=record_id,
            requested_decision="approve",
            decided_by="don-vi",
            reject_reason=None,
            ownership=OwnershipClient(store),
            receipts=receipts,
            materialize=_dem,
        )

    assert receipts.so_lan_open == 1, "cố dựng lại biên lai bằng chính database đang hỏng"
    assert da_materialize["n"] == 0, "chạy nghiệp vụ khi chưa ghi được trạng thái nào"
    assert store.decide_calls == 0
    assert (await _counts(db_pool, a))["links"] == 0


# ---------------------------------------------------------------------------
# Mục C — `PENDING + receipt PENDING` là hợp lệ hay drift?
#
# Không đoán. `run_decision` mở biên lai TRƯỚC khi đọc provider, nên tồn tại một
# khoảng trong đó biên lai đã `PENDING` còn đơn vị chưa quyết định gì. Câu hỏi
# là khoảng ấy có thật không — và nó trả lời được bằng cách chụp trạng thái
# database tại đúng ranh giới, không phải bằng `sleep`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pending_receipt_before_the_provider_decides_is_a_valid_moment(client, db_pool):
    """Chụp trạng thái DB tại ĐÚNG ranh giới giữa hai bước.

    Điểm dừng là lần gọi `get_record` đầu tiên: tới lúc đó `open_receipt` đã
    chạy xong và provider chưa được hỏi gì. Không `sleep`, không đoán thời
    điểm — chính lời gọi ấy là tín hiệu đồng bộ.
    """
    from src.orchestration.verification_status import derive

    a = await _account(client, db_pool, "gateC_khach")
    store = ProviderStore()
    record = _record(a, f"GC{uuid.uuid4().hex[:6].upper()}", status="PENDING")
    record_id = record["record_id"]
    store.records[record_id] = record

    chup: dict = {}
    goc = OwnershipClient(store)

    class _ChupTaiRanhGioi(OwnershipClient):
        def __init__(self, store_):
            super().__init__(store_)

        async def get_record(self, rid):
            if "bien_lai" not in chup:
                row = await db_pool.fetchrow(
                    "SELECT materialization_status, record_type FROM verification_materializations "
                    "WHERE record_id=$1::uuid",
                    rid,
                )
                chup["bien_lai"] = dict(row) if row else None
                chup["provider"] = self._store.records[rid]["status"]
                chup["decide_calls"] = self._store.decide_calls
            return await goc.get_record(rid)

    await run_decision(
        record_id=record_id,
        requested_decision="approve",
        decided_by="don-vi",
        reject_reason=None,
        ownership=_ChupTaiRanhGioi(store),
        receipts=VerificationReceipts(db_pool),
        materialize=_real_materializer(db_pool),
    )

    # --- khoảnh khắc ấy CÓ THẬT --------------------------------------------
    assert chup["bien_lai"] is not None, "biên lai chưa được mở trước khi hỏi đơn vị"
    assert chup["bien_lai"]["materialization_status"] == "PENDING"
    assert chup["provider"] == "PENDING", "đơn vị đã quyết định trước khi biên lai được mở"
    assert chup["decide_calls"] == 0
    # Và loại hồ sơ chưa được điền — chưa đọc provider thì chưa biết.
    assert chup["bien_lai"]["record_type"] is None

    # --- nên nó phải là trạng thái HỢP LỆ, không phải drift -----------------
    view = derive(chup["provider"], chup["bien_lai"]["materialization_status"], None)
    assert view.effective_status == "WAITING_PROVIDER", view.effective_status
    assert view.consistency_status == "CONSISTENT", view.consistency_status
    assert view.display_status == "Đang chờ đơn vị xác minh"
    assert view.effective_status != "VERIFIED"
