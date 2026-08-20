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

-- 2026-08 — viewing_approvals
-- Ngữ cảnh chờ provider/admin duyệt lịch tham quan (`/review`), để resume KHÔNG
-- phụ thuộc RAM.
--
-- Giống `payment_approvals` nhưng người duyệt là provider/admin (KHÔNG phải chủ
-- workflow), và sau khi duyệt backend materialize lịch tour rồi chạy nốt các
-- task phụ thuộc (book_shuttle). Bảng giữ đủ thông tin dựng lại lệnh đặt lịch
-- từ số 0: project/ngày/giờ đọc từ task input lúc persist, applicant_name/phone
-- là snapshot từ bảng `users` (đừng JOIN lúc sau — user có thể đổi họ tên).
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL THEN
        CREATE TABLE IF NOT EXISTS viewing_approvals (
            workflow_id      UUID         PRIMARY KEY REFERENCES workflows(workflow_id),
            task_id          VARCHAR(20)  NOT NULL,
            status           VARCHAR(20)  NOT NULL DEFAULT 'AWAITING'
                                 CHECK (status IN ('AWAITING', 'APPROVED', 'REJECTED', 'EXPIRED')),
            project_id       VARCHAR(20)  NOT NULL,
            project_name     VARCHAR(200),
            viewing_date     DATE         NOT NULL,
            viewing_time     VARCHAR(5)   NOT NULL,
            passenger_count  INTEGER,
            wants_shuttle    BOOLEAN      NOT NULL DEFAULT FALSE,
            applicant_user_id UUID,
            applicant_name   VARCHAR(200),
            applicant_phone  VARCHAR(20),
            reject_reason    VARCHAR(500),
            decided_by       VARCHAR(100),
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            decided_at       TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_viewing_approvals_status ON viewing_approvals(status);
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

-- 2026-08 — canonical fields cho schedule_property_viewing / register_property_interest
--
-- Contract public (shared_contracts.md) dùng project_id + viewing_date +
-- viewing_time (HH:MM). Implementation nội bộ vẫn là bảng `tour_bookings` /
-- `consultations` của nhánh Hoàng Anh — đổi tên bảng lúc này rủi ro hơn giá
-- trị mang lại — nhưng bảng phải mang đủ field canonical.
--
-- `tour_slot` chỉ có MORNING/AFTERNOON nên KHÔNG biểu diễn được HH:MM. Thêm
-- cột `viewing_time` thay vì ép giờ về hai buổi: contract yêu cầu phút giờ, và
-- làm tròn nó là làm mất dữ liệu người dùng đã nhập.
DO $$
BEGIN
    IF to_regclass('tour_bookings') IS NOT NULL THEN
        ALTER TABLE tour_bookings
            ADD COLUMN IF NOT EXISTS project_id   VARCHAR(20),
            ADD COLUMN IF NOT EXISTS project_name VARCHAR(120),
            ADD COLUMN IF NOT EXISTS viewing_time VARCHAR(5),
            ADD COLUMN IF NOT EXISTS viewing_status VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED',
            ADD COLUMN IF NOT EXISTS contact_name  VARCHAR(200),
            ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(20);

        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_tour_viewing_time_hhmm') THEN
            ALTER TABLE tour_bookings
                ADD CONSTRAINT ck_tour_viewing_time_hhmm
                CHECK (viewing_time IS NULL OR viewing_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$')
                NOT VALID;
        END IF;
    END IF;

    IF to_regclass('consultations') IS NOT NULL THEN
        ALTER TABLE consultations
            ADD COLUMN IF NOT EXISTS project_id   VARCHAR(20),
            ADD COLUMN IF NOT EXISTS project_name VARCHAR(120),
            -- interest_type canonical: buy | rent | consultation.
            ADD COLUMN IF NOT EXISTS interest_type VARCHAR(20),
            ADD COLUMN IF NOT EXISTS preferred_contact_time VARCHAR(20),
            -- `consent` phải là literal true; NOT NULL DEFAULT false để row cũ
            -- không bị coi là đã đồng ý.
            ADD COLUMN IF NOT EXISTS consent BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS interest_status VARCHAR(20) NOT NULL DEFAULT 'RECEIVED',
            ADD COLUMN IF NOT EXISTS contact_channel VARCHAR(20);

        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_consultations_interest_type') THEN
            ALTER TABLE consultations
                ADD CONSTRAINT ck_consultations_interest_type
                CHECK (interest_type IS NULL OR interest_type IN ('buy', 'rent', 'consultation'))
                NOT VALID;
        END IF;

        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_consultations_contact_time') THEN
            ALTER TABLE consultations
                ADD CONSTRAINT ck_consultations_contact_time
                CHECK (preferred_contact_time IS NULL
                       OR preferred_contact_time IN ('morning', 'afternoon', 'evening'))
                NOT VALID;
        END IF;
    END IF;
END
$$;

-- =============================================================
-- Phase B — danh tính đã xác thực và quyền cư dân
-- =============================================================
--
-- Tách hai trục từng bị trộn làm một:
--
--   role            = tài khoản này là loại gì (customer | admin)
--   resident link   = tài khoản này đã liên kết căn hộ đã xác minh chưa
--
-- Role cũ tên 'resident' làm hai thứ đó trông như một. Hệ quả không phải chỉ
-- là đặt tên xấu: đăng ký xong là có role 'resident', nên bất kỳ chỗ nào kiểm
-- "role == resident" để mở dịch vụ cư dân đều mở cho mọi tài khoản vừa tạo.
--
-- Admin KHÔNG tự động có quyền cư dân. Quản trị viên là người vận hành hệ
-- thống, không phải chủ căn hộ; gộp hai thứ lại là cho một tài khoản vận hành
-- đặt chỗ đỗ xe và thanh toán phí dưới danh nghĩa cư dân nào đó.
DO $$
BEGIN
    IF to_regclass('users') IS NOT NULL THEN
        -- Bỏ constraint cũ TRƯỚC khi đổi dữ liệu, nếu không UPDATE sẽ vi phạm.
        ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;

        UPDATE users SET role = 'customer' WHERE role = 'resident';

        ALTER TABLE users ALTER COLUMN role SET DEFAULT 'customer';

        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_role') THEN
            ALTER TABLE users
                ADD CONSTRAINT ck_users_role CHECK (role IN ('customer', 'admin'));
        END IF;
    END IF;
END
$$;

-- Liên kết tài khoản ↔ cư dân. Đây là NGUỒN SỰ THẬT DUY NHẤT cho câu hỏi
-- "tài khoản này được dùng dịch vụ cư dân chưa".
--
--   - Không có row  = NOT_LINKED. Không cần một trạng thái riêng cho nó: thiếu
--     bằng chứng và có bằng chứng bị từ chối đều dẫn tới cùng một kết quả là
--     từ chối, nên fail-closed là mặc định tự nhiên.
--   - PENDING / REJECTED đều KHÔNG mở quyền. Chỉ VERIFIED mở.
--   - `verification_status` do provider/admin ghi. Không có đường nào cho
--     customer tự đặt nó, và cũng không có Agent tool nào chạm tới bảng này.
-- Bảng liên kết chỉ dựng được khi `users` và `residents` đã tồn tại.
DO $$
BEGIN
    IF to_regclass('users') IS NULL OR to_regclass('residents') IS NULL THEN
        RETURN;
    END IF;

    CREATE TABLE IF NOT EXISTS user_resident_links (
    user_id             UUID         NOT NULL REFERENCES users(id),
    resident_id         VARCHAR(20)  NOT NULL REFERENCES residents(resident_id),
    verification_status VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                            CHECK (verification_status IN ('PENDING', 'VERIFIED', 'REJECTED')),
    verified_at         TIMESTAMPTZ  DEFAULT NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Một user tối đa MỘT liên kết. Nhiều liên kết nghĩa là "cư dân hiện tại"
    -- không xác định, và khi đó guard phải chọn một trong số đó — chọn thế nào
    -- cũng sai với một nửa trường hợp.
        CONSTRAINT pk_user_resident_links PRIMARY KEY (user_id)
    );

    CREATE INDEX IF NOT EXISTS idx_user_resident_links_resident
        ON user_resident_links(resident_id);
END
$$;

-- Chủ sở hữu workflow. NULL được phép để giữ dữ liệu legacy tạo trước Phase B;
-- endpoint authenticated KHÔNG bao giờ trả row NULL cho customer, nên dữ liệu
-- cũ vẫn còn để truy vết mà không lọt sang tài khoản nào.
DO $$
BEGIN
    -- Khoá ngoại chỉ thêm được khi `users` đã tồn tại. Database cũ hơn bản
    -- Auth thì chưa có bảng đó, và một migration bắt buộc phải chạy được trên
    -- mọi phiên bản dữ liệu đang chạy ngoài thực tế — không chỉ trên bản mới nhất.
    IF to_regclass('workflows') IS NOT NULL THEN
        IF to_regclass('users') IS NOT NULL THEN
            ALTER TABLE workflows
                ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id);
        ELSE
            ALTER TABLE workflows ADD COLUMN IF NOT EXISTS owner_user_id UUID;
        END IF;
    END IF;

    -- Session cũng phải gắn với user. Bind bằng mỗi `session_id` là bind bằng
    -- một giá trị client biết và gửi lại được.
    IF to_regclass('sessions') IS NOT NULL THEN
        IF to_regclass('users') IS NOT NULL THEN
            ALTER TABLE sessions
                ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id);
        ELSE
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_id UUID;
        END IF;
    END IF;
