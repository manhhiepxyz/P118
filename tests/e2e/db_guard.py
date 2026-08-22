"""Chốt chặn database cho mọi harness chạy trên stack thật.

Vì sao là một module riêng, có test riêng, không phải một dòng `if` trong
harness:

Harness E2E ghi thật — nó tạo tài khoản, hồ sơ, workflow, và ở vài chỗ nó
`docker compose up --force-recreate`. Nếu nó trỏ nhầm vào `p118_db` thì thứ bị
hỏng là database demo, và không ai biết cho tới lúc mở demo. Một dòng `if` nằm
lẫn trong 400 dòng harness là một dòng sẽ bị bỏ qua khi ai đó thêm nhánh mới;
một module có test thì mutation nào gỡ nó ra cũng làm suite đỏ.

Nguyên tắc: **fail-closed và so khớp CHÍNH XÁC**.

Không `startswith`, không `in`, không `endswith`. Ba cách viết ấy đều nhận
`p118_e2e_db_backup`, và `p118_db` thì lọt qua bất kỳ kiểm tra substring nào
trên `p118_e2e_db` — vì nó là chuỗi con của chính tên hợp lệ.

Cũng không nhận DSN. `postgresql://u:p@h/p118_e2e_db` KHÔNG phải tên database;
nhận nó nghĩa là chấp nhận một chuỗi mang credential đi khắp harness, và mang
theo cả khả năng trỏ tới một host khác.
"""

from __future__ import annotations

import os
import re

# Đúng MỘT tên được chạy harness. Hằng số, không đọc từ môi trường: thứ đọc từ
# môi trường là CÂU HỎI, còn ĐÁP ÁN thì nằm trong mã nguồn và đi qua code review.
ALLOWED_DATABASE = "p118_e2e_db"

# Tên định danh PostgreSQL hợp lệ. Dùng để loại sớm mọi thứ mang `:`, `/`, `?`,
# khoảng trắng, dấu nháy — tức mọi thứ trông như DSN, query-string hay SQL.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_HUONG_DAN = f"Harness E2E chỉ chạy trên database riêng. Đặt P118_DB={ALLOWED_DATABASE} rồi chạy lại."


class UnsafeDatabaseError(RuntimeError):
    """Đích không an toàn. Message KHÔNG chứa DSN, host, user hay mật khẩu.

    Lỗi cấu hình là loại lỗi hay bị dán nguyên văn vào issue và CI log, và đó
    là nơi credential sống lâu nhất.
    """


def require_e2e_database(value: str | None = None) -> str:
    """Trả về tên database an toàn, hoặc ném lỗi. Không bao giờ trả mặc định.

    `value=None` nghĩa là đọc `P118_DB` từ môi trường — để harness gọi một
    dòng, còn test gọi trực tiếp mà không phải vá `os.environ`.
    """
    raw = os.environ.get("P118_DB") if value is None else value
    if raw is None or not raw.strip():
        raise UnsafeDatabaseError(f"Thiếu P118_DB. {_HUONG_DAN}")
    ten = raw.strip()
    if not _IDENTIFIER.match(ten):
        # Không in `ten` ra: nếu nó là DSN thì nó mang mật khẩu.
        raise UnsafeDatabaseError(f"P118_DB không phải một tên database hợp lệ. {_HUONG_DAN}")
    if ten != ALLOWED_DATABASE:
        # In được ở đây vì đã qua vòng kiểm định danh — nó chắc chắn không phải DSN.
        raise UnsafeDatabaseError(f"P118_DB={ten} không được phép. {_HUONG_DAN}")
    return ten


def compose_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Môi trường cho MỌI lệnh `docker compose` mà harness gọi.

    Không phải tiện ích: harness gọi compose ở năm chỗ (up, build,
    force-recreate, đổi cấu hình LLM, restore). Bốn chỗ nhớ truyền
    `POSTGRES_DB` và một chỗ quên là đủ để container quay về `p118_db` giữa
    chừng — đã xảy ra ba lần trong lượt nghiệm thu trước.
    """
    env = dict(base if base is not None else os.environ)
    env["POSTGRES_DB"] = require_e2e_database()
    return env


def assert_ready_on_e2e_database(ready_payload: dict) -> None:
    """`/ready` phải xanh VÀ nói đúng tên database.

    Kiểm sau MỖI lần recreate/restart. `/ready` xanh một mình không đủ: nó cũng
    xanh khi container vừa quay về `p118_db`, và mọi assert sau đó sẽ nói về
    database sai.
    """
    if ready_payload.get("status") != "ready":
        raise UnsafeDatabaseError("Backend chưa sẵn sàng sau khi khởi động lại.")
    chi_tiet = next(
        (c.get("detail", "") for c in ready_payload.get("checks", []) if c.get("name") == "database"),
        "",
    )
    # `/ready` trả "kết nối được · database=<tên>". Tách lấy đúng token cuối và
    # so BẰNG, không phải `in`: `p118_db` nằm trong `p118_e2e_db`.
    ten = chi_tiet.rsplit("database=", 1)[-1].strip() if "database=" in chi_tiet else ""
    if ten != ALLOWED_DATABASE:
        raise UnsafeDatabaseError("Backend đang trỏ vào database khác sau khi khởi động lại.")
