-- =============================================================
-- P-118 — Database Schema
-- Version: v0.3.0
-- Updated: 2026-08-05
-- Owner: Hoàng Anh (src/db/)
-- =============================================================
-- Changelog v0.3.0:
--   [fix] parking_capacity: bỏ booked_count denormalized,
--         tính COUNT(*) + SELECT FOR UPDATE trong transaction
--   [fix] execution_logs: thêm composite FK → workflow_tasks
--   [fix] workflows: thêm archived_at (soft delete) thay vì
--         cascade cứng, giữ audit trail
--   [fix] payments: thêm partial unique index chống PAID trùng
--   [fix] residents: thêm updated_at cho nhất quán
--   [fix] parking_capacity: bỏ DEFAULT capacity, seed rõ ràng
--   [add] approval_decisions: HITL audit trail
--   [add] pgcrypto extension rõ ràng
-- =============================================================

-- Extension (Postgres 13 trở xuống cần pgcrypto cho gen_random_uuid)
-- Postgres 14+ có sẵn, nhưng CREATE IF NOT EXISTS an toàn trong mọi version.
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- =============================================================
-- NHÓM 1: BUSINESS DATA (Mock API tự quản lý)
-- =============================================================
-- Những bảng này lưu dữ liệu nghiệp vụ của mock service.
-- KHÔNG phải workflow state. Executor/Repository không ghi trực tiếp vào đây.
-- =============================================================

CREATE TABLE IF NOT EXISTS residents (
    resident_id      VARCHAR(20)   PRIMARY KEY,       -- RES-001, RES-002…
    full_name        VARCHAR(200)  NOT NULL,
    apartment_code   VARCHAR(50)   NOT NULL,
    residential_area VARCHAR(100)  NOT NULL,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),  -- [fix] nhất quán với các bảng khác

    -- Nghiệp vụ: một căn hộ trong một khu chỉ có 1 cư dân chính đăng ký
    -- Vi phạm → 409 RESIDENT_ALREADY_EXISTS
    CONSTRAINT uq_residents_apt_area UNIQUE (apartment_code, residential_area)
);

-- [add] Bảng chủ sở hữu căn hộ — dùng để verify quyền sở hữu khi đăng ký cư dân.
-- Khi register_resident, hệ thống nội bộ tra bảng này:
--   - Không có (apartment_code, residential_area) → 404 OWNERSHIP_NOT_FOUND
--   - Có nhưng owner_name != full_name gửi lên → 403 OWNERSHIP_MISMATCH
--   - Match → cho phép đăng ký resident
-- Dữ liệu seed sẵn trong seed.sql (data thật lấy từ ban quản lý chung cư).
CREATE TABLE IF NOT EXISTS apartment_owners (
    apartment_code   VARCHAR(50)  NOT NULL,
    residential_area VARCHAR(100) NOT NULL,
    owner_name       VARCHAR(200) NOT NULL,           -- tên chủ sở hữu (dùng verify)
    id_number        VARCHAR(20),                     -- CCCD/CMND, chỉ lưu masked
    verified_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    PRIMARY KEY (apartment_code, residential_area)
);

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id   VARCHAR(20)  PRIMARY KEY,            -- VEH-001…
    resident_id  VARCHAR(20)  NOT NULL
                     REFERENCES residents(resident_id),
    plate_number VARCHAR(20)  NOT NULL,
    vehicle_type VARCHAR(20)  NOT NULL
                     CHECK (vehicle_type IN ('car', 'motorcycle')),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Vi phạm → 409 VEHICLE_ALREADY_EXISTS
    CONSTRAINT uq_vehicles_plate UNIQUE (plate_number)
);

CREATE TABLE IF NOT EXISTS parking_bookings (
    booking_id   VARCHAR(20)  PRIMARY KEY,            -- BOOK-001…
    vehicle_id   VARCHAR(20)  NOT NULL
                     REFERENCES vehicles(vehicle_id),
    parking_zone VARCHAR(20)  NOT NULL
                     CHECK (parking_zone IN ('ZONE_A', 'ZONE_B')),
    booking_date DATE         NOT NULL,
    amount       INTEGER      NOT NULL CHECK (amount >= 0),
    currency     VARCHAR(10)  NOT NULL DEFAULT 'VND',
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Vi phạm → 409 BOOKING_ALREADY_EXISTS (xe + ngày không được trùng)
    CONSTRAINT uq_bookings_vehicle_date UNIQUE (vehicle_id, booking_date)
);

