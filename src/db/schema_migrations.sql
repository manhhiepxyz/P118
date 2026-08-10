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
