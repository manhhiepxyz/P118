-- =============================================================
-- src/db/schema_migrations.sql
-- P-118 — Incremental migration cho DB ĐÃ TỒN TẠI
--
-- schema.sql dùng CREATE TABLE IF NOT EXISTS nên KHÔNG bao giờ thêm
-- cột mới vào bảng đã có. Mọi thay đổi cột sau khi schema.sql được
-- deploy phải khai báo thêm ở đây dưới dạng ALTER ... IF NOT EXISTS
-- (idempotent, chạy lại nhiều lần vẫn an toàn).
--
-- File này chạy NGAY SAU schema.sql trong src/db/migrations.py.
-- =============================================================

-- 2026-08 — workflow_tasks.depends_on
-- TaskPlan có depends_on: list[str] nhưng bảng chưa có cột → create_task()
-- nuốt mất dependency edge, Replanner không đọc lại được DAG từ DB.
ALTER TABLE IF EXISTS workflow_tasks
    ADD COLUMN IF NOT EXISTS depends_on JSONB NOT NULL DEFAULT '[]';

-- 2026-08 — payments.idempotency_key
-- Chống double-charge khi client/Executor retry sau timeout.
--
-- Partial index uq_payments_paid_booking đã chặn "hai PAID cho cùng booking",
-- nhưng nó chỉ có tác dụng SAU KHI giao dịch đầu thành công. Một retry xảy ra
-- lúc giao dịch đầu còn PENDING vẫn tạo được row thứ hai. Idempotency key
-- deterministic (workflow_id + payment task_id) chặn ngay từ lần INSERT.
--
-- Cột cho phép NULL để không phá row đã có; UNIQUE index bỏ qua NULL nên
-- payment cũ không bị ảnh hưởng.
--
-- Toàn bộ khối này bọc trong kiểm tra to_regclass: file migration còn được
-- chạy trên database legacy chỉ có vài bảng (xem test_schema_migrations_
-- upgrades_legacy_table), nơi payments/parking_bookings chưa tồn tại.
--
-- to_regclass KHÔNG qualify schema: migration bám theo search_path, nên guard
-- phải giải tên bảng đúng cách ALTER bên dưới sẽ giải.
DO $$
BEGIN
    IF to_regclass('payments') IS NOT NULL THEN
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(120);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_idempotency_key
            ON payments(idempotency_key)
            WHERE idempotency_key IS NOT NULL;

        -- Thanh toán thật phải lớn hơn 0. NOT VALID để không đụng dữ liệu cũ.
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_payments_amount_positive') THEN
            ALTER TABLE payments
                ADD CONSTRAINT ck_payments_amount_positive CHECK (amount > 0) NOT VALID;
        END IF;

        -- MVP chỉ dùng VND.
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_payments_currency_vnd') THEN
            ALTER TABLE payments
                ADD CONSTRAINT ck_payments_currency_vnd CHECK (currency = 'VND') NOT VALID;
        END IF;
    END IF;

    IF to_regclass('parking_bookings') IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_bookings_currency_vnd') THEN
            ALTER TABLE parking_bookings
                ADD CONSTRAINT ck_bookings_currency_vnd CHECK (currency = 'VND') NOT VALID;
        END IF;
        -- Báo giá phải > 0, khớp ck_payments_amount_positive và Tool Contract.
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_bookings_amount_positive') THEN
            ALTER TABLE parking_bookings
                ADD CONSTRAINT ck_bookings_amount_positive CHECK (amount > 0) NOT VALID;
        END IF;
    END IF;
END
$$;

-- 2026-08 — sequence sinh ID nghiệp vụ
-- `SELECT max(id) + 1` bị race: nhiều transaction đồng thời đọc cùng một giá
-- trị rồi cùng INSERT, gây va chạm PRIMARY KEY. Sequence là bộ đếm nguyên tử
-- nằm ngoài transaction nên không bao giờ trả trùng.
CREATE SEQUENCE IF NOT EXISTS seq_resident_id;
CREATE SEQUENCE IF NOT EXISTS seq_vehicle_id;
CREATE SEQUENCE IF NOT EXISTS seq_booking_id;
CREATE SEQUENCE IF NOT EXISTS seq_payment_id;

