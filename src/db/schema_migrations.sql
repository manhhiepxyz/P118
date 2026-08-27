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
    -- `viewing_approvals` giờ là KHUNG NHÌN trên `service_approvals` (xem
    -- migration "GỘP hai hàng đợi duyệt" ở cuối file). Khối này chỉ còn có
    -- nghĩa với database chưa từng chạy tới đó; chạm vào một view thì
    -- `CREATE INDEX` đổ `WrongObjectTypeError` và cả file migration dừng lại.
    IF to_regclass('workflows') IS NOT NULL AND to_regclass('viewing_approvals') IS NULL THEN
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
    -- Chỉ khi nó còn là BẢNG. Sau khi gộp hàng đợi, `viewing_approvals` là
    -- khung nhìn và `ALTER TABLE` trên view đổ `WrongObjectTypeError` — lỗi ấy
    -- dừng CẢ file migration, nên mọi thay đổi sau nó cũng không chạy.
    -- Ràng buộc tương ứng giờ nằm trên `service_approvals`.
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_name = 'viewing_approvals' AND table_type = 'BASE TABLE'
    ) THEN
        ALTER TABLE viewing_approvals DROP CONSTRAINT IF EXISTS viewing_approvals_status_check;
        ALTER TABLE viewing_approvals
            ADD CONSTRAINT viewing_approvals_status_check
            CHECK (status IN ('AWAITING', 'APPROVED', 'REJECTED', 'EXPIRED'));
    END IF;
END
$$;


-- Sức chứa 0 phải hợp lệ: nó nghĩa là khu KHÔNG còn nhận đăng ký.
--
-- Ràng buộc cũ `CHECK (capacity > 0)` cấm đúng trạng thái ấy, nên muốn diễn lại
-- luồng "khu A hết chỗ → đổi sang khu B" thì phải gieo booking giả cho từng
-- ngày — vừa sai sự thật vừa không bao giờ phủ hết ngày.
DO $$
BEGIN
    IF to_regclass('zone_capacity_config') IS NOT NULL THEN
        ALTER TABLE zone_capacity_config DROP CONSTRAINT IF EXISTS zone_capacity_config_capacity_check;
        ALTER TABLE zone_capacity_config ADD CONSTRAINT zone_capacity_config_capacity_check CHECK (capacity >= 0);
    END IF;
END
$$;

-- Ràng buộc THỨ HAI, dễ bỏ sót.
--
-- `parking_capacity` (bảng theo NGÀY) có check riêng, tách khỏi
-- `zone_capacity_config` (bảng cấu hình). Nới mỗi bảng cấu hình thì seed chạy
-- tới câu đồng bộ là đổ `CheckViolationError`, và vì migration dừng ở đó nên
-- cả file seed không hoàn tất.
DO $$
BEGIN
    IF to_regclass('parking_capacity') IS NOT NULL THEN
        ALTER TABLE parking_capacity DROP CONSTRAINT IF EXISTS parking_capacity_capacity_check;
        ALTER TABLE parking_capacity ADD CONSTRAINT parking_capacity_capacity_check CHECK (capacity >= 0);
    END IF;
END
$$;

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


