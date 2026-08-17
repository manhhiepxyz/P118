"""TaskPlan fixtures cho testing.

Owner: Mạnh Hiệp (Executor layer)
File: tests/fixtures/task_plans.py
"""

from src.common.task_plan import InputRef, Task, TaskPlan

# Full flow: T1→T2→T3→T4
FULL_FLOW_PLAN = TaskPlan(
    goal="Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư dân, đăng ký xe biển số 51A-12345, đặt chỗ tại ZONE_A ngày 2026-12-10 và thanh toán phí.",
    tasks=[
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "Lâm Thành Bảo",
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park",
            },
        ),
        Task(
            task_id="T2",
            tool="register_vehicle",
            depends_on=["T1"],
            input={
                "resident_id": InputRef(from_task="T1", field="resident_id"),
                "plate_number": "51A-12345",
                "vehicle_type": "car",
            },
        ),
        Task(
            task_id="T3",
            tool="book_parking",
            depends_on=["T2"],
            input={
                "vehicle_id": InputRef(from_task="T2", field="vehicle_id"),
                "booking_date": "2026-12-10",
                "parking_zone": "ZONE_A",
            },
        ),
        Task(
            task_id="T4",
            tool="pay_fee",
            depends_on=["T3"],
            input={
                "booking_id": InputRef(from_task="T3", field="booking_id"),
                "amount": InputRef(from_task="T3", field="amount"),
                "currency": InputRef(from_task="T3", field="currency"),
            },
        ),
    ],
)

# Partial goal 1: Chỉ book_parking (đã có vehicle_id)
PARTIAL_BOOK_PARKING_PLAN = TaskPlan(
    goal="Đặt chỗ cho xe của tôi ngày mai.",
    tasks=[
        Task(
            task_id="T1",
            tool="book_parking",
            depends_on=[],
            input={
                "vehicle_id": "VEH-001",
                "booking_date": "2026-12-10",
                "parking_zone": "ZONE_A",
            },
        ),
    ],
)

# Partial goal 2: book_parking → pay_fee (đã có vehicle_id)
PARTIAL_BOOK_AND_PAY_PLAN = TaskPlan(
    goal="Đặt chỗ và thanh toán giúp tôi.",
    tasks=[
        Task(
            task_id="T1",
            tool="book_parking",
            depends_on=[],
            input={
                "vehicle_id": "VEH-001",
                "booking_date": "2026-12-10",
                "parking_zone": "ZONE_A",
            },
        ),
        Task(
            task_id="T2",
            tool="pay_fee",
            depends_on=["T1"],
            input={
                "booking_id": InputRef(from_task="T1", field="booking_id"),
                "amount": InputRef(from_task="T1", field="amount"),
                "currency": InputRef(from_task="T1", field="currency"),
            },
        ),
    ],
)

# Partial goal: Chỉ pay_fee (đã có booking_id)
PARTIAL_PAY_FEE_PLAN = TaskPlan(
    goal="Thanh toán phí cho đặt chỗ đã tạo.",
    tasks=[
        Task(
            task_id="T1",
            tool="pay_fee",
            depends_on=[],
            input={
                "booking_id": "BOOK-001",
                "amount": 150000,
                "currency": "VND",
            },
        ),
    ],
)

# Invalid plans dưới dạng dict để tránh ValidationError khi import
CYCLE_PLAN = {
    "goal": "Test cycle detection",
    "tasks": [
        {
            "task_id": "T1",
            "tool": "register_resident",
            "depends_on": ["T2"],
            "input": {
                "full_name": "Test",
                "apartment_code": "A101",
                "residential_area": "Test Area",
            },
        },
        {
            "task_id": "T2",
            "tool": "register_vehicle",
            "depends_on": ["T1"],
            "input": {
                "resident_id": {"from_task": "T1", "field": "resident_id"},
                "plate_number": "51A-11111",
                "vehicle_type": "car",
            },
        },
    ],
}

MISSING_DEPENDENCY_PLAN = {
    "goal": "Test missing dependency",
    "tasks": [
        {
            "task_id": "T1",
            "tool": "register_resident",
            "depends_on": [],
            "input": {
                "full_name": "Test",
                "apartment_code": "A101",
                "residential_area": "Test Area",
            },
        },
        {
            "task_id": "T2",
            "tool": "register_vehicle",
            "depends_on": ["T3"],
            "input": {
                "resident_id": {"from_task": "T1", "field": "resident_id"},
                "plate_number": "51A-11111",
                "vehicle_type": "car",
            },
        },
    ],
}

INVALID_TOOL_PLAN = {
    "goal": "Test invalid tool",
    "tasks": [
        {
            "task_id": "T1",
            "tool": "delete_resident",
            "depends_on": [],
            "input": {
                "resident_id": "RES-001",
            },
        },
    ],
}

MISSING_FIELD_PLAN = {
    "goal": "Test missing field",
    "tasks": [
        {
            "task_id": "T1",
            "tool": "register_resident",
            "depends_on": [],
            "input": {
                "full_name": "Test",
            },
        },
    ],
}

MISSING_RESIDENT_ID_PLAN = {
    "goal": "Test missing resident_id",
    "tasks": [
        {
            "task_id": "T1",
            "tool": "register_vehicle",
            "depends_on": [],
            "input": {
                "plate_number": "51A-11111",
                "vehicle_type": "car",
            },
        },
    ],
}

# Tất cả plans để test nhanh
ALL_PLANS = [
    FULL_FLOW_PLAN,
    PARTIAL_BOOK_PARKING_PLAN,
    PARTIAL_BOOK_AND_PAY_PLAN,
    PARTIAL_PAY_FEE_PLAN,
]

INVALID_PLANS = [
    CYCLE_PLAN,
    MISSING_DEPENDENCY_PLAN,
    INVALID_TOOL_PLAN,
    MISSING_FIELD_PLAN,
    MISSING_RESIDENT_ID_PLAN,
]
