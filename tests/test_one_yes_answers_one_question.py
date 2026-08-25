"""Một tiếng "có" trả lời MỘT câu hỏi, không phải mọi câu hỏi.

Đo được trên stack demo, dịch vụ chuyển nhà. Câu hỏi gộp ba ô:

    P-118: …có cần đăng ký thang máy hay không, có cần hỗ trợ bốc dỡ hay không
           và phương tiện chuyển nhà (none, van hoặc truck)?
    Bạn:   có

Kế hoạch ghi vào database:

    "needs_elevator": true,
    "needs_loading_support": true      ← người dùng chưa bao giờ nói câu này

Hai ô boolean cùng nhận nguyên một tiếng "có". Người dùng bị đăng ký một dịch
vụ họ không xin, và không có gì trên màn hình nói rằng điều đó vừa xảy ra.

`_extract_follow_up_answers` đã có đúng luật này cho ô VĂN BẢN TỰ DO — cả câu
chỉ được dùng khi nó là ô duy nhất đang hỏi. Ô boolean thiếu luật ấy: giá trị
của chúng ("có"/"không") không nuốt phần còn lại của câu, nên chúng bị xếp vào
nhóm "dấu hiệu mạnh". Nhưng dấu hiệu mạnh nói được GIÁ TRỊ, không nói được nó
thuộc về Ô NÀO — và khi có hai ô cùng loại thì đó mới là câu hỏi cần trả lời.

Cách xử lý: gán cho ô boolean ĐẦU TIÊN trong danh sách đang hỏi, các ô boolean
còn lại để nguyên chưa trả lời. Lượt sau chỉ còn một ô boolean nên "có" lúc đó
không còn mơ hồ nữa. Không đoán hộ ô nào cả, và vẫn tiến được.
"""

import pytest

from src.api.routes import _extract_follow_up_answers

BA_O_CHUYEN_NHA = ["needs_elevator", "needs_loading_support", "move_vehicle"]


@pytest.mark.parametrize("cau", ["có", "vâng", "ừ", "đúng rồi", "ok"])
def test_a_bare_yes_does_not_fill_two_boolean_fields(cau):
    answers, unresolved = _extract_follow_up_answers(cau, list(BA_O_CHUYEN_NHA))
    da_bat = [o for o in ("needs_elevator", "needs_loading_support") if o in answers]
    assert len(da_bat) <= 1, (
        f"{cau!r} là một câu trả lời, mà bật {da_bat} — ô thứ hai là một quyết định người dùng chưa đưa ra"
    )
    assert "needs_loading_support" in unresolved, (
        f"{cau!r} chưa trả lời ô bốc dỡ, nên ô đó phải được hỏi lại; unresolved={unresolved}"
    )


@pytest.mark.parametrize("cau", ["không", "khỏi", "không cần"])
def test_a_bare_no_does_not_answer_two_boolean_fields_either(cau):
    """Phủ định cũng là một quyết định. "Không" một lần không tắt hai dịch vụ."""
    answers, unresolved = _extract_follow_up_answers(cau, list(BA_O_CHUYEN_NHA))
    da_tra = [o for o in ("needs_elevator", "needs_loading_support") if o in answers]
    assert len(da_tra) <= 1, f"{cau!r} trả lời hộ cả {da_tra}"
    assert "needs_loading_support" in unresolved


def test_the_last_boolean_standing_alone_is_answered_normally():
    """Còn MỘT ô boolean thì "có" hết mơ hồ — phải nhận, nếu không sẽ kẹt vòng lặp."""
    answers, unresolved = _extract_follow_up_answers("có", ["needs_loading_support", "move_vehicle"])
    assert answers.get("needs_loading_support") is True, (
        f"chỉ còn một ô boolean mà vẫn không nhận: answers={answers} unresolved={unresolved}"
    )


