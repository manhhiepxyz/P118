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