END
$$;

-- Index cũng phải chờ bảng. `CREATE INDEX IF NOT EXISTS` chỉ bỏ qua khi INDEX
-- đã có, không bỏ qua khi BẢNG chưa có.
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_workflows_owner ON workflows(owner_user_id);
    END IF;
    IF to_regclass('sessions') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Lý do workflow hỏng, lưu ở dạng mã ổn định.
--
-- Không có cột này, một workflow chết vì sai cấu hình vẫn đọc lên là PENDING
-- sau restart: lỗi chỉ được ghi vào cache trong tiến trình, mà cache thì mất
-- cùng tiến trình.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL THEN
        ALTER TABLE workflows ADD COLUMN IF NOT EXISTS error_code VARCHAR(60);
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Yêu cầu liên kết căn hộ do khách hàng gửi.
--
-- Khách hàng khai căn hộ; admin duyệt. Không có bảng này thì khách hàng không
-- có đường nào bắt đầu, còn admin phải tự biết UUID của họ.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('users') IS NOT NULL THEN
        CREATE TABLE IF NOT EXISTS resident_link_requests (
            request_id       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id          UUID         NOT NULL REFERENCES users(id),
            apartment_code   VARCHAR(50)  NOT NULL,
            residential_area VARCHAR(100) NOT NULL,
            full_name        VARCHAR(200) NOT NULL,
            status           VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                                 CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            decided_at       TIMESTAMPTZ,
            decided_by       UUID         REFERENCES users(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_link_request_one_pending_per_user
            ON resident_link_requests(user_id)
            WHERE status = 'PENDING';
        CREATE INDEX IF NOT EXISTS idx_link_requests_status
            ON resident_link_requests(status, created_at);
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Câu trả lời tự nhiên của P-118, lưu ngay trên workflow.
--
-- Không tạo bảng hội thoại riêng: P-118 là Agent thực hiện tác vụ, và box chat
-- chỉ là cách trình bày lại các workflow. Câu trả lời vì thế là thuộc tính của
-- workflow, không phải một tin nhắn độc lập.
--
-- Thiếu mấy cột này, câu trả lời chỉ sống trong RAM: F5 hoặc restart là mất,
-- trong khi workflow vẫn còn nguyên.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL THEN
        ALTER TABLE workflows ADD COLUMN IF NOT EXISTS assistant_answer TEXT;
        ALTER TABLE workflows ADD COLUMN IF NOT EXISTS assistant_suggestions JSONB NOT NULL DEFAULT '[]'::jsonb;
        ALTER TABLE workflows ADD COLUMN IF NOT EXISTS assistant_response_state VARCHAR(20);
        ALTER TABLE workflows ADD COLUMN IF NOT EXISTS assistant_for_status VARCHAR(30);
        ALTER TABLE workflows ADD COLUMN IF NOT EXISTS assistant_updated_at TIMESTAMPTZ;

        -- CHECK thêm riêng: `ADD COLUMN IF NOT EXISTS` không mang theo ràng
        -- buộc khi cột đã tồn tại từ một lần chạy trước.
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'workflows_assistant_response_state_check'
        ) THEN
            ALTER TABLE workflows
                ADD CONSTRAINT workflows_assistant_response_state_check
                CHECK (assistant_response_state IN ('PENDING', 'READY', 'FALLBACK'));
        END IF;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- `preferred_contact_time`: buổi → GIỜ CỤ THỂ (HH:MM, 08:00–18:00).
--
-- "afternoon" tới tay nhân viên tư vấn vẫn không nói được nên gọi lúc mấy giờ,
-- còn người dùng muốn hẹn 14:30 thì không có cách nào diễn đạt. Cả hai đầu
-- cùng mất thông tin, và không đầu nào lấy lại được.
--
-- Dữ liệu cũ được QUY ĐỔI, không xoá: mỗi buổi thành một giờ đại diện. Đây là
-- phép đoán, nhưng là phép đoán tốt hơn hẳn việc để lại một giá trị mà không
-- tầng nào còn hiểu.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('consultations') IS NOT NULL THEN
        -- Bỏ ràng buộc cũ TRƯỚC khi đổi dữ liệu, nếu không UPDATE sẽ vi phạm nó.
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_consultations_contact_time') THEN
            ALTER TABLE consultations DROP CONSTRAINT ck_consultations_contact_time;
        END IF;

        UPDATE consultations
        SET preferred_contact_time = CASE preferred_contact_time
                WHEN 'morning'   THEN '09:30'
                WHEN 'afternoon' THEN '14:30'
                WHEN 'evening'   THEN '17:30'
                ELSE preferred_contact_time
            END
        WHERE preferred_contact_time IN ('morning', 'afternoon', 'evening');

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_consultations_contact_time_hhmm'
        ) THEN
            ALTER TABLE consultations
                ADD CONSTRAINT ck_consultations_contact_time_hhmm
                CHECK (
                    preferred_contact_time IS NULL
                    OR preferred_contact_time ~ '^(0[89]|1[0-8]):[0-5][0-9]$'
                )
                -- NOT VALID: dữ liệu lịch sử ngoài hai dạng trên vẫn nằm yên,
                -- nhưng mọi row MỚI đều phải đúng.
                NOT VALID;
        END IF;
    END IF;
