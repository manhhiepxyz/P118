"""Đọc/ghi báo giá. Mọi luật hợp lệ nằm ở `src.orchestration.quote`, không ở đây.

Tách vì một lý do đo được: luật hợp lệ của báo giá phải kiểm được KHÔNG cần
database (nhanh, và không có trạng thái để dựng sai), còn tính bền vững thì chỉ
kiểm được VỚI PostgreSQL thật. Trộn hai thứ vào một file nghĩa là mọi bài kiểm
luật đều phải dựng một pool.

Hàm ở đây nhận `pool` chứ không giữ nó — cùng khuôn với `service_approval.py`.
Composition root sở hữu vòng đời pool; một module tự mở pool riêng là một
nguồn kết nối thứ hai không ai đếm.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import asyncpg

from src.orchestration.quote import CURRENCY_CHO_PHEP, BaoGia

_COT = (
    "quote_id, external_quote_id, service_provider_id, service_type, amount, currency, "
    "request_fingerprint, valid_until, status, created_at, confirmed_at, workflow_id, task_id"
)


def _to_bao_gia(row: asyncpg.Record | None) -> BaoGia | None:
    if row is None:
        return None
    return BaoGia(
        quote_id=str(row["quote_id"]),
        external_quote_id=row["external_quote_id"],
        service_provider_id=row["service_provider_id"],
        service_type=row["service_type"],
        amount=int(row["amount"]),
        currency=row["currency"],
        request_fingerprint=row["request_fingerprint"],
        valid_until=row["valid_until"],
        status=row["status"],
        created_at=row["created_at"],
        confirmed_at=row["confirmed_at"],
        workflow_id=str(row["workflow_id"]) if row["workflow_id"] else None,
        task_id=row["task_id"],
    )


async def luu_bao_gia(
    pool: asyncpg.Pool,
    *,
    external_quote_id: str,
    service_provider_id: str,
    service_type: str,
    amount: int,
    currency: str,
    request_fingerprint: str,
    valid_until: datetime,
    workflow_id: str | None = None,
    task_id: str | None = None,
) -> BaoGia:
    """Ghim MỘT báo giá. Trả về bản đã persist, không phải bản vừa truyền vào.

    Trả bản đã persist là cố ý: `quote_id` và `created_at` do đây sinh ra, và
    caller dùng bản trả về thì không có đường nào để hai bên hiểu khác nhau về
    cùng một chứng từ.

    Kiểm `amount`/`currency` ở đây NGOÀI ràng buộc database: `CHECK` cho một
    thông điệp của PostgreSQL, còn cái ném ở đây nói được rằng lỗi đến từ dữ
    liệu provider trả về. Hai tầng cùng một luật là đúng — tầng dưới để không
    bao giờ có dòng sai, tầng trên để biết vì sao.
    """
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ValueError(f"amount phải là số nguyên dương, nhận {amount!r}")
    if currency not in CURRENCY_CHO_PHEP:
        raise ValueError(f"currency ngoài danh sách cho phép: {currency!r}")

    quote_id = uuid4()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO service_quotes
                (quote_id, external_quote_id, service_provider_id, service_type, amount, currency,
                 request_fingerprint, valid_until, status, workflow_id, task_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'ACTIVE', $9, $10)
            RETURNING {_COT}
            """,  # noqa: S608 - `_COT` là literal nội bộ, mọi giá trị đều là tham số
            quote_id,
            external_quote_id,
            service_provider_id,
            service_type,
            amount,
            currency,
            request_fingerprint,
            valid_until,
            UUID(workflow_id) if workflow_id else None,
            task_id,
        )
    bao_gia = _to_bao_gia(row)
    assert bao_gia is not None  # noqa: S101 - INSERT ... RETURNING luôn trả một dòng
    return bao_gia


async def doc_bao_gia(pool: asyncpg.Pool, quote_id: str) -> BaoGia | None:
    """Đọc theo `quote_id`. `None` khi không có — KHÔNG ném.

    Không tìm thấy là một câu trả lời hợp lệ ở tầng đọc; biến nó thành lỗi
    nghiệp vụ là việc của `kiem_bao_gia`, nơi biết caller đang định làm gì.
    """
    try:
        khoa = UUID(quote_id)
    except (ValueError, AttributeError, TypeError):
        # Một `quote_id` không phải UUID là một mã bịa. Trả `None` chứ không để
        # asyncpg ném: đường gọi đã có nhánh "không tìm thấy", và một mã sai
        # hình dạng không đáng có một nhánh xử lý thứ hai.
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COT} FROM service_quotes WHERE quote_id = $1",  # noqa: S608
            khoa,
        )
    return _to_bao_gia(row)


