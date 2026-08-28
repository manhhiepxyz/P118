"""Hộp thư TEST — nơi bài kiểm đọc mã OTP.

Vì sao đọc ở đây thay vì đọc database
-------------------------------------
Mã OTP rời hệ thống qua EMAIL. Đó là hành vi người dùng thấy, và đó là hợp đồng.
`registration_otps` là chi tiết cài đặt: ngày ai đó băm mã, đổi bảng, hay đưa
sang Redis, mọi bài kiểm đọc thẳng database sẽ đỏ trong khi không có gì người
dùng thấy thay đổi.

Đọc ở biên email còn bắt được một lớp lỗi mà đọc database không bắt được: mã
đúng được LƯU nhưng GỬI NHẦM người, hoặc không gửi gì cả.

Vì sao không đọc log
--------------------
`email_service` in mã ra log khi SMTP chưa cấu hình. Bám vào đó nghĩa là bài
kiểm phụ thuộc một nhánh dự phòng của production, và nhánh ấy tồn tại đúng vì
production đang thiếu cấu hình — hai thứ không nên buộc vào nhau.

Vì sao không bật được trong production
--------------------------------------
Không có cờ môi trường nào ở đây. Hộp thư chỉ vào được hệ thống qua
`app.dependency_overrides`, thứ chỉ tồn tại trong tiến trình test.
"""

from __future__ import annotations

import re
from collections import defaultdict

# Mã OTP trong thân email: sáu chữ số. Bắt trong thẻ <strong> để không nhặt
# phải một số sáu chữ số khác nếu mẫu email đổi.
_MA = re.compile(r"(\d{6})")


class HopThuTest:
    """Ghi lại email OTP theo NGƯỜI NHẬN. Không mạng, không stdout, không log.

    Một thực thể cho mỗi bài kiểm (fixture function-scope), nên hai bài chạy
    song song không đọc thư của nhau — và không cần dọn giữa các bài.
    """

    def __init__(self) -> None:
        self._thu: dict[str, list[str]] = defaultdict(list)

    async def gui(self, to_email: str, otp_code: str) -> None:
        """Chữ ký khớp `send_otp_email(to_email, otp_code)`."""
        self._thu[to_email.strip().lower()].append(str(otp_code))

    def ma_moi_nhat(self, email: str) -> str | None:
        """Mã GẦN NHẤT gửi tới `email`, hoặc `None` nếu chưa gửi gì.

        Gần nhất chứ không phải đầu tiên: một lượt gửi lại thay thế mã cũ ở
        phía sản phẩm, nên bài kiểm phải đọc cùng một mã mà người dùng đọc.
        """
        hop = self._thu.get(email.strip().lower())
        if not hop:
            return None
        found = _MA.search(hop[-1])
        return found.group(1) if found else hop[-1]

    def so_thu(self, email: str) -> int:
        return len(self._thu.get(email.strip().lower(), []))

    def xoa(self) -> None:
        self._thu.clear()