-- Đẩy sequence vượt qua dữ liệu đã có để không đụng ID cũ.
DO $$
DECLARE
    highest BIGINT;
BEGIN
    IF to_regclass('residents') IS NOT NULL THEN
        SELECT COALESCE(MAX(NULLIF(regexp_replace(resident_id, '\D', '', 'g'), '')::BIGINT), 0)
          INTO highest FROM residents;
        PERFORM setval('seq_resident_id', GREATEST(highest, 1), highest > 0);
    END IF;
    IF to_regclass('vehicles') IS NOT NULL THEN
        SELECT COALESCE(MAX(NULLIF(regexp_replace(vehicle_id, '\D', '', 'g'), '')::BIGINT), 0)
          INTO highest FROM vehicles;
        PERFORM setval('seq_vehicle_id', GREATEST(highest, 1), highest > 0);
    END IF;
    IF to_regclass('parking_bookings') IS NOT NULL THEN
        SELECT COALESCE(MAX(NULLIF(regexp_replace(booking_id, '\D', '', 'g'), '')::BIGINT), 0)
          INTO highest FROM parking_bookings;
        PERFORM setval('seq_booking_id', GREATEST(highest, 1), highest > 0);
    END IF;
    IF to_regclass('payments') IS NOT NULL THEN
        SELECT COALESCE(MAX(NULLIF(regexp_replace(payment_id, '\D', '', 'g'), '')::BIGINT), 0)
          INTO highest FROM payments;
        PERFORM setval('seq_payment_id', GREATEST(highest, 1), highest > 0);
    END IF;
END
$$;

