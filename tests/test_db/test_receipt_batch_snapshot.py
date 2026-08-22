"""Tra biên lai theo LÔ — một query, và phân biệt được "không có dòng".

Hai bất biến, và cái thứ hai là cái dễ mất nhất:

1. **Một query cho cả trang.** Màn giám sát của admin và hàng đợi của đơn vị
   đều trả danh sách; tra từng dòng là N+1 trên một màn hình để mở suốt ngày.

2. **`receipt_exists` là trường riêng.** "Không có biên lai" (dữ liệu cũ, hoặc
   một cú chết trước dòng đầu tiên) khác hẳn "có biên lai và nó nói
   NOT_STARTED". Gộp hai thứ ấy là mất đúng tín hiệu nhận diện hồ sơ APPROVED
   cần đối soát — và một hồ sơ như thế sẽ hiện là "đang xử lý bình thường".
"""

from __future__ import annotations

import uuid

import pytest

from src.db.verification_receipt_repository import (
    ReceiptSnapshot,
    VerificationReceipts,
    VerificationRecoveryUnavailableError,
    snapshot_or_missing,
)


class _ConnDem:
    """Bọc connection thật, đếm TỪNG LOẠI truy vấn."""

    def __init__(self, conn, so):
        self._conn = conn
        self._so = so

    async def fetch(self, *a, **k):
        self._so.fetch_count += 1
        return await self._conn.fetch(*a, **k)

    async def fetchrow(self, *a, **k):
        self._so.fetchrow_count += 1
        return await self._conn.fetchrow(*a, **k)

    async def fetchval(self, *a, **k):
        self._so.fetchval_count += 1
        return await self._conn.fetchval(*a, **k)

    async def execute(self, *a, **k):
        self._so.execute_count += 1
        return await self._conn.execute(*a, **k)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _PoolDem:
    """Pool đếm số lần acquire VÀ số câu truy vấn trên connection nó phát ra."""

    def __init__(self, that):
        self._that = that
        self.acquire_count = 0
        self.fetch_count = 0
        self.fetchrow_count = 0
        self.fetchval_count = 0
        self.execute_count = 0

    def acquire(self):
        self.acquire_count += 1
        goc = self._that.acquire()
        so = self

        class _Ctx:
            async def __aenter__(self):
                return _ConnDem(await goc.__aenter__(), so)

            async def __aexit__(self, *exc):
                return await goc.__aexit__(*exc)

        return _Ctx()


async def _mo(repo, record_id, *, decision="approve"):
    await repo.open_receipt(
        record_id=record_id,
        record_type="apartment",
        requested_decision=decision,
        idempotency_key=f"verif:{record_id}",
    )


@pytest.mark.asyncio
async def test_an_empty_list_never_touches_the_database(db_pool):
    """Danh sách rỗng không được mở kết nối — pool này sẽ nổ nếu ai đó thử."""

    class _PoolNo:
        def acquire(self):
            raise AssertionError("mở kết nối cho một danh sách rỗng")

    assert await VerificationReceipts(_PoolNo()).snapshot_for([]) == {}


@pytest.mark.asyncio
async def test_a_missing_receipt_is_not_the_same_as_not_started(db_pool):
    repo = VerificationReceipts(db_pool)
    co = str(uuid.uuid4())
    khong = str(uuid.uuid4())
    await _mo(repo, co)

    snap = await repo.snapshot_for([co, khong])

    assert snap[co].receipt_exists is True
    assert snap[co].materialization_status == "PENDING"
    assert khong not in snap, "ID không có dòng lại xuất hiện trong kết quả"
    # Và cái "thiếu" phải mô tả được, không phải None trần.
    thieu = ReceiptSnapshot.missing()
    assert thieu.receipt_exists is False
    assert thieu.materialization_status is None


@pytest.mark.asyncio
async def test_the_snapshot_carries_the_error_category(db_pool):
    """Mapper cần `safe_error_code` để phân biệt "thử lại được" với "nghiệp vụ chặn"."""
    repo = VerificationReceipts(db_pool)
    rid = str(uuid.uuid4())
    await _mo(repo, rid)
    await repo.finish(rid, "FAILED", "BUSINESS_REFUSED")

    snap = (await repo.snapshot_for([rid]))[rid]

    assert snap.materialization_status == "FAILED"
    assert snap.safe_error_code == "BUSINESS_REFUSED"