async def bao_gia_dang_song(
    pool: asyncpg.Pool, *, workflow_id: str, task_id: str, request_fingerprint: str | None = None
) -> list[BaoGia]:
    """Các báo giá còn ACTIVE của một bước, rẻ nhất trước.

    Sắp xếp theo giá ngay ở SQL: mọi đường hiển thị và mọi đường chọn đều muốn
    thứ tự ấy, và để mỗi call site tự sắp là để chúng sắp khác nhau.

    KHÔNG lọc hết hạn ở đây. `valid_until` là dữ kiện, `het_han` là luật — và
    luật sống ở `quote.py`. Lọc ở SQL nghĩa là có hai nơi định nghĩa "còn hiệu
    lực", và chúng lệch nhau đúng vào lúc đồng hồ database khác đồng hồ ứng dụng.
    """
    dieu_kien = ""
    tham_so: list[object] = [UUID(workflow_id), task_id]
    if request_fingerprint is not None:
        dieu_kien = " AND request_fingerprint = $3"
        tham_so.append(request_fingerprint)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_COT} FROM service_quotes
             WHERE workflow_id = $1 AND task_id = $2 AND status = 'ACTIVE'{dieu_kien}
             ORDER BY amount ASC, service_provider_id ASC
            """,  # noqa: S608
            *tham_so,
        )
    ket_qua = [_to_bao_gia(r) for r in rows]
    return [q for q in ket_qua if q is not None]


async def xac_nhan_bao_gia(pool: asyncpg.Pool, quote_id: str) -> BaoGia | None:
    """ACTIVE → CONFIRMED, và chỉ một lượt thắng.

    `WHERE status = 'ACTIVE'` làm việc chuyển trạng thái trở thành một phép
    so-sánh-rồi-đổi nguyên tử ở database. Đọc-rồi-ghi ở tầng ứng dụng thì hai
    lượt bấm đồng thời đều đọc thấy ACTIVE và đều ghi CONFIRMED — hai lần xác
    nhận cho một chứng từ, và bên cung cấp nhận hai đơn.

    Trả `None` khi không đổi được: caller phân biệt được "đã xác nhận rồi" với
    "vừa xác nhận xong", và đó là khác biệt giữa 409 và 200.
    """
    try:
        khoa = UUID(quote_id)
    except (ValueError, AttributeError, TypeError):
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE service_quotes
               SET status = 'CONFIRMED', confirmed_at = NOW()
             WHERE quote_id = $1 AND status = 'ACTIVE'
            RETURNING {_COT}
            """,  # noqa: S608
            khoa,
        )
    return _to_bao_gia(row)


async def thay_the_bao_gia_cu(pool: asyncpg.Pool, *, workflow_id: str, task_id: str, van_tay_moi: str) -> int:
    """Yêu cầu đã đổi → mọi báo giá ACTIVE của vân tay CŨ thành SUPERSEDED.

    SUPERSEDED chứ không xoá, và không phải EXPIRED: ba trạng thái ấy nói ba
    điều khác nhau. Hết hạn là thời gian trôi qua; bị thay thế là khách đổi ý.
    Gộp chúng thì lúc có sự cố không phân biệt được "đơn vị báo giá quá ngắn"
    với "khách đổi ngày ba lần".

    Chỉ đụng ACTIVE: một báo giá đã CONFIRMED là một cam kết đã xảy ra, và viết
    đè lên nó là xoá dấu vết của một việc có thật.
    """
    async with pool.acquire() as conn:
        ket_qua = await conn.execute(
            """
            UPDATE service_quotes SET status = 'SUPERSEDED'
             WHERE workflow_id = $1 AND task_id = $2
               AND status = 'ACTIVE' AND request_fingerprint <> $3
            """,
            UUID(workflow_id),
            task_id,
            van_tay_moi,
        )
    return int(str(ket_qua).rsplit(" ", 1)[-1])
