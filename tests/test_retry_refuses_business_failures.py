"""Retry chỉ dành cho lỗi HẠ TẦNG, không dành cho lỗi nghiệp vụ.

"Khu A đã hết chỗ" chạy lại với đúng input cũ thì hỏng y hệt. Cho retry chạy ở
đó là mời người dùng bấm một nút không bao giờ hoạt động, và mỗi lần bấm là một
vòng gọi provider vô ích — trong khi lối ra thật là câu hỏi lại để họ đổi khu.

Đo trên dữ liệu thật: toàn bộ lỗi đã ghi đều `retryable=false`
(BOOKING_ALREADY_EXISTS 8, DEPENDENCY_ERROR 5, NO_AVAILABILITY 3). Nghĩa là
nếu ranh giới này sai thì nó sai với 100% trường hợp đang có.
"""

from __future__ import annotations

import inspect

import pytest

from src.orchestration import demo_service
from src.orchestration.demo_service import RetryNotAllowed


def _rows(*specs):
    return [
        {"task_id": t, "status": s, "error_code": c, "retryable": r, "tool": "book_parking", "input_data": {}}
        for t, s, c, r in specs
    ]


class _Repo:
    def __init__(self, rows):
        self._rows = rows
        self._pool = _Pool()

    async def get_workflow(self, workflow_id):
        return {"workflow": {"goal": "đặt chỗ đỗ xe", "task_plan": None}, "tasks": self._rows}


class _Pool:
    async def close(self):
        return None


@pytest.fixture
def repo(monkeypatch):
    def _install(rows):
        repository = _Repo(rows)

        async def _acquire():
            return repository

        monkeypatch.setattr(demo_service, "acquire_repository", _acquire)
        return repository

    return _install


@pytest.mark.asyncio
async def test_a_business_failure_is_refused_with_a_useful_reason(repo) -> None:
    repo(_rows(("T1", "SUCCESS", None, False), ("T2", "FAILED", "NO_AVAILABILITY", False)))

    with pytest.raises(RetryNotAllowed) as caught:
        await demo_service.retry_failed_tasks("wf-1")

    assert caught.value.code == "NOT_RETRYABLE"
    # Không được chỉ nói "không thử lại được" rồi thôi — phải chỉ lối ra.
    assert "đổi" in caught.value.message


@pytest.mark.asyncio
async def test_a_dependency_error_alone_does_not_unlock_retry(repo) -> None:
    """`DEPENDENCY_ERROR` là HỆ QUẢ.

    Nó không mang thông tin về việc chạy lại có ích hay không; quyết định phải
    dựa vào bước hỏng THẬT. Tính nhầm nó vào là mở retry cho mọi workflow có
    một bước phụ thuộc.
    """
    repo(
        _rows(
            ("T2", "FAILED", "NO_AVAILABILITY", False),
            ("T3", "FAILED", "DEPENDENCY_ERROR", True),
        )
    )

    with pytest.raises(RetryNotAllowed) as caught:
        await demo_service.retry_failed_tasks("wf-1")

    assert caught.value.code == "NOT_RETRYABLE"


@pytest.mark.asyncio
async def test_nothing_failed_means_nothing_to_retry(repo) -> None:
    repo(_rows(("T1", "SUCCESS", None, False)))

    with pytest.raises(RetryNotAllowed) as caught:
        await demo_service.retry_failed_tasks("wf-1")

    assert caught.value.code == "NOTHING_TO_RETRY"


@pytest.mark.asyncio
async def test_a_missing_workflow_is_not_found(repo) -> None:
    repository = repo([])

    async def _none(_workflow_id):
        return None

    repository.get_workflow = _none

    with pytest.raises(RetryNotAllowed) as caught:
        await demo_service.retry_failed_tasks("wf-1")

    assert caught.value.code == "NOT_FOUND"


def test_retry_reuses_the_seeding_helper_instead_of_a_second_copy() -> None:
    """Hai đường chạy lại phải dùng CHUNG cách giữ bước đã xong.

    Một bản sao thứ hai là chỗ để hai đường lệch nhau — và thứ lệch được ở đây
    là "có chạy lại `book_parking` đã thành công hay không", tức là có nhân đôi
    một chỗ đỗ đã tính phí hay không.
    """
    source = inspect.getsource(demo_service.retry_failed_tasks)
    assert "_seed_completed(" in source
    assert "on_failure=repair_manager" in source