@pytest.mark.asyncio
async def test_ten_records_take_exactly_one_query(db_pool):
    """Đếm query bằng HÀNH VI: đếm số lần pool bị xin kết nối."""
    repo = VerificationReceipts(db_pool)
    ids = [str(uuid.uuid4()) for _ in range(10)]
    for rid in ids:
        await _mo(repo, rid)

    dem = _PoolDem(db_pool)
    snap = await VerificationReceipts(dem).snapshot_for(ids)

    assert len(snap) == 10
    # Đếm acquire MỘT MÌNH không đủ: một vòng lặp `fetch()` trên cùng một
    # connection vẫn là N+1, và nó chỉ acquire một lần.
    assert dem.acquire_count == 1, f"{len(ids)} hồ sơ mất {dem.acquire_count} lượt kết nối"
    assert dem.fetch_count == 1, f"{len(ids)} hồ sơ mất {dem.fetch_count} câu truy vấn"
    assert (dem.fetchrow_count, dem.fetchval_count, dem.execute_count) == (0, 0, 0), (
        f"còn truy vấn khác: fetchrow={dem.fetchrow_count} fetchval={dem.fetchval_count} execute={dem.execute_count}"
    )


@pytest.mark.asyncio
async def test_duplicate_ids_do_not_corrupt_the_mapping(db_pool):
    repo = VerificationReceipts(db_pool)
    rid = str(uuid.uuid4())
    await _mo(repo, rid)
    await repo.finish(rid, "SUCCESS", None)

    snap = await repo.snapshot_for([rid, rid, rid])

    assert len(snap) == 1
    assert snap[rid].materialization_status == "SUCCESS"


@pytest.mark.asyncio
async def test_a_database_outage_becomes_the_domain_error(db_pool):
    """Không fallback, không danh sách rỗng giả — lỗi phải nổi lên."""

    class _PoolHong:
        def acquire(self):
            raise ConnectionError("mất kết nối tới postgresql://p118:matkhau@h/db")

    with pytest.raises(VerificationRecoveryUnavailableError) as loi:
        await VerificationReceipts(_PoolHong()).snapshot_for([str(uuid.uuid4())])

    assert "matkhau" not in str(loi.value)
    assert "postgresql://" not in str(loi.value)
    assert loi.value.__cause__ is None


@pytest.mark.asyncio
async def test_the_snapshot_never_carries_receipt_internals(db_pool):
    """Trường thừa ở đây là trường có thể vô tình đi tiếp ra response."""
    repo = VerificationReceipts(db_pool)
    rid = str(uuid.uuid4())
    await _mo(repo, rid)

    snap = (await repo.snapshot_for([rid]))[rid]

    truong = set(vars(snap))
    assert truong == {"receipt_exists", "materialization_status", "safe_error_code"}, truong


# --- helper canonical cho "không có biên lai" -------------------------------


def test_every_consumer_builds_the_missing_snapshot_the_same_way():
    """Ba endpoint không được tự dựng ba bản "missing" khác nhau.

    Bản lệch sẽ là bản coi một hồ sơ APPROVED không biên lai như đang xử lý
    bình thường — đúng ô nguy hiểm nhất của ma trận.
    """
    thieu = snapshot_or_missing({}, "khong-co")
    assert thieu.receipt_exists is False
    assert thieu.materialization_status is None
    assert thieu.safe_error_code is None
    assert thieu == ReceiptSnapshot.missing()


@pytest.mark.asyncio
async def test_the_helper_returns_the_real_snapshot_when_it_exists(db_pool):
    repo = VerificationReceipts(db_pool)
    rid = str(uuid.uuid4())
    await _mo(repo, rid)
    snapshots = await repo.snapshot_for([rid])

    co = snapshot_or_missing(snapshots, rid)

    assert co.receipt_exists is True
    assert co.materialization_status == "PENDING"


@pytest.mark.asyncio
async def test_the_compatibility_alias_never_invents_a_status(db_pool):
    """`statuses_for` bỏ QUA id thiếu, không ánh xạ chúng tới một giá trị bịa."""
    repo = VerificationReceipts(db_pool)
    co, khong = str(uuid.uuid4()), str(uuid.uuid4())
    await _mo(repo, co)

    ket_qua = await repo.statuses_for([co, khong])

    assert set(ket_qua) == {co}
    assert khong not in ket_qua
    assert "NOT_STARTED" not in str(ket_qua)
