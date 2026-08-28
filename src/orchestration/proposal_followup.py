"""Trả lời một câu hỏi tiếp về đề xuất đơn vị — bằng chính chứng từ đã lưu.

Vấn đề
------
Khi một yêu cầu chuyển nhà đang chờ khách chọn đơn vị và khách hỏi "còn chỗ nào
rẻ hơn không", câu ấy rơi xuống làn lập kế hoạch của `/workflows/demo/start`.
Làn ấy lập một kế hoạch MỚI — đó là việc duy nhất nó biết làm — nên một lượt hỏi
sinh ra một workflow thứ hai và một câu trả lời nói về bất động sản, trong khi
ba báo giá cho đúng câu hỏi ấy đang nằm sẵn trong `service_quotes`.

Ranh giới này đứng TRƯỚC làn ấy và chỉ nhận đúng một tình huống: `schedule_move`
đang ở `WAITING_PROVIDER_PROPOSAL`. Ngoài tình huống đó nó trả `None` và mọi thứ
chạy như cũ.

Ai quyết định cái gì
--------------------
    model      đọc câu và chọn MỘT nhãn trong tập đóng. Hết.
    file này   đọc chứng từ đã persist, quyết định, và soạn câu trả lời.

Model không bao giờ thấy `provider_id`, `quote_id`, giá hay điểm đánh giá, và
không được trả về chúng. Một con số do model bịa ra trông y hệt một con số thật,
và khách không có cách nào phân biệt.

Câu trả lời dựng bằng CHUỖI GHÉP từ dữ kiện, không qua model lần thứ hai: một
lượt viết lại có thể nói sai lý do cho đúng con số.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import asyncpg

from src.agents.proposal_followup_intent import DeXuatYDinh, YDinhHoiThem
from src.db.proposal_repository import de_xuat_dang_cho, ghim_de_xuat, xac_nhan_de_xuat
from src.db.quote_repository import bao_gia_dang_song, doc_bao_gia
from src.orchestration.budget_text import doc_ngan_sach
from src.orchestration.proposal import KetQuaXacNhan
from src.orchestration.provider_directory import ten_don_vi
from src.orchestration.provider_resolver import tra_ten_don_vi
from src.orchestration.quote import BaoGia
from src.orchestration.quote_service import DICH_VU_CHUYEN_NHA

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CauTraLoi:
    """Một câu trả lời, và việc nó đã làm (nếu có).

    `da_doi_de_xuat` để tầng trên biết có cần đọc lại state hay không. Không suy
    từ câu chữ — câu chữ là cho người đọc.
    """

    cau: str
    da_doi_de_xuat: bool = False
    da_xac_nhan: bool = False


def _tien(so: int, don_vi_tien: str) -> str:
    """`430000, "VND"` → `"430.000 VND"`.

    Định dạng số TRƯỚC rồi mới ghép: `.replace(",", ".")` trên cả câu sẽ nuốt
    luôn dấu phẩy của tiếng Việt — lỗi đã xảy ra một lần trong dự án này.
    """
    return f"{so:,}".replace(",", ".") + f" {don_vi_tien}"


def _ten(bao_gia: BaoGia) -> str:
    return ten_don_vi(bao_gia.service_provider_id) or bao_gia.service_provider_id


async def _bao_gia_cua_de_xuat(pool: asyncpg.Pool, quote_id: str) -> BaoGia | None:
    return await doc_bao_gia(pool, quote_id)


# ------------------------------------------------------------------ trả lời
def _cau_re_hon(hien_tai: BaoGia, con_song: list[BaoGia]) -> str:
    """ASK_CHEAPER. Nêu tên, giá và CHÊNH LỆCH — chưa tự đổi gì."""
    re_hon = [q for q in con_song if q.amount < hien_tai.amount]
    if not re_hon:
        return (
            f"Trong các báo giá còn hiệu lực cho yêu cầu này, {_ten(hien_tai)} với "
            f"{_tien(hien_tai.amount, hien_tai.currency)} đang là mức thấp nhất. "
            "Bạn muốn mình giữ đơn vị này chứ?"
        )
    dong = "; ".join(
        f"{_ten(q)} {_tien(q.amount, q.currency)} (rẻ hơn {_tien(hien_tai.amount - q.amount, q.currency)})"
        for q in re_hon
    )
    return (
        f"Có. So với {_ten(hien_tai)} ({_tien(hien_tai.amount, hien_tai.currency)}): {dong}. "
        "Bạn muốn đổi sang bên nào không? Mình chưa đổi gì cả."
    )


def _cau_so_sanh(hien_tai: BaoGia, con_song: list[BaoGia], danh_gia: dict[str, float]) -> str:
    """COMPARE_OPTIONS. Rẻ trước, rồi tới mã đơn vị — đúng thứ tự SQL đã trả.

    KHÔNG gọi bên rẻ nhất là "tốt nhất": giá là một dữ kiện, "tốt" là một phán
    xét, và hệ thống không có căn cứ nào cho phán xét ấy.
    """
    dong = []
    for q in con_song:
        phan = f"{_ten(q)}: {_tien(q.amount, q.currency)}"
        diem = danh_gia.get(q.service_provider_id)
        if diem is not None:
            phan += f", đánh giá {diem}/5 theo danh mục"
        if q.quote_id == hien_tai.quote_id:
            phan += " (đang đề xuất)"
        dong.append(phan)
    return "Các lựa chọn còn hiệu lực cho yêu cầu này: " + "; ".join(dong) + ". Bạn muốn chọn bên nào?"


def _cau_uy_tin(hien_tai: BaoGia, danh_gia: dict[str, float]) -> str:
    """ASK_REPUTATION. Chỉ nêu thứ danh mục thật sự có.

    Không bịa số khách, số đơn, chứng nhận hay cam kết. Nói rõ đây là dữ liệu
    danh mục — một điểm số trong dữ liệu mẫu không phải một bảo đảm chất lượng,
    và trình bày nó như bảo đảm là hứa thay cho một bên thứ ba.
    """
    diem = danh_gia.get(hien_tai.service_provider_id)
    if diem is None:
        return (
            f"Mình chưa có đánh giá đã xác minh cho {_ten(hien_tai)}. "
            "Mình chỉ nêu được giá và điều kiện của báo giá này thôi."
        )
    return (
        f"{_ten(hien_tai)} có điểm {diem}/5 trong danh mục đơn vị của hệ thống. "
        "Đây là dữ liệu danh mục để so sánh, không phải một bảo đảm chất lượng."
    )


def _cau_ly_do(hien_tai: BaoGia, con_song: list[BaoGia], ly_do_luu: str | None) -> str:
    """ASK_RECOMMENDATION_REASON. Giải thích bằng dữ liệu chọn THẬT."""
    if ly_do_luu:
        return ly_do_luu + " Bạn muốn xem các lựa chọn khác không?"
    thap_nhat = min(con_song, key=lambda q: q.amount) if con_song else hien_tai
    if hien_tai.quote_id == thap_nhat.quote_id:
        return (
            f"Mình chọn {_ten(hien_tai)} vì đây là mức thấp nhất trong các báo giá còn hiệu lực "
            f"cho đúng yêu cầu của bạn: {_tien(hien_tai.amount, hien_tai.currency)}."
        )
    return (
        f"Mình đang đề xuất {_ten(hien_tai)} với {_tien(hien_tai.amount, hien_tai.currency)}. "
        f"Mức thấp nhất còn hiệu lực là {_ten(thap_nhat)} {_tien(thap_nhat.amount, thap_nhat.currency)}."
    )


# ------------------------------------------------------------------ đổi lựa chọn
async def _doi_sang(pool: asyncpg.Pool, *, workflow_id: str, task_id: str, moi: BaoGia) -> CauTraLoi:
    """Ghim đề xuất mới. Đề xuất cũ tự thành SUPERSEDED trong cùng transaction.

    KHÔNG mở hàng đợi đơn vị: đó là việc của lượt xác nhận, và gộp hai bước lại
    là bỏ mất chính cái cổng khách đang đứng trước.
    """
    try:
        await ghim_de_xuat(pool, workflow_id=workflow_id, task_id=task_id, quote_id=moi.quote_id)
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("khong ghim duoc de xuat moi (%s)", type(exc).__name__)
        return CauTraLoi("Mình chưa đổi được sang đơn vị đó. Bạn thử lại giúp mình nhé.")
    return CauTraLoi(
        f"Mình đã đổi sang {_ten(moi)} với {_tien(moi.amount, moi.currency)}. "
        "Bạn xác nhận để mình gửi yêu cầu cho đơn vị nhé.",
        da_doi_de_xuat=True,
    )


async def _chon_theo_ten(
    pool: asyncpg.Pool, *, workflow_id: str, task_id: str, ten_khach_noi: str | None, con_song: list[BaoGia]
) -> CauTraLoi:
    """SELECT_PROVIDER. Tên lạ hoặc mơ hồ thì HỎI LẠI, không đoán."""
    if not (ten_khach_noi or "").strip():
        return CauTraLoi("Bạn cho mình biết tên đơn vị bạn muốn chọn nhé.")

    ket_qua = tra_ten_don_vi(ten_khach_noi, service_type=DICH_VU_CHUYEN_NHA)
    if ket_qua.trang_thai == "AMBIGUOUS":
        ten = ", ".join(ten_don_vi(m) or m for m in ket_qua.ung_vien)
        return CauTraLoi(f"Bạn muốn nói tới bên nào: {ten}?")
    if ket_qua.trang_thai != "FOUND" or ket_qua.provider_id is None:
        co = ", ".join(_ten(q) for q in con_song)
        return CauTraLoi(f"Mình chưa nhận ra đơn vị đó. Các bên đang có báo giá cho yêu cầu này: {co}.")

    moi = next((q for q in con_song if q.service_provider_id == ket_qua.provider_id), None)
    if moi is None:
        co = ", ".join(_ten(q) for q in con_song)
        return CauTraLoi(
            f"{ten_don_vi(ket_qua.provider_id)} chưa có báo giá còn hiệu lực cho yêu cầu này. Các bên đang có: {co}."
        )
    return await _doi_sang(pool, workflow_id=workflow_id, task_id=task_id, moi=moi)


async def _dat_ngan_sach(
    pool: asyncpg.Pool, *, workflow_id: str, task_id: str, budget_text: str | None, con_song: list[BaoGia]
) -> CauTraLoi:
    """SET_MAX_BUDGET. Lọc ở PHÍA P-118, không gửi ngân sách sang đơn vị.

    Đơn vị báo giá theo yêu cầu, không theo túi tiền khách. Gửi ngân sách sang
    là mời họ báo đúng bằng trần — và khách mất phần chênh lệch mà không biết.
    """
    muc = doc_ngan_sach(budget_text)
    if muc is None:
        return CauTraLoi("Mình chưa đọc được mức ngân sách. Bạn nói giúp mình một con số cụ thể nhé.")

    vua_tui = [q for q in con_song if q.amount <= muc]
    if not vua_tui:
        thap_nhat = min(con_song, key=lambda q: q.amount)
        return CauTraLoi(
            f"Không có báo giá nào dưới {_tien(muc, thap_nhat.currency)} cho yêu cầu này. "
            f"Mức thấp nhất là {_ten(thap_nhat)} {_tien(thap_nhat.amount, thap_nhat.currency)}. "
            "Mình giữ nguyên đề xuất hiện tại nhé."
        )
    return await _doi_sang(pool, workflow_id=workflow_id, task_id=task_id, moi=vua_tui[0])


async def _xac_nhan(pool: asyncpg.Pool, *, proposal_id: str, owner_user_id: str) -> CauTraLoi:
    """CONFIRM_CURRENT. Gọi ĐÚNG hàm mà cái nút đang gọi.

    Không có đường xác nhận thứ hai. Hai đường nghĩa là hai bộ luật, và bộ luật
    ít được nhìn hơn sẽ là bộ luật sai.
    """
    ket_qua = await xac_nhan_de_xuat(pool, proposal_id, owner_user_id=owner_user_id)
    if ket_qua.ket_qua is KetQuaXacNhan.CONFIRMED:
        return CauTraLoi("Mình đã gửi yêu cầu cho đơn vị. Bạn chờ họ xác nhận nhé.", da_xac_nhan=True)
    if ket_qua.ket_qua is KetQuaXacNhan.QUOTE_EXPIRED:
        return CauTraLoi("Báo giá vừa hết hiệu lực nên mình chưa chốt được. Bạn để mình xin lại báo giá nhé.")
    if ket_qua.ket_qua is KetQuaXacNhan.ALREADY_DECIDED:
        return CauTraLoi("Yêu cầu này đã được gửi cho đơn vị rồi.")
    return CauTraLoi("Mình chưa chốt được đề xuất này. Bạn thử lại giúp mình nhé.")


_CAU_NGOAI_PHAM_VI = (
    "Yêu cầu đang mở của bạn là đặt lịch chuyển nhà, và nó đang chờ bạn xác nhận đơn vị. "
    "Bạn tạo một Hành trình mới cho dịch vụ kia giúp mình nhé — mình sẽ xử lý riêng."
)
_CAU_KHONG_HIEU = (
    "Mình chưa rõ ý bạn. Bạn muốn xem các lựa chọn khác, đổi sang đơn vị khác, hay xác nhận đơn vị đang đề xuất?"
)


async def tra_loi_hoi_them(
    pool: asyncpg.Pool,
    *,
    workflow_id: str,
    y_dinh: DeXuatYDinh | None,
    owner_user_id: str,
) -> CauTraLoi | None:
    """Câu trả lời cho một lượt hỏi tiếp. `None` = không phải việc của tầng này.

    `None` chỉ xảy ra khi không còn đề xuất nào đang chờ — lúc ấy người gọi đi
    tiếp như cũ. Mọi nhánh khác đều trả về một câu, kể cả khi không hiểu: rơi
    xuống làn lập kế hoạch với một câu hỏi về báo giá là cách sinh ra workflow
    thứ hai.
    """
    de_xuat = await de_xuat_dang_cho(pool, workflow_id=workflow_id, task_id=None)
    if de_xuat is None:
        return None

    hien_tai = await _bao_gia_cua_de_xuat(pool, de_xuat.quote_id)
    if hien_tai is None:  # pragma: no cover - khoá ngoại không cho chứng từ biến mất
        return None

    con_song = await bao_gia_dang_song(
        pool,
        workflow_id=workflow_id,
        task_id=de_xuat.task_id,
        request_fingerprint=hien_tai.request_fingerprint,
    )
    if not con_song:
        return CauTraLoi("Các báo giá cho yêu cầu này đã hết hiệu lực. Bạn để mình xin lại báo giá mới nhé.")

    from src.orchestration.provider_selection import _DANH_GIA

    nhan = y_dinh.y_dinh if y_dinh else YDinhHoiThem.UNKNOWN

    if nhan is YDinhHoiThem.ASK_CHEAPER:
        return CauTraLoi(_cau_re_hon(hien_tai, con_song))
    if nhan is YDinhHoiThem.COMPARE_OPTIONS:
        return CauTraLoi(_cau_so_sanh(hien_tai, con_song, _DANH_GIA))
    if nhan is YDinhHoiThem.ASK_REPUTATION:
        return CauTraLoi(_cau_uy_tin(hien_tai, _DANH_GIA))
    if nhan is YDinhHoiThem.ASK_RECOMMENDATION_REASON:
        return CauTraLoi(_cau_ly_do(hien_tai, con_song, None))
    if nhan is YDinhHoiThem.SELECT_PROVIDER:
        return await _chon_theo_ten(
            pool,
            workflow_id=workflow_id,
            task_id=de_xuat.task_id,
            ten_khach_noi=y_dinh.provider_name_text if y_dinh else None,
            con_song=con_song,
        )
    if nhan is YDinhHoiThem.SELECT_CHEAPEST:
        return await _doi_sang(
            pool, workflow_id=workflow_id, task_id=de_xuat.task_id, moi=min(con_song, key=lambda q: q.amount)
        )
    if nhan is YDinhHoiThem.SET_MAX_BUDGET:
        return await _dat_ngan_sach(
            pool,
            workflow_id=workflow_id,
            task_id=de_xuat.task_id,
            budget_text=y_dinh.budget_text if y_dinh else None,
            con_song=con_song,
        )
    if nhan is YDinhHoiThem.CONFIRM_CURRENT:
        return await _xac_nhan(pool, proposal_id=de_xuat.proposal_id, owner_user_id=owner_user_id)
    if nhan is YDinhHoiThem.OUT_OF_SCOPE:
        return CauTraLoi(_CAU_NGOAI_PHAM_VI)
    return CauTraLoi(_CAU_KHONG_HIEU)
