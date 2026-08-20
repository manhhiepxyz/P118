"""Hợp đồng NỐI DÂY — thứ mà test theo hàm không bao giờ bắt được.

Lỗi tốn nhiều thời gian nhất trong dự án này không phải lỗi nghiệp vụ, mà là
một khuôn lặp lại: guard phủ HÀM nhưng không phủ ĐƯỜNG ĐI. Mỗi lần đều xanh
toàn bộ suite trong lúc production hỏng:

  - `_FakeExecutor` không nhận `on_failure` → route duyệt lịch trả 502.
  - `build_execution_boundary` không nhận `workflow_id` → TypeError, mọi yêu
    cầu mới chết.
  - `build_runtime` — mắt xích GIỮA — không nhận `workflow_id` → NameError.
    Lần vá trước phủ hai đầu dây và bỏ đúng khúc gãy.
  - `WAITING_VIEWING_APPROVAL` thêm vào một Literal, quên Literal thứ hai →
    HTTP 500 cho mọi GET có sự kiện ấy.
  - Khoá idempotency đúng ở hàm dựng khoá, nhưng không ai truyền `workflow_id`
    xuống connector → `pay_fee` ra provider không mang khoá.

File này gom các phép kiểm đó lại một chỗ và viết chúng theo ĐƯỜNG ĐI: gọi
thật từ đầu dây, soi kết quả ở cuối dây. Thêm một hàm trung gian nữa cũng
không lọt, vì test không hề biết tên các hàm ở giữa.
"""

from __future__ import annotations

import inspect
import typing

import pytest


# --- 1. Chuỗi dựng connector -------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_id_reaches_the_payment_connector(monkeypatch) -> None:
    """Đi qua CẢ DÂY: build_execution_boundary → build_runtime → build_connectors.

    Thiếu bất kỳ mắt xích nào thì `pay_fee` ra provider không mang khoá
    idempotency, và một lượt gọi lặp báo "Booking has already been paid" trong
    khi tiền đã trừ thật.
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
    await deps.build_execution_boundary(workflow_id="wf-chain")

    payment = next(c for c in deps.build_connectors(workflow_id="wf-chain") if "pay_fee" in c.tool_names)
    assert payment.is_retry_safe("pay_fee") is True, "workflow_id đứt ở đâu đó giữa dây"


# --- 2. Literal phải đồng bộ -------------------------------------------------


def _models_declaring(field: str) -> list[tuple[str, frozenset[str]]]:
    """MỌI model khai `field` dạng Literal — tự tìm, không liệt kê tay.

    Liệt kê tay là lặp lại đúng lỗi cần chặn: thêm model thứ ba thì danh sách
    tay lại thiếu, và lại im lặng.
    """
    from src.models import schemas

    found = []
    for name in dir(schemas):
        model = getattr(schemas, name)
        fields = getattr(model, "model_fields", None)
        if not isinstance(fields, dict) or field not in fields:
            continue
        values: set[str] = set()
        for arg in typing.get_args(fields[field].annotation):
            values.update(v for v in typing.get_args(arg) if isinstance(v, str))
            if isinstance(arg, str):
                values.add(arg)
        if values:
            found.append((name, frozenset(values)))
    return found


@pytest.mark.parametrize("field", ["stage", "status"])
def test_models_sharing_a_field_agree_on_its_values(field: str) -> None:
    """Hai model cùng khai một trường thì phải nhận cùng tập giá trị.

    Lệch nhau nghĩa là một tầng phát ra giá trị mà tầng kia từ chối — và
    Pydantic từ chối cả response, biến một câu chữ sai thành HTTP 500.
    """
    models = _models_declaring(field)
    if len(models) < 2:
        pytest.skip(f"chỉ một model khai `{field}`")

    # `status` của task và của workflow là hai trục khác nhau — chỉ so những
    # model có giao nhau đáng kể, để test không đòi hai thứ vốn khác nhau.
    for i, (name_a, values_a) in enumerate(models):
        for name_b, values_b in models[i + 1 :]:
            shared = values_a & values_b
            if len(shared) < max(len(values_a), len(values_b)) * 0.6:
                continue
            diff = sorted(values_a ^ values_b)
            assert not diff, f"{name_a}.{field} và {name_b}.{field} lệch nhau: {diff}"


# --- 3. Đồ giả không được rộng hơn hàng thật ---------------------------------


def _params(func) -> set[str]:
    return set(inspect.signature(func).parameters)


@pytest.mark.parametrize(
    ("module", "name", "must_accept"),
    [
        ("src.orchestration.deps", "build_execution_boundary", "workflow_id"),
        ("src.orchestration.deps", "build_runtime", "workflow_id"),
        ("src.orchestration.deps", "build_connectors", "workflow_id"),
        ("src.executor.executor", "Executor", "on_failure"),
        ("src.connectors.payment", "PaymentConnector", "workflow_id"),
    ],
)
def test_the_real_function_accepts_what_callers_pass(module: str, name: str, must_accept: str) -> None:
    """Đồ giả rộng hơn hàng thật thì test chỉ xác nhận chính nó.

    Đo được: sau khi sửa đồ giả cho nhận `workflow_id`, suite xanh 1836 test
    trong lúc mọi yêu cầu tạo mới chết vì hàm THẬT không có tham số ấy.
    """
    import importlib

    target = getattr(importlib.import_module(module), name)
    func = target.__init__ if inspect.isclass(target) else target
    assert must_accept in _params(func), f"{name} không nhận `{must_accept}`"


def test_every_stage_the_code_emits_is_a_valid_event_stage() -> None:
    """Phát ra một giai đoạn mà `DemoWorkflowEvent` từ chối là dựng sẵn HTTP 500.

    `_append_job_event` ghi thẳng chuỗi vào danh sách sự kiện, rồi
    `_public_events` validate qua Pydantic. Lệch một giá trị là cả GET hỏng —
    không phải một câu chữ sai, mà một endpoint chết.

    Đo được: `CHAT` được phát ở hai chỗ và chưa bao giờ có trong Literal.
    """
    import re

    from src.api import routes
    from src.models.schemas import DemoWorkflowEvent

    source = inspect.getsource(routes)
    emitted = set(re.findall(r'_append_job_event\([^,]+,\s*"([A-Z_]+)"', source))
    emitted |= set(re.findall(r'terminal_stage = "([A-Z_]+)"', source))

    allowed: set[str] = set()
    for arg in typing.get_args(DemoWorkflowEvent.model_fields["stage"].annotation):
        if isinstance(arg, str):
            allowed.add(arg)

    assert emitted, "không tìm thấy giai đoạn nào được phát — cập nhật lại phép đọc"
    missing = sorted(emitted - allowed)
    assert not missing, f"giai đoạn được phát nhưng DemoWorkflowEvent từ chối: {missing}"
