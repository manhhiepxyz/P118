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


class KhongNeoDuocError(ValueError):
    """Chứng từ không neo được vào bước tiêu thụ.

    Kiểu RIÊNG chứ không phải `ValueError` chung, vì đây là lỗi CỦA P-118 —
    kế hoạch trỏ vào một bước không tồn tại, hoặc vào một bước làm việc khác.
    Gộp nó với "đơn vị trả dữ liệu sai" sẽ gửi người đọc log đi gọi cho đối tác
    về một lỗi nằm trong chính mã của mình.
    """


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
        workflow_id=str(row["workflow_id"]),
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
    workflow_id: str,
    task_id: str,
) -> BaoGia:
    """Ghim MỘT báo giá vào ĐÚNG bước tiêu thụ. Trả về bản đã persist.

    Trả bản đã persist là cố ý: `quote_id` và `created_at` do đây sinh ra, và
    caller dùng bản trả về thì không có đường nào để hai bên hiểu khác nhau về
    cùng một chứng từ.

    `workflow_id`/`task_id` KHÔNG có mặc định. Một tham số tuỳ chọn nghĩa là
    luật neo chỉ tồn tại với những call site nhớ tới nó — tức không tồn tại.

    Bước được neo phải có `tool` TRÙNG `service_type`. `INSERT ... SELECT` làm
    việc kiểm ấy nguyên tử với lượt ghi, và nó chặn đúng một ca mà khoá ngoại
    thôi không chặn được: neo báo giá chuyển nhà vào một bước TRA CỨU nhà cung
    cấp. Bước tra cứu không tiêu thụ gì, nên một chứng từ neo ở đó sẽ không bao
    giờ được đối chiếu — và cũng không bao giờ hết hạn theo cách ai đó nhìn thấy.

    Báo giá ĐÃ hết hạn không được ghi. Ghi nó vào chỉ để một bước quét sau đó
    chuyển sang EXPIRED là tạo rác kèm một khoảng thời gian nó trông như còn
    sống.

    Kiểm `amount`/`currency` ở đây NGOÀI ràng buộc database: `CHECK` cho một
    thông điệp của PostgreSQL, còn cái ném ở đây nói được rằng lỗi đến từ dữ
    liệu provider trả về. Hai tầng cùng một luật là đúng — tầng dưới để không
    bao giờ có dòng sai, tầng trên để biết vì sao.
    """
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ValueError(f"amount phải là số nguyên dương, nhận {amount!r}")
    if currency not in CURRENCY_CHO_PHEP:
        raise ValueError(f"currency ngoài danh sách cho phép: {currency!r}")
    if not workflow_id or not task_id:
        raise ValueError("báo giá phải neo vào một workflow và một bước cụ thể")

    quote_id = uuid4()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO service_quotes
                (quote_id, external_quote_id, service_provider_id, service_type, amount, currency,
                 request_fingerprint, valid_until, status, workflow_id, task_id)
            SELECT $1, $2, $3, $4::varchar, $5, $6, $7, $8::timestamptz, 'ACTIVE', t.workflow_id, t.task_id
              FROM workflow_tasks t
             WHERE t.workflow_id = $9 AND t.task_id = $10 AND t.tool = $4::varchar
               AND $8::timestamptz > NOW()
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
            UUID(workflow_id),
            task_id,
        )
    bao_gia = _to_bao_gia(row)
    if bao_gia is None:
        # Không dòng nào ghi được. Ba nguyên nhân, và cả ba đều là "chứng từ
        # này không có chỗ đứng": bước không tồn tại, bước có `tool` khác, hoặc
        # báo giá đã quá hạn ngay lúc tới nơi.
        raise KhongNeoDuocError(
            f"không neo được báo giá: bước {task_id!r} của {workflow_id!r} "
            f"không tồn tại, không phải {service_type!r}, hoặc báo giá đã hết hạn"
        )
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

    "Đang sống" gồm CẢ hạn, không chỉ trạng thái. Bản đầu cố ý không lọc hạn ở
    đây với lý do "luật sống ở `quote.py`" — nhưng rồi không tầng nào lọc, và
    một báo giá quá hạn vẫn đi thẳng vào đề xuất. Lý do ấy còn sai ở chỗ khác:
    ĐỒNG HỒ CỦA DATABASE là đồng hồ mà bước quét hết hạn và lệnh xác nhận đều
    dùng. Lọc ở đây bằng chính đồng hồ ấy làm ba chỗ nhất quán, chứ không phải
    tạo ra một định nghĩa thứ hai.
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
             WHERE workflow_id = $1 AND task_id = $2 AND status = 'ACTIVE'
               AND valid_until > NOW(){dieu_kien}
             ORDER BY amount ASC, service_provider_id ASC
            """,  # noqa: S608
            *tham_so,
        )
    ket_qua = [_to_bao_gia(r) for r in rows]
    return [q for q in ket_qua if q is not None]


async def xac_nhan_bao_gia(
    pool: asyncpg.Pool,
    quote_id: str,
    *,
    service_type: str,
    service_provider_id: str,
    request_fingerprint: str,
    amount: int,
    currency: str,
    workflow_id: str,
    task_id: str,
) -> BaoGia | None:
    """ACTIVE + còn hạn + khớp ĐỦ chín điều kiện → CONFIRMED. Một lượt thắng.

    MỘT lệnh, không phải "kiểm rồi ghi". Bản đầu chỉ có
    `WHERE quote_id = $1 AND status = 'ACTIVE'` và để `kiem_bao_gia()` canh
    phần còn lại ở tầng ứng dụng. Hai vấn đề, và cái thứ hai không sửa được
    bằng cách gọi cẩn thận hơn:

      1. Báo giá HẾT HẠN vẫn chuyển được sang CONFIRMED — hạn không có mặt
         trong mệnh đề nào.
      2. Giữa lúc `kiem_bao_gia()` nói "được" và lúc `UPDATE` chạy, báo giá có
         thể vừa hết hạn hoặc vừa bị một lượt sửa làm SUPERSEDED. Cửa sổ ấy
         nhỏ, nhưng nó mở đúng vào lúc hệ thống bận nhất.

    Nên mọi điều kiện đi vào cùng một mệnh đề `WHERE`, nơi PostgreSQL giữ khoá
    dòng. `kiem_bao_gia()` vẫn còn giá trị của nó: nó nói VÌ SAO không được, và
    nói trước khi ai đó nhìn thấy một lựa chọn không có thật. Nhưng nó không
    còn là thứ duy nhất đứng giữa một chứng từ hỏng và một cam kết.

    Trả `None` khi không đổi được: caller phân biệt được "đã xác nhận rồi" với
    "vừa xác nhận xong", và đó là khác biệt giữa 409 và 200.
    """
    try:
        khoa = UUID(quote_id)
        wid = UUID(workflow_id)
    except (ValueError, AttributeError, TypeError):
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE service_quotes
               SET status = 'CONFIRMED', confirmed_at = NOW()
             WHERE quote_id = $1
               AND status = 'ACTIVE'
               AND valid_until > NOW()
               AND service_type = $2
               AND service_provider_id = $3
               AND request_fingerprint = $4
               AND amount = $5
               AND currency = $6
               AND workflow_id = $7
               AND task_id = $8
            RETURNING {_COT}
            """,  # noqa: S608
            khoa,
            service_type,
            service_provider_id,
            request_fingerprint,
            amount,
            currency,
            wid,
            task_id,
        )
    return _to_bao_gia(row)


