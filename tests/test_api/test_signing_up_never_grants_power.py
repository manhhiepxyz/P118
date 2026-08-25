"""Đăng ký công khai KHÔNG bao giờ được cấp quyền, dù tên là gì.

Owner: Thành Bảo (Decision layer)
File: tests/test_api/test_signing_up_never_grants_power.py

CA THẬT sinh ra file này. Trên `hotfix/deploy-fixes-for-vercel`, commit 8f74d13
"allow creating admin and provider accounts with specific prefixes for demo"
đổi đúng một dòng trong `/auth/register`:

    role="customer"
    → role="provider" if username.startswith("provider_")
      else ("admin" if username.startswith("admin_") else "customer")

`/auth/register` KHÔNG cần đăng nhập. Nên bất kỳ ai cũng tự cấp cho mình quyền
admin bằng cách chọn tên bắt đầu bằng `admin_`.

Quyền ấy mở 7 route, trong đó có:

    GET  /admin/users                đọc toàn bộ người dùng
    PUT  /admin/users/{id}/role      ĐỔI ROLE CỦA BẤT KỲ AI
    PUT  /admin/users/{id}/status    khoá/mở tài khoản
    GET  /admin/requests             đọc workflow của mọi người

`provider_` thì mở quyền duyệt/từ chối dịch vụ thật của người khác.

VÌ SAO SUITE KHÔNG BẮT ĐƯỢC: `test_register_creates_a_customer_account` đăng ký
tên `nguyen.van.a` — không khớp tiền tố nào, nên nó vẫn xanh. Một thay đổi mở
toang hệ thống mà mọi phép kiểm đều im lặng.

File này kiểm chính thứ đó: role sau khi đăng ký là `customer`, KỂ CẢ khi tên
được chọn hòng lách. Nâng quyền là việc của admin qua `/admin/users/{id}/role`,
không phải việc của người tự đặt tên.
"""

from __future__ import annotations

import pytest

from tests.test_api.test_auth_routes import _register, auth_env  # noqa: F401 - fixture dùng chung


@pytest.mark.anyio
@pytest.mark.parametrize(
    "username",
    [
        # Đúng hai tiền tố commit 8f74d13 mở ra.
        "admin_demo",
        "provider_demo",
        # Biến thể để một bản vá "chỉ chặn đúng hai chuỗi ấy" không đủ qua bài này.
        "admin_",
        "ADMIN_hoa",
        "Provider_Hoa",
        "admin_nguyen.van.a",
        "provider",
        "admin",
    ],
)
async def test_a_username_never_buys_a_role(client, auth_env, username: str) -> None:
    """Tên người dùng không phải giấy uỷ quyền."""
    res = await _register(client, username=username)
    assert res.status_code in (201, 422), res.text
    if res.status_code == 201:
        assert res.json()["role"] == "customer", (
            f"{username!r} tự cấp được quyền {res.json()['role']!r} qua đăng ký công khai"
        )
