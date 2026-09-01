"""Facade phải expose MỌI method public của repository con.

Sự cố: `DELETE /workflows/demo/{id}` trả 500 với
`AttributeError: 'PostgreSQLWorkflowStateRepository' object has no attribute
'delete_workflow_for_owner'`. Method tồn tại ở `WorkflowRepository` nhưng chưa
bao giờ được expose qua facade — nên nút Xoá trên trang Lịch sử không xoá được
gì, và người dùng chỉ thấy nó im lặng không phản hồi.

Lỗi này im lặng ở mọi tầng TRỪ runtime: type checker không bắt (facade dùng
`__getattr__`-style composition), test đơn vị không bắt (chúng gọi thẳng lớp
con), và test tích hợp chỉ bắt nếu có ai đó nhớ gọi đúng method ấy.

Đây là phép kiểm cấu trúc: một method mới thêm vào lớp con mà quên bắc cầu sẽ
đỏ ngay, thay vì đợi tới lúc người dùng bấm nút.
"""

from __future__ import annotations

import inspect

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.db.workflow_repository import WorkflowRepository


def test_every_public_method_is_reachable_through_the_facade():
    def public(cls) -> set[str]:
        return {name for name, _ in inspect.getmembers(cls, inspect.isfunction) if not name.startswith("_")}

    thieu = sorted(public(WorkflowRepository) - public(PostgreSQLWorkflowStateRepository))

    assert thieu == [], (
        "Method có ở WorkflowRepository nhưng KHÔNG bắc cầu qua facade — "
        f"route gọi vào sẽ nổ AttributeError lúc chạy: {thieu}"
    )


def _required_keywords(func) -> set[str]:
    """Tham số BẮT BUỘC của một hàm, bỏ `self` và bỏ `*args/**kwargs`."""
    params = inspect.signature(func).parameters
    return {
        name
        for name, param in params.items()
        if name != "self"
        and param.default is inspect.Parameter.empty
        and param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }


def _accepts_anything(func) -> bool:
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in inspect.signature(func).parameters.values())


def test_the_facade_can_actually_satisfy_what_it_forwards_to():
    """Tồn tại thôi CHƯA đủ — chữ ký cũng phải gọi được.

    Guard cũ chỉ kiểm method có mặt. Nhưng facade chuyển tiếp bằng `**kwargs`:

        async def save_assistant_response(self, workflow_id, **kwargs):
            await self.workflows.save_assistant_response(workflow_id, **kwargs)

    Lớp con thêm một tham số BẮT BUỘC mới thì facade vẫn có method, vẫn gọi
    được, và vẫn nổ `TypeError` lúc chạy — trong tác vụ nền, nơi log chỉ giữ
    TÊN loại lỗi.

    Đây đúng khuôn lỗi đã cắn nhiều lần trong dự án này: `build_runtime` thiếu
    `workflow_id` (NameError, mọi yêu cầu mới chết), `_FakeExecutor` thiếu
    `on_failure` (502), `build_execution_boundary` thiếu `workflow_id`
    (TypeError). Mỗi lần đều xanh toàn bộ suite.
    """
    problems: list[str] = []
    for name, delegate in inspect.getmembers(WorkflowRepository, inspect.isfunction):
        if name.startswith("_"):
            continue
        facade = getattr(PostgreSQLWorkflowStateRepository, name, None)
        if facade is None:
            continue  # test ở trên đã lo phần thiếu method
        if _accepts_anything(facade):
            continue  # `**kwargs` chuyển tiếp được mọi thứ
        missing = _required_keywords(delegate) - set(inspect.signature(facade).parameters)
        if missing:
            problems.append(f"{name}: facade thiếu {sorted(missing)}")

    assert not problems, "facade không gọi nổi lớp con:\n  " + "\n  ".join(problems)
