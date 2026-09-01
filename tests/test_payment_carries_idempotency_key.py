"""`pay_fee` phải LUÔN mang khoá idempotency.

Không có khoá thì provider coi mỗi request là một giao dịch mới, và dedupe phía
provider (`create_payment`) chỉ chạy khi `idempotency_key is not None`. Lượt gọi
thứ hai rơi thẳng vào kiểm `already_paid` và trả "Booking has already been paid".

Đo nguyên văn trên stack thật:

    BOOK-048   ZONE_B   100.000 VND
    PAY-016    PAID     idempotency_key = NULL
    task T3    FAILED   "Booking has already been paid"

Tiền đã trừ thật. Chỉ có màn hình nói là thất bại.
"""

from __future__ import annotations

import inspect

import pytest

from src.connectors.payment import PaymentConnector
from src.db.parking_payment_repository import payment_idempotency_key


def test_retry_safety_is_a_capability_of_the_tool_not_state_of_the_connector() -> None:
    """`is_retry_safe` giờ trả lời "tool này GỬI ĐƯỢC khoá không", không phải
    "connector này có sẵn khoá không".

    Connector được dựng MỘT lần cho cả workflow và dùng chung cho mọi task, nên
    state của nó không thể là dữ liệu của một lần gọi. Câu hỏi "lần gọi NÀY có
    khoá không" do Executor trả lời, vì chỉ nó cầm permit.
    """
    connector = PaymentConnector(base_url="http://x")
    assert connector.is_retry_safe("pay_fee") is True
    assert connector.is_retry_safe("search_properties") is False


def test_a_payment_without_a_key_is_still_only_sent_once() -> None:
    """Bất biến CŨ vẫn được giữ, chỉ đổi chỗ giữ.

    Trước: `is_retry_safe` trả False nên Executor không thử lại. Giờ: lần thử
    thứ hai bị `prepare_submission` chặn — trạng thái là SUBMITTING và không có
    khoá nào để provider dedupe (`IN_FLIGHT_WITHOUT_KEY`). Hàng rào thật nằm ở
    database, chỗ nó quan sát được sau restart.
    """
    from src.db.workflow_repository import WorkflowRepository

    source = inspect.getsource(WorkflowRepository.prepare_submission)
    assert "IN_FLIGHT_WITHOUT_KEY" in source


def test_the_key_is_derived_from_the_workflow_and_the_booking() -> None:
    connector = PaymentConnector(base_url="http://x")
    key = connector.idempotency_key_for("wf-1", "T1", "pay_fee", {"booking_id": "BOOK-048", "amount": 100000})

    assert key == payment_idempotency_key("wf-1", "BOOK-048")
    assert "wf-1" in key and "BOOK-048" in key


def test_the_same_booking_in_the_same_workflow_always_yields_one_key() -> None:
    """Deterministic: retry sau timeout, sau restart, hay từ đường resume khác
    đều phải rơi vào đúng một khoá."""
    a = PaymentConnector(base_url="http://x")
    b = PaymentConnector(base_url="http://y")
    args = ("wf-1", "T1", "pay_fee", {"booking_id": "BOOK-048"})

    assert a.idempotency_key_for(*args) == b.idempotency_key_for(*args)


def test_different_workflows_do_not_share_a_key() -> None:
    """Bỏ `workflow_id` khỏi khoá thì một lần trả tiền MỚI cho booking đã hoàn
    tiền sẽ rơi vào bản ghi REFUNDED cũ và bị coi là đã trả."""
    connector = PaymentConnector(base_url="http://x")
    booking = {"booking_id": "BOOK-048"}

    assert connector.idempotency_key_for("wf-1", "T1", "pay_fee", booking) != connector.idempotency_key_for(
        "wf-2", "T1", "pay_fee", booking
    )


def test_the_context_key_is_the_only_key_that_goes_out() -> None:
    """Không còn "khoá đặt tay ở constructor". Khoá đi ra dây là khoá của
    `ProviderCallContext`, tức khoá database đang giữ."""
    import inspect as _inspect

    from src.connectors.base import ProviderCallContext

    body = _inspect.getsource(PaymentConnector.execute)
    assert "context.idempotency_key" in body
    # Bỏ ghi chú trước khi soi: chúng NHẮC tên cũ để giải thích vì sao nó biến mất.
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))
    assert "self._idempotency_key" not in code
    assert ProviderCallContext.__dataclass_params__.frozen is True


