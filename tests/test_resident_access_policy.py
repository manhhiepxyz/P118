"""ResidentAccessBoundary — quyền cư dân và quyền sở hữu tài nguyên.

Guard chạy TRƯỚC Executor và không nằm trong TaskPlan, nên LLM không thể bỏ qua
bằng cách đổi thứ tự task, bỏ dependency hay dùng ID literal.
"""

from __future__ import annotations

import pytest

from src.common.task_plan import InputRef, Task, TaskPlan
from src.orchestration.demo_service import (
    ResidentAccessBoundary,
    ResidentAccessRequiredError,
    ResidentLinkingOutsideAgentError,
)

TRUSTED_RESIDENT = "RES-TRUSTED"
OTHER_RESIDENT = "RES-NGUOI-KHAC"


class _RecordingBoundary:
    def __init__(self) -> None:
        self.executed: list[TaskPlan] = []

    async def execute(self, plan, workflow_id=None, **_kwargs):
        self.executed.append(plan)
        return "wf-1", {}


class _AlwaysValidVerifier:
    async def verify(self, resident_id: str) -> bool:
        return resident_id == TRUSTED_RESIDENT


class _Ownership:
    """Chỉ tài nguyên của TRUSTED_RESIDENT mới thuộc về họ."""

    OWNED_VEHICLE = "VEH-CUA-TOI"
    OWNED_BOOKING = "BK-CUA-TOI"

    async def vehicle_belongs_to(self, resident_id: str, vehicle_id: str) -> bool:
        return resident_id == TRUSTED_RESIDENT and vehicle_id == self.OWNED_VEHICLE

    async def booking_belongs_to(self, resident_id: str, booking_id: str) -> bool:
        return resident_id == TRUSTED_RESIDENT and booking_id == self.OWNED_BOOKING


def _verified_context() -> dict:
    return {"resident_id": TRUSTED_RESIDENT, "resident_verification_status": "VERIFIED"}


def _boundary(context: dict) -> tuple[ResidentAccessBoundary, _RecordingBoundary]:
    inner = _RecordingBoundary()
    return (
        ResidentAccessBoundary(
            inner,
            context,
            verifier=_AlwaysValidVerifier(),
            resource_verifier=_Ownership(),
        ),
        inner,
    )


def _plan(*tasks: Task) -> TaskPlan:
    return TaskPlan(goal="Kiểm tra chính sách truy cập cư dân.", tasks=list(tasks))


PUBLIC_TOOL_TASKS = {
    "search_properties": {
        "transaction_type": "buy",
        "property_type": "apartment",
        "residential_area": "Vinhomes Ocean Park",
        "max_price": 5_000_000_000,
    },
    "schedule_property_viewing": {
        "project_id": "PRJ-001",
        "viewing_date": "2030-05-05",
        "viewing_time": "09:30",
    },
    "register_property_interest": {
        "project_id": "PRJ-001",
        "interest_type": "buy",
        "preferred_contact_time": "09:30",
        "consent": True,
    },
    "book_shuttle": {
        "viewing_id": "VIEW-001",
        "tour_date": "2030-05-05",
        "passenger_count": 2,
    },
    # Huỷ một lịch tham quan CÔNG KHAI như lúc đặt: khách chưa là cư dân vẫn đặt
    # được, nên họ cũng phải huỷ được. Quyền thật nằm ở chỗ khác — `viewing_id`
    # chỉ đến từ kết quả một bước của CHÍNH yêu cầu này.
    "cancel_property_viewing": {"viewing_id": "VIEW-001"},
    # Xe đưa đón đi kèm buổi tham quan — công khai như chính buổi ấy.
    "cancel_shuttle": {"shuttle_id": "SHUTTLE-001"},
}

RESIDENT_TOOL_TASKS = {
    # Đổi khu chạm vào một chỗ đỗ ĐÃ GIỮ của cư dân — quyền y như lúc đặt.
    "change_parking_zone": {"booking_id": "BOOK-001", "parking_zone": "ZONE_B"},
    "register_vehicle": {"resident_id": TRUSTED_RESIDENT, "plate_number": "30A-11111", "vehicle_type": "car"},
    "book_parking": {"vehicle_id": _Ownership.OWNED_VEHICLE, "booking_date": "2030-05-05", "parking_zone": "ZONE_A"},
    "pay_fee": {"booking_id": _Ownership.OWNED_BOOKING, "amount": 120_000, "currency": "VND"},
    # Huỷ một chỗ đỗ chạm vào tài sản của cư dân — quyền y như lúc đặt.
    "cancel_parking": {"booking_id": _Ownership.OWNED_BOOKING},
    "cancel_maintenance": {"maintenance_id": "MAINT-001"},
    "cancel_move": {"move_request_id": "MOVE-001"},
    "create_maintenance_request": {
        "issue_type": "plumbing",
        "description": "Vòi nước rò rỉ",
        "location": "Bếp",
        "preferred_date": "2030-05-05",
        "preferred_time": "09:00",
    },
    "schedule_move": {
        "move_date": "2030-05-05",
        "move_time": "09:00",
        "needs_elevator": True,
        "needs_loading_support": False,
        "move_vehicle": "truck",
    },
}


