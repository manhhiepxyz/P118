"""Example TaskPlan instances for testing and documentation."""

from src.common.task_plan import InputRef, Task, TaskPlan

# ---------------------------------------------------------------------------
# Full onboarding flow: T1 → T2 → T3 → T4
# register_resident → register_vehicle → book_parking → pay_fee
# ---------------------------------------------------------------------------

PLAN_FULL_FLOW = TaskPlan(
    goal=(
        "Tôi mới chuyển vào căn hộ A1201 tại Vinhomes Ocean Park. "
        "Hãy đăng ký cư dân, đăng ký xe biển số 51A-12345, "
        "đặt chỗ tại ZONE_A ngày 2030-08-10 và thanh toán phí."
    ),
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
                "booking_date": "2030-08-10",
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

# ---------------------------------------------------------------------------
# Partial goal 1: user already has vehicle_id — only book_parking needed
# ---------------------------------------------------------------------------

PLAN_PARTIAL_BOOK_ONLY = TaskPlan(
    goal="Đặt chỗ cho xe của tôi tại ZONE_A ngày 2030-08-10.",
    tasks=[
        Task(
            task_id="T1",
            tool="book_parking",
            depends_on=[],
            input={
                "vehicle_id": "VEH-001",
                "booking_date": "2030-08-10",
                "parking_zone": "ZONE_A",
            },
        ),
    ],
)

# ---------------------------------------------------------------------------
# Demo flow: đặt lịch tham quan dự án
#
# Tên canonical là `schedule_property_viewing`. Implementation nội bộ vẫn là
# provider tour cũ, nhưng contract public dùng project_id/viewing_date/
# viewing_time — `tour_id`/`tour_slot` không được lộ ra khỏi Connector.
# ---------------------------------------------------------------------------

PLAN_PROPERTY_VIEWING = TaskPlan(
    goal="Đặt lịch tham quan dự án Vinhomes Ocean Park lúc 09:30 ngày 2030-08-20.",
    tasks=[
        Task(
            task_id="T1",
            tool="schedule_property_viewing",
            depends_on=[],
            input={
                "project_id": "PRJ-001",
                "viewing_date": "2030-08-20",
                "viewing_time": "09:30",
            },
        ),
    ],
)

# ---------------------------------------------------------------------------
# Demo flow: đăng ký nhận tư vấn về dự án
#
# Tên canonical là `register_property_interest`. `consent` phải là literal true
# — Planner không được tự suy diễn sự đồng ý của người dùng.
# ---------------------------------------------------------------------------

PLAN_PROPERTY_INTEREST = TaskPlan(
    goal="Đăng ký nhận tư vấn mua căn hộ tại Vinhomes Ocean Park.",
    tasks=[
        Task(
            task_id="T1",
            tool="register_property_interest",
            depends_on=[],
            input={
                "project_id": "PRJ-001",
                "interest_type": "buy",
                "preferred_contact_time": "morning",
                "consent": True,
            },
        ),
    ],
)

# ---------------------------------------------------------------------------
# Partial goal 2: user already has vehicle_id — book_parking then pay_fee
# ---------------------------------------------------------------------------

PLAN_PARTIAL_BOOK_AND_PAY = TaskPlan(
    goal="Đặt chỗ đậu xe và thanh toán phí cho tôi.",
    tasks=[
        Task(
            task_id="T1",
            tool="book_parking",
            depends_on=[],
            input={
                "vehicle_id": "VEH-001",
                "booking_date": "2030-08-10",
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