-- Khung nhìn `viewing_approvals` GHI ĐƯỢC.
--
-- Gộp hàng đợi thì mọi chỗ ĐỌC giữ nguyên nhờ khung nhìn, nhưng chỗ GHI thì
-- không: PostgreSQL từ chối `INSERT` vào một view có cột dẫn xuất
-- (`details->>'project_id'` không phải cột của bảng gốc).
--
-- Đo được: 12 test đỏ ngay khi gộp, tất cả vì chúng seed dữ liệu bằng `INSERT
-- INTO viewing_approvals`. Sửa từng test là bỏ sót — mã cũ, script vận hành và
-- test chưa viết đều có thể ghi vào đây.
--
-- Trigger dịch ngược: cột riêng của tham quan gói lại thành `details`, phần
-- còn lại đi thẳng. Sau đó bảng chỉ còn MỘT, mà giao diện cũ vẫn nguyên vẹn.
CREATE OR REPLACE FUNCTION viewing_approvals_write() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO service_approvals (
            workflow_id, task_id, tool, service_label, details, status,
            applicant_user_id, applicant_name, applicant_phone,
            reject_reason, decided_by, created_at, decided_at
        ) VALUES (
            NEW.workflow_id, NEW.task_id, 'schedule_property_viewing',
            'Đặt lịch tham quan',
            jsonb_strip_nulls(jsonb_build_object(
                'project_id', NEW.project_id,
                'project_name', NEW.project_name,
                'viewing_date', to_char(NEW.viewing_date, 'YYYY-MM-DD'),
                'viewing_time', NEW.viewing_time,
                'passenger_count', NEW.passenger_count,
                'wants_shuttle', NEW.wants_shuttle
            )),
            COALESCE(NEW.status, 'AWAITING'),
            NEW.applicant_user_id, NEW.applicant_name, NEW.applicant_phone,
            NEW.reject_reason, NEW.decided_by,
            COALESCE(NEW.created_at, NOW()), NEW.decided_at
        )
        -- GHIM LẠI nghĩa là cần một quyết định MỚI — không phải "đã có rồi, thôi".
        --
        -- `DO NOTHING` ở đây là bản sao còn sót của luật cũ; luật mới đã được
        -- sửa ở `save_pending_service_approvals` cho các dịch vụ khác, nhưng
        -- lịch tham quan đi qua view này nên nó giữ nguyên hành vi cũ. Một luật,
        -- hai bản cài đặt — và bản cũ mới là bản người dùng chạm vào.
        --
        -- Đo được trên 09430928, sau khi đổi ngày tham quan bằng lời:
        --
        --     workflow_tasks.T1   WAITING_APPROVAL   viewing_date 2026-09-30
        --     service_approvals   EXPIRED            viewing_date 2026-09-10
        --
        -- Bước chờ một quyết định, hồ sơ thì đã hết hạn và mang ngày CŨ. Không
        -- ai được hỏi, không gì tới đơn vị tour, và yêu cầu treo vĩnh viễn.
        ON CONFLICT (workflow_id, task_id) DO UPDATE SET
            status        = COALESCE(EXCLUDED.status, 'AWAITING'),
            details       = EXCLUDED.details,
            decided_by    = NULL,
            decided_at    = NULL,
            reject_reason = NULL,
            created_at    = NOW()
        WHERE service_approvals.status IN ('AWAITING', 'EXPIRED');
        RETURN NEW;
    END IF;

    UPDATE service_approvals SET
        status            = COALESCE(NEW.status, status),
        reject_reason     = NEW.reject_reason,
        decided_by        = NEW.decided_by,
        decided_at        = NEW.decided_at,
        applicant_name    = NEW.applicant_name,
        applicant_phone   = NEW.applicant_phone,
        details           = details || jsonb_strip_nulls(jsonb_build_object(
                                'project_id', NEW.project_id,
                                'project_name', NEW.project_name,
                                'viewing_date', to_char(NEW.viewing_date, 'YYYY-MM-DD'),
                                'viewing_time', NEW.viewing_time,
                                'passenger_count', NEW.passenger_count,
                                'wants_shuttle', NEW.wants_shuttle
                            ))
     WHERE workflow_id = OLD.workflow_id
       AND task_id = OLD.task_id
       AND tool = 'schedule_property_viewing';
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS viewing_approvals_write_trg ON viewing_approvals;
CREATE TRIGGER viewing_approvals_write_trg
    INSTEAD OF INSERT OR UPDATE ON viewing_approvals
    FOR EACH ROW EXECUTE FUNCTION viewing_approvals_write();

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


-- 2026-08 — workflow_tasks: bằng chứng gửi provider
--
-- Ba cột, và thứ tự các bước ở đây có nghĩa. Cột được thêm KHÔNG default trước,
-- rồi backfill row cũ thành `UNKNOWN`, rồi mới đặt default `NOT_SUBMITTED` cho
-- row mới.
--
-- Thêm thẳng với `DEFAULT 'NOT_SUBMITTED'` sẽ backfill mọi row cũ thành "chưa
-- gửi" — một khẳng định không ai kiểm được, và nó nghiêng đúng về phía nguy
-- hiểm: lần chạy sau sẽ gửi lại một việc có thể đã được provider ghi nhận.
-- Dữ liệu không có bằng chứng phải là `UNKNOWN`.
DO $$
BEGIN
    IF to_regclass('workflow_tasks') IS NOT NULL THEN
        ALTER TABLE workflow_tasks
            ADD COLUMN IF NOT EXISTS provider_submission_status VARCHAR(20),
            ADD COLUMN IF NOT EXISTS external_request_id        VARCHAR(120),
            ADD COLUMN IF NOT EXISTS provider_idempotency_key   VARCHAR(160);

        -- Idempotent: chạy lại chỉ chạm những row còn NULL.
        UPDATE workflow_tasks SET provider_submission_status = 'UNKNOWN'
         WHERE provider_submission_status IS NULL;

        ALTER TABLE workflow_tasks
            ALTER COLUMN provider_submission_status SET DEFAULT 'NOT_SUBMITTED';
        -- `SET NOT NULL` vốn CHẠY LẶP ĐƯỢC: gọi lại trên cột đã NOT NULL không
        -- lỗi. Nên một `EXCEPTION WHEN others` quanh nó không bảo vệ gì — nó chỉ
        -- che một lỗi thật. Và lỗi thật ở đúng bước này nghĩa là deployment đi
        -- tiếp với một cột còn cho phép NULL, tức mọi hàng rào dựng trên cột ấy
        -- im lặng biến mất. Hỏng thì migration phải dừng.
        ALTER TABLE workflow_tasks
            ALTER COLUMN provider_submission_status SET NOT NULL;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_workflow_tasks_submission_status'
        ) THEN
            ALTER TABLE workflow_tasks
                ADD CONSTRAINT ck_workflow_tasks_submission_status
                CHECK (provider_submission_status IN (
                    'NOT_SUBMITTED', 'SUBMITTING', 'ACKNOWLEDGED', 'UNKNOWN'
                ));
        END IF;
    END IF;
