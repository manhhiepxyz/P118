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