END
$$;

-- =============================================================
-- Phase D — hồ sơ user thực tế + role provider
-- =============================================================
--
-- `users` chỉ có username/email trước đây; giờ bổ sung thông tin thực tế.
-- CCCD lưu MẶT NẠ: chỉ 4 số cuối (`cccd_last4`), đủ để người dùng tự nhận diện
-- mà không phơi toàn bộ giấy tờ ra. Toàn bộ cột nullable — hồ sơ tự khai, không
-- bắt buộc lúc đăng ký.
DO $$
BEGIN
    IF to_regclass('users') IS NOT NULL THEN
        ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name     VARCHAR(200);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS phone         VARCHAR(20);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS address       VARCHAR(255);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS date_of_birth DATE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS gender        VARCHAR(10);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS cccd_last4    CHAR(4);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url    TEXT;
    END IF;
END
$$;

-- Role mới 'provider' — người duyệt hồ sơ xác thực (căn hộ / xe).
--
-- DROP rồi ADD lại thay vì IF NOT EXISTS: constraint cũ (từ schema.sql hoặc một
-- lần chạy migration trước) chỉ chứa ('customer','admin'); phải thay toàn bộ,
-- không thể nới thêm giá trị vào một CHECK đã tồn tại.
DO $$
BEGIN
    IF to_regclass('users') IS NOT NULL THEN
        ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role;
        ALTER TABLE users ADD CONSTRAINT ck_users_role
            CHECK (role IN ('customer', 'admin', 'provider'));
    END IF;