@pytest.mark.parametrize("payload", [{}, {"booking_id": ""}, {"booking_id": None}])
def test_no_booking_means_no_key_rather_than_a_wrong_one(payload: dict) -> None:
    """Thà không có khoá còn hơn một khoá sai — khoá sai gộp hai giao dịch
    khác nhau làm một."""
    connector = PaymentConnector(base_url="http://x")
    assert connector.idempotency_key_for("wf-1", "T1", "pay_fee", payload) is None


# --- Lá chắn cho ĐƯỜNG DÂY, không chỉ cho hàm dựng khoá --------------------
#
# Các test trên gọi thẳng `idempotency_key_for` — chúng chỉ kiểm CÔNG THỨC.
# Công thức đúng mà khoá không đi ra dây thì `pay_fee` vẫn đi tới provider không
# mang khoá, đúng trạng thái đã gây ra PAY-016. `_key_for` (bản cũ đọc
# `self._workflow_id`) đã bị xoá: một connector dùng chung không được giữ dữ
# liệu của một lần gọi.


def test_build_connectors_hands_the_workflow_to_the_payment_connector() -> None:
    from src.orchestration.deps import build_connectors

    connectors = build_connectors(workflow_id="wf-1")
    payment = next(c for c in connectors if "pay_fee" in c.tool_names)

    assert payment.is_retry_safe("pay_fee") is True, (
        "connector thanh toán không nhận được workflow_id nên không dựng được khoá"
    )


@pytest.mark.asyncio
async def test_execute_actually_sends_the_header() -> None:
    """Kiểm thứ ĐI RA DÂY, không phải thứ tính được trong bộ nhớ."""
    import httpx

    seen: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("Idempotency-Key")
        return httpx.Response(
            200,
            json={"success": True, "data": {"payment_id": "PAY-1", "payment_status": "PAID"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    from src.connectors.base import ProviderCallContext

    connector = PaymentConnector(base_url="http://x", client=client)
    # Khoá do ORCHESTRATION cấp cho từng lần gọi, và đó là khoá database đang
    # giữ — không phải khoá connector tự dựng từ state của mình.
    key = connector.idempotency_key_for("wf-1", "T1", "pay_fee", {"booking_id": "BOOK-048"})

    await connector.execute(
        "pay_fee",
        {"booking_id": "BOOK-048", "amount": 100000, "currency": "VND"},
        context=ProviderCallContext(idempotency_key=key),
    )

    assert seen["key"] == payment_idempotency_key("wf-1", "BOOK-048"), "request đi ra KHÔNG mang khoá idempotency"


@pytest.mark.asyncio
async def test_no_key_in_the_context_means_no_header_at_all() -> None:
    """Không có khoá thì gửi KHÔNG khoá — connector không được tự dựng một cái.

    Đây là chỗ một fallback lẻn về được: `context.idempotency_key or self._something`.
    Trông vô hại, nhưng nó dựng lại đúng lỗi cũ — khoá đi ra dây không còn là
    khoá database đang giữ, và sau restart hai lượt gửi mang hai khoá khác nhau.

    Gửi không khoá là trung thực: provider không dedupe, và `prepare_submission`
    biết điều đó nên nó sẽ không cho gửi lại.
    """
    import httpx

    from src.connectors.base import ProviderCallContext

    seen: dict = {}

    async def _handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("Idempotency-Key")
        seen["all"] = dict(request.headers)
        return httpx.Response(200, json={"success": True, "data": {"payment_id": "PAY-X", "payment_status": "PAID"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://x")
    connector = PaymentConnector(base_url="http://x", client=client)

    await connector.execute(
        "pay_fee",
        {"booking_id": "BOOK-048", "amount": 100000, "currency": "VND"},
        context=ProviderCallContext(idempotency_key=None),
    )
    assert seen["key"] is None, f"connector tự dựng khoá: {seen['key']}"

    await connector.execute("pay_fee", {"booking_id": "BOOK-048", "amount": 100000, "currency": "VND"}, context=None)
    assert seen["key"] is None
