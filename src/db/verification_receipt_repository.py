"""Biên lai materialization — SQL sống ở đây, không rải trong route.

Xem `src/orchestration/verification_recovery.py` cho lý do bảng này tồn tại.
Ở đây chỉ có một quy tắc riêng đáng nói: mọi hàm đều idempotent và an toàn khi
hai request chạy song song, vì đường gọi vào nó CHÍNH LÀ đường retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def snapshot_or_missing(snapshots: dict[str, ReceiptSnapshot], record_id: str) -> ReceiptSnapshot:
    """Tra một hồ sơ trong kết quả batch, thiếu thì trả bản "không có".

    Tồn tại để ba endpoint không tự dựng ba bản "missing" khác nhau. Ba bản như
    thế sẽ lệch — và bản lệch sẽ là bản coi một hồ sơ APPROVED không biên lai
    như đang xử lý bình thường.
    """
    return snapshots.get(str(record_id)) or ReceiptSnapshot.missing()


@dataclass(frozen=True)
class ReceiptSnapshot:
    """Những gì mapper cần biết về MỘT biên lai. Không hơn.

    Không mang `idempotency_key`, `attempt_count`, `record_type` hay timestamp:
    chúng là chuyện nội bộ của recovery, và mỗi trường thừa ở đây là một trường
    có thể vô tình đi tiếp ra response.
    """

    receipt_exists: bool
    materialization_status: str | None
    safe_error_code: str | None

    @staticmethod
    def missing() -> ReceiptSnapshot:
        """Không có dòng nào trong bảng.

        `NOT_STARTED` KHÔNG phải trạng thái persisted — schema chỉ cho
        `NOT_REQUIRED | PENDING | SUCCESS | FAILED`, và biên lai mới mặc định
        `PENDING`. "Chưa bắt đầu" là một giá trị CÔNG KHAI do mapper suy ra, và
        chỉ đúng khi đơn vị cũng chưa quyết định.

        Đây là dữ kiện NỘI BỘ: "không có dòng". Việc nó nghĩa là gì với người
        đọc — `NOT_STARTED` khi provider còn PENDING, `UNKNOWN` + đối soát khi
        provider đã APPROVED — là việc của mapper, không phải của lớp này.
        """
        return ReceiptSnapshot(receipt_exists=False, materialization_status=None, safe_error_code=None)


class VerificationRecoveryUnavailableError(RuntimeError):
    """Không ghi/đọc được biên lai vì HẠ TẦNG. Khác hẳn "biên lai không còn ở đó".

    Tồn tại để route KHÔNG phải bắt `asyncpg.PostgresError` hay `ConnectionError`.
    Một route bắt exception của thư viện là một route buộc phải đổi khi thư viện
    đổi, và cách sửa nhanh nhất lúc đó luôn là `except Exception` — rồi nó nuốt
    cả những lỗi không được nuốt.

    Message CỐ ĐỊNH. Lỗi hạ tầng hay mang theo DSN, SQL và payload; đây là chỗ
    chúng dừng lại.
    """

    def __init__(self) -> None:
        super().__init__("Không ghi nhận được tiến trình xác minh.")


# Lỗi HẠ TẦNG — và chỉ những lỗi này.
#
# Bản trước bọc cả lớp bằng `except Exception`, nên `TypeError`,
# `AttributeError`, `KeyError` — tức bug trong chính code này — đều biến thành
# 503 "hệ thống đang bận". Một defect lập trình được trình bày như sự cố hạ
# tầng là defect không bao giờ được sửa: nó trông giống thứ tự khỏi.
_LOI_HA_TANG: tuple[type[BaseException], ...] = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    ConnectionError,
    TimeoutError,
)


def _translate(exc: BaseException) -> BaseException:
    """Lỗi hạ tầng → lỗi domain. Mọi thứ khác giữ NGUYÊN và nổi lên.

    Không `raise ... from exc`: chuỗi `__cause__` đi theo exception tới handler
    và tới log, còn nguyên nhân gốc là thứ mang DSN.
    """
    if isinstance(exc, ReceiptMissingError | VerificationRecoveryUnavailableError):
        return exc
    if isinstance(exc, _LOI_HA_TANG):
        return VerificationRecoveryUnavailableError()
    return exc


class ReceiptMissingError(RuntimeError):
    """`UPDATE` khớp 0 dòng — biên lai không còn ở đó.

    Một `UPDATE ... WHERE record_id=...` khớp 0 dòng KHÔNG báo lỗi ở
    PostgreSQL; nó thành công và đổi số không. Nghĩa là mọi hàm dưới đây có thể
    "ghi xong" một biên lai đã bị xoá, rồi route trả 200 cho một trạng thái
    chưa từng được lưu.

    Message không mang SQL, không mang payload, không mang DSN — chỉ `record_id`.
    """

    def __init__(self, record_id: Any) -> None:
        super().__init__(f"Không tìm thấy biên lai cho hồ sơ {record_id}.")


def _require_one(tag: str, record_id: Any) -> None:
    """Command tag của asyncpg là `UPDATE <n>`. `n == 0` là một lời nói dối im lặng."""
    if not tag.endswith(" 1"):
        raise ReceiptMissingError(record_id)


class VerificationReceipts:
    """Sở hữu bảng `verification_materializations`. Không giữ trạng thái RAM.

    Mọi method public đều chuyển lỗi HẠ TẦNG thành
    `VerificationRecoveryUnavailableError` — xem `__getattribute__` ở cuối lớp.
    `ReceiptMissingError` đi qua nguyên vẹn: nó là một dữ kiện, không phải sự cố.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ---- ranh giới I/O DUY NHẤT -------------------------------------------
    #
    # Mọi câu SQL của lớp này đi qua đây. Một chỗ để dịch lỗi hạ tầng, và một
    # chỗ để test kiểm — thay vì một wrapper bọc cả lớp, thứ tự động coi mọi
    # method (kể cả method logic thêm sau) là I/O và che luôn bug trong chúng.

    async def _execute(self, query: str, *args) -> str:
        try:
            async with self._pool.acquire() as conn:
                return await conn.execute(query, *args)
        except BaseException as exc:
            raise _translate(exc) from None

    async def _fetchrow(self, query: str, *args):
        try:
            async with self._pool.acquire() as conn:
                return await conn.fetchrow(query, *args)
        except BaseException as exc:
            raise _translate(exc) from None

    async def _fetch(self, query: str, *args):
        try:
            async with self._pool.acquire() as conn:
                return await conn.fetch(query, *args)
        except BaseException as exc:
            raise _translate(exc) from None

    async def _fetchval(self, query: str, *args):
        try:
            async with self._pool.acquire() as conn:
                return await conn.fetchval(query, *args)
        except BaseException as exc:
            raise _translate(exc) from None

    async def open_receipt(
        self,
        *,
        record_id: str,
        record_type: str | None,
        requested_decision: str,
        idempotency_key: str,
    ) -> str | None:
        """Mở biên lai, hoặc đếm thêm một lần thử. Trả `requested_decision` ĐÃ CÓ.

        `ON CONFLICT DO UPDATE` chứ không `DO NOTHING`: hai request đồng thời
        cho cùng hồ sơ phải cùng rơi vào MỘT dòng, và dòng ấy phải biết nó đã
        được thử mấy lần. `DO NOTHING` khiến request thứ hai không biết mình là
        người thứ hai.

        `requested_decision` KHÔNG bị ghi đè: nó là ý định của lượt ĐẦU — giữ
        nguyên là để biên lai kể đúng chuyện đã xảy ra.
        """
        await self._execute(
            """
            INSERT INTO verification_materializations
                (record_id, record_type, requested_decision, idempotency_key, attempt_count)
            VALUES ($1, $2, $3, $4, 1)
            ON CONFLICT (record_id) DO UPDATE
               SET attempt_count = verification_materializations.attempt_count + 1,
                   updated_at    = NOW()
            """,
            _uuid(record_id),
            record_type if record_type in ("apartment", "vehicle") else None,
            requested_decision,
            idempotency_key,
        )
        return await self._fetchval(
            "SELECT requested_decision FROM verification_materializations WHERE record_id=$1",
            _uuid(record_id),
        )

    async def set_record_type(self, record_id: str, record_type: str) -> None:
        if record_type not in ("apartment", "vehicle"):
            return
        tag = await self._execute(
            "UPDATE verification_materializations SET record_type=$2, updated_at=NOW() WHERE record_id=$1",
            _uuid(record_id),
            record_type,
        )
        _require_one(tag, record_id)

    async def set_provider_status(self, record_id: str, status: str) -> None:
        tag = await self._execute(
            "UPDATE verification_materializations SET provider_decision_status=$2, updated_at=NOW() WHERE record_id=$1",
            _uuid(record_id),
            status,
        )
        _require_one(tag, record_id)

    async def start_materialization(self, record_id: str) -> None:
        """Đánh dấu "đang làm" TRƯỚC khi làm.

        Không `_require_one`: 0 dòng ở đây cũng có nghĩa "đã SUCCESS rồi", một
        trạng thái hợp lệ. Phân biệt bằng một lượt đọc.
        """
        await self._execute(
            "UPDATE verification_materializations "
            "SET materialization_status='PENDING', safe_error_code=NULL, updated_at=NOW() "
            "WHERE record_id=$1 AND materialization_status <> 'SUCCESS'",
            _uuid(record_id),
        )
        if not await self._fetchval("SELECT 1 FROM verification_materializations WHERE record_id=$1", _uuid(record_id)):
            raise ReceiptMissingError(record_id)

    async def finish(self, record_id: str, status: str, safe_error_code: str | None) -> None:
        """Chốt kết quả. `SUCCESS` là trạng thái CUỐI — không lùi lại được.

        `WHERE materialization_status <> 'SUCCESS'` cho các trạng thái khác:
        một lượt retry chậm chân không được hạ một biên lai đã thành công xuống
        FAILED. Đó là cách một người đã có quyền bỗng bị báo là chưa.
        """
        if status == "SUCCESS":
            tag = await self._execute(
                "UPDATE verification_materializations "
                "SET materialization_status='SUCCESS', safe_error_code=NULL, updated_at=NOW() "
                "WHERE record_id=$1",
                _uuid(record_id),
            )
            _require_one(tag, record_id)
            return
        await self._execute(
            "UPDATE verification_materializations "
            "SET materialization_status=$2, safe_error_code=$3, updated_at=NOW() "
            "WHERE record_id=$1 AND materialization_status <> 'SUCCESS'",
            _uuid(record_id),
            status,
            safe_error_code,
        )

    async def get(self, record_id: str) -> dict[str, Any] | None:
        row = await self._fetchrow("SELECT * FROM verification_materializations WHERE record_id=$1", _uuid(record_id))
        return dict(row) if row else None

    async def snapshot_for(self, record_ids: list[str]) -> dict[str, ReceiptSnapshot]:
        """`record_id → ReceiptSnapshot`, tra ĐÚNG MỘT lượt cho cả danh sách.

        Trả `ReceiptSnapshot` chứ không phải chuỗi trạng thái: mapper cần cả
        `safe_error_code` để phân biệt "hỏng vì hạ tầng, thử lại được" với
        "nghiệp vụ chặn, thử lại vô nghĩa". Trả mỗi trạng thái thì route phải đi
        hỏi lần hai — và lần hai ấy sẽ là một query cho mỗi dòng.

        `receipt_exists` là trường RIÊNG, không suy từ `materialization_status`:
        "không có biên lai" (dữ liệu cũ, hoặc chết trước dòng đầu tiên) khác hẳn
        "có biên lai" ở bất kỳ trạng thái nào. Gộp hai thứ ấy là mất đúng tín hiệu
        nhận diện hồ sơ APPROVED cần đối soát.

        `record_ids` rỗng → KHÔNG mở kết nối. ID trùng lặp không làm sai mapping
        vì kết quả khoá theo `record_id`.
        """
        if not record_ids:
            return {}
        rows = await self._fetch(
            "SELECT record_id, materialization_status, safe_error_code "
            "FROM verification_materializations WHERE record_id = ANY($1::uuid[])",
            [_uuid(r) for r in record_ids],
        )
        return {
            str(r["record_id"]): ReceiptSnapshot(
                receipt_exists=True,
                materialization_status=r["materialization_status"],
                safe_error_code=r["safe_error_code"],
            )
            for r in rows
        }

    async def statuses_for(self, record_ids: list[str]) -> dict[str, str | None]:
        """Tương thích ngược: chỉ trạng thái. Dùng `snapshot_for` cho code mới.

        Hai điều caller phải biết, và cả hai đều không nhìn thấy từ tên hàm:

          * ID **không có biên lai** KHÔNG xuất hiện trong kết quả — không phải
            ánh xạ tới một giá trị mặc định nào;
          * giá trị có thể là `None` nếu cột rỗng. Không ép nó thành chuỗi giả:
            một `"NOT_STARTED"` bịa ra ở đây sẽ được caller đọc như dữ kiện.
        """
        return {rid: snap.materialization_status for rid, snap in (await self.snapshot_for(record_ids)).items()}
