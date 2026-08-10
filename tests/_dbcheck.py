"""Guard dùng chung cho các test cần PostgreSQL thật.

Vì sao cần: nếu `TEST_DATABASE_URL` không được set, các test PostgreSQL sẽ tự
skip và suite vẫn báo xanh — toàn bộ tầng database không được kiểm mà không ai
biết. Chấp nhận được khi dev chạy local, KHÔNG chấp nhận được trong CI.

Quy tắc:
  - Local (không có biến CI): thiếu `TEST_DATABASE_URL` → skip, kèm hướng dẫn.
  - CI (`CI=true`): thiếu `TEST_DATABASE_URL` → FAIL ngay, không skip.
"""

from __future__ import annotations

import os

import pytest

_MISSING_MESSAGE = (
    "TEST_DATABASE_URL chưa được set — không thể chạy test PostgreSQL. "
    "Set biến này trong .env (local) hoặc trong job env (CI)."
)


def is_ci() -> bool:
    """True nếu đang chạy trong CI.

    GitHub Actions luôn set ``CI=true``; các CI khác cũng theo quy ước này.
    """
    return os.environ.get("CI", "").lower() in {"1", "true", "yes"}


def require_test_database_url() -> str:
    """Trả về `TEST_DATABASE_URL`.

    Thiếu biến: FAIL trong CI, skip khi chạy local.
    """
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        return test_url

    if is_ci():
        pytest.fail(f"{_MISSING_MESSAGE} Trong CI, test PostgreSQL không được phép skip.")
    pytest.skip(_MISSING_MESSAGE)