def test_the_policy_covers_every_canonical_tool() -> None:
    """Mười tool canonical phải được phân loại hết — không tool nào rơi ra ngoài.

    Một tool không nằm trong nhóm nào sẽ chạy được với MỌI quyền, kể cả prospect.
    """
    import typing

    from src.common.task_plan import AllowedTool

    canonical = set(typing.get_args(AllowedTool))
    classified = (
        set(PUBLIC_TOOL_TASKS) | set(RESIDENT_TOOL_TASKS) | set(ResidentAccessBoundary._LINKING_TOOLS)  # noqa: SLF001 - test kiểm chính bảng phân loại
    )

    assert classified == canonical, f"chưa phân loại: {sorted(canonical - classified)}"
    assert set(RESIDENT_TOOL_TASKS) == set(ResidentAccessBoundary._RESIDENT_TOOLS)  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(PUBLIC_TOOL_TASKS))
async def test_a_prospect_can_use_the_public_tools(tool) -> None:
    boundary, inner = _boundary({"resident_verification_status": "NOT_LINKED"})

    await boundary.execute(_plan(Task(task_id="T1", tool=tool, depends_on=[], input=PUBLIC_TOOL_TASKS[tool])))

    assert len(inner.executed) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(RESIDENT_TOOL_TASKS))
@pytest.mark.parametrize("status", ["NOT_LINKED", "PENDING", "REJECTED"])
async def test_an_unverified_account_cannot_use_the_resident_tools(tool, status) -> None:
    """PENDING và REJECTED fail-closed y như chưa liên kết."""
    boundary, inner = _boundary({"resident_verification_status": status})

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(_plan(Task(task_id="T1", tool=tool, depends_on=[], input=RESIDENT_TOOL_TASKS[tool])))

    assert inner.executed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(RESIDENT_TOOL_TASKS))
async def test_a_verified_resident_can_use_the_resident_tools(tool) -> None:
    boundary, inner = _boundary(_verified_context())

    await boundary.execute(_plan(Task(task_id="T1", tool=tool, depends_on=[], input=RESIDENT_TOOL_TASKS[tool])))

    assert len(inner.executed) == 1


@pytest.mark.asyncio
async def test_register_resident_is_always_blocked_outside_the_agent() -> None:
    """Kể cả với cư dân đã xác minh. Liên kết hồ sơ không phải việc của Agent."""
    boundary, inner = _boundary(_verified_context())

    with pytest.raises(ResidentLinkingOutsideAgentError):
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="register_resident",
                    depends_on=[],
                    input={"full_name": "Nguyễn Văn A", "apartment_code": "A-0101", "residential_area": "X"},
                )
            )
        )

    assert inner.executed == []


@pytest.mark.asyncio
async def test_a_forged_resident_id_is_rejected() -> None:
    """`register_vehicle` chỉ được dùng resident_id đã xác minh."""
    boundary, inner = _boundary(_verified_context())

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="register_vehicle",
                    depends_on=[],
                    input={"resident_id": OTHER_RESIDENT, "plate_number": "30A-99999", "vehicle_type": "car"},
                )
            )
        )

    assert inner.executed == []


@pytest.mark.asyncio
async def test_booking_a_space_for_someone_elses_vehicle_is_rejected() -> None:
    boundary, inner = _boundary(_verified_context())

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="book_parking",
                    depends_on=[],
                    input={"vehicle_id": "VEH-CUA-NGUOI-KHAC", "booking_date": "2030-05-05", "parking_zone": "ZONE_A"},
                )
            )
        )

    assert inner.executed == []


@pytest.mark.asyncio
async def test_paying_someone_elses_booking_is_rejected() -> None:
    boundary, inner = _boundary(_verified_context())

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="pay_fee",
                    depends_on=[],
                    input={"booking_id": "BK-CUA-NGUOI-KHAC", "amount": 1, "currency": "VND"},
                )
            )
        )

    assert inner.executed == []