def test_a_single_boolean_question_is_untouched():
    for cau, mong in (("có", True), ("không", False)):
        answers, _ = _extract_follow_up_answers(cau, ["needs_elevator"])
        assert answers.get("needs_elevator") is mong, f"{cau!r} → {answers}"


def test_other_kinds_of_field_still_come_out_of_the_same_sentence():
    """Luật này chỉ chạm ô boolean. Ngày, giờ, enum vẫn rút chung một câu."""
    answers, _ = _extract_follow_up_answers(
        "ngày 2026-08-31 lúc 08:00 đi van", ["move_date", "move_time", "move_vehicle"]
    )
    assert answers.get("move_date") == "2026-08-31"
    assert answers.get("move_time") == "08:00"
    assert answers.get("move_vehicle") == "van"


# --------------------------------------------------------------------------
# Cùng một lớp lỗi, ở kiểu field khác
# --------------------------------------------------------------------------
#
# Luật "một boolean" ở trên chưa đủ, vì "không" không chỉ là một boolean. Đo
# được trên stack demo ngay sau khi vá luật ấy:
#
#     P-118: …có cần hỗ trợ bốc dỡ hay không và phương tiện chuyển nhà
#            (none, van hoặc truck)?
#     Bạn:   không
#     →      "needs_loading_support": false,
#            "move_vehicle": "none"        ← chưa bao giờ được nói ra
#
# `move_vehicle` là enum, và một trong các giá trị của nó ĐƯỢC ĐÁNH VẦN bằng
# đúng từ ấy ("khong" → "none"). Nên cùng một tiếng đáp lại một câu hỏi lại
# đóng luôn một câu hỏi khác — lần này là "chuyển nhà mà không cần xe".
#
# Luật đúng rộng hơn kiểu dữ liệu: khi cả câu KHÔNG CÓ GÌ ngoài một tiếng
# có/không, nó là câu trả lời cho ĐÚNG MỘT ô. Ô nào nhận trước thì thôi.

CAU_TRONG_TRON = ["có", "không", "vâng", "khỏi", "ừ", "ok", "không ạ", "có nhé"]


def test_a_bare_token_answers_exactly_one_field_whatever_its_type():
    for cau in CAU_TRONG_TRON:
        answers, _ = _extract_follow_up_answers(cau, ["needs_loading_support", "move_vehicle"])
        assert len(answers) <= 1, (
            f"{cau!r} là một tiếng đáp, mà đóng {sorted(answers)} — ô thứ hai là quyết định người dùng chưa đưa ra"
        )


def test_the_moving_vehicle_is_never_decided_by_a_yes_or_no_meant_for_something_else():
    answers, unresolved = _extract_follow_up_answers("không", ["needs_loading_support", "move_vehicle"])
    assert answers.get("needs_loading_support") is False
    assert "move_vehicle" not in answers, f"xe chuyển nhà bị chốt bằng {answers!r}"
    assert "move_vehicle" in unresolved


def test_a_vehicle_asked_on_its_own_still_takes_the_word():
    """Chỉ còn ô xe thì "không" đúng là "không cần xe" — phải nhận."""
    answers, _ = _extract_follow_up_answers("không", ["move_vehicle"])
    assert answers.get("move_vehicle") == "none", answers


def test_a_sentence_with_real_content_is_not_a_bare_token():
    """Câu có nội dung thật vẫn rút được nhiều ô như cũ.

    (`move_vehicle` không rút được từ câu ghép — đó là hành vi sẵn có của bộ
    đọc enum, không phải thứ luật này đụng tới. Bài kiểm chỉ khoá phần luật
    này CÓ thể làm hỏng: ngày và giờ vẫn phải cùng ra từ một câu.)
    """
    answers, _ = _extract_follow_up_answers(
        "ngày 2026-08-31 lúc 08:00 đi truck", ["move_date", "move_time", "move_vehicle"]
    )
    assert answers.get("move_date") == "2026-08-31"
    assert answers.get("move_time") == "08:00"
