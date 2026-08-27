"""Đề xuất đơn vị: ghim, đọc lại, và lượt xác nhận — MỘT transaction.

Lượt xác nhận là chỗ ba bảng phải đổi cùng nhau: chứng từ thành CONFIRMED, đề
xuất thành CONFIRMED, và một dòng vào hàng đợi của đơn vị. Ba lệnh rời nhau
nghĩa là có ba lúc hệ thống ở giữa chừng — và lúc giữa chừng tệ nhất là chứng
từ đã chốt, tiền đã có chủ, mà không ai bên kia nhận được việc.

Nên tất cả nằm trong một `BEGIN`, với khoá dòng lấy theo ĐÚNG thứ tự mà phần
còn lại của hệ thống đang dùng: `workflows` trước, rồi các bảng con
(`service_approval._lock_workflow_row` ghi rõ vì sao). Khác thứ tự là công thức
của một deadlock hiện ra như "một test khác nhau mỗi lượt".

Mọi điều kiện được kiểm TRONG transaction, sau khi đã cầm khoá. Kiểm trước rồi
mới `BEGIN` là kiểm trên một ảnh chụp đã cũ: giữa hai bước ấy chứng từ có thể
vừa hết hạn, đề xuất có thể vừa bị thay thế, và cửa sổ đó mở đúng lúc hệ thống
bận nhất.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID, uuid4

import asyncpg

from src.orchestration.proposal import DeXuat, KetQuaXacNhan, KetQuaXacNhanDeXuat
from src.orchestration.service_approval import SERVICE_LABELS, chi_tiet_cho_don_vi

logger = logging.getLogger(__name__)

_COT = "proposal_id, workflow_id, task_id, quote_id, status, created_at, confirmed_at"


class DeXuatBatNhatQuanError(RuntimeError):
    """Một bất biến vỡ SAU khi transaction đã bắt đầu đổi dữ liệu.

    NÉM chứ không trả về một kết quả nghiệp vụ. Đây là khác biệt quan trọng
    nhất của lượt xác nhận: một `return` sau khi chứng từ đã thành `CONFIRMED`
    sẽ COMMIT trạng thái nửa chừng — chứng từ đã chốt, tiền đã có chủ, và không
    ai bên kia nhận được việc. Ném thì `conn.transaction()` rollback tất cả, và
    hệ thống quay về đúng chỗ nó đứng trước lượt bấm.

    Đây luôn là lỗi của P-118, không phải tình huống của khách — nên nó không
    có mã HTTP riêng, và nó phải đi lên tới log.
    """


class KhongGhimDuocDeXuatError(ValueError):
    """Không ghim được đề xuất: bước không tồn tại, hoặc chứng từ không dùng được.

    Kiểu riêng vì đây là lỗi CỦA P-118 — luồng vừa đề xuất một chứng từ không
    còn hợp lệ, hoặc neo vào một bước không có. Gộp với lỗi dữ liệu của đối tác
    sẽ gửi người đọc log đi sai hướng.
    """


def _to_de_xuat(row: asyncpg.Record | None) -> DeXuat | None:
    if row is None:
        return None
    return DeXuat(
        proposal_id=str(row["proposal_id"]),
        workflow_id=str(row["workflow_id"]),
        task_id=row["task_id"],
        quote_id=str(row["quote_id"]),
        status=row["status"],
        created_at=row["created_at"],
        confirmed_at=row["confirmed_at"],
    )


def _uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _so_dong(command_tag: object) -> int:
    """Số dòng một lệnh vừa đụng tới, đọc từ command tag của PostgreSQL."""
    return int(str(command_tag).rsplit(" ", 1)[-1])


async def ghim_de_xuat(pool: asyncpg.Pool, *, workflow_id: str, task_id: str, quote_id: str) -> DeXuat:
    """Ghim MỘT đề xuất đang chờ khách. Đề xuất cũ của bước này thành SUPERSEDED.

    Thay thế TRƯỚC khi chèn, trong cùng transaction. Ràng buộc "đúng một
    PROPOSED mỗi bước" ở database sẽ chặn dòng thứ hai, nhưng chặn không phải
    là xử lý: một lượt đề xuất mới hợp lệ (khách đổi ngày, giá đổi) phải đi qua
    được, và cách đúng là cái cũ nhường chỗ.

    SUPERSEDED chỉ áp cho PROPOSED. Một đề xuất đã CONFIRMED là một quyết định
    ĐÃ XẢY RA — viết đè lên nó là xoá dấu vết của một việc có thật, và việc ấy
    đã sinh ra một dòng trong hàng đợi của đơn vị.

    Chứng từ phải ĐANG SỐNG và neo đúng bước này. Kiểm bằng `INSERT ... SELECT`
    nên nó nguyên tử với lượt ghi: kiểm trước rồi chèn sau là hai lệnh, và giữa
    chúng chứng từ có thể vừa hết hạn.
    """
    proposal_id = uuid4()
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchrow("SELECT workflow_id FROM workflows WHERE workflow_id = $1 FOR UPDATE", _uuid(workflow_id))

        # KIỂM VÀ KHOÁ chứng từ mới TRƯỚC khi đụng vào đề xuất cũ.
        #
        # Bản đầu làm ngược: thay thế cái cũ, rồi `INSERT ... SELECT` cái mới,
        # rồi ném ở NGOÀI transaction nếu không có dòng nào. Khi chứng từ mới
        # không hợp lệ, transaction thoát bình thường và commit — đề xuất cũ đã
        # mất `PROPOSED` mà không có gì thay chỗ. Khách mất nút "đồng ý" cho
        # một đề xuất vẫn còn hiệu lực, vì một lượt ghim MỚI đã hỏng.
        #
        # Thứ tự đúng: cái mới phải chứng minh mình dùng được trước khi cái cũ
        # nhường chỗ.
        moi = await conn.fetchrow(
            "SELECT quote_id FROM service_quotes "
            " WHERE quote_id = $1 AND workflow_id = $2 AND task_id = $3 "
            "   AND status = 'ACTIVE' AND valid_until > NOW() "
            "   FOR UPDATE",
            _uuid(quote_id),
            _uuid(workflow_id),
            task_id,
        )
        if moi is None:
            # NÉM TRONG transaction → rollback. Khoá `workflows` được nhả,
            # không dòng nào đổi, đề xuất cũ giữ nguyên `PROPOSED`.
            raise KhongGhimDuocDeXuatError(
                f"không ghim được đề xuất cho bước {task_id!r}: chứng từ không còn sống hoặc không thuộc bước này"
            )

        await conn.execute(
            "UPDATE service_provider_proposals SET status = 'SUPERSEDED' "
            "WHERE workflow_id = $1 AND task_id = $2 AND status = 'PROPOSED'",
            _uuid(workflow_id),
            task_id,
        )
        row = await conn.fetchrow(
            f"""
            INSERT INTO service_provider_proposals (proposal_id, workflow_id, task_id, quote_id, status)
            VALUES ($1, $2, $3, $4, 'PROPOSED')
            RETURNING {_COT}
            """,  # noqa: S608 - `_COT` là literal nội bộ, mọi giá trị đều là tham số
            proposal_id,
            _uuid(workflow_id),
            task_id,
            _uuid(quote_id),
        )

        # Bước và workflow chuyển sang chờ duyệt CÙNG transaction với đề xuất.
        # Tách ra thì có một khoảnh khắc đề xuất đã tồn tại mà trạng thái vẫn
        # nói "đang chạy", và lượt poll rơi đúng vào đó sẽ dựng một màn hình
        # không mời khách bấm gì.
        #
        # Người chờ lúc này là KHÁCH. Điều đó không được ghi ở đâu cả: nó được
        # suy ra lúc dựng câu trả lời, vì "đang chờ ai" đổi khi khách bấm mà
        # không cột nào phải đổi theo.
        await conn.execute(
            "UPDATE workflow_tasks SET status = 'WAITING_APPROVAL', updated_at = NOW() "
            "WHERE workflow_id = $1 AND task_id = $2",
            _uuid(workflow_id),
            task_id,
        )
        await conn.execute(
            "UPDATE workflows SET status = 'WAITING_APPROVAL', updated_at = NOW() WHERE workflow_id = $1",
            _uuid(workflow_id),
        )

    de_xuat = _to_de_xuat(row)
    if de_xuat is None:  # pragma: no cover - `INSERT ... RETURNING` luôn trả một dòng
        raise DeXuatBatNhatQuanError("INSERT ... RETURNING không trả dòng nào")
    return de_xuat


async def doc_de_xuat(pool: asyncpg.Pool, proposal_id: str) -> DeXuat | None:
    """Đọc theo mã. `None` khi không có — KHÔNG ném.

    Không tìm thấy là câu trả lời hợp lệ ở tầng đọc. Biến nó thành lỗi nghiệp
    vụ là việc của tầng biết caller đang định làm gì.
    """
    try:
        khoa = _uuid(proposal_id)
    except (ValueError, AttributeError, TypeError):
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COT} FROM service_provider_proposals WHERE proposal_id = $1",  # noqa: S608
            khoa,
        )
    return _to_de_xuat(row)


async def de_xuat_dang_cho(pool: asyncpg.Pool, *, workflow_id: str, task_id: str) -> DeXuat | None:
    """Đề xuất còn chờ khách bấm của một bước. Nhiều nhất một — ràng buộc ở database."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COT} FROM service_provider_proposals "  # noqa: S608
            "WHERE workflow_id = $1 AND task_id = $2 AND status = 'PROPOSED'",
            _uuid(workflow_id),
            task_id,
        )
    return _to_de_xuat(row)


