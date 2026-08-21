"""Spy GHI THẬT xuống PostgreSQL — cho tầng "duyệt → chạy tiếp → hoàn tất".

`SpyConnector` trả output canonical nhưng không để lại dấu vết nào trong
database. Điều đó đủ cho tầng A/B, và KHÔNG đủ ở đây: ba bất biến quan trọng
nhất của đường resume đều được phát biểu bằng dòng dữ liệu nghiệp vụ, không
bằng số lần gọi.

  * `resume_payment_after_approval` đọc lại báo giá bằng `quote_from_database`.
    Không có dòng `parking_bookings` thì nó dừng ở NOT_FOUND và mọi assert phía
    sau nói về một luồng chưa từng chạy.
  * "đúng MỘT payment" chỉ kiểm được nếu có bảng `payments` thật, với đúng
    `uq_payments_idempotency_key` làm trọng tài.
  * "duyệt lần hai không thu tiền lần hai" phải do KHOÁ chặn, không do test tự
    đếm — và khoá ấy chỉ có nghĩa khi nó đi vào một câu INSERT thật.

Nên spy này gọi thẳng `src.db.parking_payment_repository`, tức đúng module mà
Transport provider và Payment provider dùng. Nó KHÔNG mô phỏng nghiệp vụ; nó
chạy nghiệp vụ thật, chỉ bỏ chặng HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.db.parking_payment_repository import (
    BookingError,
    create_booking,
    create_payment,
    create_vehicle,
)
from tests.matrix.spies import OUTPUTS, Call


@dataclass
class DomainSpyConnector:
    """Một connector phục vụ mọi tool, ghi lời gọi VÀ ghi dữ liệu nghiệp vụ."""

    pool: Any = None
    tool_names: list[str] = field(default_factory=lambda: sorted(OUTPUTS))
    calls: list[Call] = field(default_factory=list)
    # tool → message; dùng để dựng kịch bản provider từ chối.
    fail_tools: dict[str, str] = field(default_factory=dict)

    def is_retry_safe(self, tool_name: str) -> bool:
        return False

    def idempotency_key_for(self, workflow_id, task_id, tool_name, resolved_input):
        """Chỉ `pay_fee` có khoá, và khoá theo BOOKING — đúng công thức production."""
        if tool_name != "pay_fee":
            return None
        booking = (resolved_input or {}).get("booking_id")
        return f"wf:{workflow_id}:booking:{booking}" if booking else None

    async def execute(self, tool_name, input_data, *, context=None):
        self.calls.append(
            Call(
                tool=tool_name,
                input_data=dict(input_data),
                idempotency_key=getattr(context, "idempotency_key", None),
            )
        )
        if tool_name in self.fail_tools:
            return StandardResult.fail(ErrorCode.UNKNOWN_EXTERNAL_ERROR, self.fail_tools[tool_name], retryable=False)
        try:
            return await self._domain(tool_name, input_data, context)
        except BookingError as exc:
            return StandardResult.fail(ErrorCode.UNKNOWN_EXTERNAL_ERROR, str(exc), retryable=False)

    async def _domain(self, tool_name, input_data, context) -> StandardResult:
        if tool_name == "register_vehicle":
            vehicle = await create_vehicle(
                self.pool,
                resident_id=input_data["resident_id"],
                plate_number=input_data["plate_number"],
                vehicle_type=input_data["vehicle_type"],
            )
            return StandardResult.ok({"vehicle_id": vehicle.vehicle_id})
        if tool_name == "book_parking":
            booking = await create_booking(
                self.pool,
                vehicle_id=input_data["vehicle_id"],
                parking_zone=input_data["parking_zone"],
                booking_date=input_data["booking_date"],
            )
            return StandardResult.ok(booking.as_output())
        if tool_name == "pay_fee":
            payment = await create_payment(
                self.pool,
                booking_id=input_data["booking_id"],
                amount=input_data["amount"],
                currency=input_data["currency"],
                idempotency_key=getattr(context, "idempotency_key", None),
            )
            return StandardResult.ok(payment.as_output())
        return StandardResult.ok(dict(OUTPUTS[tool_name]))

    # --- tiện ích cho assert ------------------------------------------------

    @property
    def tools_called(self) -> list[str]:
        return [c.tool for c in self.calls]

    def count(self, tool: str) -> int:
        return sum(1 for c in self.calls if c.tool == tool)

    def input_of(self, tool: str) -> dict[str, Any]:
        return next(c.input_data for c in self.calls if c.tool == tool)

    def key_of(self, tool: str) -> str | None:
        return next(c.idempotency_key for c in self.calls if c.tool == tool)
