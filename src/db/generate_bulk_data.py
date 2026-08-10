"""
P-118 — Bulk test data generator (mặc định 100 cư dân, tuỳ chỉnh được)

Sinh dữ liệu nghiệp vụ giả cho demo/test, tuân thủ MỌI ràng buộc trong
src/db/schema.sql + seed.sql:

  - uq_residents_apt_area    → (apartment_code, residential_area) unique
  - uq_vehicles_plate        → plate_number unique
  - uq_bookings_vehicle_date → 1 xe 1 booking/ngày
  - parking_capacity         → ZONE_A 3 chỗ/ngày, ZONE_B 10 chỗ/ngày (seed.sql)
  - uq_payments_paid_booking → mỗi booking tối đa 1 payment PAID (partial index)
  - FK execution_logs → workflow_tasks (composite FK) → workflow_tasks → workflows

Kết quả mặc định (`--residents 100 --workflows 20`):
  - 100 cư dân, mỗi người 1 xe → booking → payment (PAID) — chuỗi happy path
    đầy đủ "Register Resident → Vehicle → Parking → Pay Fee" theo brief.md.
  - Booking trải đều qua nhiều ngày, không vượt sức chứa zone/ngày.
  - 20 workflow state (SUCCESS ~70% / RUNNING+HITL ~20% / FAILED ~10%) bắc
    cầu VÀO business data thật (result_data chứa resident_id/vehicle_id/
    booking_id/payment_id thật) → test "100% data propagation".

Cách dùng:
  .venv/Scripts/python.exe src/db/generate_bulk_data.py
  .venv/Scripts/python.exe src/db/generate_bulk_data.py --residents 500 --workflows 50
  .venv/Scripts/python.exe src/db/generate_bulk_data.py --no-wipe     # thêm vào dữ liệu cũ
  DATABASE_URL=postgresql://postgres:root@localhost:5432/p118_db \
      .venv/Scripts/python.exe src/db/generate_bulk_data.py

Idempotent: mặc định TRUNCATE ... RESTART IDENTITY CASCADE trước khi insert
(giống src/db/demo_data.sql) — chạy lại = reset về đúng trạng thái vừa sinh.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import date, timedelta

import asyncpg

# ─────────────────────────────────────────────────────────────────────
# Cấu hình
# ─────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:root@localhost:5432/p118_db",
)
BASE_DATE = date(2026, 8, 10)  # ngày bắt đầu phát sinh booking (gần "hôm nay")

ZONE_PRICE = {"ZONE_A": 150_000, "ZONE_B": 100_000}  # khớp zone_capacity_config
ZONE_CAPACITY = {"ZONE_A": 3, "ZONE_B": 10}  # khớp seed.sql
PER_DAY = sum(ZONE_CAPACITY.values())  # 13 booking/ngày tối đa

# Tên tiếng Việt — lặp lại theo vòng nếu N > len(FULL_NAMES)
FULL_NAMES = [
    "Nguyễn Văn An",
    "Trần Thị Bích",
    "Lê Hoàng Cường",
    "Phạm Minh Dũng",
    "Hoàng Thị Hoa",
    "Vũ Quốc Huy",
    "Đặng Thị Hạnh",
    "Bùi Thanh Long",
    "Đỗ Minh Ngọc",
    "Hồ Thị Phương",
    "Ngô Quang Sơn",
    "Dương Văn Tài",
    "Lý Thị Thu",
    "Phan Công Vinh",
    "Võ Thị Yến",
    "Tạ Quốc Bảo",
    "Đoàn Thị Cúc",
    "Trịnh Văn Đạt",
    "Nguyễn Thị Giang",
    "Lương Minh Hiếu",
    "Phùng Thị Kim",
    "Cao Văn Lâm",
    "Nghiêm Thị Mơ",
    "Kiều Văn Nam",
    "Phạm Thị Oanh",
    "Trần Quốc Phong",
    "Mai Thị Quỳnh",
    "Hà Văn Sơn",
    "Lê Thị Tuyết",
    "Đinh Văn Uyên",
    "Nguyễn Quốc Vũ",
    "Trần Thị Xuyến",
    "Lý Văn Yên",
    "Ngô Thị Yến Nhi",
    "Phạm Hồng Đức",
    "Vũ Thị Lan",
]

CONNECTOR_BY_TOOL = {
    "register_resident": "ResidentConnector",
    "register_vehicle": "TransportConnector",
    "book_parking": "TransportConnector",
    "pay_fee": "PaymentConnector",
}


# ─────────────────────────────────────────────────────────────────────
# Phân bổ workflow status cho từng chain business (theo tỷ lệ gate-2 demo)
# ─────────────────────────────────────────────────────────────────────
def workflow_types(n_workflows: int) -> list[str]:
    """Trả về list status ('SUCCESS'/'RUNNING'/'FAILED') cho n_workflows đầu tiên.

    Tỷ lệ: ~70% happy-path SUCCESS, ~20% HITL (pay_fee WAITING_APPROVAL),
    ~10% FAILED (book_parking NO_AVAILABILITY → kịch bản REPLAN theo brief).
    """
    success = round(0.7 * n_workflows)
    running = round(0.2 * n_workflows)
    return ["SUCCESS"] * success + ["RUNNING"] * running + ["FAILED"] * max(0, n_workflows - success - running)


def _apartment_code(i: int, base: int = 0) -> str:
    """Apartment code unique: A1000, A1001..., offset theo base khi --no-wipe."""
    building = "ABCD"[(base + i) // 25 % 4]
    return f"{building}{1000 + base + i}"


# ─────────────────────────────────────────────────────────────────────
# Business data
# ─────────────────────────────────────────────────────────────────────
def build_business(
    n_residents: int, n_workflows: int, base: int = 0, existing_capacity: set[tuple[str, str]] | None = None
) -> dict[str, list[list[object]]]:
    """Sinh residents/vehicles/bookings/payments/capacity + metadata cho workflow.

    `base` = offset ID khi --no-wipe (số cư dân hiện có) để không trùng khóa.
    `existing_capacity` = set[(zone, date)] đã có trong DB (bỏ qua khi --no-wipe).
    Trả về dict các list row (dạng tuple) để executemany, kèm:
      chains:  [{type, resident_id, full_name, apartment_code, vehicle_id,
                 plate_number, vehicle_type, booking_id|None, booking_date|None,
                 parking_zone|None, amount|None, payment_id|None}]
    """
    types = workflow_types(min(n_workflows, n_residents))
    chains: list[dict] = []
    residents: list[tuple] = []
    vehicles: list[tuple] = []
    bookings: list[tuple] = []
    payments: list[tuple] = []

    booking_seq = 0
    for i in range(n_residents):
        wf_type = types[i] if i < len(types) else "SUCCESS"  # ngoài phạm vi workflow → happy-path thuần
        resident_id = f"RES-{base + i + 1:03d}"
        full_name = FULL_NAMES[i % len(FULL_NAMES)]
        apartment_code = _apartment_code(i, base=base)
        area = "Vinhomes Ocean Park"

        residents.append((resident_id, full_name, apartment_code, area))

        vehicle_id = f"VEH-{base + i + 1:03d}"
        plate = f"51A-{10000 + base + i:05d}"
        vehicle_type = "car" if i % 2 == 0 else "motorcycle"
        vehicles.append((vehicle_id, resident_id, plate, vehicle_type))

        # Ngày + zone phân bổ theo vòng, không vượt sức chứa 13 booking/ngày.
        # Gán cho MỌI chain (kể cả FAILED — là ngày chain "thử" đặt nhưng hết chỗ).
        day = booking_seq // PER_DAY
        slot = booking_seq % PER_DAY
        # Booking/Payment ID cũng offset theo base — không trùng khi --no-wipe
        booking_number = base + booking_seq
        zone = "ZONE_A" if slot < ZONE_CAPACITY["ZONE_A"] else "ZONE_B"
        booking_date = BASE_DATE + timedelta(days=day)
        amount = ZONE_PRICE[zone]

        chain = {
            "type": wf_type,
            "resident_id": resident_id,
            "full_name": full_name,
            "apartment_code": apartment_code,
            "vehicle_id": vehicle_id,
            "plate_number": plate,
            "vehicle_type": vehicle_type,
            "booking_date": booking_date,
            "parking_zone": zone,
            "amount": amount,
            "booking_id": None,
            "payment_id": None,
        }

        if wf_type == "FAILED":
            # book_parking thất bại (NO_AVAILABILITY) → không có booking/payment
            chains.append(chain)
            continue

        booking_id = f"BOOK-{booking_number + 1:03d}"
        bookings.append((booking_id, vehicle_id, zone, booking_date, amount, "VND"))

        # RUNNING = pay_fee đang chờ HITL → payment PENDING (chưa PAID)
        payment_status = "PENDING" if wf_type == "RUNNING" else "PAID"
        payment_id = f"PAY-{booking_number + 1:03d}"
        payments.append((payment_id, booking_id, amount, "VND", payment_status))

        chain.update(booking_id=booking_id, payment_id=payment_id)
        chains.append(chain)
        booking_seq += 1

    # parking_capacity: 1 row (zone, date) cho mọi ngày có booking.
    # Khi --no-wipe chạy vào ngày đã có capacity → skip để khỏi trùng PK.
    capacity_rows: list[tuple] = []
    n_days = (booking_seq - 1) // PER_DAY + 1
    for day in range(n_days):
        d = BASE_DATE + timedelta(days=day)
        for zone, cap in ZONE_CAPACITY.items():
            if (zone, d.isoformat()) in existing_capacity:
                continue
            capacity_rows.append((zone, d, cap))

    return {
        "residents": residents,
        "vehicles": vehicles,
        "bookings": bookings,
        "payments": payments,
        "capacity": capacity_rows,
        "chains": chains,
    }


# ─────────────────────────────────────────────────────────────────────
# Workflow state (SUCCESS / RUNNING+HITL / FAILED)
# ─────────────────────────────────────────────────────────────────────
def build_workflows(business: dict, n_workflows: int, base: int = 0) -> dict[str, list[list[object]]]:
    """Sinh workflows + workflow_tasks + execution_logs + approval_decisions.

    Chỉ xử lý n_workflows chain đầu tiên (phần còn lại là business data thuần).
    Bắc cầu vào business data thật qua `business["chains"]` — mỗi workflow
    dùng chain cùng index, result_data chứa ID thật → test data propagation.
    """
    chains = business["chains"]
    workflows: list[tuple] = []
    tasks: list[tuple] = []
    logs: list[tuple] = []
    approvals: list[tuple] = []

    for j, c in enumerate(chains[:n_workflows]):
        if c["type"] not in ("SUCCESS", "RUNNING", "FAILED"):
            continue

        # Deterministic theo (base, j) → uuid4-safe khi --no-wipe (j mới, không trùng)
        workflow_id = uuid.UUID(int=(base * 10_000 + j + 1))
        if c["type"] == "SUCCESS":
            goal = (
                f"Đăng ký cư dân {c['full_name']} ({c['apartment_code']}), "
                f"đăng ký xe {c['plate_number']}, đặt chỗ {c['parking_zone']} "
                f"ngày {c['booking_date'].isoformat()} và thanh toán phí."
            )
        elif c["type"] == "RUNNING":
            goal = (
                f"Đặt chỗ đỗ xe {c['parking_zone']} ngày {c['booking_date'].isoformat()} "
                f"và thanh toán phí — chờ người duyệt."
            )
        else:
            goal = (
                f"Đăng ký xe {c['plate_number']} và đặt chỗ đỗ xe "
                f"{c['parking_zone']} ngày {c['booking_date'].isoformat()}."
            )
        workflows.append((workflow_id, goal, c["type"]))

        # Mọi task tuple đều ĐỦ 9 trường: (workflow_id, task_id, tool, status,
        # input_data, result_data, error_code, error_message, retryable).
        # Trường lỗi = None khi task thành công → executemany cùng arity.
        if c["type"] in ("SUCCESS", "RUNNING"):
            tasks.append(
                (
                    workflow_id,
                    "T1",
                    "register_resident",
                    "SUCCESS",
                    {
                        "full_name": c["full_name"],
                        "apartment_code": c["apartment_code"],
                        "residential_area": "Vinhomes Ocean Park",
                    },
                    {"resident_id": c["resident_id"]},
                    None,
                    None,
                    False,
                )
            )
            logs.append(_ok_log(workflow_id, "T1", 1, "register_resident"))

            tasks.append(
                (
                    workflow_id,
                    "T2",
                    "register_vehicle",
                    "SUCCESS",
                    {
                        "resident_id": c["resident_id"],
                        "plate_number": c["plate_number"],
                        "vehicle_type": c["vehicle_type"],
                    },
                    {"vehicle_id": c["vehicle_id"]},
                    None,
                    None,
                    False,
                )
            )
            logs.append(_ok_log(workflow_id, "T2", 1, "register_vehicle"))

            booking_input = {
                "vehicle_id": c["vehicle_id"],
                "booking_date": c["booking_date"].isoformat(),
                "parking_zone": c["parking_zone"],
            }
            booking_result = {
                "booking_id": c["booking_id"],
                "parking_zone": c["parking_zone"],
                "booking_date": c["booking_date"].isoformat(),
                "amount": c["amount"],
                "currency": "VND",
            }
            tasks.append(
                (workflow_id, "T3", "book_parking", "SUCCESS", booking_input, booking_result, None, None, False)
            )
            logs.append(_ok_log(workflow_id, "T3", 1, "book_parking"))
            if c["type"] == "RUNNING":  # T4 pay_fee chờ HITL approve
                tasks.append(
                    (
                        workflow_id,
                        "T4",
                        "pay_fee",
                        "WAITING_APPROVAL",
                        {"booking_id": c["booking_id"], "amount": c["amount"], "currency": "VND"},
                        None,
                        None,
                        None,
                        False,
                    )
                )
                # HITL audit: quyết định approve (chưa execute nên không có execution_log)
                approvals.append(
                    (workflow_id, "T4", "user:hoanganh", "APPROVED", f"Đồng ý thanh toán phí đỗ xe {c['amount']:,} VND")
                )

        else:  # FAILED: T1, T2 thành công; T3 book_parking thất bại NO_AVAILABILITY
            tasks.append(
                (
                    workflow_id,
                    "T1",
                    "register_resident",
                    "SUCCESS",
                    {
                        "full_name": c["full_name"],
                        "apartment_code": c["apartment_code"],
                        "residential_area": "Vinhomes Ocean Park",
                    },
                    {"resident_id": c["resident_id"]},
                    None,
                    None,
                    False,
                )
            )
            logs.append(_ok_log(workflow_id, "T1", 1, "register_resident"))
            tasks.append(
                (
                    workflow_id,
                    "T2",
                    "register_vehicle",
                    "SUCCESS",
                    {
                        "resident_id": c["resident_id"],
                        "plate_number": c["plate_number"],
                        "vehicle_type": c["vehicle_type"],
                    },
                    {"vehicle_id": c["vehicle_id"]},
                    None,
                    None,
                    False,
                )
            )
            logs.append(_ok_log(workflow_id, "T2", 1, "register_vehicle"))
            tasks.append(
                (
                    workflow_id,
                    "T3",
                    "book_parking",
                    "FAILED",
                    {
                        "vehicle_id": c["vehicle_id"],
                        "booking_date": c["booking_date"].isoformat(),
                        "parking_zone": c["parking_zone"],
                    },
                    None,
                    "NO_AVAILABILITY",
                    f"Parking Zone A ({c['parking_zone']}) is full on {c['booking_date'].isoformat()}",
                    False,
                )
            )
            logs.append(_fail_log(workflow_id, "T3", 1, "book_parking", c))

    return {"workflows": workflows, "tasks": tasks, "logs": logs, "approvals": approvals}


def _ok_log(wf_id: uuid.UUID, task_id: str, attempt: int, tool: str) -> tuple:
    return (
        wf_id,
        task_id,
        attempt,
        CONNECTOR_BY_TOOL[tool],
        201,
        None,
        {"success": True, "data": {"ok": True}, "error_code": None},
        100,
    )


def _fail_log(wf_id: uuid.UUID, task_id: str, attempt: int, tool: str, c: dict) -> tuple:
    return (
        wf_id,
        task_id,
        attempt,
        CONNECTOR_BY_TOOL[tool],
        409,
        "NO_AVAILABILITY",
        {
            "success": False,
            "data": None,
            "error_code": "NO_AVAILABILITY",
            "message": f"Parking Zone A ({c['parking_zone']}) is full on {c['booking_date'].isoformat()}",
            "retryable": False,
        },
        150,
    )


# ─────────────────────────────────────────────────────────────────────
# Insert + verify
# ─────────────────────────────────────────────────────────────────────
TABLE_ORDER = [
    "residents",
    "vehicles",
    "parking_bookings",
    "parking_capacity",
    "payments",
    "workflows",
    "workflow_tasks",
    "execution_logs",
    "approval_decisions",
]


async def wipe(conn: asyncpg.Connection) -> None:
    """Reset toàn bộ bảng demo (giống demo_data.sql)."""
    await conn.execute(
        "TRUNCATE TABLE approval_decisions, execution_logs, workflow_tasks, "
        "workflows, payments, parking_bookings, parking_capacity, vehicles, "
        "residents RESTART IDENTITY CASCADE"
    )


async def insert_rows(conn: asyncpg.Connection, table: str, rows: list[list[object]]) -> int:
    if not rows:
        return 0
    # Khai báo cột tường minh → bỏ qua cột auto (workflow_tasks.id BIGSERIAL,
    # execution_logs.id, approval_decisions.id), tránh UUID parse vào id.
    placeholders = ", ".join(f"${i}" for i in range(1, len(rows[0]) + 1))
    # dict → JSON string (asyncpg encode JSONB dạng str; cột là JSONB)
    encoded = [[json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v for v in row] for row in rows]
    await conn.executemany(
        f"INSERT INTO {table} ({COLUMNS[table]}) VALUES ({placeholders})",
        encoded,
    )
    return len(rows)


# Cột INSERT tường minh — bỏ qua cột auto/id, khớp thứ tự tuple trong build_*
COLUMNS = {
    "residents": "resident_id, full_name, apartment_code, residential_area",
    "vehicles": "vehicle_id, resident_id, plate_number, vehicle_type",
    "parking_bookings": "booking_id, vehicle_id, parking_zone, booking_date, amount, currency",
    "parking_capacity": "parking_zone, booking_date, capacity",
    "payments": "payment_id, booking_id, amount, currency, payment_status",
    "workflows": "workflow_id, goal, status",
    "workflow_tasks": "workflow_id, task_id, tool, status, input_data, result_data, error_code, error_message, retryable",
    "execution_logs": "workflow_id, task_id, attempt_number, connector_name, http_status, raw_error_code, standard_result, duration_ms",
    "approval_decisions": "workflow_id, task_id, decided_by, decision, comment",
}


async def verify(conn: asyncpg.Connection) -> None:
    """In số lượng mỗi bảng + kiểm tra toàn vẹn FK (không row mồ côi)."""
    row = await conn.fetchrow(
        """
        SELECT
          (SELECT COUNT(*) FROM residents)        AS residents,
          (SELECT COUNT(*) FROM vehicles)         AS vehicles,
          (SELECT COUNT(*) FROM parking_bookings) AS bookings,
          (SELECT COUNT(*) FROM parking_capacity) AS capacity,
          (SELECT COUNT(*) FROM payments)         AS payments,
          (SELECT COUNT(*) FROM workflows)        AS workflows,
          (SELECT COUNT(*) FROM workflow_tasks)   AS tasks,
          (SELECT COUNT(*) FROM execution_logs)   AS logs,
          (SELECT COUNT(*) FROM approval_decisions) AS approvals
        """
    )
    orphans = await conn.fetchrow(
        """
        SELECT
          (SELECT COUNT(*) FROM vehicles v
             LEFT JOIN residents r USING (resident_id) WHERE r.resident_id IS NULL)            AS vehicles_orphan,
          (SELECT COUNT(*) FROM parking_bookings b
             LEFT JOIN vehicles v USING (vehicle_id) WHERE v.vehicle_id IS NULL)               AS bookings_orphan,
          (SELECT COUNT(*) FROM payments p
             LEFT JOIN parking_bookings b USING (booking_id) WHERE b.booking_id IS NULL)        AS payments_orphan,
          (SELECT COUNT(*) FROM workflow_tasks t
             LEFT JOIN workflows w USING (workflow_id) WHERE w.workflow_id IS NULL)             AS tasks_orphan,
          (SELECT COUNT(*) FROM execution_logs l
             LEFT JOIN workflow_tasks t
               ON l.workflow_id = t.workflow_id AND l.task_id = t.task_id
             WHERE t.id IS NULL)                                                               AS logs_orphan,
          (SELECT COUNT(*) FROM approval_decisions a
             LEFT JOIN workflow_tasks t
               ON a.workflow_id = t.workflow_id AND a.task_id = t.task_id
             WHERE t.id IS NULL)                                                               AS approvals_orphan
        """
    )

    print("\n── Số dòng mỗi bảng ──────────────────────────────")
    for col in ("residents", "vehicles", "bookings", "capacity", "payments", "workflows", "tasks", "logs", "approvals"):
        print(f"  {col:<12} {row[col]:>6}")
    print("── Toàn vẹn FK (mồ côi = 0) ──────────────────────")
    total_orphan = 0
    for col in (
        "vehicles_orphan",
        "bookings_orphan",
        "payments_orphan",
        "tasks_orphan",
        "logs_orphan",
        "approvals_orphan",
    ):
        val = orphans[col]
        total_orphan += val
        print(f"  {col:<16} {val}")
    if total_orphan == 0:
        print("  ✅ Không có row mồ côi — FK chain nhất quán.")
    else:
        raise SystemExit("❌ Phát hiện row mồ côi — dừng.")


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
async def run(n_residents: int, n_workflows: int, do_wipe: bool) -> None:
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            # base = offset ID để --no-wipe không trùng khóa (số cư dân hiện có)
            existing_count = await conn.fetchval("SELECT COUNT(*) FROM residents")
            base = 0 if do_wipe else existing_count
            existing_capacity = set()
            if not do_wipe:
                rows = await conn.fetch("SELECT parking_zone, booking_date FROM parking_capacity")
                existing_capacity = {(r["parking_zone"], r["booking_date"].isoformat()) for r in rows}

            business = build_business(n_residents, n_workflows, base=base, existing_capacity=existing_capacity)
            workflows = build_workflows(business, n_workflows, base=base)

            async with conn.transaction():
                if do_wipe:
                    await wipe(conn)
                counts = {}
                for table, rows in [
                    ("residents", business["residents"]),
                    ("vehicles", business["vehicles"]),
                    ("parking_bookings", business["bookings"]),
                    ("parking_capacity", business["capacity"]),
                    ("payments", business["payments"]),
                    ("workflows", workflows["workflows"]),
                    ("workflow_tasks", workflows["tasks"]),
                    ("execution_logs", workflows["logs"]),
                    ("approval_decisions", workflows["approvals"]),
                ]:
                    counts[table] = await insert_rows(conn, table, rows)

            print("Đã chèn dữ liệu (idempotent, TRUNCATE trước):")
            for table in TABLE_ORDER:
                print(f"  + {table:<18} {counts[table]}")

            await verify(conn)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P-118 bulk test data generator — mặc định 100 cư dân + chuỗi happy path + 20 workflow.",
    )
    parser.add_argument(
        "--residents", type=int, default=100, help="Số cư dân (mỗi người 1 xe → booking → payment). Mặc định 100."
    )
    parser.add_argument(
        "--workflows", type=int, default=20, help="Số workflow state sinh thêm (≤ residents). Mặc định 20."
    )
    parser.add_argument("--no-wipe", action="store_true", help="KHÔNG TRUNCATE — thêm vào dữ liệu hiện có.")
    args = parser.parse_args()

    if args.workflows > args.residents:
        args.workflows = args.residents  # workflow cần bắc cầu vào business data

    asyncio.run(run(args.residents, args.workflows, not args.no_wipe))


if __name__ == "__main__":
    main()
