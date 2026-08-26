"""Mở lại một yêu cầu đang chờ trả lời (từ Lịch sử) phải giữ nguyên câu hỏi VÀ danh sách ô.

Owner: Thành Bảo (Decision layer)
File: tests/test_api/test_reopening_an_unanswered_workflow_keeps_its_question.py

ROOT CAUSE đo được trên máy người dùng: `GET /workflows/demo/{id}` trả
`status="NEEDS_INFORMATION"` nhưng `missing_fields=[]`. Nhánh DUY NHẤT đọc
`workflow_clarifications` để dựng lại câu hỏi bị khoá sau điều kiện
`job is None` — nên chỉ cần `_DEMO_JOBS` CÒN một entry cho workflow đó mà
entry ấy chưa có `response` dùng được (vừa restart một phần, vừa `retry` đặt
`response = None`, hoặc job được dựng lại bởi một đường khác), nhánh này bị bỏ
qua và response rơi xuống bản dựng từ `workflows`/`workflow_tasks` — bản đó
KHÔNG mang `question` lẫn `missing_fields`.

Hậu quả dây chuyền tới tận giao diện: `missing_fields` rỗng khiến workspace vẽ
MỘT ô "trả lời chung" khoá `answer`; ô đó không phải field của contract, nên
`POST /continue` từ chối cả lượt với "Biểu mẫu gửi lên có mục không nằm trong
câu hỏi, nên mình chưa nhận được." Người dùng gõ gì cũng hỏng y hệt, và câu
"tải lại trang" không cứu được vì lỗi không nằm ở trang.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.api import routes


@pytest.fixture
def _khong_co_viec_cho_duyet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ba loại "đang chờ" khác đều trống — cô lập đúng nhánh clarification."""
    for name in ("_load_pending_viewing", "_load_pending_payment"):
        monkeypatch.setattr(routes, name, _tra_ve(None))
    monkeypatch.setattr(routes, "_load_pending_services", _tra_ve([]))
    monkeypatch.setattr(routes, "_read_repair_hints", _tra_ve([]))
    monkeypatch.setattr(routes, "_read_events", _tra_ve([]))
    monkeypatch.setattr(routes, "_require_workflow_owner", _tra_ve(None))
    monkeypatch.setattr(routes, "_with_stored_answer", _giu_nguyen)


def _tra_ve(value: Any):
    async def _fake(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _fake


async def _giu_nguyen(response: Any, *_args: Any, **_kwargs: Any) -> Any:
    return response


_RECORD = {
    "workflow": {"status": "RUNNING", "goal": "Đặt lịch tham quan", "owner_user_id": "u1"},
    "tasks": [],
}

_CLARIFICATION = {
    "question": "Bạn cho mình biết số khách đi cùng nhé.",
    "missing_fields": ["passenger_count"],
    "goal": "Đặt lịch tham quan",
    "existing_context": {},
}


@pytest.mark.asyncio
async def test_a_partially_restored_job_still_returns_the_pending_question(
    monkeypatch: pytest.MonkeyPatch, _khong_co_viec_cho_duyet: None
) -> None:
    """`_DEMO_JOBS` còn entry nhưng CHƯA có `response` — vẫn phải dựng lại câu hỏi từ database.

    Đây chính là ca hỏng: bản trước khoá nhánh clarification sau `job is None`,
    nên một entry RAM dở dang che mất câu hỏi đã ghim trong PostgreSQL.
    """
    workflow_id = "wf-dang-cho"
    monkeypatch.setattr(routes, "read_demo_workflow", _tra_ve(dict(_RECORD)))
    monkeypatch.setattr(routes, "_load_clarification_safely", _tra_ve(dict(_CLARIFICATION)))
    monkeypatch.setitem(
        routes._DEMO_JOBS,
        workflow_id,
        {"stage": "EXECUTING", "message": "", "plan": None, "response": None, "events": []},
    )

    response = await routes._demo_workflow_status(workflow_id, {"id": "u1"})

    assert response.status == "NEEDS_INFORMATION"
    assert response.missing_fields == ["passenger_count"], (
        "ô đang chờ phải đi cùng câu hỏi — rỗng là giao diện dựng ô 'answer' giả và mọi lượt gửi đều 422"
    )
    assert response.question == _CLARIFICATION["question"]


@pytest.mark.asyncio
async def test_an_empty_job_cache_still_returns_the_pending_question(
    monkeypatch: pytest.MonkeyPatch, _khong_co_viec_cho_duyet: None
) -> None:
    """Đường đã chạy đúng từ trước (sau restart, `_DEMO_JOBS` trống) — không được hồi quy."""
    workflow_id = "wf-sau-restart"
    monkeypatch.setattr(routes, "read_demo_workflow", _tra_ve(dict(_RECORD)))
    monkeypatch.setattr(routes, "_load_clarification_safely", _tra_ve(dict(_CLARIFICATION)))
    routes._DEMO_JOBS.pop(workflow_id, None)

    response = await routes._demo_workflow_status(workflow_id, {"id": "u1"})

    assert response.status == "NEEDS_INFORMATION"
    assert response.missing_fields == ["passenger_count"]


@pytest.mark.asyncio
async def test_a_cancelled_workflow_never_reopens_its_old_question(
    monkeypatch: pytest.MonkeyPatch, _khong_co_viec_cho_duyet: None
) -> None:
    """Đã bấm Dừng thì câu hỏi cũ KHÔNG được sống lại, dù dòng clarification vẫn nằm đó.

    `cancel` chốt `workflows.status = CANCELLED` nhưng KHÔNG xoá dòng
    `workflow_clarifications`. Nới điều kiện `job is None` mà quên chặn theo
    trạng thái sẽ biến một yêu cầu đã huỷ thành "Cần thêm thông tin" lần nữa —
    đúng triệu chứng người dùng báo: bấm Dừng mà màn hình vẫn đòi bổ sung.
    """
    workflow_id = "wf-da-huy"
    record = {"workflow": {"status": "CANCELLED", "goal": "x", "owner_user_id": "u1"}, "tasks": []}
    monkeypatch.setattr(routes, "read_demo_workflow", _tra_ve(record))
    monkeypatch.setattr(routes, "_load_clarification_safely", _tra_ve(dict(_CLARIFICATION)))
    routes._DEMO_JOBS.pop(workflow_id, None)

    response = await routes._demo_workflow_status(workflow_id, {"id": "u1"})

    assert response.status == "CANCELLED"
    assert response.missing_fields == []
