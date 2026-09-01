"""Một hành động, một chỗ: workspace làm, Lịch sử chỉ đọc.

Luật
----
Mọi thao tác resume/fallback — dừng, trả lời câu hỏi đang treo, duyệt khoản
thanh toán — sống ở workspace. Trang chi tiết trong Lịch sử là màn hình ĐỌC.
Ngoại lệ duy nhất: hai nút gửi yêu cầu hỗ trợ trên thẻ kết quả, và chúng không
đụng vào workflow — chúng mở một hồ sơ cho đơn vị.

Vì sao
------
Trang chi tiết từng có năm thao tác, và mỗi thao tác là một đường thứ hai tới
cùng một quyết định. Đường thứ hai lệch được, và chỗ lệch đắt nhất nằm ngay
trên khoản tiền: hai nút "Xác nhận thanh toán" ở hai màn hình, hai lần nạp dữ
liệu khác nhau, không có gì bảo đảm chúng nói cùng một con số.

Cái bẫy đi kèm — và nó là lý do có test cuối cùng
-------------------------------------------------
Lịch sử chỉ trỏ tới `/workflow/{id}`; không có đường nào quay lại workspace. Gỡ
các nút mà không mở lối này thì mọi yêu cầu đang dở mở ra từ Lịch sử đều thành
ngõ cụt: đọc được, không làm gì được, không có chỗ nào để đi tiếp.

Frontend không có hạ tầng test, nên kiểm bằng cách đọc file TSX — cùng kỹ thuật
`tests/test_every_refusal_carries_a_cause.py` đã dùng.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_GOC = Path(__file__).resolve().parents[1] / "frontend" / "src"
_LICH_SU = _GOC / "pages" / "WorkflowPage.tsx"
_WORKSPACE = _GOC / "pages" / "JourneyWorkspacePage.tsx"


def _ngoai_ghi_chu(text: str) -> str:
    """Bỏ comment — chúng NÓI VỀ code đã gỡ, không phải code."""
    out, i = [], 0
    while i < len(text):
        for mo, dong in (("{/*", "*/}"), ("/*", "*/")):
            if text.startswith(mo, i):
                ket = text.find(dong, i)
                i = len(text) if ket < 0 else ket + len(dong)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _lich_su() -> str:
    return _ngoai_ghi_chu(_LICH_SU.read_text(encoding="utf-8"))


# Thao tác đổi TRẠNG THÁI của một workflow. Tên hàm API là hợp đồng ổn định
# hơn nhãn nút — nhãn đổi theo câu chữ, hàm thì không.
_THAO_TAC = ("cancelWorkflow", "decidePayment", "continueWorkflow")


@pytest.mark.parametrize("thao_tac", _THAO_TAC)
def test_the_history_page_never_changes_a_workflow(thao_tac: str):
    assert thao_tac not in _lich_su(), f"trang Lịch sử vẫn tự quyết: {thao_tac}"


@pytest.mark.parametrize("thao_tac", _THAO_TAC)
def test_the_workspace_still_has_every_action(thao_tac: str):
    """Gỡ khỏi Lịch sử chỉ đúng khi workspace CÓ. Nếu không là gỡ hẳn."""
    assert thao_tac in _WORKSPACE.read_text(encoding="utf-8"), f"workspace thiếu {thao_tac}"


def test_an_unfinished_request_has_a_way_back_to_the_workspace():
    """Không có lối này thì luật trên biến Lịch sử thành ngõ cụt."""
    code = _lich_su()

    assert "/workspace?w=" in code, "yêu cầu đang dở mở từ Lịch sử không có chỗ nào để đi tiếp"
    assert "!TERMINAL_STATUSES.has(data.status)" in code, "lối quay lại không gắn với việc yêu cầu còn dở"


def test_the_way_back_uses_the_parameter_the_workspace_already_reads():
    """`?w=` là tham số workspace đã dùng để khôi phục sau khi tải lại trang.

    Dựng một tham số thứ hai cho cùng một việc là hai đường có thể lệch nhau.
    """
    ws = _WORKSPACE.read_text(encoding="utf-8")

    assert "const WORKFLOW_PARAM = 'w'" in ws, "workspace đổi tên tham số mà lối quay lại không đổi theo"


def test_the_quote_is_still_readable_from_history():
    """Gỡ NÚT bấm, không gỡ CON SỐ. Khách vẫn phải xem lại được khoản phải trả."""
    assert "data-quote-amount" in _lich_su(), "gỡ luôn báo giá khỏi trang Lịch sử"


def test_the_result_card_shows_the_thing_the_customer_holds():
    """Bước xong CUỐI là `pay_fee` — một biên lai, không phải chỗ đỗ.

    Đo được trên browser: một chỗ đỗ xe đã đặt và trả tiền xong hiện ra là
    "Thanh toán phí thành công · Mã thanh toán · Trạng thái PAID" — không ngày,
    không khu, và KHÔNG có hai nút Đổi/Huỷ. Cái khách giữ trong tay là chỗ đỗ,
    không phải biên lai của nó.

    Chọn theo DỮ KIỆN (`Thời gian`) chứ không theo tên tool: cùng một luật với
    `ResultSummary` khi nó quyết định dựng thẻ hẹn hay danh sách trơn.
    """
    code = _lich_su()

    assert "d.label === 'Thời gian'" in code, "thẻ kết quả vẫn lấy bước cuối cùng, tức biên lai"
    # Đường lui cho dịch vụ KHÔNG có mốc hẹn (đăng ký tư vấn, tìm bất động sản):
    # không có bước nào mang `Thời gian` thì vẫn phải còn MỘT thẻ, dựng từ bước
    # xong cuối cùng. Trước đây viết là `successWithDetails[... .length - 1]`;
    # giờ là `.slice(-1)` vì `resultTasks` đã thành một MẢNG — một thẻ cho mỗi
    # buổi hẹn, xem `test_every_appointment_gets_its_own_card`.
    assert "successWithDetails.slice(-1)" in code, (
        "không còn đường lui cho dịch vụ KHÔNG có mốc hẹn — chúng sẽ mất luôn thẻ kết quả"
    )
