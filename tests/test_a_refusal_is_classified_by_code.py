"""Đơn vị từ chối rồi thì SAO — phân loại bằng MÃ, và fail closed.

Ba đường xử lý một lời từ chối, và chúng loại trừ nhau:

    REPAIR_REQUEST     hỏi khách một ô cụ thể ("chọn ngày khác?")
    RESELECT_PROVIDER  mời khách tìm đơn vị khác
    TERMINAL_REVIEW    hệ thống không tự đi tiếp được; nói thẳng ra

Phân loại bằng `reject_code` — một allowlist đóng — chứ không bằng
`reject_reason`, vốn là văn bản tự do do người của đơn vị gõ. Đọc câu chữ để
quyết định nghiệp vụ nghĩa là biến chính tả của họ thành logic, và một
`LIKE '%hết chỗ%'` hỏng ngay lần đầu ai đó gõ "không còn slot".

Bài kiểm ở đây là một MA TRẬN đầy đủ `tool × reject_code × đang-hỏi-khách`, chứ
không phải vài ca tiêu biểu: cái nguy hiểm nhất của một bảng phân loại là ô
không ai nghĩ tới, và ô ấy chỉ lộ ra khi liệt kê hết.
"""

from __future__ import annotations

import pytest

from src.orchestration.refusal_policy import (
    HuongXuLyTuChoi,
    co_o_de_hoi_lai,
    huong_xu_ly,
)
from src.orchestration.service_approval import REJECT_CODES

CO_BAO_GIA = "schedule_move"
KHONG_BAO_GIA = "book_parking"
KHONG_CO_O_SUA = "register_vehicle"


# --------------------------------------------------- a. câu hỏi đang treo thắng
@pytest.mark.parametrize("tool", [CO_BAO_GIA, KHONG_BAO_GIA, KHONG_CO_O_SUA])
@pytest.mark.parametrize("ma", [*REJECT_CODES, None, "MOT_MA_LA"])
def test_an_open_question_always_wins(tool, ma):
    """Đang hỏi khách một ô → câu hỏi ấy thắng MỌI mã từ chối.

    Nó cụ thể hơn, rẻ hơn cho khách, và nó đã ở trên màn hình. Mời "tìm đơn vị
    khác" chồng lên một câu hỏi đang treo là cho khách hai việc trong khi chỉ
    có một — và họ sẽ trả lời một cái rồi thấy cái kia vẫn còn.
    """
    assert huong_xu_ly(tool=tool, reject_code=ma, dang_hoi_khach=True) is HuongXuLyTuChoi.REPAIR_REQUEST


# --------------------------------------------------- b. SERVICE_UNAVAILABLE
def test_service_unavailable_on_a_quoted_service_means_another_provider():
    """ "Chúng tôi không làm được việc này" — không ô nào để sửa, và thứ thay
    thế được chính là họ."""
    assert (
        huong_xu_ly(tool=CO_BAO_GIA, reject_code="SERVICE_UNAVAILABLE", dang_hoi_khach=False)
        is HuongXuLyTuChoi.RESELECT_PROVIDER
    )


@pytest.mark.parametrize("tool", [KHONG_BAO_GIA, KHONG_CO_O_SUA])
def test_service_unavailable_without_a_quote_system_is_terminal(tool):
    """Không có đối tác nào khác thì "tìm đơn vị khác" là một lời hứa suông.

    Bãi xe và đăng ký phương tiện do ban quản lý làm — không có đơn vị thứ hai
    để chuyển sang, nên hệ thống nói thẳng thay vì dựng một cái nút.
    """
    assert (
        huong_xu_ly(tool=tool, reject_code="SERVICE_UNAVAILABLE", dang_hoi_khach=False)
        is HuongXuLyTuChoi.TERMINAL_REVIEW
    )


# --------------------------------------------------- c. NO_AVAILABILITY
@pytest.mark.parametrize("tool", [CO_BAO_GIA, KHONG_BAO_GIA])
def test_no_availability_asks_for_a_field_when_it_knows_which(tool):
    """Có mapping ô cụ thể → hỏi lại ô ấy, KHÔNG đổi đơn vị.

    `schedule_move` hỏi ngày/giờ, `book_parking` hỏi khu. Đơn vị vẫn nhận việc,
    chỉ không nhận cấu hình ấy — và đổi ngày rẻ hơn đổi đơn vị.
    """
    assert co_o_de_hoi_lai(tool, "NO_AVAILABILITY")
    assert huong_xu_ly(tool=tool, reject_code="NO_AVAILABILITY", dang_hoi_khach=False) is HuongXuLyTuChoi.REPAIR_REQUEST


def test_no_availability_without_a_mapping_is_terminal_not_a_provider_swap():
    """Không có ô nào để hỏi → dừng lại, TUYỆT ĐỐI không tự đổi đơn vị.

    Đây là ô dễ sai nhất của cả bảng: `NO_AVAILABILITY` nghe như "hết chỗ, tìm
    chỗ khác". Nhưng nó nghĩa là "không nhận được cấu hình ấy", và trên một
    dịch vụ không có mapping thì hệ thống không biết cấu hình nào — nên nó cũng
    không biết đổi đơn vị có giúp gì không.
    """
    assert not co_o_de_hoi_lai(KHONG_CO_O_SUA, "NO_AVAILABILITY")
    assert (
        huong_xu_ly(tool=KHONG_CO_O_SUA, reject_code="NO_AVAILABILITY", dang_hoi_khach=False)
        is HuongXuLyTuChoi.TERMINAL_REVIEW
    )


