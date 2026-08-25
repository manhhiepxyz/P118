"""Danh mục capability CANONICAL và bộ sinh kế hoạch cho ma trận tổ hợp.

Owner: Thành Bảo (Decision layer)
File: tests/matrix/capabilities.py

Agent có đúng 8 tool với tới được, gom thành 5 capability nghiệp vụ. Ma trận
kiểm mọi tổ hợp kích thước 2–5: C(5,2)+C(5,3)+C(5,4)+C(5,5) = 26.

Vì sao là bộ SINH chứ không phải 26 case viết tay
-------------------------------------------------
26 case rời rạc là 26 chỗ để một kỳ vọng bị sửa cho khớp output hiện tại. Một
danh mục duy nhất thì kỳ vọng được TÍNH ra từ contract, và sửa contract mà quên
sửa kỳ vọng sẽ làm cả ma trận đỏ cùng lúc — đó mới là tín hiệu.

Ràng buộc quan trọng nhất của bộ sinh: capability ĐỘC LẬP không được dính
dependency với nhau. `depends_on` chỉ nối các bước BÊN TRONG một capability.
Một phụ thuộc giả giữa hai nhánh độc lập sẽ tuần tự hoá thứ vốn chạy song song,
và nó chỉ lộ ra khi một nhánh phải chờ duyệt — lúc ấy nhánh kia đứng im vô cớ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations

from src.common.task_plan import InputRef, Task, TaskPlan


def future(days: int) -> str:
    """Ngày trong tương lai tính từ HÔM NAY.

    Ngày cố định trong fixture tự hỏng khi nó thành quá khứ, và hỏng theo kiểu
    khó đọc: Validator từ chối, rồi test báo lỗi cấu trúc cho một kế hoạch vốn
    đúng.
    """
    return (date.today() + timedelta(days=days)).isoformat()


@dataclass(frozen=True)
class Step:
    """Một bước. `refs` nối tới bước TRƯỚC TRONG CÙNG capability, theo chỉ số."""

    tool: str
    literal: dict = field(default_factory=dict)
    refs: dict[str, tuple[int, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Capability:
    code: str
    name: str
    steps: tuple[Step, ...]
    # Dịch vụ chỉ dành cho cư dân đã xác minh. `V`/`C` mở cho cả khách tiềm năng.
    resident_only: bool = True


VIEWING = Capability(
    code="V",
    name="Tham quan dự án và xe đưa đón",
    resident_only=False,
    steps=(
        Step(
            tool="schedule_property_viewing",
            literal={"project_id": "PRJ-001", "viewing_date": future(30), "viewing_time": "09:30"},
        ),
        Step(
            tool="book_shuttle",
            literal={"tour_date": future(30), "passenger_count": 2},
            refs={"viewing_id": (0, "viewing_id")},
        ),
    ),
)

VIEWING_ONLY = Capability(
    code="V0",
    name="Chỉ tham quan, không xe đưa đón",
    resident_only=False,
    steps=(VIEWING.steps[0],),
)

CONSULTATION = Capability(
    code="C",
    name="Đăng ký nhận tư vấn",
    resident_only=False,
    steps=(
        Step(
            tool="register_property_interest",
            literal={
                "project_id": "PRJ-002",
                "interest_type": "buy",
                "preferred_contact_time": "10:00",
                "consent": True,
            },
        ),
    ),
)

MAINTENANCE = Capability(
    code="M",
    name="Bảo trì",
    steps=(
        Step(
            tool="create_maintenance_request",
            literal={
                "issue_type": "air_conditioning",
                "description": "Điều hoà phòng khách không mát",
                "location": "Tầng 3, phòng 302",
                "preferred_date": future(20),
                "preferred_time": "09:00",
            },
        ),
    ),
)

MOVING = Capability(
    code="R",
    name="Chuyển nhà",
    steps=(
        Step(
            tool="schedule_move",
            literal={
                "move_date": future(25),
                "move_time": "08:00",
                "needs_elevator": True,
                "needs_loading_support": False,
                "move_vehicle": "truck",
            },
        ),
    ),
)

PARKING = Capability(
    code="P",
    name="Phương tiện, chỗ đỗ và thanh toán",
    steps=(
        Step(
            tool="register_vehicle",
            literal={"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"},
        ),
        Step(
            tool="book_parking",
            literal={"booking_date": future(15), "parking_zone": "ZONE_A"},
            refs={"vehicle_id": (0, "vehicle_id")},
        ),
        Step(
            tool="pay_fee",
            refs={
                "booking_id": (1, "booking_id"),
                "amount": (1, "amount"),
                "currency": (1, "currency"),
            },
        ),
    ),
)

PARKING_NO_PAYMENT = Capability(
    code="P0",
    name="Phương tiện và chỗ đỗ, chưa thanh toán",
    steps=PARKING.steps[:2],
)

# Năm capability của ma trận. `V0`/`P0` là BIẾN THỂ, không tham gia tổ hợp — nếu
# tham gia, chúng chồng tool với `V`/`P` và multiset kỳ vọng mất ý nghĩa.
MATRIX: dict[str, Capability] = {c.code: c for c in (VIEWING, CONSULTATION, MAINTENANCE, MOVING, PARKING)}
VARIANTS: dict[str, Capability] = {c.code: c for c in (VIEWING_ONLY, PARKING_NO_PAYMENT)}
ALL_CAPABILITIES: dict[str, Capability] = {**MATRIX, **VARIANTS}


def combos(min_size: int = 2, max_size: int = 5) -> list[tuple[str, ...]]:
    """Mọi tổ hợp capability, kích thước 2–5. Thứ tự ổn định để test lặp lại được."""
    codes = list(MATRIX)
    out: list[tuple[str, ...]] = []
    for size in range(min_size, max_size + 1):
        out.extend(combinations(codes, size))
    return out


def build_plan(codes: tuple[str, ...], *, goal: str | None = None) -> TaskPlan:
    """Dựng TaskPlan canonical cho một tổ hợp.

    `task_id` đánh số liên tục theo thứ tự capability. `depends_on` CHỈ nối bên
    trong một capability — hai nhánh độc lập không được dính vào nhau.
    """
    tasks: list[Task] = []
    for code in codes:
        capability = ALL_CAPABILITIES[code]
        first = len(tasks)
        for offset, step in enumerate(capability.steps):
            task_id = f"T{len(tasks) + 1}"
            payload: dict = dict(step.literal)
            depends: list[str] = []
            for name, (source_offset, output) in step.refs.items():
                source = tasks[first + source_offset].task_id
                payload[name] = InputRef(from_task=source, field=output)
                if source not in depends:
                    depends.append(source)
            tasks.append(Task(task_id=task_id, tool=capability.steps[offset].tool, depends_on=depends, input=payload))
    return TaskPlan(goal=goal or "+".join(codes), tasks=tasks)


def expected_tools(codes: tuple[str, ...]) -> list[str]:
    """Multiset tool kỳ vọng, tính TỪ danh mục — không chép tay."""
    return [step.tool for code in codes for step in ALL_CAPABILITIES[code].steps]
