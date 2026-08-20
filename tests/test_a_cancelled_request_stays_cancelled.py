"""Dừng phải có nghĩa là dừng — nhưng chỉ với phần CHƯA chạy.

Ba điều kiện, đo trên dữ liệu thật:

  1. Bước đã xong thì giữ.   `CANCELLED` giữ nguyên 54 bước SUCCESS; chỉ 33
                             bước dở dang chuyển sang CANCELLED.
  2. Chat vẫn trả lời.       Câu hỏi đi đường của nó, không tạo bước nào.
  3. Chỉ chạy lại khi được YÊU CẦU chạy lại.

Điều 3 từng hỏng, và hỏng ở một chỗ không nhìn thấy được từ giao diện: ký ức
hội thoại. Ký ức được đọc TRƯỚC khi Planner chạy và dùng để hiểu một câu nói
cụt. Giữ lại trong đó một yêu cầu vừa bị huỷ nghĩa là câu cụt nào cũng có thể
được dựng lại thành chính yêu cầu ấy.

Đo được nguyên văn:

    Bạn:   đặt lịch tham quan Vinhomes Green Paradise…
    P-118: Mình đã dừng yêu cầu này.
    Bạn:   a
    P-118: Mình cần thêm chút thông tin để đặt lịch tham quan Vinhomes
           Green Paradise…

Gõ một ký tự vô nghĩa và nhận lại việc vừa chủ động huỷ. Sau bản vá, cùng thao
tác cho `a` và `zzz`: cả hai tạo 0 bước.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from src.db import workflow_repository


def test_the_planner_is_not_told_about_cancelled_requests() -> None:
    """Lọc ở phía NGƯỜI ĐỌC, không lọc ở nguồn.

    Bản vá đầu lọc `CANCELLED` ngay trong truy vấn. Nó chặn được việc chạy
    lại, nhưng cắt luôn ngữ cảnh hội thoại — xem
    `test_the_answer_layer_still_knows_what_was_being_discussed`. Nguồn giữ
    đủ; mỗi bên tự bỏ phần không thuộc về mình.
    """
    from src.api import routes

    source = inspect.getsource(routes)
    assert 'if not turn.get("_da_huy")' in source, (
        "Planner vẫn nhận lượt đã huỷ — nó sẽ dựng lại yêu cầu ấy từ một câu "
        "nói cụt bất kỳ, và bấm Dừng không dừng được gì"
    )

    query = inspect.getsource(workflow_repository.WorkflowRepository.recent_turns_for_owner)
    assert "w.status <> 'CANCELLED'" not in query, (
        "lọc ở nguồn thì tầng trả lời cũng mất ngữ cảnh, và P-118 hỏi lại "
        "'bạn đang dùng dịch vụ nào?' ngay sau khi vừa nói về nó"
    )
    assert "w.status," in query, "không mang trạng thái theo thì không bên nào lọc được"


def test_memory_still_remembers_everything_else() -> None:
    """Loại yêu cầu đã huỷ không được kéo theo phần còn lại.

    Ký ức là thứ làm câu hỏi hay hơn ("vẫn khu A như lần trước phải không?").
    Cắt quá tay thì mọi câu hỏi lại quay về hỏi trống.
    """
    source = inspect.getsource(workflow_repository.WorkflowRepository.recent_turns_for_owner)
    assert "w.status <> 'FAILED'" not in source, "yêu cầu hỏng vẫn là ngữ cảnh — người dùng thường sửa rồi thử lại"
    assert "w.status <> 'SUCCESS'" not in source, "cắt mất chính phần ký ức có giá trị nhất"
    assert "w.goal IS NOT NULL" in source, "mất điều kiện gốc — sẽ nhặt cả workflow không có câu nào"


def test_the_ui_shows_nothing_pending_for_a_cancelled_request() -> None:
    """Thẻ chờ của một yêu cầu đã dừng là một lời mời chạy lại nó."""
    live = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "liveJourney.ts"
    source = live.read_text(encoding="utf-8")
    body = source[source.index("export function pendingFromWorkflow") :]
    body = body[: body.index("\nexport ", 1) if "\nexport " in body[1:] else len(body)]
    assert "if (res.status === 'CANCELLED') return null" in body, (
        "yêu cầu đã huỷ vẫn hiện thẻ chờ; câu tiếp theo người dùng gõ sẽ bị "
        "đọc là câu TRẢ LỜI cho thẻ đó"
    )


def test_the_answer_layer_still_knows_what_was_being_discussed() -> None:
    """Loại khỏi Planner, KHÔNG loại khỏi hội thoại.

    Bản vá đầu lọc `CANCELLED` ngay trong truy vấn ký ức. Nó chặn được việc
    chạy lại, nhưng cắt luôn thứ cần giữ: người dùng huỷ một lịch tham quan
    rồi hỏi "tôi muốn đổi dịch vụ", và P-118 hỏi lại "bạn đang dùng dịch vụ
    nào?" — nó không còn biết vừa nói chuyện gì.

    Hai bên đọc ký ức vì hai lý do:
        Planner       cần biết NÊN LÀM GÌ  → bỏ
        tầng trả lời  cần biết ĐANG NÓI GÌ → giữ, kèm ghi chú đã huỷ
    """
    from src.api.routes import _recent_turns_view

    phien = "33333333-3333-3333-3333-333333333333"
    ky_uc = [
        {
            "_session_id": phien,
            "_da_huy": True,
            "_trang_thai": "CANCELLED",
            "ban_da_noi": "đặt lịch tham quan Vinhomes Pearl Bay",
            "p118_da_tra_loi": "Mình đã huỷ yêu cầu.",
        }
    ]
    turns = _recent_turns_view(ky_uc, phien)
    assert turns, "lượt đã huỷ bị loại khỏi hội thoại — model mất ngữ cảnh vừa nói"
    assert "huỷ" in turns[0].get("ghi_chu", ""), (
        "giữ mà không nói rõ đã huỷ thì model đọc nó như việc đang chạy và đi "
        "tiếp theo hướng đó"
    )

    # Và không chỉ CANCELLED: model cần biết việc nào ĐANG DỞ, nếu không nó
    # hỏi ngược lại chính điều người dùng vừa làm. Đo được: hội thoại có đủ 3
    # lượt cùng phiên, model vẫn hỏi "mình cần biết dịch vụ hiện tại".
    dang_do = _recent_turns_view(
        [{"_session_id": phien, "_trang_thai": "WAITING_APPROVAL", "ban_da_noi": "đặt chỗ đỗ xe"}],
        phien,
    )
    assert "ĐANG DỞ" in dang_do[0].get("ghi_chu", ""), (
        "lượt đang chờ không được đánh dấu — model không biết việc nào còn treo"
    )


def test_the_planner_never_sees_a_cancelled_turn() -> None:
    source = inspect.getsource(__import__("src.api.routes", fromlist=["x"]))
    assert 'if not turn.get("_da_huy")' in source, (
        "Planner vẫn nhận lượt đã huỷ — nó sẽ dựng lại yêu cầu ấy từ một câu "
        "nói cụt bất kỳ"
    )
