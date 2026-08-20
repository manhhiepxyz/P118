"""Màn hành trình chỉ được mở khi ĐÃ BIẾT có hành trình.

Gõ một câu ở màn khởi động thì chưa biết nó là gì: có thể là một yêu cầu, cũng
có thể chỉ là một câu hỏi. Planner mất 20–120 giây mới phân loại xong. Chuyển
cảnh ngay nghĩa là suốt quãng đó màn hành trình treo tiêu đề "Đang chuẩn bị…"
— hứa một kế hoạch có thể không bao giờ tồn tại.

Đo được: gõ "tôi muốn đổi dịch vụ", màn hành trình hiện 26 giây với 0 bước và
tiêu đề "Đang chuẩn bị…", rồi kết thúc bằng một câu trả lời. Không tác vụ nào
chạy — chỉ là giao diện nói sai chuyện đang xảy ra.

Sau bản vá:

    câu hỏi  → không đổi màn, câu đáp về sau 4s
    yêu cầu  → đổi màn khi kế hoạch có thật, có bước sau 6s
"""

from __future__ import annotations

from pathlib import Path

_PAGE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "JourneyWorkspacePage.tsx"


def test_free_text_does_not_switch_screens_on_its_own() -> None:
    page = _PAGE.read_text(encoding="utf-8")
    assert "if (provisional.current.length > 0) setMode('journey')" in page, (
        "vẫn đổi màn ngay khi gửi — câu hỏi nào cũng bị trình bày như một "
        "hành trình đang được chuẩn bị"
    )


def test_a_real_plan_does_switch_screens() -> None:
    """Chọn dịch vụ từ danh sách thì biết chắc có kế hoạch; và khi kế hoạch
    thật về thì phải mở màn hành trình, nếu không người dùng chạy một tác vụ
    mà không bao giờ nhìn thấy nó."""
    page = _PAGE.read_text(encoding="utf-8")
    absorb = page[page.index("function absorb(") :]
    absorb = absorb[: absorb.index("\n  function ", 1)]
    assert "res.plan.length > 0" in absorb and "setMode('journey')" in absorb, (
        "kế hoạch có thật mà không mở màn hành trình"
    )


def test_the_conversation_is_visible_in_both_screens() -> None:
    """Không đổi màn KHÔNG được đồng nghĩa với không thấy gì.

    Hội thoại từng bị buộc vào chế độ hành trình. Bản vá đầu của tôi giữ người
    dùng ở màn khởi động cho một câu hỏi — và họ gõ xong thì không thấy gì cả:
    cả câu của họ lẫn câu trả lời đều nằm trong `turns` mà không được vẽ. Đo
    được: 40 giây sau vẫn không có chữ nào của lượt đó trên màn hình.
    """
    page = _PAGE.read_text(encoding="utf-8")
    assert "const talking = turns.length > 0" in page, (
        "mất khái niệm 'đang có hội thoại' — nó là thứ tách khung trang khỏi "
        "sân khấu, và trộn hai thứ đó lại là gốc của cả loạt lỗi"
    )
    assert "{(mode === 'journey' || talking) && (" in page, (
        "hội thoại chỉ vẽ ở màn hành trình — gõ ở màn khởi động sẽ không thấy gì"
    )


def test_the_service_menu_does_not_come_back_mid_conversation() -> None:
    """Ở lại hội thoại KHÔNG được đọc thành "về trang chủ".

    Bản vá đầu của tôi gọi `setMode('launcher')` để rời màn hành trình. Nhưng
    `launcher` kéo theo cả BẢNG DỊCH VỤ: sau khi huỷ một yêu cầu rồi gõ tiếp,
    danh sách năm dịch vụ ập trở lại phía trên và đẩy hội thoại xuống đáy.
    Người dùng đọc thành "chat đổi dịch vụ sao lại văng ra trang chủ" — dù câu
    của họ vẫn còn nguyên bên dưới.

    Ba trạng thái, không phải hai:

        chưa nói gì  → bảng dịch vụ
        đang nói     → hội thoại, bảng dịch vụ LÙI đi
        có kế hoạch  → canvas hành trình
    """
    page = _PAGE.read_text(encoding="utf-8")
    assert "{mode === 'journey' || talking ? null : (" in page, (
        "bảng dịch vụ vẫn hiện khi đang có hội thoại"
    )
    assert "{mode === 'journey' && (\n                <JourneyCanvas" in page, (
        "canvas hành trình không còn gắn với đúng trạng thái của nó"
    )


def test_the_page_frame_does_not_change_when_you_chat() -> None:
    """Cột phải là KHUNG TRANG, không phải nội dung của một chế độ.

    Buộc nó vào `mode` thì gõ một câu chat làm cả cột 360px biến mất — trang
    đổi hình dạng, và người dùng đọc thành "bị chuyển sang trang khác". Một
    câu chat không được quyền đổi bố cục ứng dụng.
    """
    page = _PAGE.read_text(encoding="utf-8")
    assert "{(mode === 'journey' || talking) && (\n            <aside" in page, (
        "cột phải vẫn gắn cứng vào chế độ hành trình; gõ một câu là nó biến mất"
    )


def test_an_empty_right_column_says_so_instead_of_drawing_a_blank_panel() -> None:
    """`JourneySummary` với 0 chặng vẽ một khung có tiêu đề và không có gì bên
    dưới — đọc như một hành trình đã hỏng, chứ không như "chưa bắt đầu"."""
    page = _PAGE.read_text(encoding="utf-8")
    assert "steps.length === 0 && !pending ?" in page, "cột phải rỗng vẫn dựng bảng tóm tắt trống"
    assert "Chưa có hành trình nào đang chạy" in page, "không nói gì khi chưa có hành trình"


def test_nothing_in_the_frame_depends_on_mode_alone() -> None:
    """Quét TOÀN BỘ trang tìm chỗ còn buộc khung vào `mode`.

    Đây là test tôi cần từ đầu. Bốn lượt liên tiếp đều là cùng một lỗi lộ ra ở
    bốn chỗ khác nhau — thanh trên, cột phải, nhịp ba chấm, tin nhắn — vì tôi
    sửa từng chỗ thay vì hỏi "còn chỗ nào nữa không".

    Khung trang và nhịp báo trạng thái phải phụ thuộc `mode === 'journey' ||
    talking`, không phải mình `mode`.
    """
    page = _PAGE.read_text(encoding="utf-8")

    # Những nơi được phép chỉ đọc `mode`: chính sân khấu, và nhãn của nó.
    duoc_phep = {
        "if (mode === 'journey') {",                 # rẽ nhánh trong execute()
        "mode === 'journey' && live?.status === 'SUCCESS' && live.summary && (",
        "{mode === 'journey' && (\n                <JourneyCanvas",
        "journeyLabel={mode === 'journey' ? title : undefined}",
        "mode === 'journey' || !talking ? 'min-h-0 flex-1' : ''",
        "mode === 'journey' ? '' : 'flex min-h-0 flex-1 flex-col justify-end'",
        "{mode === 'journey' || talking ? null : (",
    }
    con_lai = []
    for dong in page.splitlines():
        if "mode === 'journey'" not in dong:
            continue
        if any(mau in dong or dong.strip() in mau for mau in duoc_phep):
            continue
        if "talking" in dong:
            continue
        con_lai.append(dong.strip())

    assert not con_lai, (
        "còn chỗ buộc khung trang vào mình `mode` — gõ một câu chat sẽ làm nó "
        f"biến mất: {con_lai}"
    )