END
$$;

-- =============================================================
-- Phase D — verification_records: xác thực căn hộ / xe (provider duyệt)
-- =============================================================
--
-- Nguồn sự thật cho xác thực CÓ BẰNG CHỨNG (ảnh giấy tờ). Provider sở hữu bảng
-- này; main app proxy qua HTTP (`OwnershipConnector`).
--
-- Một record PENDING duy nhất mỗi (loại + khoá khai báo): chống spam nhiều đơn
-- cùng biển số / cùng căn hộ đứng chờ duyệt. Khoá nằm trong `claimed_data` nên
-- partial unique index đọc thẳng JSONB path.
DO $$
BEGIN
    -- Khoá ngoại `applicant_user_id REFERENCES users(id)` chỉ thêm được khi bảng
    -- `users` đã tồn tại — migration còn được test chạy trên schema cũ không có
    -- users (test_schema_migrations_upgrades_legacy_table). Guard giống khối
    -- user_resident_links bên trên.
    IF to_regclass('users') IS NULL THEN
        RETURN;
    END IF;

    CREATE TABLE IF NOT EXISTS verification_records (
        record_id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
        record_type       VARCHAR(20)  NOT NULL CHECK (record_type IN ('apartment', 'vehicle')),
        status            VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                              CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
        applicant_user_id UUID         REFERENCES users(id),
        claimed_data      JSONB        NOT NULL,
        proof_image_urls  JSONB        NOT NULL DEFAULT '[]'::jsonb,
        reject_reason     TEXT,
        decided_by        VARCHAR(50),
        created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        decided_at        TIMESTAMPTZ
    );
