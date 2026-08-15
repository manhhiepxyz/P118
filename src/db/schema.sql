-- =============================================================
-- P-118 — Database Schema
-- Version: v0.5.0
-- Updated: 2026-08-14
-- Owner: Hoàng Anh (src/db/)
-- =============================================================
-- Changelog v0.5.0:
--   [add] tour_slot_config / tour_bookings / tour_capacity: đặt lịch tham quan
--         dự án căn hộ (demo) — service book_tour
--   [add] shuttle_bookings: đặt xe tham quan (demo) — service book_shuttle
--   [add] consultations: đăng ký tư vấn mua (ở/kinh doanh/đầu tư) + thuê
--         (demo) — service register_consultation
-- Changelog v0.4.0:
--   [add] users: auth (login/register + RBAC) — scrypt password_hash
--         không seed bằng SQL (scrypt hash không tính được trong SQL);
--         admin đầu tiên tạo bằng scripts/create_admin.py
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
    amount       INTEGER      NOT NULL CHECK (amount > 0),
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
    amount         INTEGER      NOT NULL CHECK (amount > 0),
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
-- NHÓM 1b: DEMO SERVICES (đặt lịch tham quan / đặt xe / tư vấn)
-- =============================================================
-- [add] v0.5.0 — Dịch vụ demo sau Gate 2, mock API tự quản lý (giống NHÓM 1).
-- KHÔNG phải workflow state; Executor/Repository không ghi vào đây.
-- =============================================================

-- Cấu hình sức chứa slot tham quan theo (residential_area, tour_slot).
-- Seed rõ ràng trong seed.sql (giống zone_capacity_config cho parking).
CREATE TABLE IF NOT EXISTS tour_slot_config (
    residential_area VARCHAR(100) NOT NULL,
    tour_slot        VARCHAR(20)  NOT NULL
                         CHECK (tour_slot IN ('MORNING', 'AFTERNOON')),
    capacity         INTEGER      NOT NULL CHECK (capacity > 0),
    PRIMARY KEY (residential_area, tour_slot)
);

-- Đặt lịch tham quan dự án căn hộ.
-- resident_id NULL = khách tham quan (không phải cư dân).
CREATE TABLE IF NOT EXISTS tour_bookings (
    tour_id          VARCHAR(20)  PRIMARY KEY,          -- TOUR-001…
    resident_id      VARCHAR(20)
                         REFERENCES residents(resident_id),
    residential_area VARCHAR(100) NOT NULL,
    tour_date        DATE         NOT NULL,
    tour_slot        VARCHAR(20)  NOT NULL
                         CHECK (tour_slot IN ('MORNING', 'AFTERNOON')),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Một resident không đặt trùng (resident_id, tour_date, tour_slot).
    -- NULL resident_id (khách) không bị ràng buộc này — Postgres UNIQUE coi
    -- NULL khác nhau; sức chứa slot là guard chính cho khách.
    CONSTRAINT uq_tour_bookings_res_date_slot UNIQUE (resident_id, tour_date, tour_slot)
);

-- [add] Sức chứa slot tham quan theo ngày — đọc COUNT(*) + SELECT FOR UPDATE
-- trong transaction (giống parking_capacity). Không dùng booked_count denormalized.
CREATE TABLE IF NOT EXISTS tour_capacity (
    residential_area VARCHAR(100) NOT NULL,
    tour_date        DATE         NOT NULL,
    tour_slot        VARCHAR(20)  NOT NULL
                         CHECK (tour_slot IN ('MORNING', 'AFTERNOON')),
    capacity         INTEGER      NOT NULL CHECK (capacity > 0),
    PRIMARY KEY (residential_area, tour_date, tour_slot)
);

-- Đặt xe tham quan dự án.
-- Một lịch tham quan (tour_id) chỉ đặt 1 xe.
CREATE TABLE IF NOT EXISTS shuttle_bookings (
    shuttle_id      VARCHAR(20)  PRIMARY KEY,           -- SHUTTLE-001…
    tour_id         VARCHAR(20)  NOT NULL
                        REFERENCES tour_bookings(tour_id),
    tour_date       DATE         NOT NULL,
    passenger_count INTEGER      NOT NULL
                        CHECK (passenger_count BETWEEN 1 AND 30),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_shuttle_bookings_tour UNIQUE (tour_id)
);

