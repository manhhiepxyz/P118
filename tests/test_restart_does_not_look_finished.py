"""Mất tiến trình trong RAM không được đọc thành "đã xong".

`_DEMO_JOBS` chỉ sống trong process. Restart backend là mất sạch, và mọi
workflow chưa kết thúc đều đi qua nhánh "không tìm thấy job". Mặc định cũ ở đó
là `FINISHED`, nên trang chi tiết hiện cùng lúc:

    tiêu đề     : Yêu cầu đã hoàn tất.
    trạng thái  : Đang thực hiện

Đo được trên stack thật, workflow 186a24b3 sau một lần `docker compose up -d
backend`: `status='RUNNING'` mà `stage='FINISHED'`.

Người dùng đọc tiêu đề trước. Họ đóng tab, tin rằng lịch đã đặt xong.
"""

from __future__ import annotations

import inspect

from src.api import routes


def test_stage_is_derived_from_the_database_when_the_job_is_gone() -> None:
    source = inspect.getsource(routes)
    assert 'stage = job["stage"] if job is not None else "FINISHED"' not in source, (
        "job mất khỏi RAM lại được coi là đã hoàn tất — sau restart, mọi yêu "
        "cầu đang chạy đều báo xong"
    )
    assert 'stage = "FINISHED" if status in {"SUCCESS", "CANCELLED", "FAILED"} else "EXECUTING"' in source, (
        "stage dự phòng không còn đọc theo trạng thái đã ghi trong database"
    )


def test_the_two_lines_never_contradict_each_other() -> None:
    """Câu của `stage` phải hợp với `status` mà nó đi kèm.

    Kiểm bằng chính bảng câu chữ: `FINISHED` nói "đã hoàn tất", nên nó không
    được ghép với một trạng thái chưa kết thúc.
    """
    assert "hoàn tất" in routes._STAGE_MESSAGES["FINISHED"]
    assert "Đang" in routes._STAGE_MESSAGES["EXECUTING"]
    assert routes._STAGE_MESSAGES["EXECUTING"] != routes._STAGE_MESSAGES["FINISHED"]
