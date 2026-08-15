"""Xoá lịch sử workflow: chỉ của mình, chỉ việc đã xong, và không mất bằng chứng.

Ba tính chất được kiểm riêng vì mỗi cái hỏng theo một kiểu khác nhau:

  - quyền  — xoá được workflow của người khác là IDOR, và "không tìm thấy" phải
             giống hệt "không phải của bạn", nếu không kẻ dò biết id nào có thật
  - phạm vi — yêu cầu đang chờ duyệt thanh toán mà biến khỏi danh sách thì khoản
             tiền vẫn treo, chỗ đỗ vẫn bị giữ, và người dùng hết đường nhìn thấy
  - dữ liệu — `payments` và `payment_approvals` là bằng chứng tiền đã đi; dọn
             màn hình không được xoá dấu vết giao dịch của chính người dùng
"""

from __future__ import annotations

from typing import Any

import pytest


class _Conn:
    """Đủ hình dạng asyncpg mà `delete_workflow_for_owner` dùng."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.updates: list[str] = []

    async def fetchrow(self, *_args) -> dict[str, Any] | None:
        return self.row

    async def execute(self, sql: str, *_args) -> str:
        self.updates.append(" ".join(sql.split()))
        return "UPDATE 1"

    def transaction(self):
        return _Ctx(self)


class _Ctx:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_exc) -> bool:
        return False


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self):
        return _Ctx(self.conn)


def _repository(row: dict[str, Any] | None):
    from src.db.workflow_repository import WorkflowRepository

    conn = _Conn(row)
    repository = WorkflowRepository.__new__(WorkflowRepository)
    repository._pool = _Pool(conn)
    return repository, conn


OWNER = "11111111-1111-1111-1111-111111111111"
STRANGER = "22222222-2222-2222-2222-222222222222"
WF = "33333333-3333-3333-3333-333333333333"


# ---------------------------------------------------------------------------
# Quyền
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stranger_cannot_delete_someone_elses_request():
    repository, conn = _repository({"status": "SUCCESS", "owner_user_id": OWNER, "archived_at": None})
    assert await repository.delete_workflow_for_owner(WF, owner_user_id=STRANGER) is None
    assert conn.updates == [], "đã ghi vào workflow của người khác"


@pytest.mark.asyncio
async def test_a_missing_request_and_a_stolen_one_look_identical():
    """Khác nhau ở đây là một oracle: kẻ dò biết id nào có thật."""
    repository_missing, _ = _repository(None)
    repository_stolen, _ = _repository({"status": "SUCCESS", "owner_user_id": OWNER, "archived_at": None})
    assert await repository_missing.delete_workflow_for_owner(WF, owner_user_id=STRANGER) is None
    assert await repository_stolen.delete_workflow_for_owner(WF, owner_user_id=STRANGER) is None


# ---------------------------------------------------------------------------
# Phạm vi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["SUCCESS", "FAILED", "CANCELLED"])
@pytest.mark.asyncio
async def test_a_finished_request_can_be_deleted(status):
    repository, conn = _repository({"status": status, "owner_user_id": OWNER, "archived_at": None})
    outcome = await repository.delete_workflow_for_owner(WF, owner_user_id=OWNER)
    assert outcome == {"deleted": True, "status": status}
    assert len(conn.updates) == 1


@pytest.mark.parametrize("status", ["PENDING", "RUNNING", "NEEDS_INFORMATION", "WAITING_APPROVAL"])
@pytest.mark.asyncio
async def test_an_unfinished_request_is_refused(status):
    """WAITING_APPROVAL là ca đắt nhất: giấu nó đi là giấu một khoản tiền đang treo."""
    repository, conn = _repository({"status": status, "owner_user_id": OWNER, "archived_at": None})
    outcome = await repository.delete_workflow_for_owner(WF, owner_user_id=OWNER)
    assert outcome == {"deleted": False, "status": status}
    assert conn.updates == []


@pytest.mark.asyncio
async def test_deleting_twice_is_not_an_error():
    """Bấm hai lần, hoặc hai tab cùng bấm — không được thành 500."""
    repository, conn = _repository({"status": "SUCCESS", "owner_user_id": OWNER, "archived_at": "2026-08-15"})
    assert await repository.delete_workflow_for_owner(WF, owner_user_id=OWNER) == {
        "deleted": True,
        "status": "SUCCESS",
    }
    assert conn.updates == []


# ---------------------------------------------------------------------------
# Dữ liệu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_it_hides_the_row_instead_of_destroying_it():
    """Xoá mềm. `payments`/`payment_approvals` là bằng chứng tiền đã đi."""
    repository, conn = _repository({"status": "SUCCESS", "owner_user_id": OWNER, "archived_at": None})
    await repository.delete_workflow_for_owner(WF, owner_user_id=OWNER)
    written = conn.updates[0].upper()
    assert "ARCHIVED_AT = NOW()" in written
    assert "DELETE" not in written, "xoá cứng — mất dấu vết giao dịch của người dùng"


@pytest.mark.asyncio
async def test_it_locks_the_row_before_deciding():
    """Không khoá thì hai request song song đọc cùng trạng thái cũ."""
    repository, conn = _repository({"status": "SUCCESS", "owner_user_id": OWNER, "archived_at": None})

    seen: list[str] = []
    original = conn.fetchrow

    async def spy(sql, *args):
        seen.append(" ".join(sql.split()).upper())
        return await original(sql, *args)

    conn.fetchrow = spy
    await repository.delete_workflow_for_owner(WF, owner_user_id=OWNER)
    assert any("FOR UPDATE" in sql for sql in seen)
