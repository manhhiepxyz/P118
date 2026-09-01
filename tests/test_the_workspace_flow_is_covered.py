"""Kịch bản kiểm MỘT LUỒNG HOÀN CHỈNH của màn hành trình.

Viết ra vì cách làm cũ sai: sửa một triệu chứng, đưa cho người dùng, họ tìm ra
triệu chứng kế tiếp của CÙNG một nguyên nhân. Bốn lượt liên tiếp đều là một
gốc — `mode` vừa quyết định sân khấu vẽ gì, vừa quyết định khung trang:

    gõ một câu → mất thanh trên
                → mất cột phải
                → mất nhịp ba chấm
                → tin nhắn thứ hai xoá tin nhắn thứ nhất

Bốn triệu chứng, một nguyên nhân. Sửa từng cái một thì không bao giờ hết.

`tests/e2e_workspace_flow.mjs` chạy đủ sáu chặng trên trình duyệt thật với
backend thật, và khẳng định KHUNG TRANG không đổi ở bất kỳ chặng nào:

    1. mới vào            bảng dịch vụ, chưa có bước
    2. một câu hỏi        có ba chấm, không sinh bước, bảng dịch vụ lùi
    3. tin nhắn thứ hai   tin nhắn thứ nhất còn nguyên
    4. một yêu cầu thật   sinh bước, hội thoại cũ vẫn còn
    5. bấm dừng           đúng MỘT câu báo dừng
    6. nhắn tiếp          lịch sử còn, không chạy lại, không đổ lỗi

Nó cần backend + frontend đang chạy nên KHÔNG chạy trong `pytest`. Test dưới
đây chỉ giữ cho kịch bản không mục nát: mất chặng nào thì nó nói ngay.
"""

from __future__ import annotations

from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "tests" / "e2e_workspace_flow.mjs"

_CHANG = [
    "1. Mới vào",
    "2. Gửi một CÂU HỎI",
    "3. Gửi tin thứ HAI",
    "4+5. YÊU CẦU THẬT rồi BẤM DỪNG",
    "6. Nhắn tiếp SAU KHI DỪNG",
]


def test_the_scenario_script_exists() -> None:
    assert _SCRIPT.exists(), "mất kịch bản kiểm luồng — xem tài liệu ở đầu file này"


def test_every_stage_is_still_covered() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    thieu = [c for c in _CHANG if c not in source]
    assert not thieu, f"kịch bản mất chặng: {thieu}"


def test_the_frame_is_checked_at_every_stage() -> None:
    """Khung trang là thứ đã hỏng bốn lần; nó phải được đo ở MỌI chặng."""
    source = _SCRIPT.read_text(encoding="utf-8")
    assert source.count("await frame(") >= 5, (
        "khung trang không còn được kiểm ở đủ các chặng — đó chính là chỗ lỗi cứ mọc lại"
    )
    for neo in ("thanh điều hướng trái còn", "ô nhập còn", "cột phải còn"):
        assert neo in source, f"kịch bản thôi kiểm: {neo}"