END
$$;

-- 2026-08 — workflow_plan_revisions cho database đã tồn tại.
-- Thân bảng + trigger append-only nằm ở `schema.sql`; khối này chỉ tạo lại cho
-- database cũ chưa có bảng, và gắn lại trigger nếu bảng có mà trigger thì không.
DO $$
BEGIN
    IF to_regclass('workflows') IS NOT NULL AND to_regclass('workflow_plan_revisions') IS NULL THEN
        CREATE TABLE workflow_plan_revisions (
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
            CONSTRAINT uq_plan_revisions_order UNIQUE (workflow_id, revision_number)
        );
        CREATE INDEX idx_plan_revisions_by_workflow
            ON workflow_plan_revisions(workflow_id, revision_number);
    END IF;
END
$$;

-- Ràng buộc thứ tự cho database ĐÃ CÓ bảng.
--
-- Khối `CREATE TABLE` ở trên chỉ chạy khi bảng chưa tồn tại, nên một database
-- có bảng mà thiếu ràng buộc sẽ không bao giờ được vá. Phát hiện khi chạy
-- mutation "bỏ unique revision order": test đỏ đúng như mong đợi, nhưng sau khi
-- khôi phục file thì migration KHÔNG dựng lại được ràng buộc — chính là lỗ hổng
-- mà migration sinh ra để bịt.
DO $$
BEGIN
    IF to_regclass('workflow_plan_revisions') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_plan_revisions_order')
    THEN
        ALTER TABLE workflow_plan_revisions
            ADD CONSTRAINT uq_plan_revisions_order UNIQUE (workflow_id, revision_number);
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION workflow_plan_revisions_append_only() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'workflow_plan_revisions chi duoc GHI THEM; % bi tu choi', TG_OP;
END;
$fn$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF to_regclass('workflow_plan_revisions') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS workflow_plan_revisions_no_update ON workflow_plan_revisions;
        CREATE TRIGGER workflow_plan_revisions_no_update
            BEFORE UPDATE OR DELETE ON workflow_plan_revisions
            FOR EACH ROW EXECUTE FUNCTION workflow_plan_revisions_append_only();
    END IF;
END
$$;

-- =============================================================
-- 2026-08 — verification_materializations
--
-- Idempotent: `IF NOT EXISTS` + `ADD CONSTRAINT` bọc trong DO block, nên chạy
-- lại trên database đã có bảng là no-op. KHÔNG DROP, KHÔNG TRUNCATE — bảng này
-- mang trạng thái phục hồi, xoá nó nghĩa là mất đúng thứ nó sinh ra để giữ.
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

-- `record_type` được phép NULL cho tới khi đọc được provider.
--
-- Bảng ra đời với `NOT NULL`, và hệ quả là caller phải điền một giá trị lúc
-- CHƯA biết loại hồ sơ — thực tế nó điền 'apartment'. Một biên lai của hồ sơ
-- XE bị ghi là căn hộ nếu tiến trình chết giữa lúc mở biên lai và lúc đọc
-- provider. Đó là dữ liệu audit sai, và nó sai một cách im lặng.
--
-- Idempotent: chạy lại trên cột đã nullable là no-op.
DO $$
BEGIN
    IF to_regclass('verification_materializations') IS NOT NULL THEN
        ALTER TABLE verification_materializations ALTER COLUMN record_type DROP NOT NULL;
        ALTER TABLE verification_materializations DROP CONSTRAINT IF EXISTS verif_mat_type_check;
        ALTER TABLE verification_materializations
            ADD CONSTRAINT verif_mat_type_check
            CHECK (record_type IS NULL OR record_type IN ('apartment', 'vehicle'));
    END IF;
