"""Connector gián điệp có contract THẬT, dùng chung cho tầng B và C.

Không phải một fake "luôn trả thành công": nó trả đúng output field mà
`TOOL_CONTRACTS` khai báo, nên một kế hoạch nối sai `InputRef` sẽ hỏng ở đây
thay vì đi tiếp và hỏng ở chỗ khó đọc hơn.

Nó ghi lại THỨ TỰ gọi, INPUT provider nhận, và SỐ LẦN — ba thứ mà một assert
trên response cuối không nói gì về chúng.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.common.submission import EXTERNAL_ID_FIELD_BY_TOOL


@dataclass
class Call:
    tool: str
    input_data: dict[str, Any]
    idempotency_key: str | None


_OUTPUTS: dict[str, dict[str, Any]] = {
    "schedule_property_viewing": {
        "viewing_id": "VIEW-1",
        "viewing_status": "SCHEDULED",
        "project_id": "PRJ-001",
        "project_name": "Vinhomes Ocean Park",
    },
    "book_shuttle": {"shuttle_id": "SHU-1", "driver_name": "Anh Tài", "pickup_time": "08:45"},
    "register_property_interest": {"interest_id": "INT-1", "interest_status": "RECEIVED"},
    "create_maintenance_request": {"maintenance_id": "MNT-1", "maintenance_status": "RECEIVED"},
    "schedule_move": {"move_request_id": "MOV-1", "move_status": "SCHEDULED"},
    "register_vehicle": {"vehicle_id": "VEH-1"},
    "book_parking": {"booking_id": "BOOK-1", "amount": 120000, "currency": "VND", "parking_zone": "ZONE_A"},
    "pay_fee": {"payment_id": "PAY-1", "payment_status": "PAID"},
}


@dataclass
class SpyConnector:
    """Một connector phục vụ MỌI tool với tới được, ghi lại từng lời gọi."""

    tool_names: list[str] = field(default_factory=lambda: sorted(_OUTPUTS))
    calls: list[Call] = field(default_factory=list)
    # tool → số lần đầu tiên phải trả lỗi; dùng để dựng kịch bản hỏng.
    fail_tools: dict[str, str] = field(default_factory=dict)

    def is_retry_safe(self, tool_name: str) -> bool:
        return False

    def idempotency_key_for(self, workflow_id, task_id, tool_name, resolved_input):
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
        return StandardResult.ok(dict(_OUTPUTS[tool_name]))

    # --- tiện ích cho assert ------------------------------------------------

    @property
    def tools_called(self) -> list[str]:
        return [c.tool for c in self.calls]

    def count(self, tool: str) -> int:
        return sum(1 for c in self.calls if c.tool == tool)

    def input_of(self, tool: str) -> dict[str, Any]:
        return next(c.input_data for c in self.calls if c.tool == tool)

    def external_id_of(self, tool: str) -> Any:
        return _OUTPUTS[tool][EXTERNAL_ID_FIELD_BY_TOOL[tool]]


# Alias công khai cho tầng D: spy ghi thật xuống PostgreSQL vẫn cần đúng bộ
# output canonical này cho các tool không có bảng nghiệp vụ riêng.
OUTPUTS = _OUTPUTS
