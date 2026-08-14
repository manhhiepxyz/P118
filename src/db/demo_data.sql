-- =============================================================
-- P-118 — Demo Data (idempotent — chạy lại không lỗi, không trùng)
-- Owner: Hoàng Anh
-- Chạy SAU khi schema.sql + seed.sql đã chạy.
--
-- 2 nhóm dữ liệu:
--   1) Business data (Mock API tự quản lý): residents → vehicles
--      → parking_bookings → parking_capacity → payments
--   2) Workflow state: workflows → workflow_tasks → execution_logs
--      → approval_decisions (HITL audit)
--
-- Idempotent: bảng business dùng ID nghiệp vụ + ON CONFLICT DO NOTHING.
-- Bảng audit (execution_logs, approval_decisions) dùng BIGSERIAL id và
-- không có unique nghiệp vụ (1 task có thể log nhiều attempt retry) → reset
-- trước khi insert để chạy lại không trùng dữ liệu.
-- =============================================================

-- Reset toàn bộ dữ liệu demo (TRUNCATE RESTART IDENTITY reset cả BIGSERIAL sequence).
-- CASCADE xử lý chuỗi FK trong 1 lệnh. Chạy lại script = demo data reset về đúng trạng thái.
-- KHÔNG truncate bảng cấu hình (tour_slot_config, zone_capacity_config) — seed.sql lo.
TRUNCATE TABLE
    approval_decisions,
    execution_logs,
    workflow_tasks,
    workflows,
    consultations,
    shuttle_bookings,
    tour_capacity,
    tour_bookings,
    payments,
    parking_bookings,
    parking_capacity,
    vehicles,
    residents
RESTART IDENTITY CASCADE;

-- =============================================================
-- NHÓM 1: BUSINESS DATA
-- =============================================================

INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)
VALUES
    ('RES-001', 'Lâm Thành Bảo',     'A1201', 'Vinhomes Ocean Park'),
    ('RES-002', 'Nguyễn Văn An',     'B2202', 'Vinhomes Ocean Park')
ON CONFLICT (resident_id) DO NOTHING;

INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)
VALUES
    ('VEH-001', 'RES-001', '51A-12345', 'car'),
    ('VEH-002', 'RES-001', '51B-67890', 'motorcycle'),
    ('VEH-003', 'RES-002', '51C-11111', 'car')
ON CONFLICT (vehicle_id) DO NOTHING;

INSERT INTO parking_bookings (booking_id, vehicle_id, parking_zone, booking_date, amount, currency)
VALUES
    ('BOOK-001', 'VEH-001', 'ZONE_A', '2026-08-10', 150000, 'VND'),
    ('BOOK-002', 'VEH-002', 'ZONE_B', '2026-08-10', 100000, 'VND'),
    ('BOOK-003', 'VEH-003', 'ZONE_A', '2026-08-12', 150000, 'VND')
ON CONFLICT (booking_id) DO NOTHING;

INSERT INTO parking_capacity (parking_zone, booking_date, capacity)
VALUES
    ('ZONE_A', '2026-08-10', 3),
    ('ZONE_B', '2026-08-10', 10),
    ('ZONE_A', '2026-08-12', 3)
ON CONFLICT (parking_zone, booking_date) DO NOTHING;

-- Chú ý: unique partial index uq_payments_paid_booking — mỗi booking chỉ 1 PAID.
INSERT INTO payments (payment_id, booking_id, amount, currency, payment_status)
VALUES
    ('PAY-001', 'BOOK-001', 150000, 'VND', 'PAID'),
    ('PAY-002', 'BOOK-002', 100000, 'VND', 'PAID')
ON CONFLICT (payment_id) DO NOTHING;

-- =============================================================
-- NHÓM 1b: DEMO SERVICES (đặt lịch tham quan / đặt xe / tư vấn — v0.5.0)
-- =============================================================

-- Đặt lịch tham quan: TOUR-001 (cư dân RES-001), TOUR-002 (khách, resident_id NULL).
INSERT INTO tour_bookings (tour_id, resident_id, residential_area, tour_date, tour_slot)
VALUES
    ('TOUR-001', 'RES-001', 'Vinhomes Ocean Park', '2026-08-20', 'MORNING'),
    ('TOUR-002', NULL,      'Vinhomes Ocean Park', '2026-08-20', 'AFTERNOON')
ON CONFLICT (tour_id) DO NOTHING;

-- Sức chứa per-date cho 2 ngày demo.
INSERT INTO tour_capacity (residential_area, tour_date, tour_slot, capacity)
VALUES
    ('Vinhomes Ocean Park', '2026-08-20', 'MORNING',   3),
    ('Vinhomes Ocean Park', '2026-08-20', 'AFTERNOON', 3)
ON CONFLICT (residential_area, tour_date, tour_slot) DO NOTHING;

-- Đặt xe tham quan cho lịch TOUR-001.
INSERT INTO shuttle_bookings (shuttle_id, tour_id, tour_date, passenger_count)
VALUES
    ('SHUTTLE-001', 'TOUR-001', '2026-08-20', 4)