END $$;

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


-- 2026-08 — `service_approvals.kind`: phân biệt BƯỚC với LỜI NHỜ.
--
-- Hai nút "Đổi lịch" / "Huỷ lịch" trên thẻ kết quả ghim một hồ sơ vào chính
-- hàng đợi đơn vị đang dùng. Hồ sơ ấy không phải một bước: không tool, không
-- dòng `workflow_tasks`. Thiếu cột này thì lượt resume sau khi đơn vị duyệt sẽ
-- gọi `update_task_status` cho một `task_id` không tồn tại và ném giữa chừng.
--
-- Mặc định `TASK`: mọi dòng có trước cột này đều là bước.
ALTER TABLE service_approvals ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'TASK';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_service_approvals_kind'
    ) THEN
        ALTER TABLE service_approvals ADD CONSTRAINT ck_service_approvals_kind
            CHECK (kind IN ('TASK', 'REQUEST'));
    END IF;
END $$;


-- 2026-08 — `parking_bookings.status`: chỗ đã huỷ ở lại bảng.
--
-- Huỷ MUỘN vẫn huỷ nhưng không hoàn tiền, nên dòng `payments` PAID còn nguyên
-- và trỏ vào booking. Xoá booking khi ấy để khoản tiền trỏ vào hư không.
--
-- `to_regclass` KHÔNG qualify schema: migration bám theo search_path, nên guard
-- phải giải tên bảng đúng cách `ALTER` bên dưới sẽ giải.
DO $$
BEGIN
    IF to_regclass('parking_bookings') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE parking_bookings ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE';

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_parking_bookings_status') THEN
        ALTER TABLE parking_bookings ADD CONSTRAINT ck_parking_bookings_status
            CHECK (status IN ('ACTIVE', 'CANCELLED'));
    END IF;

    -- Ràng buộc "một xe một chỗ mỗi ngày" chỉ tính chỗ CÒN HIỆU LỰC. Giữ bản cũ
    -- nghĩa là chỗ đã huỷ vẫn chặn lần đặt lại của chính người vừa huỷ — và đặt
    -- lại là lý do phổ biến nhất người ta bấm huỷ.
    --
    -- Đổi từ CONSTRAINT sang partial INDEX: PostgreSQL không có `UNIQUE ... WHERE`
    -- ở dạng table constraint. Tên giữ nguyên nên `_violated()` vẫn nhận ra nó.
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_bookings_vehicle_date') THEN
        ALTER TABLE parking_bookings DROP CONSTRAINT uq_bookings_vehicle_date;
    END IF;

    CREATE UNIQUE INDEX IF NOT EXISTS uq_bookings_vehicle_date
        ON parking_bookings (vehicle_id, booking_date)
        WHERE status = 'ACTIVE';
END $$;


-- 2026-08 — Đơn vị cung cấp: quan hệ tài khoản ↔ đơn vị, và chủ sở hữu của
-- mỗi dòng chờ duyệt.
--
-- Trước đây `/service-approvals` chỉ kiểm ROLE: mọi tài khoản `provider` thấy
-- và quyết định được TOÀN BỘ hàng đợi. Điều đó đúng khi hệ thống chỉ có một
-- đơn vị cung cấp — "toàn bộ hàng đợi" chính là "phần của mình".
--
-- Nó hết đúng từ lúc có nhiều đơn vị. Khi P-118 đề xuất một đội cụ thể mà bất
-- kỳ tài khoản provider nào cũng bấm duyệt được, việc chọn đơn vị chỉ tồn tại
-- trên dữ liệu chứ không tồn tại trong nghiệp vụ — và lúc đó nó ĐÚNG là IDOR.
--
-- BẢNG LIÊN KẾT, không phải một cột trên `users`: một tài khoản quản lý được
-- nhiều đơn vị, và một đơn vị có nhiều nhân viên. Dùng role làm danh tính đơn
-- vị thì thêm nhân viên thứ hai là phải đổi schema lần nữa.
-- Bọc trong `to_regclass`: file migration còn được chạy trên database LEGACY
-- chỉ có vài bảng (xem `test_schema_migrations_upgrades_legacy_table`), nơi
-- `users` chưa tồn tại — và `REFERENCES users(id)` ở đó nổ ngay, kéo theo cả
-- những migration phía sau. Cùng khuôn với khối `payments` bên trên.
DO $$
BEGIN
    IF to_regclass('users') IS NOT NULL THEN
        CREATE TABLE IF NOT EXISTS service_provider_accounts (
            user_id             UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            service_provider_id VARCHAR(32) NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, service_provider_id)
        );

        CREATE INDEX IF NOT EXISTS idx_service_provider_accounts_provider
            ON service_provider_accounts (service_provider_id);
    END IF;
