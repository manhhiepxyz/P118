""" "ngày 25" phải được hiểu, y như "25/08/2026".

Chuỗi thật, ngay sau khi thẻ thanh toán thôi che câu hỏi
--------------------------------------------------------
    P-118:  Khung giờ 10:30 ngày 2026-08-24 đã kín lịch. Bạn chọn giờ hoặc
            ngày khác giúp mình nhé.
    Bạn:    đổi qua ngày 25
    P-118:  Ngày tham quan chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.

Câu ấy KHÔNG sai. Người dùng nói tắt vì tháng và năm đã nằm trong chính câu hỏi
họ vừa đọc — hệ thống hỏi về ngày 24/08, nên "ngày 25" chỉ có thể là 25/08.

Bộ neo đã có sẵn (`rewrite_relative_dates`) và đang chạy ở lane sửa yêu cầu.
Đường `/continue` thì không dùng, nên cùng một câu được hiểu ở chỗ này và bị từ
chối ở chỗ kia.

Vì sao neo vào GIÁ TRỊ CŨ chứ không vào hôm nay: câu hỏi nói về ngày 24/08, nên
"ngày 25" thuộc về tháng ấy. Neo vào hôm nay thì một lịch đặt cho tháng sau sẽ
bị kéo ngược về tháng này — và với một lịch đã đặt xa, đó là ngày trong quá khứ.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.api.routes import _extract_follow_up_answers


@pytest.mark.parametrize(
    ("cau", "mong_doi"),
    [
        ("đổi qua ngày 25", "2026-08-25"),
        ("ngày 25", "2026-08-25"),
        ("cho tôi 25/8", "2026-08-25"),
        # Dạng đầy đủ vẫn phải đúng — bộ neo không được đụng vào nó.
        ("2026-09-03", "2026-09-03"),
        ("03/09/2026", "2026-09-03"),
    ],
)
def test_a_bare_day_borrows_the_month_from_the_question(cau: str, mong_doi: str):
    answers, unresolved = _extract_follow_up_answers(
        cau, ["viewing_date"], anchor="2026-08-24", today=date(2026, 8, 23)
    )

    assert answers.get("viewing_date") == mong_doi, f"{cau!r} → {answers} / chưa đọc được: {unresolved}"


@pytest.mark.parametrize("hom_nay", [date(2020, 1, 1), date(2026, 8, 23), date(2031, 6, 15)])
def test_the_injected_clock_governs_the_whole_read(hom_nay: date):
    """`today` phải chi phối CẢ phép kiểm "ngày này đã qua chưa".

    Hợp đồng: người gọi đưa `today` nào thì lượt đọc ấy sống trong ngày ấy —
    không đọc đồng hồ tường ở bất kỳ bước nào.

    Trước bản vá, `today` chỉ tới được bộ viết lại ngày nói tắt; phép kiểm quá
    khứ bên trong `parse_field` vẫn gọi `date.today()`. Hậu quả đo được: ba bài
    ngay trên đỏ vào đúng ngày 28/08/2026 và xanh trở lại vào tháng sau — một bộ
    kiểm nói về hôm nay chứ không nói về mã.
    """
    # Ngày 28 nằm SAU cả ba mốc `hom_nay`, nên bộ neo không phải đẩy sang kỳ kế
    # tiếp — bài này đo ĐỒNG HỒ, không đo luật "đừng nhận ngày đã qua".
    neo = hom_nay.replace(day=10).isoformat()
    mong_doi = hom_nay.replace(day=28).isoformat()

    answers, unresolved = _extract_follow_up_answers("ngày 28", ["viewing_date"], anchor=neo, today=hom_nay)

    assert answers.get("viewing_date") == mong_doi, f"{hom_nay} → {answers} / {unresolved}"


def test_a_date_already_past_for_the_injected_clock_is_refused():
    """Nửa còn lại: đồng hồ đưa vào cũng phải LOẠI được, không chỉ nhận.

    Không có bài này thì một bản vá "bỏ hẳn phép kiểm quá khứ" cũng làm mọi bài
    trên xanh — và hệ thống nhận lịch cho ngày hôm qua.

    Dùng ngày ĐẦY ĐỦ chứ không phải ngày nói tắt: với ngày nói tắt, bộ neo cố ý
    đẩy sang kỳ kế tiếp thay vì loại ("ngày 5" gõ hôm 21/8 nghĩa là 5/9). Chỉ
    một ngày viết đủ mới đi thẳng tới phép kiểm quá khứ.
    """
    answers, unresolved = _extract_follow_up_answers(
        "2026-08-12", ["viewing_date"], anchor="2026-08-10", today=date(2026, 8, 20)
    )

    assert answers.get("viewing_date") is None, answers
    assert unresolved == ["viewing_date"], unresolved


def test_the_clock_is_put_back_after_the_read():
    """Đồng hồ đặt cho MỘT lượt đọc, không rò sang lượt sau.

    `dat_ngay_hom_nay` đặt rồi phải trả lại. Không trả lại thì lượt đọc kế tiếp
    — một request khác, của một người khác — sống trong ngày của lượt trước, và
    nó nhận hoặc loại lịch theo một hôm nay không có thật.

    Đo bằng chính `hom_nay()` chứ không bằng một ngày cứng: bài này phải đúng ở
    mọi ngày chạy.
    """
    from src.common import field_parsers

    _extract_follow_up_answers("ngày 28", ["viewing_date"], anchor="2020-01-10", today=date(2020, 1, 1))

    assert field_parsers.hom_nay() == date.today(), "đồng hồ của lượt trước còn nằm lại"


def test_without_an_anchor_nothing_changes():
    """Không có giá trị cũ thì giữ nguyên hành vi cũ — không đoán bừa."""
    answers, unresolved = _extract_follow_up_answers("2026-09-03", ["viewing_date"])

    assert answers.get("viewing_date") == "2026-09-03"
    assert unresolved == []


def test_the_anchor_never_invents_a_date_from_nothing():
    """Câu không có ngày nào thì vẫn là "chưa đọc được", không phải ngày neo."""
    answers, unresolved = _extract_follow_up_answers(
        "tôi chưa quyết định", ["viewing_date"], anchor="2026-08-24", today=date(2026, 8, 23)
    )

    assert answers == {}
    assert unresolved == ["viewing_date"]


def test_a_date_far_ahead_keeps_its_own_month():
    """Lịch đặt tháng 12 mà nói "ngày 5" thì là 05/12, không phải 05 tháng này."""
    answers, _unresolved = _extract_follow_up_answers(
        "ngày 5", ["viewing_date"], anchor="2026-12-20", today=date(2026, 8, 23)
    )

    assert answers.get("viewing_date") == "2026-12-05", answers


# --- phía giao diện: một câu, một bong bóng ---------------------------------


def test_an_agent_line_is_never_said_twice():
    """Đo được nguyên văn, hai bong bóng liền nhau y hệt:

        P-118: Ngày tham quan chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.
        P-118: Ngày tham quan chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.

    Câu lỗi 422 đi qua `say`, backend ghim đúng câu ấy vào `question`, rồi nhịp
    poll kế tiếp đọc lên và `sayOnce` nói lại — vì bộ nhớ chống lặp chỉ nằm
    trong `sayOnce`, còn `say` thì không ghi vào.

    Lời NGƯỜI DÙNG phải được miễn: họ có quyền nói cùng một câu hai lần.
    """
    from pathlib import Path

    code = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "JourneyWorkspacePage.tsx").read_text(
        encoding="utf-8"
    )
    than = code[code.index("function say(from:") :][:1400]

    assert "said.current.add(text)" in than, "`say` không ghi vào bộ nhớ chống lặp"
    assert "from === 'agent'" in than, "bóp cả lời người dùng, không chỉ lời agent"