ON CONFLICT (shuttle_id) DO NOTHING;

-- Đăng ký tư vấn: RES-001 mua (đầu tư), RES-002 thuê.
INSERT INTO consultations (consultation_id, resident_id, consultation_type, buy_sub_type)
VALUES
    ('CONS-001', 'RES-001', 'BUY',  'INVEST'),
    ('CONS-002', 'RES-002', 'RENT', NULL)
ON CONFLICT (consultation_id) DO NOTHING;

-- =============================================================
-- NHÓM 2: WORKFLOW STATE
-- =============================================================

-- Workflow 1: hoàn thành trọn chuỗi (SUCCESS)
INSERT INTO workflows (workflow_id, goal, status)
VALUES
    ('11111111-1111-1111-1111-111111111111',
     'Tôi mới chuyển vào căn hộ A1201. Đăng ký cư dân, đăng ký xe 51A-12345, đặt chỗ ZONE_A ngày 10/08 và thanh toán phí.',
     'SUCCESS')
ON CONFLICT (workflow_id) DO NOTHING;

INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data, result_data)
VALUES
    ('11111111-1111-1111-1111-111111111111', 'T1', 'register_resident', 'SUCCESS',
     '{"full_name":"Lâm Thành Bảo","apartment_code":"A1201","residential_area":"Vinhomes Ocean Park"}',
     '{"resident_id":"RES-001"}'),
    ('11111111-1111-1111-1111-111111111111', 'T2', 'register_vehicle', 'SUCCESS',
     '{"resident_id":"RES-001","plate_number":"51A-12345","vehicle_type":"car"}',
     '{"vehicle_id":"VEH-001"}'),
    ('11111111-1111-1111-1111-111111111111', 'T3', 'book_parking', 'SUCCESS',
     '{"vehicle_id":"VEH-001","booking_date":"2026-08-10","parking_zone":"ZONE_A"}',
     '{"booking_id":"BOOK-001","parking_zone":"ZONE_A","booking_date":"2026-08-10","amount":150000,"currency":"VND"}'),
    ('11111111-1111-1111-1111-111111111111', 'T4', 'pay_fee', 'SUCCESS',
     '{"booking_id":"BOOK-001","amount":150000,"currency":"VND"}',
     '{"payment_id":"PAY-001","payment_status":"PAID"}')
ON CONFLICT (workflow_id, task_id) DO NOTHING;

INSERT INTO execution_logs (workflow_id, task_id, attempt_number, connector_name, http_status, raw_error_code, standard_result, duration_ms)
VALUES
    ('11111111-1111-1111-1111-111111111111', 'T1', 1, 'ResidentConnector', 201, NULL,
     '{"success":true,"data":{"resident_id":"RES-001"},"error_code":null}', 120),
    ('11111111-1111-1111-1111-111111111111', 'T2', 1, 'TransportConnector', 201, NULL,
     '{"success":true,"data":{"vehicle_id":"VEH-001"},"error_code":null}', 90),
    ('11111111-1111-1111-1111-111111111111', 'T3', 1, 'TransportConnector', 201, NULL,
     '{"success":true,"data":{"booking_id":"BOOK-001","amount":150000},"error_code":null}', 150),
    ('11111111-1111-1111-1111-111111111111', 'T4', 1, 'PaymentConnector', 201, NULL,
     '{"success":true,"data":{"payment_id":"PAY-001","payment_status":"PAID"},"error_code":null}', 200)
;

-- Workflow 2: đang chạy, pay_fee chờ HITL approve (RUNNING + WAITING_APPROVAL)
INSERT INTO workflows (workflow_id, goal, status)
VALUES
    ('22222222-2222-2222-2222-222222222222',
     'Đặt chỗ đỗ xe ZONE_A ngày 12/08 và thanh toán phí.',
     'RUNNING')
ON CONFLICT (workflow_id) DO NOTHING;

INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data, result_data)
VALUES
    ('22222222-2222-2222-2222-222222222222', 'T1', 'book_parking', 'SUCCESS',
     '{"vehicle_id":"VEH-003","booking_date":"2026-08-12","parking_zone":"ZONE_A"}',
     '{"booking_id":"BOOK-003","parking_zone":"ZONE_A","booking_date":"2026-08-12","amount":150000,"currency":"VND"}'),
    ('22222222-2222-2222-2222-222222222222', 'T2', 'pay_fee', 'WAITING_APPROVAL',
     '{"booking_id":"BOOK-003","amount":150000,"currency":"VND"}',
     NULL)
ON CONFLICT (workflow_id, task_id) DO NOTHING;

INSERT INTO approval_decisions (workflow_id, task_id, decided_by, decision, comment)
VALUES
    ('22222222-2222-2222-2222-222222222222', 'T2', 'user:hoanganh', 'APPROVED',
     'Đồng ý thanh toán phí đỗ xe 150,000 VND')
;