-- 2026-08 — payment_approvals
-- Ngữ cảnh chờ xác nhận thanh toán, để resume KHÔNG phụ thuộc RAM.
--
-- `_DEMO_JOBS` và exception object đều biến mất khi backend restart. Nếu resume
-- dựa vào chúng thì một lần restart giữa lúc user đang cân nhắc là mất luôn
-- booking đã giữ chỗ. Bảng này giữ đủ để dựng lại lệnh thanh toán từ số 0.
--
-- `booking_id`/`amount`/`currency` chép từ booking đã persist tại thời điểm
-- báo giá, KHÔNG lấy từ goal hay browser.
DO $$
BEGIN
    -- Bảng này có FK tới workflows; database legacy trong test migration chỉ có
    -- vài bảng nên phải guard, nếu không migration vỡ trên chính đường nâng cấp.
    IF to_regclass('workflows') IS NOT NULL THEN
        CREATE TABLE IF NOT EXISTS payment_approvals (
            workflow_id UUID         PRIMARY KEY REFERENCES workflows(workflow_id),
            task_id     VARCHAR(20)  NOT NULL,
            booking_id  VARCHAR(20)  NOT NULL,
            amount      INTEGER      NOT NULL CHECK (amount > 0),
            currency    VARCHAR(10)  NOT NULL DEFAULT 'VND' CHECK (currency = 'VND'),
            status      VARCHAR(20)  NOT NULL DEFAULT 'AWAITING'
                            CHECK (status IN ('AWAITING', 'APPROVED', 'REJECTED')),
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            decided_at  TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_payment_approvals_status ON payment_approvals(status);
    END IF;
END
$$;

-- 2026-08 — Workflow session chain
-- Link parent-child workflows. `/continue` tạo child workflow giữ cùng session_id
-- và trỏ về workflow_id cũ, cho phép query lịch sử một cuộc hội thoại.
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL THEN
        ALTER TABLE IF EXISTS workflows
            ADD COLUMN IF NOT EXISTS parent_workflow_id UUID
                REFERENCES workflows(workflow_id),
            ADD COLUMN IF NOT EXISTS session_id VARCHAR(100);

        CREATE INDEX IF NOT EXISTS idx_workflows_by_parent
            ON workflows(parent_workflow_id)
            WHERE parent_workflow_id IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_workflows_by_session
            ON workflows(session_id)
            WHERE session_id IS NOT NULL;
    END IF;
END
$$;

-- 2026-08 — sessions
-- Session server-side: account_state + resident_id ghim tại lần /start đầu,
-- KHÔNG tin lại body request. Persona switch = tạo session mới (thread mới).
--
-- Vì sao cần bảng này: `DemoWorkflowRequest.account_state` do browser gửi trong
-- body; nếu trust trực tiếp thì bất kỳ client nào gửi `"resident"` đều được cấp
-- quyền cư dân đã xác thực. Bảng ghim persona lần đầu, mọi lần sau (/continue,
-- payment decision, list) đọc từ đây, không từ body.
--
-- Không FK tới workflows: một session có thể có trước workflow đầu tiên.
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL THEN
        CREATE TABLE IF NOT EXISTS sessions (
            session_id    VARCHAR(100) PRIMARY KEY,
            account_state VARCHAR(20) NOT NULL
                            CHECK (account_state IN ('prospect', 'resident')),
            resident_id   VARCHAR(20),
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
    END IF;
END
$$;

-- 2026-08 — llm_usage
-- Token/cost mỗi lần gọi LLM (planner.plan / replan). workflow_id NULL khi
-- chạy eval (không thuộc workflow). KHÔNG lưu prompt/response — chỉ số.
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL THEN
        CREATE TABLE IF NOT EXISTS llm_usage (
            id                BIGSERIAL   PRIMARY KEY,
            workflow_id       UUID        REFERENCES workflows(workflow_id),
            run_id            VARCHAR(64),
            stage             VARCHAR(20) NOT NULL,
            provider          VARCHAR(20) NOT NULL,
            model             VARCHAR(64) NOT NULL,
            prompt_tokens     INTEGER     NOT NULL DEFAULT 0,
            completion_tokens INTEGER     NOT NULL DEFAULT 0,
            total_tokens      INTEGER     NOT NULL DEFAULT 0,
            latency_ms        INTEGER,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_llm_usage_workflow ON llm_usage(workflow_id);
        CREATE INDEX IF NOT EXISTS idx_llm_usage_created  ON llm_usage(created_at);
    END IF;
END
$$;

-- 2026-08 — workflow_repair_hints
-- Lỗi nghiệp vụ repairable (FAILED nhưng user có thể đổi input để chạy tiếp).
--
-- `workflows.status` vẫn FAILED — không đổi enum. Bảng con này nhận diện trạng
-- thái con "FAILED nhưng repairable", đúng mẫu payment_approvals: child table
-- gắn một trạng thái bổ nghĩa cho workflow mà không đụng status chính.
--
-- Chỉ persist `error_code + message` generic (KHÔNG có field): on_failure không
-- mang tool nên RepairManager không thể map error_code → missing_fields. Bảng
-- này tồn tại để poll sau restart vẫn trả NEEDS_INFORMATION (chống zombie — hai
-- tên một trạng thái khi `_DEMO_JOBS` biến mất).
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL THEN
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
    END IF;
END
$$;

-- 2026-08 — workflow_clarifications
-- Ngữ cảnh cần để tiếp tục một workflow đang chờ người dùng bổ sung thông tin.
--
-- Trước đây `/continue` bắt buộc `_DEMO_JOBS[workflow_id]` phải còn trong RAM.
-- Một lần restart backend giữa lúc NEEDS_INFORMATION là mất hẳn hội thoại:
-- người dùng điền form xong thì nhận 409 và không có đường quay lại.
--
-- KHÔNG lưu: token, credential, raw LLM output, hay câu trả lời chưa kiểm của
-- người dùng. `existing_context` là context TRUSTED do server dựng (resident_id,
-- apartment, project_id), không phải dữ liệu browser gửi lên.
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL THEN
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
        CREATE INDEX IF NOT EXISTS idx_workflow_clarifications_open
            ON workflow_clarifications(workflow_id) WHERE resolved_at IS NULL;
    END IF;
END
$$;
