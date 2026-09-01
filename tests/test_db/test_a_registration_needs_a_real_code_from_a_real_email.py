"""Hợp đồng đăng ký: email thật, mã thật, dùng đúng một lần.

Vì sao ở đây chứ không ở `tests/test_api`
-----------------------------------------
Ở `tests/test_api`, repository là stub và `verify_otp` luôn cho qua — đo được:
đổi `otp_code` thành `"000000"` thì bài kiểm ở đó vẫn xanh. Nên những bài ấy nói
về HÌNH DẠNG request, không nói gì về hợp đồng OTP.

Hợp đồng OTP chỉ kiểm được nơi có PostgreSQL thật: hạn dùng, tính một-lần, và
ràng buộc mã thuộc về đúng email đều là hành vi của bảng `registration_otps`.

Vì sao KHÔNG dùng helper `_register_and_login`
----------------------------------------------
Helper ấy tồn tại để dựng một tài khoản hợp lệ cho những bài kiểm nói về chuyện
khác. Dùng nó ở đây là kiểm một đường tắt thay vì kiểm hợp đồng — và ngày ai đó
nới helper, những bài này vẫn xanh.

Mã OTP đọc ở HỘP THƯ, không đọc database: mã rời hệ thống qua email, và đó là
hành vi. Xem `tests/_email_outbox.py`.
"""

from __future__ import annotations

import pytest

from tests._otp_registration import MAT_KHAU, email_cua

AUTH = "/api/v1/auth"


def _than(username: str, **doi) -> dict:
    than = {"username": username, "password": MAT_KHAU, "email": email_cua(username)}
    than.update(doi)
    return than


async def _xin_ma(client, username: str, **doi):
    return await client.post(f"{AUTH}/send-registration-otp", json=_than(username, **doi))


