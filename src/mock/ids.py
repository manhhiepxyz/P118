"""Sinh ID theo thứ tự tăng dần: RES-001, VEH-001, BOOK-001, PAY-001."""

from itertools import count


def make_generator(prefix: str) -> "callable[[], str]":
    """Trả về hàm sinh ID dạng ``{prefix}-{seq}`` với seq bắt đầu từ 1."""
    counter = count(1)

    def _next_id() -> str:
        return f"{prefix}-{next(counter):03d}"

    return _next_id