END $$;

-- Cột cho phép NULL để không phá dòng đã có. NULL nghĩa là "dòng có TRƯỚC khi
-- có khái niệm đơn vị", và luật đọc phải FAIL-CLOSED với nó: KHÔNG AI thấy —
-- không provider nào, và admin cũng không (admin giám sát qua `/admin/requests`,
-- không qua hàng đợi của đơn vị).
--
-- Dòng NULL vì thế là dòng không ai quyết định được. Dọn chúng là việc NGHIỆP
-- VỤ, không phải phép biến đổi schema, nên nó KHÔNG nằm ở đây: chạy tay một
-- lần bằng `scripts/backfill_service_provider.py`, có người đọc con số trước
-- khi ghi. Một migration đoán hộ nghĩa là mọi môi trường nhận cùng một cái
-- đoán, kể cả môi trường mà cái đoán ấy sai.
--
-- Mặc định ngược lại — "chưa gán thì ai cũng thấy" — biến mọi dòng lịch sử
-- thành một lỗ hổng ngay tại thời điểm migration chạy, và không ai để ý vì
-- màn hình trông vẫn đúng.
DO $$
BEGIN
    IF to_regclass('service_approvals') IS NOT NULL THEN
        ALTER TABLE service_approvals
            ADD COLUMN IF NOT EXISTS service_provider_id VARCHAR(32);

        CREATE INDEX IF NOT EXISTS idx_service_approvals_provider
            ON service_approvals (service_provider_id, status, created_at);
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- Bước B — BÁO GIÁ CÓ DANH TÍNH
-- ---------------------------------------------------------------------------
-- Bước A khoá quyền sở hữu, nhưng đơn vị vẫn đến từ một bảng cứng trong mã.
-- Ngay khi P-118 CHỌN đơn vị theo giá, phải trả lời được: lấy gì làm bằng
-- chứng rằng đơn vị này đã báo giá này cho yêu cầu này? Không có bằng chứng
-- thì `service_provider_id` chỉ là một chuỗi đi kèm request — và mọi thứ
-- người dùng gửi được thì người dùng sửa được.
--
-- Bảng này là bằng chứng ấy. Nó KHÔNG lưu `goal`, prompt hay văn bản hội
-- thoại: báo giá là chứng từ thương mại, nó chỉ cần dữ kiện định giá.
--
-- `request_fingerprint` là khoá của toàn bộ cơ chế: băm từ input canonical của
-- dịch vụ. Đổi ngày/xe/thang máy/bốc xếp ra vân tay khác, nên báo giá cũ không
-- dùng lại được cho yêu cầu đã đổi. `max_price` KHÔNG nằm trong vân tay và
-- không bao giờ rời khỏi P-118.
CREATE TABLE IF NOT EXISTS service_quotes (
    -- ID nội bộ của P-118. Sinh ở đây, không nhận từ provider: một mã do bên
    -- ngoài đặt là một mã bên ngoài có thể trùng, hoặc đoán.
    quote_id            UUID         PRIMARY KEY,
    -- Mã do provider đặt. Giữ nguyên chuỗi họ trả — đây là thứ để đối chiếu
    -- khi có tranh chấp, nên không chuẩn hoá, không cắt, không viết hoa.
    external_quote_id   VARCHAR(128) NOT NULL,
    service_provider_id VARCHAR(32)  NOT NULL,
    service_type        VARCHAR(64)  NOT NULL,
    -- INTEGER, không phải NUMERIC/FLOAT. VND không có phần lẻ, và số thực làm
    -- hai lần cộng cùng một hoá đơn ra hai kết quả.
    amount              BIGINT       NOT NULL CHECK (amount > 0),
    currency            VARCHAR(8)   NOT NULL CHECK (currency IN ('VND')),
    request_fingerprint VARCHAR(64)  NOT NULL,
    valid_until         TIMESTAMPTZ  NOT NULL,
    status              VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'CONFIRMED', 'EXPIRED', 'SUPERSEDED')),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    confirmed_at        TIMESTAMPTZ,
    -- NEO BẮT BUỘC, không phải tuỳ chọn. Vân tay tính từ input, nên hai
    -- workflow khác nhau xin cùng một việc có CÙNG vân tay. Không neo thì báo
    -- giá của người này dùng được cho yêu cầu của người kia — đúng nghĩa IDOR,
    -- chỉ là trên chứng từ thay vì trên hàng đợi.
    --
    -- Bản đầu để hai cột NULLABLE và `kiem_bao_gia()` chỉ kiểm khi caller chịu
    -- truyền. Nghĩa là luật chỉ tồn tại với những call site nhớ tới nó — tức
    -- không tồn tại. NOT NULL đẩy nó xuống chỗ không ai quên được.
    workflow_id         UUID         NOT NULL,
    task_id             VARCHAR(20)  NOT NULL,
    -- CONFIRMED thì phải có mốc thời gian, và chưa CONFIRMED thì không được
    -- có. Ràng buộc ở database chứ không ở tầng ứng dụng: một đường ghi mới
    -- quên đặt `confirmed_at` sẽ vỡ ngay, chứ không để lại một chứng từ nói
    -- "đã xác nhận" mà không nói lúc nào.
    CONSTRAINT chk_service_quotes_confirmed_at
        CHECK ((status = 'CONFIRMED') = (confirmed_at IS NOT NULL))
);

