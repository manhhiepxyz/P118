"""Lỗi hạ tầng và bug lập trình KHÔNG được trộn làm một.

Bản đầu của lớp biên lai bọc cả class bằng `except Exception`, nên `TypeError`,
`AttributeError`, `KeyError` — tức bug trong chính code này — đều biến thành
503 "hệ thống đang bận".

Một defect lập trình được trình bày như sự cố hạ tầng là defect **không bao giờ
được sửa**: nó trông giống thứ tự khỏi. Người dùng thử lại, lỗi vẫn còn, và
không có gì trong log nói rằng có ai đó cần đọc lại một dòng code.
"""

from __future__ import annotations

import asyncpg
import pytest

from src.db.verification_receipt_repository import (
    ReceiptMissingError,
    VerificationReceipts,
    VerificationRecoveryUnavailableError,
)


class _PoolHong:
    """Pool ném đúng một lỗi cho trước ngay khi ai đó xin kết nối."""

    def __init__(self, exc):
        self._exc = exc

    def acquire(self):
        raise self._exc


def _repo(exc):
    return VerificationReceipts(_PoolHong(exc))


_HA_TANG = [
    ("postgres", asyncpg.PostgresError("connection to postgresql://p118:matkhau@h/db lost")),
    ("interface", asyncpg.InterfaceError("pool đã đóng")),
    ("connection", ConnectionError("mất kết nối")),
    ("timeout", TimeoutError("quá hạn")),
]


@pytest.mark.parametrize("ten,exc", _HA_TANG, ids=[c[0] for c in _HA_TANG])
@pytest.mark.asyncio
async def test_infrastructure_failures_become_a_stable_domain_error(ten, exc):
    with pytest.raises(VerificationRecoveryUnavailableError) as loi:
        await _repo(exc).get("00000000-0000-0000-0000-000000000001")

    # Message CỐ ĐỊNH, không mang theo nguyên nhân gốc.
    assert "matkhau" not in str(loi.value)
    assert "postgresql://" not in str(loi.value)
    # `__cause__` bị cắt: chuỗi nguyên nhân đi theo exception tới handler và
    # tới log, và nguyên nhân gốc là thứ mang DSN.
    assert loi.value.__cause__ is None


_KHONG_PHAI_HA_TANG = [
    ("type", TypeError("gọi sai tham số")),
    ("value", ValueError("giá trị lạ")),
    ("attribute", AttributeError("thiếu thuộc tính")),
    ("assertion", AssertionError("bất biến nội bộ sai")),
    ("key", KeyError("thiếu khoá")),
]


@pytest.mark.parametrize("ten,exc", _KHONG_PHAI_HA_TANG, ids=[c[0] for c in _KHONG_PHAI_HA_TANG])
@pytest.mark.asyncio
async def test_programming_defects_are_never_disguised_as_an_outage(ten, exc):
    """Bug code phải nổi lên NGUYÊN VẸN, không thành 503 giả."""
    with pytest.raises(type(exc)):
        await _repo(exc).get("00000000-0000-0000-0000-000000000001")


@pytest.mark.asyncio
async def test_a_missing_receipt_stays_a_missing_receipt():
    """`ReceiptMissingError` là DỮ KIỆN của recovery, không phải sự cố.

    Đổi nó thành "hạ tầng hỏng" là xoá mất tín hiệu duy nhất cho nhánh dựng lại
    biên lai.
    """
    with pytest.raises(ReceiptMissingError):
        await _repo(ReceiptMissingError("abc")).get("00000000-0000-0000-0000-000000000001")


@pytest.mark.asyncio
async def test_the_domain_error_passes_through_unchanged():
    goc = VerificationRecoveryUnavailableError()
    with pytest.raises(VerificationRecoveryUnavailableError) as loi:
        await _repo(goc).get("00000000-0000-0000-0000-000000000001")
    assert loi.value is goc


@pytest.mark.asyncio
async def test_every_sql_method_goes_through_the_boundary(db_pool):
    """Method mới KHÔNG được tự ý mở kết nối riêng.

    Kiểm HÀNH VI, không đọc source: cho pool ném lỗi hạ tầng rồi gọi từng
    method public — cái nào không đi qua `_execute/_fetch*` sẽ để lỗi gốc lọt
    ra thay vì lỗi domain.
    """
    rid = "00000000-0000-0000-0000-000000000002"
    repo = _repo(ConnectionError("mất kết nối"))
    goi = [
        repo.open_receipt(record_id=rid, record_type=None, requested_decision="approve", idempotency_key="k"),
        repo.set_record_type(rid, "apartment"),
        repo.set_provider_status(rid, "APPROVED"),
        repo.start_materialization(rid),
        repo.finish(rid, "SUCCESS", None),
        repo.get(rid),
        repo.statuses_for([rid]),
    ]
    for coro in goi:
        with pytest.raises(VerificationRecoveryUnavailableError):
            await coro
