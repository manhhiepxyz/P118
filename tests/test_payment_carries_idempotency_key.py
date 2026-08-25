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

import pytest

from src.connectors.payment import PaymentConnector
from src.db.parking_payment_repository import payment_idempotency_key


def test_a_connector_without_a_workflow_cannot_promise_retry_safety() -> None:
    """Không có gì để dựng khoá thì phải nói thẳng là không retry an toàn."""
    assert PaymentConnector(base_url="http://x").is_retry_safe("pay_fee") is False


def test_a_workflow_scoped_connector_is_retry_safe() -> None:
    connector = PaymentConnector(base_url="http://x", workflow_id="wf-1")
    assert connector.is_retry_safe("pay_fee") is True


def test_the_key_is_derived_from_the_workflow_and_the_booking() -> None:
    connector = PaymentConnector(base_url="http://x", workflow_id="wf-1")
    key = connector._key_for({"booking_id": "BOOK-048", "amount": 100000})

    assert key == payment_idempotency_key("wf-1", "BOOK-048")
    assert "wf-1" in key and "BOOK-048" in key


def test_the_same_booking_in_the_same_workflow_always_yields_one_key() -> None:
    """Deterministic: retry sau timeout, sau restart, hay từ đường resume khác
    đều phải rơi vào đúng một khoá."""
    a = PaymentConnector(base_url="http://x", workflow_id="wf-1")
    b = PaymentConnector(base_url="http://y", workflow_id="wf-1")

    assert a._key_for({"booking_id": "BOOK-048"}) == b._key_for({"booking_id": "BOOK-048"})


def test_different_workflows_do_not_share_a_key() -> None:
    """Bỏ `workflow_id` khỏi khoá thì một lần trả tiền MỚI cho booking đã hoàn
    tiền sẽ rơi vào bản ghi REFUNDED cũ và bị coi là đã trả."""
    a = PaymentConnector(base_url="http://x", workflow_id="wf-1")
    b = PaymentConnector(base_url="http://x", workflow_id="wf-2")

    assert a._key_for({"booking_id": "BOOK-048"}) != b._key_for({"booking_id": "BOOK-048"})


def test_an_explicit_key_still_wins() -> None:
    """`resume_payment_after_approval` truyền khoá sẵn — không được ghi đè."""
    connector = PaymentConnector(base_url="http://x", workflow_id="wf-1", idempotency_key="đặt-tay")
    assert connector._idempotency_key == "đặt-tay"


@pytest.mark.parametrize("payload", [{}, {"booking_id": ""}, {"booking_id": None}])
def test_no_booking_means_no_key_rather_than_a_wrong_one(payload: dict) -> None:
    """Thà không có khoá còn hơn một khoá sai — khoá sai gộp hai giao dịch
    khác nhau làm một."""
    connector = PaymentConnector(base_url="http://x", workflow_id="wf-1")
    assert connector._key_for(payload) is None


# --- Lá chắn cho ĐƯỜNG DÂY, không chỉ cho hàm dựng khoá --------------------
#
# Bảy test trên gọi thẳng `_key_for`. Gỡ `or self._key_for(input_data)` khỏi
# `execute`, hoặc gỡ `workflow_id=workflow_id` khỏi `build_connectors`, thì cả
# bảy vẫn xanh — trong khi `pay_fee` lại đi ra provider không mang khoá, đúng
# trạng thái đã gây ra PAY-016.


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
    connector = PaymentConnector(base_url="http://x", workflow_id="wf-1", client=client)

    await connector.execute("pay_fee", {"booking_id": "BOOK-048", "amount": 100000, "currency": "VND"})

    assert seen["key"] == payment_idempotency_key("wf-1", "BOOK-048"), (
        "request đi ra KHÔNG mang khoá idempotency"
    )
