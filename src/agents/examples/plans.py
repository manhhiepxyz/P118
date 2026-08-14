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
        "đặt chỗ tại ZONE_A ngày 2026-08-10 và thanh toán phí."
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
                "booking_date": "2026-08-10",
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
    goal="Đặt chỗ cho xe của tôi tại ZONE_A ngày 2026-08-10.",
    tasks=[
        Task(
            task_id="T1",
            tool="book_parking",
            depends_on=[],
            input={
                "vehicle_id": "VEH-001",
                "booking_date": "2026-08-10",
                "parking_zone": "ZONE_A",
            },
        ),
    ],
)

# ---------------------------------------------------------------------------
# Demo flow: tour → shuttle (tham quan + xe đưa đón)
# book_tour → book_shuttle (tour_id truyền qua InputRef)
# ---------------------------------------------------------------------------

PLAN_TOUR_SHUTTLE = TaskPlan(
    goal=("Đặt lịch tham quan dự án Vinhomes Ocean Park buổi sáng ngày 2026-08-20 và đặt xe tham quan cho 4 người."),
    tasks=[
        Task(
            task_id="T1",
            tool="book_tour",
            depends_on=[],
            input={
                "residential_area": "Vinhomes Ocean Park",
                "tour_date": "2026-08-20",
                "tour_slot": "MORNING",
            },
        ),
        Task(
            task_id="T2",
            tool="book_shuttle",
            depends_on=["T1"],
            input={
                "tour_id": InputRef(from_task="T1", field="tour_id"),
                "tour_date": InputRef(from_task="T1", field="tour_date"),
                "passenger_count": 4,
            },
        ),
    ],
)

# ---------------------------------------------------------------------------
# Demo flow: đăng ký tư vấn mua căn hộ để ở
# ---------------------------------------------------------------------------

PLAN_CONSULTATION = TaskPlan(
    goal="Đăng ký tư vấn mua căn hộ để ở tại Vinhomes.",
    tasks=[
        Task(
            task_id="T1",
            tool="register_consultation",
            depends_on=[],
            input={
                "consultation_type": "BUY",
                "buy_sub_type": "RESIDE",
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
                "booking_date": "2026-08-10",
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