async def trang_thai_hieu_luc(pool: asyncpg.Pool, de_xuat: DeXuat) -> tuple[str, bool]:
    """Đề xuất này THẬT SỰ đang ở đâu, và khách còn bấm được không.

    `proposal.status` một mình là chưa đủ, và tin nó là fail-OPEN. Chứng từ có
    thể vừa hết hạn hoặc vừa bị thay thế trong khi lượt dọn chưa chạy tới — lúc
    ấy đề xuất vẫn ghi `PROPOSED` và màn hình vẫn dựng nút "đồng ý" cho một cái
    giá không còn tồn tại. Khách bấm, nhận lỗi, bấm lại, nhận lỗi.

    Nên "còn bấm được không" phải tính từ CẢ HAI phía, và tính bằng đồng hồ của
    database — cùng đồng hồ mà lệnh xác nhận dùng, nên hai chỗ không thể nói
    hai điều khác nhau:

        đề xuất còn PROPOSED · chứng từ còn ACTIVE · chưa quá hạn
        · chứng từ và đề xuất cùng workflow/task

    KHÔNG ghi gì. Một `GET` chữa dữ liệu là một `GET` đổi trạng thái, và nó sẽ
    đổi trạng thái từ một tab đang mở, từ một lượt poll, từ một con bot quét
    link. Việc dọn thuộc về `don_bao_gia_va_de_xuat`; ở đây chỉ nói ra sự thật.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, valid_until > NOW() AS con_han, workflow_id, task_id "
            "  FROM service_quotes WHERE quote_id = $1",
            _uuid(de_xuat.quote_id),
        )
    if not de_xuat.dang_cho_khach:
        return de_xuat.status, False
    if row is None:
        return "SUPERSEDED", False
    neo_dung = str(row["workflow_id"]) == de_xuat.workflow_id and row["task_id"] == de_xuat.task_id
    if not neo_dung or row["status"] == "SUPERSEDED":
        # Yêu cầu đã đổi. Trạng thái HIỆU LỰC là `SUPERSEDED` dù cột chưa kịp
        # đổi — người đọc cần biết sự thật, không cần biết lượt dọn chạy chưa.
        return "SUPERSEDED", False
    if row["status"] == "EXPIRED" or not row["con_han"]:
        return "EXPIRED", False
    if row["status"] != "ACTIVE":
        # Chứng từ đã `CONFIRMED` mà đề xuất vẫn `PROPOSED`: không phải trạng
        # thái hợp lệ nào, nên không mời bấm.
        return "SUPERSEDED", False
    return "PROPOSED", True


async def xac_nhan_de_xuat(pool: asyncpg.Pool, proposal_id: str, *, owner_user_id: str) -> KetQuaXacNhanDeXuat:
    """Khách đồng ý → chứng từ chốt, đề xuất chốt, hàng đợi đơn vị mở. MỘT transaction.

    Tám bước, tất cả sau khi đã cầm khoá:

      1. khoá `workflows`, rồi đề xuất, rồi chứng từ — đúng thứ tự chung
      2. workflow có thuộc người đang hỏi không
      3. đề xuất còn PROPOSED không
      4. chứng từ ACTIVE và neo đúng workflow/task
      5. chứng từ → CONFIRMED, và ĐÂY là chỗ duy nhất quyết định "còn hạn không"
      6. đề xuất → CONFIRMED
      7. ĐÚNG MỘT dòng AWAITING vào hàng đợi, chủ sở hữu LẤY TỪ CHỨNG TỪ
      8. bước và workflow giữ nguyên `WAITING_APPROVAL`; người chờ đổi từ khách
         sang đơn vị, và điều đó được SUY RA lúc đọc chứ không ghi ở đâu cả

    Chủ sở hữu của dòng duyệt lấy từ `service_quotes.service_provider_id`, KHÔNG
    từ body, không từ task, không từ model. Đó là toàn bộ lý do bước B tồn tại:
    thứ quyết định ai nhận việc và ai được trả tiền phải là thứ có chứng từ.

    Hàm nhận ĐÚNG HAI thứ: mã đề xuất, và ai đang bấm. Mọi dữ kiện khác —
    `tool`, nhãn dịch vụ, chi tiết, người yêu cầu — đọc từ database bên trong
    transaction. Nhận chúng làm tham số nghĩa là mở một đường cho người gọi tự
    khai mình đang đặt dịch vụ gì, cho ai; và một tham số nhận được thì sớm
    muộn sẽ có một route nối nó thẳng vào body.

    Không gọi provider thật ở đây. Lượt xác nhận chỉ MỞ hàng đợi; đơn vị bấm
    duyệt mới là lúc yêu cầu đi ra ngoài.

    `NOT_FOUND` dùng chung cho "không có" và "không phải của bạn". Phân biệt
    chúng là xác nhận với người đang dò rằng một `proposal_id` nào đó có thật.
    """
    try:
        khoa = _uuid(proposal_id)
        chu = _uuid(owner_user_id)
    except (ValueError, AttributeError, TypeError):
        return KetQuaXacNhanDeXuat(KetQuaXacNhan.NOT_FOUND)

    async with pool.acquire() as conn, conn.transaction():
        # Đọc KHÔNG khoá để biết workflow nào — `workflow_id` của một đề xuất
        # không bao giờ đổi, nên ảnh chụp này không thể cũ đi theo nghĩa có hại.
        so_bo = await conn.fetchrow("SELECT workflow_id FROM service_provider_proposals WHERE proposal_id = $1", khoa)
        if so_bo is None:
            return KetQuaXacNhanDeXuat(KetQuaXacNhan.NOT_FOUND)

        # `workflows` TRƯỚC — cùng thứ tự với mọi người ghi khác.
        # `FOR UPDATE` KHÔNG đi cùng outer join (PostgreSQL từ chối khoá vế
        # nullable). Nên khoá `workflows` trước, đọc thông tin liên hệ sau —
        # hai lệnh, nhưng vẫn trong cùng transaction và sau khi đã cầm khoá.
        wf = await conn.fetchrow(
            "SELECT workflow_id, owner_user_id FROM workflows WHERE workflow_id = $1 FOR UPDATE",
            so_bo["workflow_id"],
        )
        # `owner_user_id IS NULL` cũng rơi vào đây: `None != UUID(...)` là
        # `True` trong Python, nên một workflow không chủ thì không ai là chủ.
        # Từng có một vế `is None` riêng đứng trước — nó không thêm hành vi nào
        # và mutation bỏ nó không làm test nào đỏ, nên nó là chú thích đội lốt
        # điều kiện.
        if wf is None or wf["owner_user_id"] != chu:
            # Người khác, hoặc workflow không có chủ. Cùng một mã với "không
            # tìm thấy" — xem docstring.
            logger.info("chặn xác nhận đề xuất ngoài quyền sở hữu")
            return KetQuaXacNhanDeXuat(KetQuaXacNhan.NOT_FOUND)

        nguoi_yeu_cau = await conn.fetchrow("SELECT full_name, phone FROM users WHERE id = $1", wf["owner_user_id"])

        de_xuat = _to_de_xuat(
            await conn.fetchrow(
                f"SELECT {_COT} FROM service_provider_proposals WHERE proposal_id = $1 FOR UPDATE",  # noqa: S608
                khoa,
            )
        )
        if de_xuat is None:
            return KetQuaXacNhanDeXuat(KetQuaXacNhan.NOT_FOUND)
        if not de_xuat.dang_cho_khach:
            return KetQuaXacNhanDeXuat(KetQuaXacNhan.ALREADY_DECIDED, de_xuat=de_xuat)

        bao_gia = await conn.fetchrow(
            "SELECT quote_id, service_provider_id, service_type, status, workflow_id, task_id "
            "  FROM service_quotes WHERE quote_id = $1 FOR UPDATE",
            _uuid(de_xuat.quote_id),
        )
        neo_dung = (
            bao_gia is not None
            and str(bao_gia["workflow_id"]) == de_xuat.workflow_id
            and bao_gia["task_id"] == de_xuat.task_id
        )
        if bao_gia is None or bao_gia["status"] != "ACTIVE" or not neo_dung:
            # Không phải chuyện hết hạn: chứng từ đã chốt, đã bị thay thế, hoặc
            # neo sang bước khác. Tách khỏi `QUOTE_EXPIRED` vì hết hạn thì xin
            # giá mới là đủ, còn đây là dấu hiệu có gì đó sai trong luồng.
            return KetQuaXacNhanDeXuat(KetQuaXacNhan.QUOTE_NOT_USABLE)

        # Dữ kiện của bước, đọc TRƯỚC mọi lượt ghi. `tool` quyết định đơn vị
        # nhìn thấy loại việc gì và mã từ chối nào được phép — nhận nó từ người
        # gọi là để họ tự khai mình đang đặt dịch vụ gì.
        #
        # Đọc ở ĐÂY chứ không sau lượt chốt: bản đầu đọc sau, và nhánh "không
        # có bước" của nó `return` một kết quả nghiệp vụ SAU khi chứng từ đã
        # thành `CONFIRMED` — tức commit một trạng thái nửa chừng. Mọi kiểm tra
        # có thể trả về bình thường phải nằm TRƯỚC lượt ghi đầu tiên; sau đó
        # thì chỉ còn đường ném.
        buoc = await conn.fetchrow(
            "SELECT tool, input_data FROM workflow_tasks WHERE workflow_id = $1 AND task_id = $2",
            _uuid(de_xuat.workflow_id),
            de_xuat.task_id,
        )
        if buoc is None or str(buoc["tool"]) != bao_gia["service_type"]:
            # Khoá ngoại tổng hợp không cho chuyện này xảy ra, và `luu_bao_gia`
            # đã buộc `tool` trùng `service_type`. Nếu nó vẫn xảy ra thì có gì
            # đó sai hẳn — và ghim một dòng duyệt không biết mình là việc gì
            # còn tệ hơn không ghim.
            return KetQuaXacNhanDeXuat(KetQuaXacNhan.QUOTE_NOT_USABLE)
        tool = str(buoc["tool"])

        # ------------------------------------------------------------------
        # TỪ ĐÂY TRỞ XUỐNG KHÔNG CÒN `return` NÀO CHO NHÁNH HỎNG.
        #
        # Mọi bất biến vỡ đều NÉM, để `conn.transaction()` rollback. Một
        # `return` sau lượt ghi đầu tiên sẽ commit trạng thái nửa chừng, và
        # trạng thái nửa chừng tệ nhất là chứng từ đã chốt mà không ai bên kia
        # nhận được việc.
        # ------------------------------------------------------------------

        # 5 — chứng từ chốt, VÀ đây là chỗ DUY NHẤT quyết định "còn hạn hay không".
        #
        # Bản đầu kiểm hạn ở một nhánh riêng phía trên rồi lặp lại điều kiện
        # trong `WHERE` cho chắc. Đo được bằng mutation: bỏ `valid_until >
        # NOW()` khỏi `WHERE` thì KHÔNG bài kiểm nào đỏ — nhánh phía trên đã
        # chặn hết, nên mệnh đề ấy không mang trách nhiệm nào và không ai biết
        # nếu nó biến mất.
        #
        # Một luật, một chỗ. Lệnh này là nơi hạn được quyết định, và nó quyết
        # định bằng đồng hồ của database TRONG transaction đang giữ khoá — chứ
        # không phải bằng một ảnh chụp đọc vài dòng trước.
        da_chot = _so_dong(
            await conn.execute(
                "UPDATE service_quotes SET status = 'CONFIRMED', confirmed_at = NOW() "
                "WHERE quote_id = $1 AND status = 'ACTIVE' AND valid_until > NOW()",
                _uuid(de_xuat.quote_id),
            )
        )
        if da_chot > 1:  # pragma: no cover - `quote_id` là khoá chính
            raise DeXuatBatNhatQuanError(f"lệnh chốt chứng từ đụng {da_chot} dòng")
        if da_chot == 0:
            # Không chốt được. ĐỌC LẠI để biết vì sao, thay vì suy ra.
            #
            # Ảnh chụp vài dòng trước nói chứng từ ACTIVE và neo đúng, nên "lý
            # do duy nhất còn lại là hết hạn" nghe hợp lý — và nó SAI nếu một
            # lượt song song vừa chốt xong giữa hai lệnh. Lúc ấy khách nhận
            # "báo giá hết hạn, xin giá mới đi" cho một lượt bấm đã THÀNH CÔNG,
            # và họ sẽ đi đặt lần thứ hai.
            #
            # Đọc lại tốn một lời gọi và trả lời đúng câu hỏi thật: dòng này
            # bây giờ đang thế nào.
            hien_tai = await conn.fetchrow(
                "SELECT status, valid_until <= NOW() AS het_han FROM service_quotes WHERE quote_id = $1",
                _uuid(de_xuat.quote_id),
            )
            if hien_tai is not None and hien_tai["status"] == "ACTIVE" and hien_tai["het_han"]:
                # Đề xuất chết theo. `AND status = 'PROPOSED'` KHÔNG phải thừa:
                # thiếu nó thì một lượt đến muộn ghi đè `CONFIRMED` thành
                # `EXPIRED` — hàng đợi đơn vị đã mở, mà chứng từ nói việc này
                # đã chết. Đó là split-brain khó thấy nhất, vì mọi lệnh đều
                # thành công.
                await conn.execute(
                    "UPDATE service_provider_proposals SET status = 'EXPIRED' "
                    "WHERE proposal_id = $1 AND status = 'PROPOSED'",
                    khoa,
                )
                return KetQuaXacNhanDeXuat(KetQuaXacNhan.QUOTE_EXPIRED)
            # Chứng từ vừa bị ai đó chốt hoặc thay thế. Đề xuất KHÔNG bị đụng
            # tới — nếu lượt kia đã chốt nó thì nó đang CONFIRMED, và đó là sự
            # thật.
            return KetQuaXacNhanDeXuat(KetQuaXacNhan.QUOTE_NOT_USABLE)

        # 6 — đề xuất chốt. ĐÚNG một dòng, và không có nhánh trả về nào ở đây:
        # chứng từ vừa thành `CONFIRMED` ngay trên, nên bất kỳ sai lệch nào từ
        # điểm này cũng phải kéo cả hai quay lại.
        da_chot_de_xuat = _so_dong(
            await conn.execute(
                "UPDATE service_provider_proposals SET status = 'CONFIRMED', confirmed_at = NOW() "
                "WHERE proposal_id = $1 AND status = 'PROPOSED'",
                khoa,
            )
        )
        if da_chot_de_xuat != 1:
            raise DeXuatBatNhatQuanError(f"lệnh chốt đề xuất đụng {da_chot_de_xuat} dòng sau khi chứng từ đã chốt")

        # 7 — ĐÚNG MỘT dòng vào hàng đợi. Chủ sở hữu lấy từ CHỨNG TỪ.
        #
        # `ON CONFLICT DO UPDATE` chứ không `DO NOTHING`: bước này có thể đã có
        # một dòng cũ từ một vòng trước (bị từ chối rồi sửa lại), và giữ nguyên
        # dòng ấy nghĩa là đơn vị không bao giờ được hỏi về yêu cầu mới.
        da_ghim = _so_dong(
            await conn.execute(
                """
            INSERT INTO service_approvals
                (workflow_id, task_id, tool, service_label, details, status,
                 applicant_user_id, applicant_name, applicant_phone, service_provider_id)
            VALUES ($1, $2, $3, $4, $5::jsonb, 'AWAITING', $6, $7, $8, $9)
            ON CONFLICT (workflow_id, task_id) DO UPDATE SET
                status = 'AWAITING',
                details = EXCLUDED.details,
                service_label = EXCLUDED.service_label,
                service_provider_id = EXCLUDED.service_provider_id,
                decided_by = NULL,
                decided_at = NULL,
                reject_reason = NULL,
                reject_code = NULL,
                created_at = NOW()
            """,
                _uuid(de_xuat.workflow_id),
                de_xuat.task_id,
                tool,
                SERVICE_LABELS.get(tool, tool),
                # `chi_tiet_cho_don_vi` bỏ định danh nội bộ VÀ bỏ `max_price`. Ngân
                # sách của khách không bao giờ được đi tới đơn vị: biết nó thì họ
                # định giá theo túi tiền người hỏi thay vì theo công việc, và đó
                # chính là điều bước B dựng hai hàng rào để chặn.
                json.dumps(chi_tiet_cho_don_vi(buoc["input_data"]), ensure_ascii=False),
                wf["owner_user_id"],
                nguoi_yeu_cau["full_name"] if nguoi_yeu_cau else None,
                nguoi_yeu_cau["phone"] if nguoi_yeu_cau else None,
                bao_gia["service_provider_id"],
            )
        )
        if da_ghim != 1:
            raise DeXuatBatNhatQuanError(f"ghim hàng đợi đơn vị đụng {da_ghim} dòng")

        ket_thuc = _to_de_xuat(
            await conn.fetchrow(
                f"SELECT {_COT} FROM service_provider_proposals WHERE proposal_id = $1",  # noqa: S608
                khoa,
            )
        )
    return KetQuaXacNhanDeXuat(KetQuaXacNhan.CONFIRMED, de_xuat=ket_thuc, task_id=de_xuat.task_id)
