"""Đơn vị từ chối rồi thì SAO — một luật, một chỗ, phân loại bằng MÃ.

Vì sao phải có module này
-------------------------
Ba đường xử lý một lời từ chối, và chúng loại trừ nhau:

    REPAIR_REQUEST     hỏi khách một ô cụ thể ("chọn ngày khác?")
    RESELECT_PROVIDER  mời khách tìm đơn vị khác
    TERMINAL_REVIEW    hệ thống không tự đi tiếp được; nói thẳng ra

Trước module này, việc chọn đường nằm rải ở hai chỗ — một phép lọc theo `tool`
trong `provider_reselection`, và một nhánh `_has_open_repair` trong
`routes`. Hai chỗ nghĩa là hai luật, và chúng chỉ lệch nhau đúng vào lúc ai đó
thêm một mã từ chối mới.

Phân loại bằng MÃ, không bằng câu chữ
-------------------------------------
`reject_code` là allowlist đóng (`REJECT_CODES`). Câu `reject_reason` là văn
bản tự do do người của đơn vị gõ — đọc nó để quyết định nghiệp vụ nghĩa là biến
chính tả của họ thành logic, và một `LIKE '%hết chỗ%'` hỏng ngay lần đầu ai đó
gõ "không còn slot".

FAIL CLOSED
-----------
Mã lạ, mã `None`, hoặc một mã mới thêm vào `REJECT_CODES` mà quên cập nhật ở
đây — tất cả rơi về `TERMINAL_REVIEW`. Đó là lựa chọn an toàn duy nhất: nó
KHÔNG tự đổi đơn vị và KHÔNG hứa một đường sửa không tồn tại; nó nói ra rằng hệ
thống chưa biết làm gì tiếp.

Hướng ngược lại — mặc định "tìm đơn vị khác" — sẽ gửi một yêu cầu sang một
doanh nghiệp khác vì một lý do hệ thống không hiểu.
"""

from __future__ import annotations

from enum import StrEnum

from src.common.enums import ErrorCode
from src.orchestration.repair import repair_missing_fields


class HuongXuLyTuChoi(StrEnum):
    """Ba đường, loại trừ nhau. Tập ĐÓNG."""

    # Hỏi khách một ô cụ thể. Rẻ nhất cho khách: đơn vị vẫn nhận việc.
    REPAIR_REQUEST = "REPAIR_REQUEST"
    # Mời khách tìm đơn vị khác. Chỉ khi ĐƠN VỊ là thứ thay thế được.
    RESELECT_PROVIDER = "RESELECT_PROVIDER"
    # Hệ thống không tự đi tiếp được. Nói thẳng, không dựng nút giả.
    TERMINAL_REVIEW = "TERMINAL_REVIEW"


# Mã từ chối → mã lỗi nghiệp vụ mà `repair` biết map sang field.
#
# Chỉ hai mã có đường hỏi lại. `SERVICE_UNAVAILABLE` và `OTHER` thì không, và
# đó là điều đúng: "hệ thống chúng tôi đang bảo trì" không có ô nào để sửa.
_MA_CO_DUONG_HOI_LAI: dict[str, ErrorCode] = {
    "NO_AVAILABILITY": ErrorCode.NO_AVAILABILITY,
    # Yêu cầu sai — về nguyên tắc có thể hỏi lại một ô, NẾU hệ thống biết ô nào.
    # Hôm nay `repair` không có map cho mã này, nên nó sẽ rơi về
    # `TERMINAL_REVIEW`. Để nó ở đây là cố ý: ngày `repair` biết map, luật này
    # tự đúng mà không phải sửa ở hai chỗ.
    "INVALID_REQUEST": ErrorCode.INVALID_INPUT,
}


# `repair` trả ô này khi nó KHÔNG biết ô nào hỏng: "mô tả lại mục tiêu giúp
# mình". Đó là một lời thú nhận, không phải một ô nghiệp vụ — và coi nó là "đã
# biết ô cần hỏi" sẽ biến mọi `INVALID_REQUEST` thành một form hỏi lại cả yêu
# cầu, cho một lỗi khách không gây ra và không sửa được.
_O_NGHIA_LA_KHONG_BIET = "supported_goal"


