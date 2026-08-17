"""Biến môi trường README nêu phải có thật, và đúng tên.

Lỗi vừa xảy ra khi viết bảng này: README ghi `JWT_SECRET_KEY` trong khi biến
thật là `JWT_SECRET`. Người mới clone repo về sẽ đặt đúng cái tên README dạy,
rồi nhận HTTP 500 lúc đăng nhập mà không hiểu vì sao — README sai còn tệ hơn
README thiếu, vì nó khiến người ta tin mình đã làm đúng.

Cùng loại với `test_frontend_error_messages.py`: hai chỗ mô tả cùng một sự thật
thì phải có một chỗ bắt lúc chúng lệch nhau.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"
_ENV_EXAMPLE = _ROOT / ".env.example"


def _documented_vars() -> list[str]:
    text = _README.read_text(encoding="utf-8")
    section = text.split("## Biến môi trường", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\| `([A-Z0-9_]+)` \|", section, re.M)


def test_the_readme_actually_documents_some_variables():
    """Bảng rỗng thì mọi test dưới đây xanh một cách vô nghĩa."""
    assert len(_documented_vars()) >= 5


@pytest.mark.parametrize("name", _documented_vars())
def test_every_documented_variable_exists_in_env_example(name):
    env = _ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(rf"^#?\s*{name}=", env, re.M), f"README dạy {name} nhưng .env.example không có"


@pytest.mark.parametrize("name", ["LLM_PROVIDER", "DATABASE_URL", "TEST_DATABASE_URL", "JWT_SECRET"])
def test_the_variables_that_break_startup_are_documented(name):
    """Bốn biến này sai là hệ thống không chạy — không được thiếu khỏi README."""
    assert name in _documented_vars(), f"{name} thiếu trong bảng README"


def test_the_test_database_is_not_the_demo_database():
    """Fixture pytest có TRUNCATE. Trỏ nhầm là mất dữ liệu demo."""
    env = _ENV_EXAMPLE.read_text(encoding="utf-8")
    demo = re.search(r"^DATABASE_URL=(.+)$", env, re.M).group(1).strip()
    test = re.search(r"^TEST_DATABASE_URL=(.+)$", env, re.M).group(1).strip()
    assert demo != test
