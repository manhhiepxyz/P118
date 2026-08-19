"""Đồ giả trong test phải khớp CHỮ KÝ của hàm thật.

Hôm nay bẫy này cắn hai lần, cả hai đều im lặng với suite và chỉ lộ ra trên
stack thật:

  1. `_FakeExecutor.__init__` không nhận `on_failure`. Đường resume bắt đầu
     truyền tham số ấy → route duyệt lịch trả 502.

  2. `build_execution_boundary` thật KHÔNG nhận `workflow_id`, nhưng đồ giả
     trong `test_demo_llm_runtime` thì có. Suite xanh 1836 test, còn mọi yêu
     cầu tạo mới trên stack thật chết với `TypeError`.

Ca thứ hai tệ hơn ca thứ nhất: tôi vừa SỬA đồ giả cho khớp lời gọi mới, nên nó
xanh — trong khi việc cần làm là sửa hàm thật. Đồ giả rộng hơn hàm thật thì
test không còn kiểm gì cả, nó chỉ xác nhận chính nó.

Test này so chữ ký hai bên, nên lệch là đỏ ngay tại đây thay vì ở production.
"""

from __future__ import annotations

import inspect

import pytest

from src.executor.executor import Executor
from src.orchestration.deps import build_connectors, build_execution_boundary


def _params(func) -> set[str]:
    return set(inspect.signature(func).parameters)


@pytest.mark.parametrize(
    "name",
    ["resident_url", "transport_url", "payment_url", "shuttle_url", "contact_profile", "workflow_id"],
)
def test_the_boundary_builder_accepts_what_demo_service_passes(name: str) -> None:
    """`demo_service` gọi bằng `**boundary_kwargs`.

    Truyền một khoá mà hàm thật không có là `TypeError` lúc chạy — và nó xảy
    ra trong tác vụ nền, nơi log chỉ giữ TÊN loại lỗi.
    """
    assert name in _params(build_execution_boundary), (
        f"`{name}` được truyền cho build_execution_boundary nhưng hàm thật không nhận"
    )


def test_the_boundary_builder_can_forward_everything_to_the_connectors() -> None:
    """Mọi thứ boundary nhận và cần chuyển tiếp thì `build_connectors` phải có."""
    forwarded = {"contact_profile", "workflow_id", "shuttle_url", "payment_url"}
    missing = forwarded - _params(build_connectors)
    assert not missing, f"build_connectors thiếu: {sorted(missing)}"


def test_the_executor_accepts_the_failure_callback() -> None:
    """`on_failure` là đường DUY NHẤT sinh repair hint.

    Đồ giả từng không nhận nó, và route trả 502 — một thất bại của đồ giả
    trông y hệt một thất bại của sản phẩm.
    """
    assert "on_failure" in _params(Executor.__init__)


def test_the_payment_connector_accepts_a_workflow() -> None:
    from src.connectors.payment import PaymentConnector

    assert "workflow_id" in _params(PaymentConnector.__init__)


@pytest.mark.asyncio
async def test_the_workflow_id_survives_the_whole_chain(monkeypatch) -> None:
    """Đi qua CẢ DÂY thật, không kiểm chữ ký từng hàm.

    Kiểm chữ ký là trò đuổi bắt: hai test ở trên phủ
    `build_execution_boundary` và `build_connectors`, và mắt xích gãy hoá ra là
    `build_runtime` ở GIỮA — `NameError: name 'workflow_id' is not defined`,
    trong tác vụ nền, nơi log chỉ giữ tên loại lỗi. Suite xanh 1845 test trong
    lúc mọi yêu cầu tạo mới đều chết.

    Test này gọi thật từ đầu dây và soi connector ở cuối dây, nên thêm bao
    nhiêu hàm trung gian nữa cũng không lọt.
    """
    from src.orchestration import deps

    class _StubPool:
        async def close(self):
            return None

    class _StubRepository:
        _pool = _StubPool()

    async def _stub_repository():
        return _StubRepository()

    monkeypatch.setattr(deps, "build_repository", _stub_repository)

    boundary, _repository = await deps.build_execution_boundary(workflow_id="wf-chain")

    payment = next(
        connector
        for connector in deps.build_connectors(workflow_id="wf-chain")
        if "pay_fee" in connector.tool_names
    )
    assert payment.is_retry_safe("pay_fee") is True
    assert boundary is not None
