from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InputRef(BaseModel):
    """Reference to an output field from a previously completed task."""

    model_config = ConfigDict(extra="forbid")

    from_task: Annotated[str, Field(min_length=1)]
    field: Annotated[str, Field(min_length=1)]

    @field_validator("from_task", "field")
    @classmethod
    def not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank or whitespace")
        return v


# A task input value is either a literal JSON-compatible scalar or an InputRef.
InputValue = str | int | float | bool | None | InputRef

# 10 tool canonical theo `shared_contracts.md`. Đây là contract PUBLIC mà
# Planner được phép sinh ra.
#
# `book_tour` và `register_consultation` KHÔNG có mặt: chúng là tên nội bộ của
# implementation cũ, đã được adapter sang `schedule_property_viewing` và
# `register_property_interest`. `book_shuttle` đi theo chuỗi
# schedule_property_viewing → book_shuttle: nó nhận `viewing_id` (không phải
# `tour_id`) qua InputRef từ lịch tham quan đã xác nhận.
AllowedTool = Literal[
    "search_properties",
    "schedule_property_viewing",
    "register_property_interest",
    "create_maintenance_request",
    "schedule_move",
    "register_resident",
    "register_vehicle",
    "book_parking",
    # Đổi khu cho một chỗ ĐÃ GIỮ. Planner KHÔNG được lập kế hoạch với nó (xem
    # `AGENT_FORBIDDEN_TOOLS`): nó chỉ có nghĩa khi đã có `booking_id` thật từ
    # một bước trước, và đường sửa lỗi dựng task này từ kết quả đã chạy.
    "change_parking_zone",
    # Huỷ một lịch ĐÃ ĐẶT. Cùng lý do với `change_parking_zone`: Planner không
    # được chạm (`AGENT_FORBIDDEN_TOOLS`), vì `viewing_id` chỉ có thật khi đến
    # từ một bước đã chạy — để model tự viết ra là để nó huỷ lịch của người khác.
    "cancel_property_viewing",
    "cancel_parking",
    "cancel_maintenance",
    "cancel_move",
    "cancel_shuttle",
    "pay_fee",
    "book_shuttle",
]


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: Annotated[str, Field(min_length=1)]
    tool: AllowedTool
    depends_on: list[str]
    input: dict[str, InputValue]

    @field_validator("task_id")
    @classmethod
    def task_id_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("task_id must not be blank or whitespace")
        return v


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: Annotated[str, Field(min_length=1)]
    tasks: Annotated[list[Task], Field(min_length=1)]

    @field_validator("goal")
    @classmethod
    def goal_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("goal must not be blank")
        return v