async def het_han_bao_gia_qua_han(pool: asyncpg.Pool, *, workflow_id: str, task_id: str) -> int:
    """ACTIVE + quá hạn → EXPIRED, nguyên tử. Chạy TRƯỚC mỗi lượt xin báo giá.

    Đây là nghĩa vụ đi kèm ràng buộc `UNIQUE ... WHERE status = 'ACTIVE'`. Thời
    gian trôi qua KHÔNG tự đổi `status`, nên một dòng quá hạn vẫn mang `ACTIVE`
    — và nó chặn vĩnh viễn mọi lượt hỏi lại của cùng đơn vị cho cùng yêu cầu.
    Ràng buộc dựng lên để ngăn báo giá trùng lại trở thành thứ khoá cứng người
    dùng ra khỏi việc xin giá mới.

    Không có bộ đếm giờ nền: quét ĐÚNG bước sắp được ghi, ngay trước khi ghi.
    Một job định kỳ sẽ đúng "phần lớn thời gian", và phần còn lại là đúng lúc
    người dùng đang chờ.
    """
    async with pool.acquire() as conn:
        ket_qua = await conn.execute(
            """
            UPDATE service_quotes SET status = 'EXPIRED'
             WHERE workflow_id = $1 AND task_id = $2
               AND status = 'ACTIVE' AND valid_until <= NOW()
            """,
            UUID(workflow_id),
            task_id,
        )
    return int(str(ket_qua).rsplit(" ", 1)[-1])