def co_o_de_hoi_lai(tool: str, reject_code: str | None, task_input: dict | None = None) -> bool:
    """Hệ thống có biết CHÍNH XÁC ô nào cần hỏi lại không.

    "Biết" nghĩa là `repair` trả về một ô NGHIỆP VỤ cho đúng cặp (tool, mã).
    Đoán một ô rồi hỏi sai tên thì câu trả lời hợp lệ của khách vẫn bị từ chối,
    và họ không có cách nào biết vì sao.

    `supported_goal` KHÔNG tính. `repair` trả nó khi nó không biết ô nào hỏng,
    nên nhận nó ở đây là đọc một lời thú nhận thành một câu trả lời.
    """
    ma_loi = _MA_CO_DUONG_HOI_LAI.get(reject_code or "")
    if ma_loi is None:
        return False
    o = [f for f in repair_missing_fields(tool, ma_loi, task_input or {}) if f != _O_NGHIA_LA_KHONG_BIET]
    return bool(o)


def huong_xu_ly(
    *,
    tool: str,
    reject_code: str | None,
    dang_hoi_khach: bool,
    task_input: dict | None = None,
) -> HuongXuLyTuChoi:
    """Lời từ chối này dẫn tới đường nào. MỘT hàm, dùng ở mọi nơi.

    Thứ tự KHÔNG đảo được:

      1. Đang hỏi khách một ô → câu hỏi ấy THẮNG. Nó cụ thể hơn, rẻ hơn cho
         khách, và nó đã ở trên màn hình. Mời "tìm đơn vị khác" chồng lên một
         câu hỏi đang treo là cho khách hai việc trong khi chỉ có một.

      2. `SERVICE_UNAVAILABLE` trên dịch vụ có báo giá → đổi đơn vị. Đây là câu
         "chúng tôi không làm được việc này" — không có ô nào để sửa, và thứ
         thay thế được chính là họ.

      3. Còn lại: có ô để hỏi thì hỏi, không có thì `TERMINAL_REVIEW`.

    `NO_AVAILABILITY` KHÔNG bao giờ thành `RESELECT_PROVIDER` ở bước 3, kể cả
    khi không có ô nào để hỏi. Nó nghĩa là "không nhận được NGÀY ẤY", và trên
    dịch vụ có nhiều đối tác thì đổi ngày với chính đơn vị ấy là lựa chọn khác
    hẳn đổi đơn vị. Cho khách chọn giữa hai thứ ấy là một quyết định sản phẩm
    chưa được đưa ra — xem ghi chú NỢ ở cuối file.
    """
    from src.orchestration.provider_matching import DICH_VU_CO_BAO_GIA

    if dang_hoi_khach:
        return HuongXuLyTuChoi.REPAIR_REQUEST

    if reject_code == "SERVICE_UNAVAILABLE" and tool in DICH_VU_CO_BAO_GIA:
        return HuongXuLyTuChoi.RESELECT_PROVIDER

    if co_o_de_hoi_lai(tool, reject_code, task_input):
        return HuongXuLyTuChoi.REPAIR_REQUEST

    # Mã lạ, `None`, `OTHER`, hay một mã mới chưa ai cập nhật ở đây — tất cả
    # dừng lại và nói ra. Không tự đổi đơn vị vì một lý do không hiểu.
    return HuongXuLyTuChoi.TERMINAL_REVIEW


# NỢ SẢN PHẨM — `NO_AVAILABILITY` và quyền chọn của khách.
#
# Hôm nay `NO_AVAILABILITY` luôn dẫn tới "đổi ngày với chính đơn vị này", vì đó
# là đường rẻ nhất và nó đã chạy đúng từ trước. Nhưng khách có thể muốn GIỮ
# NGÀY và đổi đơn vị — nhất là khi ngày ấy là ngày họ nhận nhà.
#
# Cho họ chọn giữa hai đường là một lựa chọn sản phẩm, không phải một dòng mã:
# nó cần một màn hình có hai nút, một câu hỏi rõ ràng, và một luật cho trường
# hợp họ bấm nhầm rồi muốn quay lại. Chưa triển khai, và KHÔNG được thêm nút
# "tìm đơn vị khác" cho `NO_AVAILABILITY` như một bản vá nhỏ — hai nút xuất
# hiện cùng lúc mà không ai thiết kế câu hỏi giữa chúng sẽ làm khách bấm bừa.