@pytest.mark.asyncio
async def test_a_valid_input_ref_chain_still_runs() -> None:
    """Chuỗi hợp lệ trong cùng plan không được chặn nhầm.

    `vehicle_id`/`booking_id` ở đây là InputRef: giá trị thật chưa tồn tại lúc
    guard chạy, và tài nguyên sẽ do chính plan này tạo ra dưới resident_id đã
    xác minh. Kiểm quyền sở hữu trên InputRef sẽ chặn đúng luồng nghiệp vụ chính.
    """
    boundary, inner = _boundary(_verified_context())

    await boundary.execute(
        _plan(
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": TRUSTED_RESIDENT, "plate_number": "30A-11111", "vehicle_type": "car"},
            ),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                    "booking_date": "2030-05-05",
                    "parking_zone": "ZONE_A",
                },
            ),
            Task(
                task_id="T3",
                tool="pay_fee",
                depends_on=["T2"],
                input={
                    "booking_id": InputRef(from_task="T2", field="booking_id"),
                    "amount": InputRef(from_task="T2", field="amount"),
                    "currency": InputRef(from_task="T2", field="currency"),
                },
            ),
        )
    )

    assert len(inner.executed) == 1


@pytest.mark.asyncio
async def test_mixing_a_valid_chain_with_someone_elses_literal_is_rejected() -> None:
    """Một literal sai là đủ để từ chối cả plan.

    Đây là đường lách rõ nhất: bọc ID của người khác giữa các bước hợp lệ để
    guard chỉ nhìn thấy một chuỗi trông đúng.
    """
    boundary, inner = _boundary(_verified_context())

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="register_vehicle",
                    depends_on=[],
                    input={"resident_id": TRUSTED_RESIDENT, "plate_number": "30A-11111", "vehicle_type": "car"},
                ),
                Task(
                    task_id="T2",
                    tool="book_parking",
                    depends_on=["T1"],
                    input={
                        "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                        "booking_date": "2030-05-05",
                        "parking_zone": "ZONE_A",
                    },
                ),
                Task(
                    task_id="T3",
                    tool="pay_fee",
                    depends_on=["T2"],
                    input={"booking_id": "BK-CUA-NGUOI-KHAC", "amount": 1, "currency": "VND"},
                ),
            )
        )

    assert inner.executed == []


@pytest.mark.asyncio
async def test_the_rejection_message_never_leaks_any_identifier() -> None:
    """Thông báo không được biến guard thành công cụ dò ID."""
    boundary, _ = _boundary(_verified_context())

    with pytest.raises(ResidentAccessRequiredError) as excinfo:
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="pay_fee",
                    depends_on=[],
                    input={"booking_id": "BK-BI-MAT-999", "amount": 1, "currency": "VND"},
                )
            )
        )

    message = str(excinfo.value)
    for leaked in ("BK-BI-MAT-999", TRUSTED_RESIDENT, OTHER_RESIDENT, "SELECT", "postgresql://"):
        assert leaked not in message, f"message rò {leaked!r}"


@pytest.mark.asyncio
async def test_a_plan_bypassing_the_planner_is_still_blocked_at_execution() -> None:
    """Hai lớp độc lập: Planner chặn lúc lập kế hoạch, boundary chặn lúc chạy.

    Planner giờ loại `register_resident` khỏi không gian kế hoạch. Nhưng một
    TaskPlan có thể tới execution boundary mà KHÔNG đi qua Planner — plan dựng
    thủ công, plan đọc lại từ snapshot, hoặc một đường API tương lai. Nếu chỉ có
    guard ở Planner thì mọi đường đó là lỗ hổng.

    Ở đây plan đi thẳng vào boundary, bỏ qua Planner hoàn toàn.
    """
    boundary, inner = _boundary(_verified_context())

    with pytest.raises(ResidentLinkingOutsideAgentError):
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="register_resident",
                    depends_on=[],
                    input={
                        "full_name": "Nguyễn Văn Bỏ Qua",
                        "apartment_code": "Z-9999",
                        "residential_area": "Vinhomes Ocean Park",
                    },
                ),
                Task(
                    task_id="T2",
                    tool="register_vehicle",
                    depends_on=["T1"],
                    input={"resident_id": TRUSTED_RESIDENT, "plate_number": "30A-55555", "vehicle_type": "car"},
                ),
            )
        )

    assert inner.executed == [], "Executor không được nhận plan có bước liên kết cư dân"


@pytest.mark.asyncio
async def test_the_execution_refusal_never_echoes_the_submitted_identity() -> None:
    """Message không được lặp lại tên/căn hộ mà người gửi vừa khai."""
    boundary, _ = _boundary(_verified_context())

    with pytest.raises(ResidentLinkingOutsideAgentError) as excinfo:
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="register_resident",
                    depends_on=[],
                    input={
                        "full_name": "Nguyễn Văn Bỏ Qua",
                        "apartment_code": "Z-9999",
                        "residential_area": "Vinhomes Ocean Park",
                    },
                )
            )
        )

    message = str(excinfo.value)
    for leaked in ("Nguyễn Văn Bỏ Qua", "Z-9999", TRUSTED_RESIDENT):
        assert leaked not in message, f"message rò {leaked!r}"