END
$$;

-- Index chỉ dựng được khi bảng đã tồn tại — bảng bị guard bởi `users` bên trên,
-- index này phải guard theo bảng để không nổ UndefinedTableError trên schema cũ
-- (test_schema_migrations_upgrades_legacy_table).
DO $$
BEGIN
    IF to_regclass('verification_records') IS NULL THEN
        RETURN;
    END IF;

    CREATE UNIQUE INDEX IF NOT EXISTS uq_verif_pending_apartment
        ON verification_records (record_type, (claimed_data->>'apartment_code'), (claimed_data->>'residential_area'))
        WHERE status = 'PENDING';

    CREATE UNIQUE INDEX IF NOT EXISTS uq_verif_pending_vehicle
        ON verification_records (record_type, (claimed_data->>'plate_number'))
        WHERE status = 'PENDING';
END
$$;


-- =============================================================
-- 2026-08 — Vòng đời hàng chờ duyệt lịch tham quan: thêm EXPIRED
-- =============================================================
--
-- Một yêu cầu AWAITING có thể mất hiệu lực mà không ai quyết định gì: ngày
-- tham quan trôi qua, hoặc khung giờ bị người khác đặt mất. Trước đây nó nằm
-- lại trong hàng chờ và trông y hệt yêu cầu hợp lệ — người duyệt bấm Duyệt rồi
-- mới vỡ ở Tour provider với một lỗi 502 không nói được gì.
--
-- EXPIRED là một trạng thái KHÔNG PHẢI quyết định của con người, nên nó tách
-- khỏi REJECTED: từ chối có người chịu trách nhiệm và có lý do gửi cho khách,
-- còn hết hạn thì không.
--
-- Chỉ NỚI ràng buộc, không xoá dòng nào: bằng chứng ai yêu cầu gì vẫn giữ.
DO $$
BEGIN
    IF to_regclass('viewing_approvals') IS NOT NULL THEN
        ALTER TABLE viewing_approvals DROP CONSTRAINT IF EXISTS viewing_approvals_status_check;
        ALTER TABLE viewing_approvals
            ADD CONSTRAINT viewing_approvals_status_check
            CHECK (status IN ('AWAITING', 'APPROVED', 'REJECTED', 'EXPIRED'));
    END IF;
END
$$;

-- =============================================================
-- 2026-08 — Observability Metrics
-- =============================================================
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL THEN
        ALTER TABLE workflows ADD COLUMN IF NOT EXISTS total_tokens INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE workflows ADD COLUMN IF NOT EXISTS total_cost NUMERIC(10, 4) NOT NULL DEFAULT 0.0;
        ALTER TABLE workflows ADD COLUMN IF NOT EXISTS latency_ms INTEGER;
    END IF;
END
$$;