# ============================================================ hình dạng request
@pytest.mark.asyncio
async def test_registering_without_an_email_is_refused(client, hop_thu_otp):
    res = await client.post(
        f"{AUTH}/register", json={"username": "kh_thieu_email", "password": MAT_KHAU, "otp_code": "123456"}
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
@pytest.mark.parametrize("xau", ["khong-co-a-cong", "a@b", "@thieu-ten.vn", "a b@c.vn", ""])
async def test_a_malformed_email_never_registers(client, hop_thu_otp, xau):
    """Email sai định dạng KHÔNG đăng ký được. Đây là khẳng định người dùng thấy.

    Quan trọng vì email là KHOÁ của bảng OTP: một chuỗi lạ lọt qua sẽ thành một
    hàng không ai gửi tới được, và người dùng chờ một email không tồn tại.
    """
    res = await client.post(
        f"{AUTH}/register",
        json={"username": "kh_email_xau", "password": MAT_KHAU, "email": xau, "otp_code": "123456"},
    )
    assert res.status_code != 201, f"{xau!r} đăng ký được: {res.text}"


@pytest.mark.asyncio
@pytest.mark.parametrize("xau", ["khong-co-a-cong", "a@b", "@thieu-ten.vn", ""])
async def test_the_schema_catches_the_malformed_email_before_the_database(client, hop_thu_otp, xau):
    r"""Bốn dạng này dừng ở tầng schema (422), không tới được database.

    NỢ — dạng thứ năm KHÔNG dừng ở đây: `"a b@c.vn"`.

    Mẫu đang dùng là `^[^@]+@[^@]+\.[^@]+$`, và `[^@]` khớp cả DẤU CÁCH. Nên một
    địa chỉ có khoảng trắng đi qua schema, được ghi vào `registration_otps` làm
    khoá, rồi mới hỏng ở bước kiểm mã — trả 400 thay vì 422.

    Hệ quả hôm nay là nhỏ: người dùng vẫn không đăng ký được (bài ngay trên khoá
    điều đó). Nhưng nó tạo một hàng OTP với khoá không gửi tới được ai, và câu
    báo lỗi nói về MÃ trong khi thứ sai là ĐỊA CHỈ.

    KHÔNG sửa mẫu trong lượt tích hợp này: nó là điểm yếu có sẵn của nhánh
    registration, không phải xung đột giữa hai nhánh, và siết validation là một
    thay đổi hợp đồng đáng có lượt riêng với bộ kiểm riêng.
    """
    res = await client.post(
        f"{AUTH}/register",
        json={"username": "kh_email_xau_2", "password": MAT_KHAU, "email": xau, "otp_code": "123456"},
    )
    assert res.status_code == 422, f"{xau!r} không dừng ở schema: {res.text}"


@pytest.mark.asyncio
async def test_registering_without_an_otp_is_refused(client, hop_thu_otp):
    res = await client.post(
        f"{AUTH}/register",
        json={"username": "kh_thieu_ma", "password": MAT_KHAU, "email": email_cua("kh_thieu_ma")},
    )
    assert res.status_code == 422, res.text


# ============================================================ đường hợp lệ
@pytest.mark.asyncio
async def test_a_code_from_the_email_registers_once_and_logs_in(client, db_pool, hop_thu_otp):
    """Đường đúng, từ đầu tới cuối — và mã chỉ dùng được MỘT lần."""
    u = "kh_duong_dung"
    assert (await _xin_ma(client, u)).status_code == 200
    assert hop_thu_otp.so_thu(email_cua(u)) == 1, "không có email nào được gửi"
    ma = hop_thu_otp.ma_moi_nhat(email_cua(u))
    assert ma and ma.isdigit() and len(ma) == 6, ma

    tao = await client.post(f"{AUTH}/register", json=_than(u, otp_code=ma))
    assert tao.status_code == 201, tao.text
    assert tao.json()["role"] == "customer"

    dang_nhap = await client.post(f"{AUTH}/login", json={"username": u, "password": MAT_KHAU})
    assert dang_nhap.status_code == 200, dang_nhap.text
    assert dang_nhap.json()["access_token"]

    # Mã đã bị tiêu — không còn hàng nào cho email ấy.
    con_lai = await db_pool.fetchval("SELECT count(*) FROM registration_otps WHERE email = $1", email_cua(u))
    assert int(con_lai) == 0


# ============================================================ mã không dùng được
@pytest.mark.asyncio
async def test_a_wrong_code_is_refused(client, hop_thu_otp):
    u = "kh_ma_sai"
    await _xin_ma(client, u)
    that = hop_thu_otp.ma_moi_nhat(email_cua(u))
    sai = "000000" if that != "000000" else "111111"

    res = await client.post(f"{AUTH}/register", json=_than(u, otp_code=sai))

    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_an_expired_code_is_refused(client, db_pool, hop_thu_otp):
    """Hết hạn thì từ chối, kể cả khi mã đúng từng chữ số.

    Đẩy `expires_at` về quá khứ chứ không chờ 5 phút: thứ đang đo là LUẬT hạn
    dùng, và một bài kiểm ngủ 5 phút là một bài kiểm không ai chạy.
    """
    u = "kh_ma_het_han"
    await _xin_ma(client, u)
    ma = hop_thu_otp.ma_moi_nhat(email_cua(u))
    await db_pool.execute(
        "UPDATE registration_otps SET expires_at = NOW() - INTERVAL '1 second' WHERE email = $1", email_cua(u)
    )

    res = await client.post(f"{AUTH}/register", json=_than(u, otp_code=ma))

    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_a_code_issued_for_another_email_is_refused(client, hop_thu_otp):
    """Mã gắn với EMAIL. Mượn mã của người khác không đăng ký hộ được.

    Đây là nhánh đáng lo nhất: hai người cùng xin mã trong một phút, và nếu mã
    không neo vào email thì người đọc được mã của người kia chiếm được email ấy.
    """
    a, b = "kh_chu_ma", "kh_muon_ma"
    await _xin_ma(client, a)
    await _xin_ma(client, b)
    ma_cua_a = hop_thu_otp.ma_moi_nhat(email_cua(a))
    ma_cua_b = hop_thu_otp.ma_moi_nhat(email_cua(b))
    assert ma_cua_a and ma_cua_b

    # B thử dùng mã của A cho chính email của B.
    res = await client.post(f"{AUTH}/register", json=_than(b, otp_code=ma_cua_a))

    assert res.status_code == 400, res.text
    # Và mã thật của B vẫn còn dùng được — lượt thử hỏng không tiêu mã của ai.
    assert (await client.post(f"{AUTH}/register", json=_than(b, otp_code=ma_cua_b))).status_code == 201


@pytest.mark.asyncio
async def test_a_used_code_cannot_be_used_again(client, hop_thu_otp):
    """Dùng một lần. Lượt thứ hai với CÙNG mã phải hỏng.

    Không có luật này, một mã lộ ra (chuyển tiếp email, ảnh chụp màn hình) mở
    được nhiều tài khoản chừng nào nó chưa hết hạn.
    """
    u = "kh_ma_dung_lai"
    await _xin_ma(client, u)
    ma = hop_thu_otp.ma_moi_nhat(email_cua(u))
    assert (await client.post(f"{AUTH}/register", json=_than(u, otp_code=ma))).status_code == 201

    lai = await client.post(f"{AUTH}/register", json=_than("kh_ma_dung_lai_2", otp_code=ma))

    assert lai.status_code == 400, lai.text


# ============================================================ chống spam
@pytest.mark.asyncio
async def test_asking_again_too_soon_is_rate_limited(client, hop_thu_otp):
    """Xin lại trong vòng 60 giây → 429, và KHÔNG gửi thêm email.

    Kiểm cả hai vế: mã trạng thái, và số thư trong hộp. Chỉ kiểm mã trạng thái
    thì một bản cài đặt vẫn gửi email rồi mới trả 429 sẽ đi lọt — và nó là một
    kênh spam qua địa chỉ người khác.
    """
    u = "kh_xin_lai_som"
    assert (await _xin_ma(client, u)).status_code == 200
    truoc = hop_thu_otp.so_thu(email_cua(u))

    lai = await _xin_ma(client, u)

    assert lai.status_code == 429, lai.text
    assert hop_thu_otp.so_thu(email_cua(u)) == truoc, "vẫn gửi email dù đã chặn"


@pytest.mark.asyncio
async def test_asking_for_a_taken_username_is_refused_before_any_email(client, hop_thu_otp):
    """Tên đã có → 409, và không email nào được gửi tới địa chỉ vừa khai.

    Nếu vẫn gửi, endpoint thành một cách gửi thư tới địa chỉ bất kỳ.
    """
    u = "kh_ten_da_co"
    await _xin_ma(client, u)
    ma = hop_thu_otp.ma_moi_nhat(email_cua(u))
    assert (await client.post(f"{AUTH}/register", json=_than(u, otp_code=ma))).status_code == 201
    truoc = hop_thu_otp.so_thu(email_cua(u))

    lai = await _xin_ma(client, u)

    assert lai.status_code == 409, lai.text
    assert hop_thu_otp.so_thu(email_cua(u)) == truoc


# ============================================================ không rò mã
@pytest.mark.asyncio
async def test_no_response_ever_carries_the_code(client, hop_thu_otp):
    """Mã chỉ đi qua email. Không response nào — kể cả lỗi — được mang nó.

    Trả mã trong response biến kênh xác thực hai bước thành một bước: ai gọi
    được endpoint là đọc được mã, và email không còn chứng minh điều gì.
    """
    u = "kh_khong_ro_ma"
    xin = await _xin_ma(client, u)
    ma = hop_thu_otp.ma_moi_nhat(email_cua(u))
    assert ma

    assert ma not in xin.text, xin.text
    # Cả nhánh lỗi: một câu báo lỗi "mã đúng là X" cũng là rò.
    sai = await client.post(f"{AUTH}/register", json=_than(u, otp_code="000000" if ma != "000000" else "111111"))
    assert ma not in sai.text, sai.text
    # Và lượt xin lại bị chặn cũng không kèm mã.
    assert ma not in (await _xin_ma(client, u)).text
