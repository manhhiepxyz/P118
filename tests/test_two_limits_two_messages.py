"""Hai giới hạn khác nhau, cùng mã 429 — và chúng cần hai hành động khác nhau.

    bùng phát tức thời   chờ vài giây rồi bấm lại
    hết hạn ngạch ngày   chờ hàng GIỜ, hoặc xin nâng trần

Frontend trả MỘT câu cho cả hai, và đó là câu của trường hợp thứ nhất. Người
dùng đã dùng hết 50/50 suất được bảo "thử lại sau giây lát", nên họ bấm lại
liên tục — đúng thứ hạn ngạch định chặn, và không có cách nào biết chuyện gì
đang xảy ra.

Đo được: tài khoản `thanhbao` đứng ở 50/50 trong 24 giờ, giới hạn phút KHÔNG hề
chặn (8/8 request đi qua), nhưng màn hình vẫn nói họ thao tác quá nhanh.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from src.api import routes

_API = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "agentApi.ts"


def test_the_backend_says_when_the_quota_reopens() -> None:
    """"Thử lại sau" mà không nói KHI NÀO thì người dùng chỉ còn cách bấm dò."""
    source = inspect.getsource(routes._enforce_daily_quota)
    assert "giới hạn" in source and "dùng tiếp được sau" in source, (
        "câu từ chối hạn ngạch không còn mang mốc mở lại"
    )


def test_the_frontend_shows_that_message_instead_of_the_burst_one() -> None:
    source = _API.read_text(encoding="utf-8")
    branch = source[source.index("case 429:") :]
    branch = branch[: branch.index("case 503:")]
    assert "quotaDetail(" in branch, (
        "429 vẫn trả một câu duy nhất — người hết suất trong ngày được bảo chờ "
        "vài giây"
    )
    assert "thao tác hơi nhanh" in branch, "mất câu cho trường hợp bùng phát thật"


def test_the_body_is_actually_read_on_429() -> None:
    """Lọc một chuỗi luôn rỗng thì không lọc được gì.

    Body CHỈ được đọc khi mã là 422. Nên câu hạn ngạch — thứ backend viết
    riêng, kèm mốc thời gian mở lại — không bao giờ tới được nhánh 429. Bản vá
    đầu của tôi thêm bộ lọc ở nhánh ấy nhưng bỏ quên chính chỗ này.
    """
    source = _API.read_text(encoding="utf-8")
    assert "async function rawDetail(" in source, "không còn đường đọc `detail` cho mã khác 422"
    # CẢ HAI đường request đều phải đọc; một đường quên là lỗi chỉ hiện ở nửa
    # số lời gọi, và nửa nào thì tuỳ chỗ người dùng bấm.
    assert source.count("await rawDetail(response)") >= 2, (
        "chỉ một trong hai đường request đọc `detail` — nhánh kia vẫn hiện câu chung"
    )


def test_the_frontend_only_accepts_the_known_shape() -> None:
    """Không hiện `detail` thô cho MỌI 429.

    `detail` là chuỗi từ server; một đường 429 khác sau này có thể mang nội
    dung không dành cho người dùng đọc. Nhận theo dấu hiệu, đúng cùng cách câu
    422 được lọc.
    """
    source = _API.read_text(encoding="utf-8")
    body = source[source.index("function quotaDetail(") :]
    body = body[: body.index("\n}")]
    assert "giới hạn" in body and "dùng tiếp được sau" in body, "nhận bừa mọi detail"
    assert re.search(r"length > \d+", body), "không chặn chuỗi dài bất thường"


def test_the_demo_quota_leaves_room_to_demonstrate() -> None:
    """Hết suất giữa buổi demo thì không còn gì để trình bày."""
    from src.config import Settings

    quota = Settings().daily_workflow_quota
    assert quota >= 150, f"{quota} suất/ngày chạm trần giữa một buổi thử"
    # Vẫn phải có trần: ~12.264 token mỗi tác vụ.
    assert quota <= 500, f"{quota} suất/ngày là ~${quota * 12264 * 0.000000298:.2f}/người/ngày"
