"""Giới hạn tốc độ phải khoá theo PHIÊN, không phải theo IP dùng chung.

Khoá theo mình IP sai ngay khi có một lớp mạng ở giữa. Sau NAT của Docker,
backend thấy MỌI request đến từ cùng một địa chỉ — đo được trên log thật:
289/289 request từ `192.168.65.1`.

Nên bucket "theo IP" thực chất là một bucket TOÀN HỆ THỐNG. Người dùng gõ vài
tin nhắn liền nhau, và người kế bên nhận "Bạn thao tác hơi nhanh. Vui lòng thử
lại sau giây lát." cho một thao tác họ chưa hề làm. Với 20 request/phút và
burst 10 dùng chung, một buổi demo hai người là đủ chạm trần.
"""

from __future__ import annotations

from src.api.middleware import _bucket_key


def _scope(*, ip: str = "192.168.65.1", token: str | None = None) -> dict:
    headers = [(b"host", b"test")]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return {"type": "http", "client": (ip, 12345), "headers": headers}


def test_two_sessions_behind_one_ip_get_separate_buckets() -> None:
    """Đây là chính tình huống của Docker."""
    a = _bucket_key(_scope(token="token-cua-nguoi-A"))
    b = _bucket_key(_scope(token="token-cua-nguoi-B"))
    assert a != b, "hai người sau cùng một IP vẫn dùng chung một bucket"


def test_the_same_session_keeps_the_same_bucket() -> None:
    """Khoá phải ỔN ĐỊNH; đổi mỗi request thì giới hạn không còn tác dụng."""
    assert _bucket_key(_scope(token="abc")) == _bucket_key(_scope(token="abc", ip="10.0.0.9"))


def test_anonymous_requests_are_still_limited_by_ip() -> None:
    """Đăng nhập/đăng ký chưa có tài khoản để khoá, và đó là chỗ cần chặn dò
    mật khẩu nhất."""
    key = _bucket_key(_scope())
    assert key.startswith("ip:"), f"yêu cầu chưa đăng nhập không còn khoá theo IP: {key}"
    assert "192.168.65.1" in key


def test_the_token_is_not_stored_in_the_key() -> None:
    """Khoá đi vào bộ nhớ và có thể lọt ra log. Băm, không giữ nguyên."""
    token = "eyJhbGciOiJIUzI1NiJ9.bi-mat"
    key = _bucket_key(_scope(token=token))
    assert token not in key
    assert "bi-mat" not in key
    assert key.startswith("s:")


def test_a_malformed_authorization_header_falls_back_to_ip() -> None:
    """Header rỗng không được biến thành một bucket dùng chung tên 'Bearer'."""
    scope = {"type": "http", "client": ("1.2.3.4", 1), "headers": [(b"authorization", b"Bearer ")]}
    assert _bucket_key(scope) == "ip:1.2.3.4"


def test_the_burst_allowance_fits_a_real_conversation() -> None:
    """Giới hạn phút chặn BÙNG PHÁT, không được hãm người đang gõ.

    20/phút + burst 10 được chọn khi bucket dùng chung cho mọi người. Khoá theo
    phiên rồi thì cùng con số ấy thành trần của MỘT người — và nó chạm thật:
    sau 10 thao tác, người dùng bị hãm còn một thao tác mỗi 3 giây. Đo được 11
    lần 429 trong 25 phút dùng bình thường, tất cả ở `/workflows/demo/start`.
    """
    from src.config import Settings

    s = Settings()
    assert s.rate_limit_per_minute >= 60, (
        f"{s.rate_limit_per_minute}/phút hãm người dùng còn một thao tác mỗi "
        f"{60 / s.rate_limit_per_minute:.1f} giây sau khi hết burst"
    )
    assert s.rate_limit_burst >= 15, "burst quá nhỏ: gõ vài tin liền nhau là chạm trần"


def test_volume_is_still_capped_somewhere() -> None:
    """Nới giới hạn phút chỉ an toàn khi hạn ngạch NGÀY còn nguyên.

    Đó mới là thứ giữ hoá đơn LLM: ~12.264 token mỗi workflow, nên 50/ngày là
    trần khoảng $0,18 mỗi người mỗi ngày.
    """
    from src.config import Settings

    s = Settings()
    # Trần cụ thể do `test_two_limits_two_messages.py` giữ; ở đây chỉ khẳng
    # định nó CÒN TỒN TẠI. Hai test cùng khoá một con số thì đổi trần là phải
    # sửa hai chỗ, và chỗ bị quên sẽ đỏ mà không nói được vì sao.
    assert s.daily_workflow_quota > 0, "mất trần theo ngày — giới hạn phút một mình không chặn được chi phí"