def _so_dong(command_tag: object) -> int:
    """Số dòng một lệnh vừa đụng tới, đọc từ command tag của PostgreSQL."""
    return int(str(command_tag).rsplit(" ", 1)[-1])


async def _dong_bo_de_xuat_theo_bao_gia(conn: asyncpg.Connection, *, workflow_id: UUID, task_id: str) -> int:
    """Đề xuất đang chờ đi theo số phận của CHỨNG TỪ nó trỏ vào.

    Chứng từ SUPERSEDED → đề xuất SUPERSEDED. Chứng từ EXPIRED → đề xuất
    EXPIRED. Chỉ đụng đề xuất còn `PROPOSED`: một cái đã CONFIRMED là quyết
    định đã xảy ra và đã sinh ra một dòng trong hàng đợi của đơn vị.

    Nhận `conn` chứ không nhận `pool`: hàm này KHÔNG được gọi ngoài một
    transaction. Chứng từ chết mà đề xuất còn sống là một trạng thái nửa
    chừng — màn hình vẫn mời khách bấm đồng ý cho một cái giá không còn tồn
    tại — và một khe nửa chừng thì sớm muộn sẽ có một lượt poll rơi vào.
    """
    ket_qua = await conn.execute(
        """
        UPDATE service_provider_proposals p
           SET status = q.status
          FROM service_quotes q
         WHERE p.quote_id = q.quote_id
           AND p.workflow_id = $1
           AND p.task_id = $2
           AND p.status = 'PROPOSED'
           AND q.status IN ('SUPERSEDED', 'EXPIRED')
        """,
        workflow_id,
        task_id,
    )
    return _so_dong(ket_qua)


async def don_bao_gia_va_de_xuat(
    pool: asyncpg.Pool, *, workflow_id: str, task_id: str, van_tay_moi: str | None = None
) -> dict[str, int]:
    """MỘT transaction dọn cả chứng từ lẫn đề xuất của một bước.

    Đây là đường dọn CANONICAL, và là đường duy nhất. Trước đó có hai hàm rời
    nhau — thay thế theo vân tay, và quét hết hạn — mỗi hàm một transaction, và
    đề xuất thì không hàm nào đụng tới. Ba lượt ghi rời nhau nghĩa là có hai
    khe mà chứng từ đã chết trong khi đề xuất vẫn `PROPOSED`, và trong khe ấy
    màn hình còn nguyên nút "đồng ý" cho một cái giá không còn tồn tại.

    Ba việc, một `BEGIN`:

      1. vân tay đổi  → chứng từ đời cũ `SUPERSEDED`
      2. quá hạn      → chứng từ `EXPIRED`
      3. đề xuất đang chờ đi theo chứng từ của nó

    `van_tay_moi=None` nghĩa là chỉ dọn hạn — dùng khi yêu cầu không đổi.

    Khoá `workflows` trước, cùng thứ tự với `xac_nhan_de_xuat` và với mọi người
    ghi hàng đợi duyệt. Khác thứ tự là công thức của một deadlock hiện ra như
    "một test khác nhau mỗi lượt".
    """
    khoa_wf = UUID(workflow_id)
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchrow("SELECT workflow_id FROM workflows WHERE workflow_id = $1 FOR UPDATE", khoa_wf)

        da_thay_the = 0
        if van_tay_moi is not None:
            ket_qua = await conn.execute(
                """
                UPDATE service_quotes SET status = 'SUPERSEDED'
                 WHERE workflow_id = $1 AND task_id = $2
                   AND status = 'ACTIVE' AND request_fingerprint <> $3
                """,
                khoa_wf,
                task_id,
                van_tay_moi,
            )
            da_thay_the = _so_dong(ket_qua)

        het_han = _so_dong(
            await conn.execute(
                """
                UPDATE service_quotes SET status = 'EXPIRED'
                 WHERE workflow_id = $1 AND task_id = $2
                   AND status = 'ACTIVE' AND valid_until <= NOW()
                """,
                khoa_wf,
                task_id,
            )
        )
        de_xuat_theo = await _dong_bo_de_xuat_theo_bao_gia(conn, workflow_id=khoa_wf, task_id=task_id)

    return {"thay_the": da_thay_the, "het_han": het_han, "de_xuat": de_xuat_theo}