-- [fix] Bảng kiểm soát sức chứa — bỏ booked_count denormalized.
-- booked_count được tính real-time bằng COUNT(*) FROM parking_bookings
-- trong transaction có SELECT ... FOR UPDATE trên row này.
-- Xem: src/db/postgres_repository.py :: check_and_reserve_capacity()
CREATE TABLE IF NOT EXISTS parking_capacity (
    parking_zone VARCHAR(20)  NOT NULL,
    booking_date DATE         NOT NULL,
    capacity     INTEGER      NOT NULL CHECK (capacity > 0),
    PRIMARY KEY (parking_zone, booking_date)
    -- Không có booked_count — tránh race condition và lệch dữ liệu.
    -- Lý do: nếu update booked_count ở service layer mà không có
    -- transaction chặt (SELECT FOR UPDATE), 2 request đồng thời đều
    -- pass check rồi cùng book vượt capacity.
    -- Giải pháp: đọc COUNT(*) FROM parking_bookings trong cùng transaction.
);

-- [add] Seed capacity mặc định theo zone khi migration.
-- Service layer tự INSERT row nếu chưa tồn tại (ON CONFLICT DO NOTHING).
-- Seed rõ ràng để tránh dựa vào DEFAULT cứng.
-- Xem: src/db/seed.sql
-- ZONE_A: 3 chỗ/ngày (giả lập), ZONE_B: 10 chỗ/ngày

CREATE TABLE IF NOT EXISTS payments (
    payment_id     VARCHAR(20)  PRIMARY KEY,          -- PAY-001…
    booking_id     VARCHAR(20)  NOT NULL
                       REFERENCES parking_bookings(booking_id),
    amount         INTEGER      NOT NULL CHECK (amount >= 0),
    currency       VARCHAR(10)  NOT NULL DEFAULT 'VND',
    payment_status VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                       CHECK (payment_status IN ('PENDING', 'PAID', 'FAILED', 'REFUNDED')),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- [fix] Chỉ được có 1 payment thành công (PAID) cho mỗi booking.
-- Partial index: nhiều PENDING/FAILED được phép, nhưng chỉ 1 PAID.
-- Vi phạm → raise UniqueViolationError → service map sang PAYMENT_FAILED.
CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_paid_booking
    ON payments(booking_id)
    WHERE payment_status = 'PAID';


-- =============================================================
-- NHÓM 2: WORKFLOW ENGINE STATE
-- =============================================================
-- Executor ghi/đọc qua WorkflowStateRepository Protocol.
-- Không ai ghi thẳng SQL vào đây ngoài postgres_repository.py.
-- =============================================================

CREATE TABLE IF NOT EXISTS workflows (
    workflow_id UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    goal        TEXT,                                -- user goal gốc (natural language)
    status      VARCHAR(30)  NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN (
                        'PENDING', 'RUNNING', 'WAITING_APPROVAL',
                        'SUCCESS', 'FAILED', 'CANCELLED'
                    )),
    task_plan   JSONB,                               -- snapshot TaskPlan tại thời điểm tạo
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- [fix] Soft delete thay vì DELETE cứng.
    -- Lý do: execution_logs cần giữ audit trail ngay cả khi workflow "bị xóa".
    -- NULL = active; NOT NULL = archived (không hiển thị ở UI nhưng không mất dữ liệu).
    archived_at TIMESTAMPTZ  DEFAULT NULL
);

