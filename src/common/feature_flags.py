"""Công tắc tính năng — MỘT chỗ đọc, và chỉ bật bằng đúng một giá trị.

Vì sao phải có một chỗ
----------------------
Một cờ đọc rải rác ở năm nơi là năm cách hiểu về "bật". `os.getenv("X") != "0"`
bật với chuỗi rỗng; `bool(os.getenv("X"))` bật với `"0"`; `os.getenv("X") ==
"true"` tắt với `"1"`. Ba dòng ấy cùng tồn tại trong một codebase là chuyện
bình thường, và chúng chỉ lệch nhau đúng vào lúc ai đó gõ nhầm một ký tự trong
`.env` của production.

Vì sao ALLOWLIST chứ không blocklist
------------------------------------
Chỉ đúng chuỗi `"1"` là bật. Mọi thứ khác — thiếu biến, `""`, `"0"`, `"true"`,
`"yes"`, `"01"`, khoảng trắng — đều TẮT.

Đây là fail-closed cho một tính năng đụng vào tiền: nó chọn đơn vị cung cấp và
mở đường cho một cam kết thương mại. Một cấu hình gõ sai phải để hệ thống chạy
như CŨ, chứ không phải bật một đường mới chưa ai xem lại. `"true"` bị từ chối
là cố ý, không phải sơ suất — nhận nó nghĩa là phải trả lời "thế còn `True`,
`TRUE`, `yes`, `on`?", và mỗi câu trả lời thêm một cách để hai môi trường hiểu
khác nhau.
"""

from __future__ import annotations

import os

# Chọn đơn vị cung cấp theo báo giá: hỏi giá, đề xuất, khách xác nhận, rồi mới
# tới hàng đợi của đơn vị. Tắt thì đường cũ chạy nguyên vẹn — đơn vị mặc định
# theo `provider_directory`, và hàng đợi duyệt mở ngay như trước.
SERVICE_PROVIDER_MATCHING = "SERVICE_PROVIDER_MATCHING"

_BAT = "1"


def _bat(ten: str) -> bool:
    """Biến môi trường `ten` có đúng giá trị bật không.

    KHÔNG `strip()`, không `lower()`. Chuẩn hoá ở đây nghĩa là `" 1 "` cũng
    bật — và một khoảng trắng lọt vào `.env` là dấu hiệu file ấy được sinh ra
    bởi một công cụ không ai kiểm soát, chứ không phải một ý định.
    """
    return os.environ.get(ten) == _BAT


def chon_don_vi_theo_bao_gia_bat() -> bool:
    """Đường chọn đơn vị theo báo giá có đang bật không.

    Đọc `os.environ` mỗi lần, không cache: bài kiểm bật/tắt cờ bằng
    `monkeypatch.setenv` trong cùng tiến trình, và một giá trị cache sẽ làm
    lượt kiểm thứ hai đo lại lượt thứ nhất.
    """
    return _bat(SERVICE_PROVIDER_MATCHING)
