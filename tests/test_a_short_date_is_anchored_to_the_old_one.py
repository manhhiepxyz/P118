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