-- Đăng ký tư vấn bất động sản.
-- consultation_type: BUY (mua) / RENT (thuê).
-- buy_sub_type (chỉ khi BUY): RESIDE (ở) / BUSINESS (kinh doanh) / INVEST (đầu tư).
CREATE TABLE IF NOT EXISTS consultations (
    consultation_id   VARCHAR(20)  PRIMARY KEY,         -- CONS-001…
    resident_id       VARCHAR(20)
                          REFERENCES residents(resident_id),
    consultation_type VARCHAR(20)  NOT NULL
                          CHECK (consultation_type IN ('BUY', 'RENT')),
    buy_sub_type      VARCHAR(20)
                          CHECK (buy_sub_type IN ('RESIDE', 'BUSINESS', 'INVEST')),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Một resident chỉ đăng ký 1 tư vấn cho mỗi loại.
    CONSTRAINT uq_consultations_resident_type UNIQUE (resident_id, consultation_type)
);


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
    archived_at TIMESTAMPTZ  DEFAULT NULL,

    -- Phiên hội thoại đã ghim (server-side) và workflow cha khi một lượt
    -- clarification sinh ra workflow con.
    parent_workflow_id UUID         REFERENCES workflows(workflow_id),
    session_id         VARCHAR(100),

    -- Lý do workflow hỏng, ở dạng MÃ ỔN ĐỊNH (LLM_CONFIGURATION_ERROR,
    -- PROVIDER_UNAVAILABLE, …). KHÔNG lưu message của exception: message đến
    -- từ thư viện bên thứ ba, đổi bất cứ lúc nào, và hay kèm chi tiết không
    -- nên nằm trong database.
    --
    -- Vì sao phải lưu: trước đây lỗi chỉ nằm trong `_DEMO_JOBS` — RAM của một
    -- tiến trình. Sau restart, workflow đọc lên là `PENDING`, giao diện map
    -- thành "đang chạy" và poll mãi một việc đã chết từ lâu.
    error_code         VARCHAR(60),

    -- Câu trả lời tự nhiên của P-118 cho CHÍNH workflow này.
    --
    -- Đây là thuộc tính TRÌNH BÀY của workflow, không phải một tin nhắn rời.
    -- P-118 là Agent thực hiện tác vụ, không phải chatbot có trí nhớ hội thoại:
    -- không có bảng conversation_messages, và box chat chỉ là cách hiển thị lại
    -- các workflow. Vì vậy câu trả lời sống ở đây, cùng chỗ với thứ nó mô tả.
    --
    -- Không có mấy cột này, câu trả lời chỉ nằm trong `_DEMO_JOBS` — RAM của
    -- một tiến trình — nên F5 hoặc restart là mất, còn workflow thì vẫn còn.
    assistant_answer      TEXT,
    assistant_suggestions JSONB       NOT NULL DEFAULT '[]'::jsonb,

    -- PENDING: đã chốt quyền sinh, đang gọi mô hình
    -- READY:   mô hình trả lời và câu đó đã qua kiểm
    -- FALLBACK: dùng câu deterministic (mô hình lỗi hoặc câu bị loại)
    assistant_response_state VARCHAR(20)
        CHECK (assistant_response_state IN ('PENDING', 'READY', 'FALLBACK')),

    -- Trạng thái workflow mà câu trả lời đang mô tả.
    --
    -- Cần thiết vì câu trả lời gắn với MỘT trạng thái: câu viết cho
    -- WAITING_APPROVAL trở thành sai ngay khi người dùng bấm duyệt. Cột này
    -- vừa là khoá idempotency (một lần gọi mô hình cho mỗi trạng thái) vừa là
    -- cách phát hiện câu đã lỗi thời.
    assistant_for_status  VARCHAR(30),
    assistant_updated_at  TIMESTAMPTZ
);

-- Index tìm workflow active (chưa archived)
CREATE INDEX IF NOT EXISTS idx_workflows_active
    ON workflows(status)
    WHERE archived_at IS NULL;