-- Index tìm workflow active (chưa archived)
CREATE INDEX IF NOT EXISTS idx_workflows_active
    ON workflows(status)
    WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS workflow_tasks (
    id            BIGSERIAL    PRIMARY KEY,
    workflow_id   UUID         NOT NULL
                      REFERENCES workflows(workflow_id),  -- không CASCADE — xem lý do ở archived_at
    task_id       VARCHAR(20)  NOT NULL,                  -- T1, T2… lấy từ TaskPlan, repo KHÔNG tạo mới
    tool          VARCHAR(60)  NOT NULL,                  -- register_resident | register_vehicle | book_parking | pay_fee
    status        VARCHAR(30)  NOT NULL DEFAULT 'PENDING'
                      CHECK (status IN (
                          'PENDING', 'READY', 'RUNNING', 'WAITING_APPROVAL',
                          'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELLED'
                      )),
    input_data    JSONB,                                  -- input resolved sau data propagation
    result_data   JSONB,                                  -- StandardResult.data khi SUCCESS
    error_code    VARCHAR(60),                            -- ErrorCode enum khi FAILED
    error_message TEXT,
    retryable     BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- task_id unique trong phạm vi một workflow
    CONSTRAINT uq_workflow_tasks_wf_task UNIQUE (workflow_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_workflow_tasks_by_workflow
    ON workflow_tasks(workflow_id);

CREATE INDEX IF NOT EXISTS idx_workflow_tasks_by_status
    ON workflow_tasks(workflow_id, status);


-- =============================================================
-- NHÓM 3: EXECUTION AUDIT LOG
-- =============================================================
-- Mỗi lần Connector gọi API (kể cả retry) ghi 1 row.
-- Lưu mã lỗi gốc trước khi normalize → hỗ trợ UNKNOWN_EXTERNAL_ERROR.
-- =============================================================

CREATE TABLE IF NOT EXISTS execution_logs (
    id              BIGSERIAL    PRIMARY KEY,
    workflow_id     UUID         NOT NULL
                        REFERENCES workflows(workflow_id),
    task_id         VARCHAR(20)  NOT NULL,
    attempt_number  INTEGER      NOT NULL DEFAULT 1,       -- retry lần thứ mấy
    connector_name  VARCHAR(60),                           -- TransportConnector, PaymentConnector…
    http_status     INTEGER,                               -- HTTP status thật từ API ngoài
    raw_error_code  VARCHAR(100),                          -- mã lỗi gốc (trước khi Connector normalize)
    standard_result JSONB,                                 -- StandardResult sau normalize
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- [fix] Composite FK thật sự → workflow_tasks(workflow_id, task_id)
    -- UNIQUE(workflow_id, task_id) đã có ở workflow_tasks.uq_workflow_tasks_wf_task
    CONSTRAINT fk_execution_logs_task
        FOREIGN KEY (workflow_id, task_id)
        REFERENCES workflow_tasks(workflow_id, task_id)
    -- Không có ON DELETE CASCADE vì workflow dùng soft delete (archived_at).
    -- Audit log tồn tại mãi mãi, kể cả khi workflow được archive.
);

CREATE INDEX IF NOT EXISTS idx_execution_logs_workflow_task
    ON execution_logs(workflow_id, task_id);


-- =============================================================
-- NHÓM 4: HITL AUDIT TRAIL
-- =============================================================
-- [add] Lưu lịch sử approve/reject của con người (HITL gate).
-- Cần thiết cho demo/bảo vệ: chứng minh có audit đầy đủ cho
-- mọi quyết định WAITING_APPROVAL → APPROVED/REJECTED.
-- =============================================================

CREATE TABLE IF NOT EXISTS approval_decisions (
    id          BIGSERIAL    PRIMARY KEY,
    workflow_id UUID         NOT NULL
                    REFERENCES workflows(workflow_id),
    task_id     VARCHAR(20)  NOT NULL,
    decided_by  VARCHAR(100),                              -- user ID hoặc "system" nếu auto
    decision    VARCHAR(20)  NOT NULL
                    CHECK (decision IN ('APPROVED', 'REJECTED')),
    comment     TEXT,                                      -- lý do từ chối hoặc ghi chú
    decided_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_approval_decisions_task
        FOREIGN KEY (workflow_id, task_id)
        REFERENCES workflow_tasks(workflow_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_approval_decisions_workflow
    ON approval_decisions(workflow_id);
