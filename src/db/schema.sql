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
    -- Chỗ đã HUỶ vẫn ở lại bảng, không bị xoá.
    --
    -- Xoá dòng là mất bản ghi duy nhất trả lời được "khách đã trả tiền cho cái
    -- gì". Với một lần huỷ MUỘN — vẫn huỷ nhưng không hoàn tiền — dòng
    -- `payments` PAID còn nguyên và trỏ vào booking này; xoá booking thì khoản
    -- tiền ấy trỏ vào hư không, và không ai giải thích được nó là tiền gì.
    status       VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE'
                     CHECK (status IN ('ACTIVE', 'CANCELLED')),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- `CREATE TABLE IF NOT EXISTS` KHÔNG thêm cột vào một bảng đã có, nên trên một
-- database đang chạy, cột `status` ở trên chưa tồn tại lúc file này chạy — và
-- chỉ mục phía dưới tham chiếu tới nó sẽ đổ. Dòng này là thứ giữ cho hai đường
-- (database mới và database đã có dữ liệu) đi tới cùng một hình dạng.
ALTER TABLE parking_bookings ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE';

-- Một xe một chỗ mỗi ngày — nhưng chỉ tính chỗ CÒN HIỆU LỰC.
--
-- Ràng buộc này từng là `UNIQUE (vehicle_id, booking_date)` trên toàn bảng. Khi
-- chỗ đã huỷ ở lại bảng, ràng buộc ấy chặn luôn lần đặt lại của chính người vừa
-- huỷ — và đặt lại là lý do phổ biến nhất người ta bấm huỷ.
--
-- Vi phạm → 409 BOOKING_ALREADY_EXISTS.
DO $$
BEGIN
    -- Bảng cũ mang ràng buộc trên TOÀN bộ dòng. Nó chặn luôn lần đặt lại của
    -- chính người vừa huỷ, nên phải nhường chỗ cho bản partial bên dưới.
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_bookings_vehicle_date') THEN
        ALTER TABLE parking_bookings DROP CONSTRAINT uq_bookings_vehicle_date;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_bookings_vehicle_date
    ON parking_bookings (vehicle_id, booking_date)
    WHERE status = 'ACTIVE';