-- Ngữ cảnh cần để tiếp tục một workflow đang chờ người dùng bổ sung thông tin.
--
-- Không có bảng này, `/continue` chỉ chạy được khi job còn trong RAM: một lần
-- restart giữa lúc NEEDS_INFORMATION là mất hẳn hội thoại.
--
-- `workflow_id` có khoá ngoại tới `workflows`, nên workflow shell phải được
-- tạo TRƯỚC khi ghi clarification (xem `_ensure_workflow_shell`).
--
-- KHÔNG lưu: token, credential, raw LLM output. `existing_context` là context
-- TRUSTED do server dựng, không phải dữ liệu browser gửi lên.
CREATE TABLE IF NOT EXISTS workflow_clarifications (
    workflow_id        UUID         PRIMARY KEY REFERENCES workflows(workflow_id),
    session_id         VARCHAR(100),
    parent_workflow_id UUID,
    goal               TEXT         NOT NULL,
    missing_fields     JSONB        NOT NULL DEFAULT '[]',
    question           TEXT,
    existing_context   JSONB        NOT NULL DEFAULT '{}',
    resolved_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Chỉ index phần chưa được trả lời — đó là tập được truy vấn.
CREATE INDEX IF NOT EXISTS idx_workflow_clarifications_open
    ON workflow_clarifications(workflow_id) WHERE resolved_at IS NULL;


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
    depends_on    JSONB        NOT NULL DEFAULT '[]',     -- list[str] task_id phụ thuộc, Replanner đọc lại
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


-- =============================================================
-- NHÓM 5: REPAIR HINTS
-- ======================================================

-- Lỗi nghiệp vụ repairable (FAILED nhưng user có thể đổi input để chạy tiếp).
-- workflows.status vẫn FAILED; bảng con này nhận diện trạng thái con
-- "FAILED nhưng repairable". Chỉ lưu error_code + message generic (KHÔNG có
-- field) — missing_fields sinh tại render từ error_code + task.tool.
-- =============================================================

CREATE TABLE IF NOT EXISTS workflow_repair_hints (
    id          BIGSERIAL    PRIMARY KEY,
    workflow_id UUID         NOT NULL
                    REFERENCES workflows(workflow_id),
    task_id     VARCHAR(20)  NOT NULL,
    error_code  VARCHAR(60)  NOT NULL,
    message     VARCHAR(500) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workflow_repair_hints_wf
    ON workflow_repair_hints(workflow_id);
-- =============================================================
-- NHÓM 6: AUTH
-- =============================================================
-- [add] v0.4.0 — Tài khoản đăng nhập (login/register) + phân quyền.
--   - role: 'customer' (mặc định khi register) | 'admin' (tạo bằng
--     scripts/create_admin.py). role là VAI TRÒ TÀI KHOẢN, KHÔNG phải quyền
--     cư dân — quyền đó nằm ở bảng user_resident_links.
--   - password_hash: chuỗi 'scrypt:N:r:p:salt_b64:hash_b64' (stdlib
--     hashlib.scrypt, salt random 16 bytes). KHÔNG seed bằng SQL vì
--     scrypt không tính được trong SQL.
--   - archived_at: soft delete — user bị xoá vẫn giữ audit trail
--     (decided_by / execution_logs).
-- =============================================================

CREATE TABLE IF NOT EXISTS users (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(50)  NOT NULL,                  -- lowercase ở tầng app
    email         VARCHAR(255),
    password_hash TEXT         NOT NULL,                  -- scrypt:N:r:p:salt_b64:hash_b64
    role          VARCHAR(20)  NOT NULL DEFAULT 'customer'
                      CONSTRAINT ck_users_role CHECK (role IN ('customer', 'admin')),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    archived_at   TIMESTAMPTZ  DEFAULT NULL,

    CONSTRAINT uq_users_username UNIQUE (username)
);

-- Email unique chỉ khi có giá trị (nhiều user có thể bỏ trống email)
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email
    ON users(email) WHERE email IS NOT NULL;


-- =============================================================
-- Yêu cầu liên kết căn hộ do CHÍNH CHỦ TÀI KHOẢN gửi
-- =============================================================
-- Trước đây customer không có đường nào để bắt đầu việc liên kết: admin phải
-- tự gõ UUID tài khoản và mã cư dân. Nghĩa là admin phải biết trước ai muốn
-- liên kết căn hộ nào — một thông tin chỉ tồn tại ngoài hệ thống.
--
-- Bảng này giữ phần khách hàng KHAI. Nó KHÔNG phải nguồn sự thật về quyền:
-- quyền vẫn nằm ở `user_resident_links.verification_status`, và chỉ admin ghi
-- được. Khách hàng gửi yêu cầu; ban quản lý quyết định.
--
-- GIỚI HẠN GATE 2: xác minh là thao tác THỦ CÔNG của admin, không phải eKYC.
-- Trust boundary thì đúng — người dùng không tự nâng quyền được — nhưng bằng
-- chứng danh tính thì chưa có.
CREATE TABLE IF NOT EXISTS resident_link_requests (
    request_id       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID         NOT NULL REFERENCES users(id),

    -- Thông tin người dùng ĐỌC ĐƯỢC và biết được. Cố ý KHÔNG có `resident_id`:
    -- đó là mã nội bộ, và cho khách hàng gửi mã cư dân nghĩa là cho họ trỏ vào
    -- hồ sơ của bất kỳ ai.
    apartment_code   VARCHAR(50)  NOT NULL,
    residential_area VARCHAR(100) NOT NULL,
    full_name        VARCHAR(200) NOT NULL,

    status           VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                         CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    decided_at       TIMESTAMPTZ,
    -- Ai đã quyết định. Audit trail: một liên kết được mở phải truy được về
    -- một con người.
    decided_by       UUID         REFERENCES users(id)
);

-- Mỗi tài khoản chỉ được có ĐÚNG MỘT yêu cầu đang chờ.
-- Không có ràng buộc này, bấm gửi mười lần tạo mười dòng chờ duyệt giống hệt
-- nhau, và admin phải đoán cái nào là thật.
CREATE UNIQUE INDEX IF NOT EXISTS uq_link_request_one_pending_per_user
    ON resident_link_requests(user_id)
    WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_link_requests_status
    ON resident_link_requests(status, created_at);
