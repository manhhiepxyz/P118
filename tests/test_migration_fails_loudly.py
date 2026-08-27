"""Migration hỏng phải DỪNG, không được nuốt lỗi rồi đi tiếp.

Bản đầu bọc `SET NOT NULL` trong:

    EXCEPTION WHEN others THEN
        NULL;

`SET NOT NULL` vốn chạy lặp được — gọi lại trên cột đã NOT NULL không lỗi. Nên
cái `catch-all` ấy không bảo vệ gì; nó chỉ che một lỗi thật. Và lỗi thật ở đúng
bước này nghĩa là deployment đi tiếp với một cột còn cho phép NULL, tức mọi
hàng rào dựng trên cột đó im lặng biến mất.
"""

from __future__ import annotations

from pathlib import Path

_SQL = Path(__file__).resolve().parents[1] / "src" / "db"


def test_the_migration_has_no_catch_all_around_the_not_null_step():
    text = (_SQL / "schema_migrations.sql").read_text(encoding="utf-8")
    assert "EXCEPTION WHEN others THEN" not in text
    assert "SET NOT NULL" in text


def test_the_migration_never_carries_a_connection_string_or_a_literal_secret():
    """Câu lỗi migration đi thẳng vào log deployment.

    Tìm CHUỖI KẾT NỐI và giá trị bí mật thật — không tìm chữ "password": cột
    `users.password_hash` là một tên cột hợp lệ, và bắt nó là báo động giả, thứ
    làm người ta tắt cả bộ kiểm.
    """
    import re

    for name in ("schema.sql", "schema_migrations.sql"):
        text = (_SQL / name).read_text(encoding="utf-8")
        assert "postgresql://" not in text
        assert not re.search(r"(?i)password\s*=\s*['\"]", text)
        assert not re.search(r"sk-[A-Za-z0-9]{16,}", text)
