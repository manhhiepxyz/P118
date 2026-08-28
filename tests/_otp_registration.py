"""Đăng ký một tài khoản test qua ĐÚNG đường sản phẩm, kể cả bước OTP.

Vì sao là một utility riêng
---------------------------
`_register_and_login` sống ở `tests/test_db/conftest.py` và được 55 file import.
Nhưng cùng một việc cũng cần cho `tests/` và `tests/test_integration/`, và một
conftest không import được từ conftest anh em mà không dựng một phụ thuộc sai
tầng. Phần cơ học nằm ở đây; các conftest chỉ còn một vỏ mỏng.

Vì sao helper tự cắm hộp thư
----------------------------
Hộp thư là một fixture, còn helper là một hàm thường được gọi từ thân bài kiểm.
Bắt 260 lời gọi phải nhận thêm một tham số fixture là sửa hàng trăm bài kiểm cho
một thay đổi hạ tầng — và mỗi bài sửa là một cơ hội đổi nghĩa của nó.

Nên helper tự cắm và tự gỡ, và nó TÔN TRỌNG một override đang có: bài kiểm nào
đã cắm hộp thư của riêng nó (để kiểm chính hợp đồng OTP) thì helper dùng chính
hộp ấy, không giẫm lên.

Vì sao không đọc database
-------------------------
Mã OTP rời hệ thống qua email. Đọc ở biên ấy là đọc HÀNH VI; đọc
`registration_otps` là đọc chi tiết cài đặt — xem `tests/_email_outbox.py`.
"""

from __future__ import annotations

from typing import Any

MAT_KHAU = "MatKhauRatDai123!"


def email_cua(username: str) -> str:
    """Email TẤT ĐỊNH suy từ username.

    Tất định chứ không ngẫu nhiên: một bài kiểm đỏ phải đỏ lại y nguyên ở lượt
    chạy sau. `.test` là TLD dành riêng cho thử nghiệm (RFC 2606) — không phân
    giải được, nên một lượt gửi thật lọt ra ngoài cũng không tới được ai.
    """
    return f"{username.strip().lower()}@p118.test"


async def dang_ky_qua_duong_that(client: Any, username: str, *, password: str = MAT_KHAU) -> dict | None:
    """Xin OTP → đọc mã ở hộp thư → đăng ký. Trả HỒ SƠ vừa tạo, hoặc `None`.

    `None` nghĩa là KHÔNG cần đăng ký: tài khoản đã tồn tại (409). Đó là đường
    bình thường của những bài gọi helper hai lần cho cùng một tên — ví dụ để lấy
    token mới sau khi đổi vai.

    Trả hồ sơ chứ không trả mã OTP: người gọi cần `id`/`username`/`role` để dựng
    token, còn mã thì không ai dùng lại được — nó đã bị tiêu ngay khi đăng ký.

    KHÔNG nới hợp đồng: mọi trường bắt buộc đều được gửi, và mã lấy từ email
    thật do endpoint sinh ra chứ không phải một hằng số.
    """
    from src.api.deps import get_otp_email_sender
    from src.main import app
    from tests._email_outbox import HopThuTest

    email = email_cua(username)
    than = {"username": username, "password": password, "email": email}

    # Tôn trọng hộp thư bài kiểm đã cắm; chỉ tự cắm khi chưa có.
    da_co = app.dependency_overrides.get(get_otp_email_sender)
    hop = HopThuTest() if da_co is None else None
    if hop is not None:
        app.dependency_overrides[get_otp_email_sender] = lambda: hop.gui
    try:
        xin = await client.post("/api/v1/auth/send-registration-otp", json=than)
        if xin.status_code == 409:
            # Tên đăng nhập hoặc email đã có — không có gì để đăng ký nữa.
            return None
        assert xin.status_code == 200, f"xin OTP hỏng: {xin.status_code} {xin.text}"

        doc = hop if hop is not None else _hop_thu_dang_cam(da_co)
        ma = doc.ma_moi_nhat(email) if doc is not None else None
        assert ma, f"không có email OTP nào gửi tới {email}"

        tao = await client.post("/api/v1/auth/register", json={**than, "otp_code": ma})
        assert tao.status_code == 201, f"đăng ký hỏng: {tao.status_code} {tao.text}"
        return tao.json()
    finally:
        if hop is not None:
            app.dependency_overrides.pop(get_otp_email_sender, None)


def _hop_thu_dang_cam(override: Any) -> Any:
    """Lấy lại `HopThuTest` từ một override đang cắm.

    Override là `lambda: hop.gui`, nên hộp nằm trong `__self__` của phương thức
    bị bind. Không lấy được thì trả `None` và người gọi khẳng định thất bại với
    một câu nói rõ — im lặng đi tiếp sẽ thành "không có email nào" ở một chỗ
    khác hẳn nguyên nhân.
    """
    try:
        return override().__self__
    except Exception:  # noqa: BLE001 - chỉ là đường lấy lại, không phải hợp đồng
        return None