-- Khoá ngoại TỔNG HỢP tới đúng BƯỚC, cùng khuôn với `approval_decisions` và
-- `execution_logs`. Trỏ vào `workflows` thôi là chưa đủ: nó cho phép neo vào
-- một `task_id` không tồn tại, và một chứng từ neo vào hư vô thì không khác gì
-- chứng từ không neo.
--
-- Tách khỏi `CREATE TABLE` và bọc điều kiện vì file này còn chạy trên database
-- LEGACY chỉ có vài bảng (xem `test_schema_migrations_upgrades_legacy_table`),
-- nơi `workflow_tasks` chưa có ràng buộc duy nhất `(workflow_id, task_id)` —
-- và `REFERENCES` ở đó nổ ngay, kéo theo mọi migration phía sau. Cùng khuôn
-- với khối `service_provider_accounts` bên trên.
--
-- Khối này cũng nâng cấp bảng đã tồn tại với hai cột nullable (bản đầu của
-- bước B). XOÁ dòng chưa neo thay vì cố vá: một chứng từ không biết mình thuộc
-- yêu cầu nào thì không tiêu thụ được — `kiem_bao_gia()` chặn nó ở mọi đường —
-- nên nó không phải dữ liệu, nó là rác. Bảng này trẻ hơn chính ràng buộc đang
-- thêm, nên không có dòng nào như vậy ngoài môi trường dev của tuần này.
DO $$
BEGIN
    IF to_regclass('service_quotes') IS NULL THEN
        RETURN;
    END IF;

    DELETE FROM service_quotes WHERE workflow_id IS NULL OR task_id IS NULL;
    ALTER TABLE service_quotes ALTER COLUMN workflow_id SET NOT NULL;
    ALTER TABLE service_quotes ALTER COLUMN task_id SET NOT NULL;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_service_quotes_task')
       AND EXISTS (
           SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_workflow_tasks_wf_task' AND contype IN ('u', 'p')
       )
    THEN
        ALTER TABLE service_quotes
            ADD CONSTRAINT fk_service_quotes_task
            FOREIGN KEY (workflow_id, task_id) REFERENCES workflow_tasks (workflow_id, task_id);
    END IF;
END $$;

-- Tra theo BƯỚC: "báo giá nào đang sống cho bước này". Đây là truy vấn nóng —
-- mọi lượt hiển thị và mọi lượt tiêu thụ đều đi qua nó.
CREATE INDEX IF NOT EXISTS idx_service_quotes_task
    ON service_quotes (workflow_id, task_id, status);

-- Tra theo VÂN TAY: "yêu cầu này đã có báo giá nào rồi". Dùng khi một lượt sửa
-- làm các báo giá của vân tay CŨ thành SUPERSEDED.
CREATE INDEX IF NOT EXISTS idx_service_quotes_fingerprint
    ON service_quotes (request_fingerprint, status);