# --------------------------------------------------- d. INVALID_REQUEST
@pytest.mark.parametrize("tool", [CO_BAO_GIA, KHONG_BAO_GIA, KHONG_CO_O_SUA])
def test_invalid_request_without_a_known_field_is_terminal(tool):
    """`repair` trả `supported_goal` khi nó KHÔNG biết ô nào hỏng.

    Đó là một lời thú nhận, không phải một ô nghiệp vụ. Nhận nó là "đã biết ô
    cần hỏi" sẽ biến mọi `INVALID_REQUEST` thành form hỏi lại cả yêu cầu, cho
    một lỗi khách không gây ra và không sửa được.

    Và tuyệt đối KHÔNG mặc định đổi đơn vị: "yêu cầu không hợp lệ" là lời nói
    về YÊU CẦU, không phải về đơn vị.
    """
    assert not co_o_de_hoi_lai(tool, "INVALID_REQUEST")
    assert (
        huong_xu_ly(tool=tool, reject_code="INVALID_REQUEST", dang_hoi_khach=False) is HuongXuLyTuChoi.TERMINAL_REVIEW
    )


def test_invalid_request_does_ask_when_the_field_is_knowable():
    """Nếu input mang một ô `repair` nhận ra → hỏi ô ấy.

    Luật viết theo "hệ thống có biết ô nào không", không theo một danh sách
    tool cứng — nên ngày `repair` biết thêm, chính sách tự đúng mà không phải
    sửa ở hai chỗ.
    """
    assert co_o_de_hoi_lai(KHONG_BAO_GIA, "INVALID_REQUEST", {"parking_zone": "ZONE_A"})
    assert (
        huong_xu_ly(
            tool=KHONG_BAO_GIA,
            reject_code="INVALID_REQUEST",
            dang_hoi_khach=False,
            task_input={"parking_zone": "ZONE_A"},
        )
        is HuongXuLyTuChoi.REPAIR_REQUEST
    )


# --------------------------------------------------- e. OTHER / lạ / None
@pytest.mark.parametrize("tool", [CO_BAO_GIA, KHONG_BAO_GIA, KHONG_CO_O_SUA])
@pytest.mark.parametrize("ma", ["OTHER", None, "", "MOT_MA_LA", "service_unavailable", "RESELECT"])
def test_anything_unrecognised_stops_and_says_so(tool, ma):
    """FAIL CLOSED.

    Mã lạ, `None`, chuỗi rỗng, hay một mã viết thường — tất cả dừng lại. Hướng
    ngược lại, mặc định "tìm đơn vị khác", sẽ gửi một yêu cầu sang một doanh
    nghiệp khác vì một lý do hệ thống không hiểu.

    `"service_unavailable"` viết thường có mặt ở đây có chủ ý: mã là allowlist
    ĐÓNG và phân biệt hoa thường, nên một biến thể chính tả không được mở cùng
    một cánh cửa.
    """
    assert huong_xu_ly(tool=tool, reject_code=ma, dang_hoi_khach=False) is HuongXuLyTuChoi.TERMINAL_REVIEW


# --------------------------------------------------- fail closed khi thêm mã
def test_every_canonical_code_is_classified_on_purpose():
    """Ma trận PHỦ HẾT `REJECT_CODES` — không mã nào rơi vào một nhánh vô tình.

    Bảng dưới đây là hợp đồng viết ra. Thêm một mã vào `REJECT_CODES` mà quên
    cập nhật chính sách sẽ làm bài kiểm này đỏ, và người thêm phải nói ra mã ấy
    dẫn tới đường nào — thay vì để nó lặng lẽ rơi về `TERMINAL_REVIEW` và không
    ai biết tính năng mới không hoạt động.
    """
    mong_doi = {
        "NO_AVAILABILITY": HuongXuLyTuChoi.REPAIR_REQUEST,
        "SERVICE_UNAVAILABLE": HuongXuLyTuChoi.RESELECT_PROVIDER,
        "INVALID_REQUEST": HuongXuLyTuChoi.TERMINAL_REVIEW,
        "OTHER": HuongXuLyTuChoi.TERMINAL_REVIEW,
    }
    assert set(REJECT_CODES) == set(mong_doi), (
        f"REJECT_CODES đổi mà chính sách chưa theo: {set(REJECT_CODES) ^ set(mong_doi)}"
    )
    for ma, huong in mong_doi.items():
        assert huong_xu_ly(tool=CO_BAO_GIA, reject_code=ma, dang_hoi_khach=False) is huong, ma


def test_the_three_outcomes_are_mutually_exclusive():
    """Mỗi ô của ma trận cho ĐÚNG một hướng — không có "vừa hỏi vừa đổi"."""
    thay = set()
    for tool in (CO_BAO_GIA, KHONG_BAO_GIA, KHONG_CO_O_SUA):
        for ma in (*REJECT_CODES, None, "LA"):
            for hoi in (True, False):
                huong = huong_xu_ly(tool=tool, reject_code=ma, dang_hoi_khach=hoi)
                assert isinstance(huong, HuongXuLyTuChoi)
                thay.add(huong)
    assert thay == set(HuongXuLyTuChoi), f"ma trận không chạm tới: {set(HuongXuLyTuChoi) - thay}"
