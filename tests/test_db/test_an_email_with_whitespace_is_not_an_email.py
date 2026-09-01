"""Khoảng trắng trong email dừng ở SCHEMA, không đi tiếp.

Bug
---
Mẫu cũ là `^[^@]+@[^@]+\\.[^@]+$`, và `[^@]` khớp cả DẤU CÁCH, TAB, XUỐNG DÒNG.
Nên `"a b@c.vn"` đi qua Pydantic, được ghi vào `registration_otps` làm khoá, một
email được gửi tới một địa chỉ không tồn tại, rồi lượt đăng ký hỏng ở bước kiểm
mã — trả 400 và nói về MÃ trong khi thứ sai là ĐỊA CHỈ.

Ba hậu quả, không cái nào lộ ra ngay:

  1. một hàng OTP có khoá không ai gửi tới được, nằm lại tới khi hết hạn;
  2. một lượt gửi thư thật tới một địa chỉ rác — endpoint thành cách phát tán;
  3. câu báo lỗi trỏ sai chỗ, nên người dùng sửa mã thay vì sửa email.

Frontend đã chặn bằng `/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/`. Backend là hàng rào
THẬT — client nào cũng gọi thẳng API được.

Phạm vi
-------
Sửa ở `RegistrationData.email`, nơi CẢ HAI endpoint kế thừa. Sửa một endpoint
rồi quên endpoint kia là để nguyên đúng một nửa lỗ — và nửa còn lại là nửa ghi
vào database.
"""

from __future__ import annotations

import pytest

from tests._otp_registration import MAT_KHAU

AUTH = "/api/v1/auth"

# Mọi cách khoảng trắng chui được vào một địa chỉ.
CO_KHOANG_TRANG = [
    "a b@c.vn",  # giữa phần tên
    "ab@c d.vn",  # giữa phần miền
    " ab@c.vn",  # đầu
    "ab@c.vn ",  # cuối
    "a\tb@c.vn",  # tab
    "ab@c.vn\n",  # xuống dòng
    "a\rb@c.vn",  # về đầu dòng
]

HOP_LE = [
    "nguyen.van.a@example.com",
    "ten+nhan@sub.domain.vn",
    "a_b-c@d.co.uk",
    "SO123@vidu.VN",
]


def _than(email: str, username: str = "kh_email_trang") -> dict:
    return {"username": username, "password": MAT_KHAU, "email": email}


# ==================================================== hai endpoint, cùng luật
@pytest.mark.parametrize("email", CO_KHOANG_TRANG)
@pytest.mark.parametrize("duong", ["send-registration-otp", "register"])
@pytest.mark.asyncio
async def test_whitespace_in_an_email_is_refused_at_the_schema(client, duong, email, hop_thu_otp):
    """422 ở CẢ HAI endpoint — chúng kế thừa cùng một khai báo."""
    than = _than(email)
    if duong == "register":
        than["otp_code"] = "123456"

    res = await client.post(f"{AUTH}/{duong}", json=than)

    assert res.status_code == 422, f"{duong} nhận {email!r}: {res.status_code} {res.text}"


@pytest.mark.parametrize("email", CO_KHOANG_TRANG)
@pytest.mark.asyncio
async def test_a_refused_email_leaves_nothing_behind(client, db_pool, hop_thu_otp, email):
    """Không hàng OTP, không thư gửi đi, không tài khoản.

    Đây là phần đắt nhất của bug: 422 mà vẫn ghi một hàng và vẫn gửi một thư thì
    mã trạng thái đúng còn hệ quả thì y như cũ.
    """
    truoc = int(await db_pool.fetchval("SELECT count(*) FROM registration_otps"))

    await client.post(f"{AUTH}/send-registration-otp", json=_than(email))

    assert int(await db_pool.fetchval("SELECT count(*) FROM registration_otps")) == truoc
    assert hop_thu_otp.so_thu(email) == 0, "vẫn gửi thư tới địa chỉ không hợp lệ"
    assert hop_thu_otp.so_thu(email.strip()) == 0, "gửi tới bản đã tự cắt khoảng trắng"
    assert int(await db_pool.fetchval("SELECT count(*) FROM users WHERE email = $1", email)) == 0


@pytest.mark.parametrize("email", CO_KHOANG_TRANG)
@pytest.mark.asyncio
async def test_the_refusal_never_carries_a_code(client, hop_thu_otp, email):
    """Câu 422 không được mang mã — kể cả một mã vừa sinh cho ai đó."""
    res = await client.post(f"{AUTH}/send-registration-otp", json=_than(email))

    assert res.status_code == 422
    assert "otp" not in res.text.lower() or "otp_code" in res.text.lower(), res.text
    # Không có chuỗi sáu chữ số nào trong thân câu trả lời.
    import re

    assert not re.search(r"\b\d{6}\b", res.text), res.text


# ==================================================== không siết quá tay
@pytest.mark.parametrize("email", HOP_LE)
@pytest.mark.asyncio
async def test_valid_addresses_still_pass(client, db_pool, hop_thu_otp, email):
    """Nửa còn lại: địa chỉ hợp lệ vẫn qua.

    Một mẫu siết quá tay chặn được bug và chặn luôn người dùng thật — và bài
    kiểm chỉ đo vế cấm sẽ không thấy điều đó.
    """
    u = "kh_hople_" + str(abs(hash(email)) % 10**6)

    res = await client.post(f"{AUTH}/send-registration-otp", json=_than(email, username=u))

    assert res.status_code == 200, f"{email!r} bị chặn oan: {res.text}"
    assert hop_thu_otp.ma_moi_nhat(email.lower()), "không gửi thư cho một địa chỉ hợp lệ"


@pytest.mark.asyncio
async def test_the_backend_is_never_looser_than_the_browser(client, hop_thu_otp):
    r"""Backend KHÔNG được nhận thứ mà form của trình duyệt đã chặn.

        Đây mới là tính chất đáng giữ, và nó một chiều. Backend lỏng hơn nghĩa là
        một client khác — script, app, curl — đi qua được cửa mà trình duyệt đóng.
        Backend CHẶT hơn thì không ai lọt; cùng lắm là một địa chỉ bị từ chối ở
        server sau khi form đã cho qua, và người dùng nhận 422.

        Có đúng một ca lệch, và nó là một nếp của JavaScript chứ không phải lỗi:
        `$` trong `/…$/` khớp cả TRƯỚC một `
    ` cuối chuỗi, nên
        `/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test("ab@c.vn
    ")` trả `true`. Backend từ chối
        — chặt hơn, và an toàn hơn.

        Thực tế người dùng không chạm vào ca ấy: `RegisterPage` gọi `email.trim()`
        TRƯỚC khi vừa kiểm vừa gửi, nên trình duyệt không bao giờ gửi một địa chỉ
        còn newline. Ca này chỉ tới từ một client tự viết — và với client ấy,
        backend chặn là đúng.
    """
    import re

    mau_frontend = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

    for email in CO_KHOANG_TRANG + HOP_LE:
        than = _than(email, username="kh_dong_thuan_" + str(abs(hash(email)) % 10**5))
        res = await client.post(f"{AUTH}/send-registration-otp", json=than)
        frontend_cho_qua = bool(mau_frontend.match(email))
        backend_cho_qua = res.status_code != 422
        if backend_cho_qua:
            assert frontend_cho_qua, (
                f"{email!r}: backend cho qua ({res.status_code}) trong khi form trình duyệt chặn — "
                "một client không qua trình duyệt sẽ lọt"
            )