-- Một provider không được báo hai giá cho CÙNG một bước và CÙNG một yêu cầu.
--
-- Không có ràng buộc này thì một lượt xin báo giá chạy hai lần (retry sau
-- timeout, hai tab, hai lượt poll) để lại hai dòng ACTIVE cùng đơn vị khác giá,
-- và luật chọn sẽ lấy dòng rẻ hơn — tức hệ thống tự thưởng cho mình mỗi lần
-- mạng chập chờn. `WHERE status = 'ACTIVE'` để lịch sử vẫn giữ được các báo
-- giá đã hết hạn hay bị thay thế.
--
-- Kèm theo nó là một NGHĨA VỤ: dòng hết hạn phải được chuyển sang EXPIRED
-- trước khi xin báo giá mới. Nếu không, một dòng quá hạn vẫn mang `ACTIVE` và
-- chặn vĩnh viễn mọi lượt hỏi lại của cùng đơn vị cho cùng yêu cầu — ràng buộc
-- an toàn biến thành ngõ cụt. Xem `don_bao_gia_va_de_xuat()` — cùng một
-- transaction dọn cả chứng từ lẫn đề xuất đang trỏ vào chúng.
CREATE UNIQUE INDEX IF NOT EXISTS uq_service_quotes_active
    ON service_quotes (workflow_id, task_id, service_provider_id, request_fingerprint)
 WHERE status = 'ACTIVE';

-- Cùng một đơn vị không được dùng MỘT mã báo giá cho hai chứng từ khác nhau.
--
-- Không unique toàn cục: hai đơn vị khác nhau hoàn toàn có thể cùng đánh số
-- `Q-001`, và ép chúng phải khác nhau là áp một luật của P-118 lên hệ thống
-- đánh mã nội bộ của người khác.
--
-- Nhưng TRONG một đơn vị thì mã phải là danh tính. Trùng mã nghĩa là lúc tranh
-- chấp, câu "chúng tôi đã xác nhận Q-001" trỏ tới hai con số khác nhau và
-- không ai phân xử được.
CREATE UNIQUE INDEX IF NOT EXISTS uq_service_quotes_external
    ON service_quotes (service_provider_id, external_quote_id);


