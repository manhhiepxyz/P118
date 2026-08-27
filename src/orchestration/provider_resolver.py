"""Tên khách nói → mã đơn vị. Chỉ khi DUY NHẤT; còn lại thì hỏi, không đoán.

Vì sao không đoán
-----------------
Đoán sai một đơn vị cung cấp không phải lỗi hiển thị: nó gửi việc và tiền của
khách sang một doanh nghiệp khác. Và nó sai một cách IM LẶNG — màn hình vẫn nói
"đã chọn Đại Tín", chỉ có đơn hàng là của người khác.

Nên ở đây không có khoảng cách chỉnh sửa, không có điểm số, không có ngưỡng.
Hai kết quả duy nhất: khớp đúng một đơn vị, hoặc không. Không khớp thì tầng
trên HỎI LẠI — một câu hỏi rẻ hơn một đơn hàng sai rất nhiều.

Nguồn CANONICAL
---------------
`src/mock/service_providers.py`, và chỉ nó. Không có bảng tên/alias/đơn vị song
song: hai danh mục nghĩa là hai chỗ để lệch nhau, và chỗ lệch sẽ nằm đúng ở
những đơn vị mới thêm.

Không có bảng "cụm từ kích hoạt"
--------------------------------
File này KHÔNG chứa một danh sách cách nói tiếng Việt nào ("đội", "bên", "công
ty", "cho tôi chọn…"). Model có nhiệm vụ trích ra ĐOẠN TÊN từ câu của khách;
mã có nhiệm vụ quyết định đoạn ấy trỏ vào đâu. Trộn hai việc lại là dựng một bộ
phân tích ngôn ngữ bằng tay — nó sẽ luôn thiếu cách nói thứ N+1, và mỗi lần
thiếu là một lần đoán sai.

Chuẩn hoá
---------
Bỏ dấu, bỏ hoa/thường, gộp khoảng trắng. "ĐẠI TÍN", "đại tín", "Dai  Tin" là
một. Đây là chuẩn hoá CHÍNH TẢ, không phải suy đoán ngữ nghĩa: nó không thêm
khả năng khớp nào ngoài việc coi hai cách gõ cùng một chuỗi là như nhau.

Chỉ TRÙNG KHỚP HOÀN TOÀN, không chứa nhau
-----------------------------------------
Bản trước còn một nhánh "chứa nhau": đoạn khách nói nằm trong tên đơn vị, hoặc
ngược lại. Nó mở một đường sai mà không bài kiểm nào lúc ấy bắt được:

    "chuyển nhà"  chỉ nằm trong "Chuyển nhà Minh Phát"  → MOV-01
    "vận tải"     chỉ nằm trong "Vận tải Đại Tín"       → MOV-02
    "dịch vụ"     chỉ nằm trong "Dịch vụ An Khang"      → MOV-03

Cả ba đều là MÔ TẢ LOẠI HÌNH, không phải một cái tên khách chỉ định. Nếu model
trích nhầm "chuyển nhà" vào ô tên đơn vị — đúng thứ nó dễ làm nhất, vì cụm ấy
có mặt trong hầu hết câu về chuyển nhà — thì resolver biến một lỗi trích thành
một lựa chọn tài chính hợp lệ. Đó là vi phạm thẳng nguyên tắc "model đề xuất,
code xác minh": code phải là chỗ lỗi ấy DỪNG LẠI, không phải chỗ nó được hợp
thức hoá.

Nên chỉ còn khớp chính xác, với ba trường: `provider_id`, `ten`,
`ten_thuong_hieu`. Trường thứ ba là thứ cho phép bỏ hẳn phép chứa-nhau mà vẫn
gọi được "Đại Tín" — nó là thuộc tính của đơn vị trong cùng nguồn canonical,
không phải một bảng tên thứ hai.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from src.mock.service_providers import DON_VI_BAO_TRI, DON_VI_CHUYEN_NHA

# Dịch vụ nào có nhiều đơn vị để chọn. Các dịch vụ còn lại do ban quản lý làm,
# không có lựa chọn nào — nên cũng không có gì để resolve.
_DANH_MUC = {
    "schedule_move": DON_VI_CHUYEN_NHA,
    "create_maintenance_request": DON_VI_BAO_TRI,
}

# `đ` không tách được bằng NFD — nó là một ký tự riêng, không phải `d` cộng dấu.
# Thiếu dòng này thì "Đại Tín" chuẩn hoá thành "đai tin" và không bao giờ khớp
# với một người gõ "dai tin".
_D_GACH = str.maketrans({"đ": "d", "Đ": "d"})
_KHONG_PHAI_CHU = re.compile(r"[^a-z0-9]+")


def chuan_hoa(text: str) -> str:
    """Chuỗi so sánh được: không dấu, không hoa, không dấu câu, một khoảng trắng.

    Dấu câu thành khoảng trắng chứ không bị xoá: "MOV-01" và "MOV 01" là một
    cách gõ, còn xoá hẳn thì "an-khang" và "ankhang" cũng thành một — và lúc đó
    khoảng cách giữa hai tên khác nhau bị thu hẹp mà không ai chủ ý.
    """
    khong_dau = unicodedata.normalize("NFD", text.translate(_D_GACH))
    khong_dau = "".join(c for c in khong_dau if not unicodedata.combining(c))
    return _KHONG_PHAI_CHU.sub(" ", khong_dau.lower()).strip()


@dataclass(frozen=True)
class KetQuaTraTen:
    """Kết quả tra tên. `trang_thai` là hợp đồng, `ung_vien` là để hỏi lại."""

    trang_thai: Literal["FOUND", "UNKNOWN", "AMBIGUOUS"]
    provider_id: str | None = None
    # Khi mơ hồ: các mã cùng khớp, đã sắp xếp. Tầng trên đưa danh sách này cho
    # khách chọn — hỏi "ý bạn là bên nào trong hai bên này" tốt hơn hẳn hỏi
    # "bạn muốn bên nào" một lần nữa từ đầu.
    ung_vien: tuple[str, ...] = ()


def tra_ten_don_vi(ten_khach_noi: str, *, service_type: str) -> KetQuaTraTen:
    """Đoạn tên khách nói trỏ vào đơn vị nào của DỊCH VỤ NÀY.

    Phạm vi theo dịch vụ là bắt buộc: một câu về chuyển nhà không được resolve
    ra một đội bảo trì. Nếu không giới hạn thì hai danh mục dùng chung một không
    gian tên, và một cái tên trùng nhau sẽ đưa việc sang nhầm ngành.

    Luật khớp: TRÙNG KHỚP HOÀN TOÀN (sau chuẩn hoá) với `provider_id`, `ten`
    hoặc `ten_thuong_hieu`. Không chứa nhau, không khoảng cách chỉnh sửa, không
    điểm số, không ngưỡng.

    Nhiều hơn một ứng viên → `AMBIGUOUS` kèm danh sách, KHÔNG chọn cái "khớp
    tốt hơn". "Khớp tốt hơn" là một điểm số, và điểm số là chỗ việc đoán lẻn
    vào. Nhánh này hiếm với danh mục hiện tại nhưng có thật: hai đối tác cùng
    mang thương hiệu "An Khang" là chuyện bình thường ngoài đời.

    Bốn thất bại CỐ Ý, và cả bốn đều là thứ một bộ so khớp "thông minh" đoán
    được:

        "chuyển nhà"              mô tả loại hình, không phải tên
        "Minh"                    một nửa thương hiệu
        "Đại Tính"                sai một chữ
        "đội Đại Tín bên quận 7"  tên lẫn với chữ khác

    Tất cả trả `UNKNOWN` và tầng trên hỏi lại. Một câu hỏi rẻ hơn một đơn hàng
    gửi nhầm doanh nghiệp rất nhiều.
    """
    danh_muc = _DANH_MUC.get(service_type)
    if danh_muc is None:
        # Dịch vụ không có lựa chọn thì không có gì để tra. Trả `UNKNOWN` chứ
        # không ném: đây là câu trả lời đúng cho một câu hỏi hợp lệ.
        return KetQuaTraTen("UNKNOWN")

    can = chuan_hoa(ten_khach_noi)
    if not can:
        return KetQuaTraTen("UNKNOWN")

    khop = sorted(
        d.provider_id
        for d in danh_muc
        if can in {chuan_hoa(d.provider_id), chuan_hoa(d.ten), chuan_hoa(d.ten_thuong_hieu)}
    )
    if len(khop) == 1:
        return KetQuaTraTen("FOUND", provider_id=khop[0])
    if len(khop) > 1:
        return KetQuaTraTen("AMBIGUOUS", ung_vien=tuple(khop))
    return KetQuaTraTen("UNKNOWN")
