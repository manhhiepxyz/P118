"""Trang chi tiết trong Lịch sử KHÔNG mời người dùng gõ một câu không dẫn tới đâu.

Lỗi đo được trên chuỗi thật
---------------------------
Khách mở một lịch tham quan đã hoàn tất, gõ vào ô chat ở cuối trang:

    Bạn:    tôi muốn đổi ngày tham quan sang ngày 28/8
    P-118:  Bạn muốn đổi ngày tham quan sang 28/8 nhé. Mình cần biết mục tiêu
            của bạn là gì để hỗ trợ chính xác hơn.

Câu ấy được HIỂU đúng — hệ thống nhắc lại chính xác cả ngày. Nhưng ô chat đó gọi
`startWorkflow`, tức mở một YÊU CẦU MỚI, và không đường nào từ đó sửa được một
việc đã xong. Đo bằng cách gọi thẳng từng cổng: bộ đọc tất định rút ra đúng
`viewing_date=2026-08-28`, rồi `_amend_target` trả `None` vì workflow đã
`SUCCESS` — câu của khách chưa bao giờ được dùng tới.

Một ô nhập không dẫn tới đâu tệ hơn là không có ô nào: người dùng gõ, được nhắc
lại đúng ý mình, và kết luận là hệ thống sắp làm — trong khi không có gì xảy ra.

Việc sửa một yêu cầu đã hoàn tất đi bằng NÚT có tên rõ ràng, không bằng câu chữ.

Lịch sử của ô nhập ấy — chép lại từ `test_a_question_does_not_start_a_task.py`
-----------------------------------------------------------------------------
Ô đó đã được sửa hai lần, mỗi lần vì một lỗi thật, và cả hai lỗi giờ không còn
áp dụng vì chính ô đó đã biến mất. Chép lại để không ai dựng lại nó rồi vấp
đúng hai lần ấy:

  ① Bản đầu gọi `startWorkflow` rồi `navigate` NGAY, không xét kết quả. Nên mọi
    câu — kể cả "hôm nay là ngày mấy" hay "cảm ơn" — đều tạo một yêu cầu mới và
    thay màn hình. Đo được: đang xem một hành trình có bước, gõ một câu hỏi, màn
    hình nhảy sang một yêu cầu 0 bước và hành trình cũ biến mất. Bản vá: chỉ
    chuyển trang khi lượt mới THẬT SỰ có kế hoạch, và vòng chờ có trần.

  ② Lời người dùng chỉ xuất hiện SAU khi Planner xong. Trong lúc chờ, textarea
    còn nguyên và nút đổi trạng thái — nhìn đúng như tin nhắn chưa được gửi, nên
    họ gửi lại. Bản vá: dựng bong bóng của họ trước round-trip.

Vì sao kiểm ở đây
-----------------
Frontend không có hạ tầng test (0 file `*.test.*`). Cùng kỹ thuật mà
`tests/test_every_refusal_carries_a_cause.py` và `test_frontend_error_messages.py`
đã dùng: đọc file TSX. Thô, nhưng nó chặn đúng cái cách đã hỏng.
"""

from __future__ import annotations

from pathlib import Path

_TRANG = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "WorkflowPage.tsx"


def _nguon() -> str:
    return _TRANG.read_text(encoding="utf-8")


def _khong_phai_ghi_chu(text: str) -> str:
    """Bỏ mọi comment — ghi chú NÓI VỀ code cũ, không phải code."""
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


def test_the_history_page_never_starts_a_new_request_from_free_text():
    """Đây là lỗi được báo."""
    code = _khong_phai_ghi_chu(_nguon())

    assert "startWorkflow(" not in code, "trang chi tiết vẫn mở yêu cầu mới từ ô chat"


def test_no_reply_box_is_left_at_all():
    """Không còn ô nhập nào — kể cả ô trả lời câu hỏi sửa lỗi.

    Bản trước giữ lại đúng một ô cho câu hỏi đang treo. Luật sau đó thắt chặt
    hơn: mọi thao tác resume/fallback sống ở workspace, Lịch sử chỉ đọc. Hai
    chỗ cùng trả lời một câu hỏi là hai chỗ có thể lệch nhau — và workspace còn
    có biểu mẫu chọn giá trị thay vì bắt gõ tay.

    Xem `tests/test_history_reads_workspace_acts.py` cho luật đầy đủ.
    """
    code = _khong_phai_ghi_chu(_nguon())

    assert "<ClarificationReply" not in code, "trang Lịch sử vẫn có ô nhập"


def test_a_pending_question_is_still_readable():
    """ "Chỉ đọc" nghĩa là bỏ NÚT, không bỏ THÔNG TIN.

    Khách mở Lịch sử ra đúng để biết việc của mình đang vướng ở đâu. Giấu câu
    hỏi đi thì nút sang workspace không nói được nó dẫn tới việc gì.
    """
    code = _khong_phai_ghi_chu(_nguon())

    assert "{data.question}" in code, "gỡ luôn câu hỏi đang treo khỏi trang Lịch sử"


def test_the_conversation_transcript_is_gone():
    """Bong bóng hội thoại lặp lại đúng thứ trang workspace đã hiện."""
    code = _khong_phai_ghi_chu(_nguon())

    for dau_vet in ('data-turn="user"', 'data-turn="agent"', "data-session-turn"):
        assert dau_vet not in code, f"trang chi tiết vẫn dựng hội thoại: {dau_vet}"