-- ---------------------------------------------------------------------------
-- Bước D — ĐỀ XUẤT ĐƠN VỊ, và lượt xác nhận của người dùng
-- ---------------------------------------------------------------------------
-- Bước C chọn được một đơn vị nhưng không ghi gì: nó chỉ ĐỌC. Giữa lúc P-118
-- nói "mình đề xuất Đại Tín, 470.000" và lúc khách bấm đồng ý có một khoảng
-- thời gian thật — họ đọc, họ hỏi người nhà, họ đóng tab rồi mở lại. Khoảng ấy
-- phải sống qua restart, qua worker thứ hai, qua một lượt deploy.
--
-- Nên đề xuất là một BẢN GHI, không phải một biến trong bộ nhớ.
--
-- KHÔNG chép provider/amount/currency vào đây
-- ------------------------------------------
-- Chúng đã nằm trên chứng từ báo giá. Chép sang là tạo nguồn sự thật thứ hai,
-- và hai nguồn thì lệch — lệch đúng vào lúc báo giá bị thay thế hoặc hết hạn,
-- tức đúng lúc con số cũ trông vẫn hợp lệ. `quote_id` là khoá ngoại; muốn biết
-- giá thì đọc chứng từ.
--
-- `approval_actor` KHÔNG có mặt ở đây, và đó là cố ý. Nó là thứ SUY RA lúc
-- dựng câu trả lời (`USER` trước xác nhận, `PROVIDER` sau), không phải một
-- trạng thái được lưu. Lưu nó nghĩa là có hai chỗ nói "đang chờ ai", và chỗ
-- thứ hai sẽ đứng im khi việc đổi tay.
CREATE TABLE IF NOT EXISTS service_provider_proposals (
    proposal_id UUID        PRIMARY KEY,
    workflow_id UUID        NOT NULL,
    task_id     VARCHAR(20) NOT NULL,
    -- Chứng từ được đề xuất. Nguồn DUY NHẤT cho đơn vị, giá và tiền tệ.
    quote_id    UUID        NOT NULL REFERENCES service_quotes (quote_id),
    status      VARCHAR(16) NOT NULL DEFAULT 'PROPOSED'
                CHECK (status IN ('PROPOSED', 'CONFIRMED', 'EXPIRED', 'SUPERSEDED')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at TIMESTAMPTZ,
    -- Cùng khuôn với `service_quotes`: đã xác nhận thì phải có mốc thời gian,
    -- chưa xác nhận thì không được có.
    CONSTRAINT chk_proposals_confirmed_at
        CHECK ((status = 'CONFIRMED') = (confirmed_at IS NOT NULL))
);

-- Khoá ngoại tổng hợp tới đúng BƯỚC, và điều kiện `uq_workflow_tasks_wf_task`
-- vì file này còn chạy trên database legacy chỉ có vài bảng.
DO $$
BEGIN
    IF to_regclass('service_provider_proposals') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_proposals_task')
       AND EXISTS (
           SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_workflow_tasks_wf_task' AND contype IN ('u', 'p')
       )
    THEN
        ALTER TABLE service_provider_proposals
            ADD CONSTRAINT fk_proposals_task
            FOREIGN KEY (workflow_id, task_id) REFERENCES workflow_tasks (workflow_id, task_id);
    END IF;
END $$;

-- ĐÚNG MỘT đề xuất đang sống trên mỗi bước.
--
-- Không có ràng buộc này thì hai lượt đề xuất đồng thời (khách bấm hỏi lại,
-- hai tab, một lượt retry) để lại hai dòng PROPOSED — và khách xác nhận một
-- cái trong khi màn hình đang hiển thị cái kia. Đề xuất mới phải làm cái cũ
-- SUPERSEDED trước, chứ không nằm cạnh nó.
CREATE UNIQUE INDEX IF NOT EXISTS uq_proposals_live
    ON service_provider_proposals (workflow_id, task_id)
 WHERE status = 'PROPOSED';

-- Tra theo bước, gồm cả lịch sử — dùng cho màn giám sát và cho lượt đọc lại.
CREATE INDEX IF NOT EXISTS idx_proposals_task
    ON service_provider_proposals (workflow_id, task_id, status, created_at);

-- Tra ngược từ chứng từ: "báo giá này đã được đề xuất chưa".
CREATE INDEX IF NOT EXISTS idx_proposals_quote
    ON service_provider_proposals (quote_id);


-- ---------------------------------------------------------------------------
-- Bước D (đóng) — KHOÁ NEO Ở DATABASE
-- ---------------------------------------------------------------------------
-- Tầng ứng dụng đã kiểm "chứng từ phải thuộc đúng workflow/task này" ở cả hai
-- đường (lúc ghim đề xuất và lúc xác nhận). Nhưng schema vẫn cho chèn thẳng
-- một đề xuất trỏ vào chứng từ của bước khác — và một luật chỉ tồn tại ở tầng
-- ứng dụng là một luật mà mọi đường ghi MỚI phải nhớ lại từ đầu.
--
-- `quote_id` đã là khoá chính nên ràng buộc dưới đây không thêm tính duy nhất
-- nào. Nó tồn tại vì PostgreSQL đòi một ràng buộc duy nhất trên ĐÚNG bộ cột mà
-- khoá ngoại tổng hợp trỏ tới.
DO $$
BEGIN
    IF to_regclass('service_quotes') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_service_quotes_anchor')
    THEN
        ALTER TABLE service_quotes
            ADD CONSTRAINT uq_service_quotes_anchor UNIQUE (quote_id, workflow_id, task_id);
    END IF;
END $$;

-- Khoá ngoại TỔNG HỢP: đề xuất và chứng từ phải nói cùng một bước.
--
-- Nếu không có nó thì `INSERT INTO service_provider_proposals` với `quote_id`
-- của bước khác vẫn qua — khoá ngoại đơn `quote_id` chỉ nói "chứng từ này có
-- thật", không nói "nó thuộc bước này".
DO $$
BEGIN
    IF to_regclass('service_provider_proposals') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_proposals_quote_anchor')
       AND EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_service_quotes_anchor')
    THEN
        -- Dọn dòng lệch trước khi siết: không có dòng nào như vậy ngoài môi
        -- trường dev của tuần này (bảng trẻ hơn chính ràng buộc đang thêm), và
        -- một đề xuất trỏ sai bước thì `xac_nhan_de_xuat` đã chặn ở mọi đường
        -- — nó không phải dữ liệu, nó là rác.
        DELETE FROM service_provider_proposals p
         USING service_quotes q
         WHERE p.quote_id = q.quote_id
           AND (p.workflow_id <> q.workflow_id OR p.task_id <> q.task_id);

        ALTER TABLE service_provider_proposals
            ADD CONSTRAINT fk_proposals_quote_anchor
            FOREIGN KEY (quote_id, workflow_id, task_id)
            REFERENCES service_quotes (quote_id, workflow_id, task_id);
    END IF;
END $$;
