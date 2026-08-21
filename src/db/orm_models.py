from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Resident(Base):
    __tablename__ = "residents"

    resident_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    apartment_code: Mapped[str] = mapped_column(String(50), nullable=False)
    residential_area: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    vehicles: Mapped[list[Vehicle]] = relationship(
        "Vehicle",
        back_populates="resident",
        cascade="all, delete-orphan",
    )

    __table_args__ = (UniqueConstraint("apartment_code", "residential_area", name="uq_residents_apt_area"),)


class ApartmentOwner(Base):
    """Chủ sở hữu căn hộ — dùng verify quyền sở hữu khi register_resident."""

    __tablename__ = "apartment_owners"

    apartment_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    residential_area: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner_name: Mapped[str] = mapped_column(String(200), nullable=False)
    id_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verified_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))


class Vehicle(Base):
    __tablename__ = "vehicles"

    vehicle_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    resident_id: Mapped[str] = mapped_column(String(20), ForeignKey("residents.resident_id"), nullable=False)
    plate_number: Mapped[str] = mapped_column(String(20), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    resident: Mapped[Resident] = relationship("Resident", back_populates="vehicles")
    bookings: Mapped[list[ParkingBooking]] = relationship("ParkingBooking", back_populates="vehicle")

    __table_args__ = (
        UniqueConstraint("plate_number", name="uq_vehicles_plate"),
        CheckConstraint("vehicle_type IN ('car', 'motorcycle')", name="chk_vehicle_type"),
    )


class ParkingBooking(Base):
    __tablename__ = "parking_bookings"

    booking_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(String(20), ForeignKey("vehicles.vehicle_id"), nullable=False)
    parking_zone: Mapped[str] = mapped_column(String(20), nullable=False)
    booking_date: Mapped[str] = mapped_column(Date, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default="VND")
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    vehicle: Mapped[Vehicle] = relationship("Vehicle", back_populates="bookings")

    __table_args__ = (
        UniqueConstraint("vehicle_id", "booking_date", name="uq_bookings_vehicle_date"),
        CheckConstraint("amount >= 0", name="chk_booking_amount_non_negative"),
        CheckConstraint("parking_zone IN ('ZONE_A', 'ZONE_B')", name="chk_parking_zone"),
    )


class ParkingCapacity(Base):
    __tablename__ = "parking_capacity"

    parking_zone: Mapped[str] = mapped_column(String(20), primary_key=True)
    booking_date: Mapped[str] = mapped_column(Date, primary_key=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (CheckConstraint("capacity > 0", name="chk_capacity_positive"),)


class ZoneCapacityConfig(Base):
    __tablename__ = "zone_capacity_config"

    parking_zone: Mapped[str] = mapped_column(String(20), primary_key=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    price_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("capacity > 0", name="chk_zone_capacity_positive"),
        CheckConstraint("price_per_day >= 0", name="chk_price_non_negative"),
    )


class Payment(Base):
    __tablename__ = "payments"

    payment_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    booking_id: Mapped[str] = mapped_column(String(20), ForeignKey("parking_bookings.booking_id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default="VND")
    payment_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="PENDING")
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        CheckConstraint(
            "payment_status IN ('PENDING', 'PAID', 'FAILED', 'REFUNDED')",
            name="chk_payment_status",
        ),
        CheckConstraint("amount >= 0", name="chk_payment_amount_non_negative"),
        Index(
            "uq_payments_paid_booking",
            "booking_id",
            unique=True,
            postgresql_where=text("payment_status = 'PAID'"),
        ),
    )


class Workflow(Base):
    __tablename__ = "workflows"

    workflow_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="PENDING")
    task_plan: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    archived_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_cost: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, server_default="0.0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    tasks: Mapped[list[WorkflowTask]] = relationship("WorkflowTask", back_populates="workflow")

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'WAITING_APPROVAL', 'SUCCESS', 'FAILED', 'CANCELLED')",
            name="chk_workflow_status",
        ),
        Index("idx_workflows_active", "status", postgresql_where=text("archived_at IS NULL")),
    )


class WorkflowTask(Base):
    __tablename__ = "workflow_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # [fix] phải khớp type workflows.workflow_id (UUID), nếu không create_all
    # fail: "Key columns are of incompatible types: character varying and uuid"
    workflow_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("workflows.workflow_id"), nullable=False)
    task_id: Mapped[str] = mapped_column(String(20), nullable=False)
    tool: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="PENDING")
    # list[str] task_id phụ thuộc (TaskPlan.depends_on) — Replanner dựng lại DAG từ đây
    depends_on: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    input_data: Mapped[dict] = mapped_column(JSONB)
    result_data: Mapped[dict] = mapped_column(JSONB)
    error_code: Mapped[str] = mapped_column(String(60))
    error_message: Mapped[str] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    workflow: Mapped[Workflow] = relationship("Workflow", back_populates="tasks")

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'READY', 'RUNNING', 'WAITING_APPROVAL', 'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELLED')",
            name="chk_workflow_task_status",
        ),
        UniqueConstraint("workflow_id", "task_id", name="uq_workflow_tasks_wf_task"),
        Index("idx_workflow_tasks_by_workflow", "workflow_id"),
        Index("idx_workflow_tasks_by_status", "workflow_id", "status"),
    )


class ExecutionLog(Base):
    __tablename__ = "execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # [fix] khớp type workflows.workflow_id (UUID)
    workflow_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    task_id: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    connector_name: Mapped[str] = mapped_column(String(60))
    http_status: Mapped[int] = mapped_column(Integer)
    raw_error_code: Mapped[str] = mapped_column(String(100))
    standard_result: Mapped[dict] = mapped_column(JSONB)
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.task_id"],
            name="fk_execution_logs_task",
        ),
        Index("idx_execution_logs_workflow_task", "workflow_id", "task_id"),
    )


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # [fix] khớp type workflows.workflow_id (UUID)
    workflow_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    task_id: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(100))
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id", "task_id"],
            ["workflow_tasks.workflow_id", "workflow_tasks.task_id"],
            name="fk_approval_decisions_task",
        ),
        CheckConstraint("decision IN ('APPROVED', 'REJECTED')", name="chk_approval_decision"),
        Index("idx_approval_decisions_workflow", "workflow_id"),
    )