-- [fix] Bảng kiểm soát sức chứa — bỏ booked_count denormalized.
-- booked_count được tính real-time bằng COUNT(*) FROM parking_bookings
-- trong transaction có SELECT ... FOR UPDATE trên row này.
-- Xem: src/db/postgres_repository.py :: check_and_reserve_capacity()
CREATE TABLE IF NOT EXISTS parking_capacity (
    parking_zone VARCHAR(20)  NOT NULL,
    booking_date DATE         NOT NULL,
    -- `>= 0`: sức chứa 0 nghĩa là khu không còn nhận đăng ký.
    capacity     INTEGER      NOT NULL CHECK (capacity >= 0),
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

    -- Observability Metrics
    total_tokens INTEGER     NOT NULL DEFAULT 0,
    total_cost   NUMERIC(10, 4) NOT NULL DEFAULT 0.0,
    latency_ms   INTEGER,


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


-- Dòng thời gian GIAI ĐOẠN của một workflow.
--
-- Trước đây `events` chỉ sống trong `_DEMO_JOBS` — bộ nhớ tiến trình — nên mỗi
-- lần backend khởi động lại là mất sạch, và mọi yêu cầu cũ mở lại từ Lịch sử
-- đều có mục "Chi tiết xử lý" trống. Trạng thái và các bước vẫn còn vì chúng
-- nằm trong database; chỉ dòng thời gian là bốc hơi.
--
-- Đây là bảng CHỈ THÊM: không sửa, không xoá theo dòng. Một sự kiện đã xảy ra
-- thì không đổi được, và ghi đè nó là viết lại lịch sử.
CREATE TABLE IF NOT EXISTS workflow_events (
    id           BIGSERIAL    PRIMARY KEY,
    workflow_id  UUID         NOT NULL REFERENCES workflows(workflow_id),
    sequence     INTEGER      NOT NULL,
    stage        VARCHAR(40)  NOT NULL,
    message      TEXT         NOT NULL,
    task_id      VARCHAR(20),
    task_status  VARCHAR(30),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Cùng một workflow không thể có hai sự kiện thứ N. Ràng buộc này biến một
    -- lượt ghi lặp (poll trùng, retry sau timeout) thành no-op thay vì thành
    -- dòng thừa: `ON CONFLICT DO NOTHING` dựa vào nó.
    UNIQUE (workflow_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_workflow_events_workflow
    ON workflow_events(workflow_id, sequence);


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

    -- Bằng chứng task đã rời hệ thống tới provider hay chưa.
    --
    -- Enum ĐÓNG, không phải boolean: câu hỏi có ba câu trả lời và câu thứ ba
    -- (`UNKNOWN` — không chứng minh được) là câu quan trọng nhất. Xem
    -- `src/common/submission.py`.
    --
    -- `NOT_SUBMITTED` là mặc định cho row MỚI, và chỉ cho row mới: một task
    -- vừa được tạo thì chắc chắn chưa gửi gì. Row có TRƯỚC cột này không có
    -- bằng chứng nào, nên migration backfill chúng thành `UNKNOWN` — xem
    -- `schema_migrations.sql`.
    provider_submission_status VARCHAR(20) NOT NULL DEFAULT 'NOT_SUBMITTED'
                      CHECK (provider_submission_status IN (
                          'NOT_SUBMITTED', 'SUBMITTING', 'ACKNOWLEDGED', 'UNKNOWN'
                      )),
    -- Tham chiếu CÓ THẨM QUYỀN do provider trả về (booking_id, viewing_id...).
    -- NULL khi chưa có; không bao giờ được điền bằng giá trị tự dựng.
    external_request_id        VARCHAR(120),
    -- Khoá idempotency đã dùng cho lần gửi này. Lưu ở BẢN GHI chứ không chỉ
    -- trong bộ nhớ process: retry sau restart phải dựng lại đúng khoá cũ, nếu
    -- không nó rơi ra ngoài bản ghi cũ và tạo giao dịch thứ hai.
    provider_idempotency_key   VARCHAR(160),

    -- task_id unique trong phạm vi một workflow
    CONSTRAINT uq_workflow_tasks_wf_task UNIQUE (workflow_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_workflow_tasks_by_workflow
    ON workflow_tasks(workflow_id);

CREATE INDEX IF NOT EXISTS idx_workflow_tasks_by_status
    ON workflow_tasks(workflow_id, status);

-- =============================================================
-- workflow_plan_revisions — sổ sửa đổi kế hoạch, CHỈ GHI THÊM
-- =============================================================
--
-- `workflow_tasks` là hình chiếu vận hành: `input_data` bị update mỗi lần một
-- bước đổi. Nó không phải nhật ký, nên nó không trả lời được "ai đã đổi gì,
-- lúc nào, từ phiên bản kế hoạch nào".
--
-- KHÔNG lưu ở đây: câu người dùng gõ, output thô của model, token/credential,
-- message của exception, DSN. Chúng là văn bản tự do đi vào một bảng lưu vĩnh
-- viễn — có thể mang dữ liệu cá nhân, và không giúp gì cho việc dựng lại lịch
-- sử sửa đổi. Chỉ giữ BẢN VÁ đã được thẩm định.
CREATE TABLE IF NOT EXISTS workflow_plan_revisions (
    revision_id         BIGSERIAL   PRIMARY KEY,
    workflow_id         UUID        NOT NULL REFERENCES workflows(workflow_id),
    revision_number     INTEGER     NOT NULL CHECK (revision_number > 0),
    requester_user_id   UUID,
    plan_version_before VARCHAR(32) NOT NULL,
    plan_version_after  VARCHAR(32) NOT NULL,
    accepted_patch      JSONB       NOT NULL,
    targets             JSONB       NOT NULL,
    consequence         VARCHAR(40) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Số thứ tự là thứ dựng lại được LỊCH SỬ. Hai dòng cùng số thì không dựng
    -- lại được, nên ràng buộc nằm ở database chứ không ở tầng ứng dụng.
    CONSTRAINT uq_plan_revisions_order UNIQUE (workflow_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_plan_revisions_by_workflow
    ON workflow_plan_revisions(workflow_id, revision_number);

-- Append-only, chặn ở DATABASE.
--
-- Một dòng audit viết đè lên được thì nó là ghi chú, không phải bằng chứng. Và
-- chặn ở tầng ứng dụng là không chặn: một script vận hành, một lần `psql`, hay
-- một tầng viết sau này đều đi vòng qua tầng ấy.
CREATE OR REPLACE FUNCTION workflow_plan_revisions_append_only() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'workflow_plan_revisions chi duoc GHI THEM; % bi tu choi', TG_OP;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS workflow_plan_revisions_no_update ON workflow_plan_revisions;
CREATE TRIGGER workflow_plan_revisions_no_update
    BEFORE UPDATE OR DELETE ON workflow_plan_revisions
    FOR EACH ROW EXECUTE FUNCTION workflow_plan_revisions_append_only();



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


CREATE TABLE IF NOT EXISTS registration_otps (
    email VARCHAR(255) PRIMARY KEY,
    otp_code VARCHAR(10) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


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

-- Hàng đợi duyệt của ĐƠN VỊ CUNG CẤP, cho mọi dịch vụ.
--
-- `viewing_approvals` chỉ phục vụ lịch tham quan và mang cột riêng của nó
-- (project_id, viewing_date...). Sáu dịch vụ còn lại — đăng ký xe, chỗ đỗ,
-- bảo trì, chuyển nhà, xe đưa đón, đăng ký tư vấn — chạy thẳng, không ai duyệt.
--
-- Một dòng cho MỖI BƯỚC cần duyệt, không phải mỗi workflow: một yêu cầu có thể
-- gồm nhiều dịch vụ của nhiều đơn vị khác nhau, và mỗi đơn vị chỉ quyết định
-- phần của mình.
--
-- `details` là JSONB thay vì cột cứng: mỗi dịch vụ có dữ kiện khác nhau, và
-- thêm một dịch vụ không được kéo theo một lần đổi schema.
CREATE TABLE IF NOT EXISTS service_approvals (
    workflow_id       UUID         NOT NULL,
    task_id           VARCHAR(20)  NOT NULL,
    tool              VARCHAR(64)  NOT NULL,
    service_label     VARCHAR(120) NOT NULL,
    details           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    status            VARCHAR(20)  NOT NULL DEFAULT 'AWAITING'
                      CHECK (status IN ('AWAITING', 'APPROVED', 'REJECTED', 'EXPIRED')),
    -- Dòng này là một BƯỚC để chạy, hay một LỜI NHỜ để đọc?
    --
    -- Mọi dòng ở đây từng được coi là bước. `resume_after_service_decision` đẩy
    -- mỗi dòng APPROVED về `PENDING` để Executor chạy — đúng với một bước, và
    -- là lỗi với một hồ sơ "xin đổi lịch": nó không có tool nào để chạy, và
    -- `task_id` của nó không có dòng `workflow_tasks`. `update_task_status` ném
    -- `TaskNotFoundError` GIỮA lượt resume, kéo theo cả những bước đơn vị vừa
    -- duyệt trong cùng lượt ấy.
    --
    -- `TASK` là mặc định vì mọi dòng có TRƯỚC cột này đều là bước.
    kind              VARCHAR(16)  NOT NULL DEFAULT 'TASK'
                      CHECK (kind IN ('TASK', 'REQUEST')),
    applicant_user_id UUID,
    applicant_name    VARCHAR(200),
    applicant_phone   VARCHAR(20),
    reject_reason     VARCHAR(500),
    decided_by        VARCHAR(100),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    decided_at        TIMESTAMPTZ,
    PRIMARY KEY (workflow_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_service_approvals_status
    ON service_approvals (status, created_at);


-- 2026-08 — GỘP hai hàng đợi duyệt thành MỘT.
--
-- `viewing_approvals` ra đời trước, phục vụ riêng lịch tham quan.
-- `service_approvals` ra sau, phục vụ sáu dịch vụ còn lại. Hai bảng nghĩa là
-- hai chỗ để lệch nhau, và người duyệt phải nhìn hai danh sách.
--
-- Gộp bằng cách giữ MỘT bảng vật lý (`service_approvals`) và biến
-- `viewing_approvals` thành KHUNG NHÌN trên nó. 108 chỗ đọc trong mã nguồn
-- không phải sửa dòng nào; chỉ 5 lệnh GHI được chuyển hướng — và cả 5 nằm
-- trong cùng một file.
--
-- Dữ liệu cũ được chuyển sang trước khi bỏ bảng: một lịch tham quan đang chờ
-- đơn vị duyệt mà biến mất giữa lúc nâng cấp là một khách bị bỏ rơi.
DO $$
BEGIN
    -- Chỉ chạy khi `viewing_approvals` còn là BẢNG. Chạy lần hai thì nó đã là
    -- view và khối này không làm gì.
    IF to_regclass('viewing_approvals') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.tables
            WHERE table_name = 'viewing_approvals' AND table_type = 'BASE TABLE'
       )
    THEN
        INSERT INTO service_approvals
            (workflow_id, task_id, tool, service_label, details, status,
             applicant_user_id, applicant_name, applicant_phone,
             reject_reason, decided_by, created_at, decided_at)
        SELECT v.workflow_id, v.task_id, 'schedule_property_viewing',
               'Đặt lịch tham quan',
               jsonb_strip_nulls(jsonb_build_object(
                   'project_id', v.project_id,
                   'project_name', v.project_name,
                   'viewing_date', to_char(v.viewing_date, 'YYYY-MM-DD'),
                   'viewing_time', v.viewing_time,
                   'passenger_count', v.passenger_count,
                   'wants_shuttle', v.wants_shuttle
               )),
               v.status, v.applicant_user_id, v.applicant_name, v.applicant_phone,
               v.reject_reason, v.decided_by, v.created_at, v.decided_at
          FROM viewing_approvals v
        ON CONFLICT (workflow_id, task_id) DO NOTHING;

        -- ĐỔI TÊN, không xoá. Dữ liệu đã được chép sang bảng gộp ở trên,
        -- nhưng "đã chép" chỉ đúng nếu câu INSERT chạy trọn — và một lệnh
        -- `DROP TABLE` thì không có đường lùi. Bảng cũ nằm lại dưới tên
        -- `_legacy` cho tới khi có người xác nhận bản gộp chạy ổn; xoá nó là
        -- một quyết định riêng, có người bấm.
        ALTER TABLE viewing_approvals RENAME TO viewing_approvals_legacy;
    END IF;

    IF to_regclass('viewing_approvals') IS NULL THEN
        -- Cùng TÊN CỘT với bảng cũ: mọi truy vấn đọc giữ nguyên.
        --
        -- `wants_shuttle` có COALESCE vì bảng cũ khai NOT NULL DEFAULT FALSE,
        -- còn `details` thì bỏ hẳn khoá khi giá trị là NULL — đọc ra NULL sẽ
        -- làm mọi nhánh `if wants_shuttle` đổi nghĩa.
        EXECUTE $view$
            CREATE VIEW viewing_approvals AS
            SELECT workflow_id, task_id, status,
                   details->>'project_id'                        AS project_id,
                   details->>'project_name'                      AS project_name,
                   (details->>'viewing_date')::date              AS viewing_date,
                   details->>'viewing_time'                      AS viewing_time,
                   (details->>'passenger_count')::int            AS passenger_count,
                   COALESCE((details->>'wants_shuttle')::boolean, FALSE) AS wants_shuttle,
                   applicant_user_id, applicant_name, applicant_phone,
                   reject_reason, decided_by, created_at, decided_at
              FROM service_approvals
             WHERE tool = 'schedule_property_viewing'
        $view$;
    END IF;
END
$$;


-- Trigger ghi cho `viewing_approvals` nằm ở `schema_migrations.sql`.
--
-- File đó là file duy nhất định nghĩa `viewing_approvals_write()`, vì nó phải
-- CHẠY ĐƯỢC MỘT MÌNH trên một database cũ (xem
-- `test_schema_migrations_upgrades_legacy_table`) — nên nó không thể mượn định
-- nghĩa từ đây. Chép sang cả hai file thì file chạy sau đè lên file chạy
-- trước, và một sửa đổi ở đây im lặng không có tác dụng: đo được khi
-- `ON CONFLICT ... DO UPDATE` viết ở file này, migration báo chạy xong, mà hàm
-- trong database vẫn giữ `DO NOTHING`.

-- =============================================================
-- Biên lai MATERIALIZATION cho hồ sơ xác minh.
--
-- Quyết định của đơn vị nằm ở Ownership Provider; kết quả nghiệp vụ
-- (liên kết cư dân, xe) nằm ở database này. Hai hệ thống, nối bằng HTTP,
-- KHÔNG chung transaction — nên tồn tại một khe mà cả hai đều không mô tả:
-- đơn vị đã ký, main app chưa ghi, và không ai biết.
--
-- Đo được trước khi có bảng này, ép lỗi đúng vào khe ấy:
--
--     provider           APPROVED
--     user_resident_links 0 dòng
--     lần đầu   http=500
--     lần hai   http=409   (ALREADY_DECIDED)
--
-- Người dùng kẹt vĩnh viễn: duyệt lại chỉ đập vào provider, không chạy nốt
-- phần còn thiếu.
--
-- Bảng này là BẰNG CHỨNG VẬN HÀNH của tiến trình nối hai hệ thống, KHÔNG phải
-- bản sao nguồn sự thật. Nó cố ý không mang `claimed_data`, ảnh giấy tờ, họ
-- tên, CCCD, token hay payload thô của provider — giữ chúng ở đây là tạo một
-- bản sao thứ hai của đúng thứ nhạy cảm nhất, trong một bảng sinh ra để phục
-- vụ retry.
--
-- KHÔNG có FK sang `verification_records`: Ownership Provider là một hệ thống
-- LOGIC khác. Hôm nay nó tình cờ dùng chung một PostgreSQL; một FK sẽ biến sự
-- trùng hợp ấy thành ràng buộc, và tách service ra sẽ vỡ.
-- =============================================================
CREATE TABLE IF NOT EXISTS verification_materializations (
    record_id                 UUID PRIMARY KEY,
    -- NULL cho tới khi đọc được provider. Xem ghi chú ở
    -- `verification_recovery.py`: đoán 'apartment' là ghi một sự kiện
    -- CHƯA BIẾT vào audit dưới dạng ĐÃ BIẾT.
    record_type               VARCHAR(20),
    requested_decision        VARCHAR(10)  NOT NULL,
    provider_decision_status  VARCHAR(20)  NOT NULL DEFAULT 'UNKNOWN',
    materialization_status    VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    -- Khoá ổn định theo record_id. Cùng một hồ sơ, dù retry bao nhiêu lần và
    -- từ tiến trình nào, luôn ra cùng một khoá.
    idempotency_key           VARCHAR(120) NOT NULL,
    -- Chỉ MÃ lỗi, không bao giờ message. Message của provider và của database
    -- đều từng mang nguyên payload, và bảng này là thứ bị dump vào issue.
    safe_error_code           VARCHAR(50),
    attempt_count             INTEGER      NOT NULL DEFAULT 0,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_verif_mat_idempotency UNIQUE (idempotency_key),
    CONSTRAINT verif_mat_type_check
        CHECK (record_type IS NULL OR record_type IN ('apartment', 'vehicle')),
    CONSTRAINT verif_mat_decision_check
        CHECK (requested_decision IN ('approve', 'reject')),
    CONSTRAINT verif_mat_provider_check
        CHECK (provider_decision_status IN ('UNKNOWN', 'PENDING', 'APPROVED', 'REJECTED')),
    CONSTRAINT verif_mat_status_check
        CHECK (materialization_status IN ('NOT_REQUIRED', 'PENDING', 'SUCCESS', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_verif_mat_unfinished
    ON verification_materializations(materialization_status)
    WHERE materialization_status IN ('PENDING', 'FAILED');

-- 2026-08 — LÝ DO TỪ CHỐI phải có MÃ, không chỉ có câu chữ.
--
-- Đo được trên yêu cầu thật: đơn vị từ chối `book_parking` với câu "Khu B đã
-- hết chỗ ngày 22/09/2028. Bạn chọn khu khác hoặc ngày khác giúp mình nhé."
-- Câu ấy nói đúng thứ khách cần làm, và hệ thống không làm gì với nó: mọi
-- REJECTED đều bị coi là kết thúc, nên workflow đứng WAITING_APPROVAL với
-- `pay_fee` treo mãi và khách không có ô nào để sửa.
--
-- Không đọc câu chữ để quyết định. Một `LIKE '%hết chỗ%'` sẽ hỏng ngay lần đầu
-- ai đó viết "không còn slot", và nó biến chính tả của người duyệt thành logic
-- nghiệp vụ. Mã đóng thì máy đọc mã, người đọc câu.
--
-- NULL được phép: dòng có TRƯỚC cột này không có mã nào, và bịa một mã cho
-- chúng là bịa ra một quyết định đơn vị chưa từng đưa ra.
ALTER TABLE service_approvals ADD COLUMN IF NOT EXISTS reject_code VARCHAR(32);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_service_approvals_reject_code'
    ) THEN
        ALTER TABLE service_approvals ADD CONSTRAINT ck_service_approvals_reject_code
            CHECK (reject_code IS NULL OR reject_code IN (
                'NO_AVAILABILITY', 'INVALID_REQUEST', 'SERVICE_UNAVAILABLE', 'OTHER'
            ));
    END IF;
END $$;
