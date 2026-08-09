-- =============================================================
-- P-118 — Seed Data
-- Version: v0.1.0
-- Updated: 2026-08-05
-- =============================================================
-- Chạy sau schema.sql.
-- Seed capacity mặc định cho parking_capacity theo từng zone.
-- Service layer dùng ON CONFLICT DO NOTHING khi tạo booking mới
-- cho ngày chưa có row — nhưng capacity không được suy ra từ DEFAULT,
-- phải seed rõ ràng để tránh ambiguity khi thêm zone mới sau này.
-- =============================================================

-- Capacity cố định theo zone (không phụ thuộc ngày).
-- Mỗi ngày service sẽ INSERT row mới với capacity này nếu chưa tồn tại.
-- Xem: src/db/postgres_repository.py :: _ensure_capacity_row()

-- Template: zone_capacity dùng để service tra cứu capacity mặc định theo zone.
-- Tách khỏi parking_capacity (per-date) để không phải hardcode trong code.
CREATE TABLE IF NOT EXISTS zone_capacity_config (
    parking_zone VARCHAR(20) PRIMARY KEY
                     CHECK (parking_zone IN ('ZONE_A', 'ZONE_B')),
    capacity     INTEGER     NOT NULL CHECK (capacity > 0),
    price_per_day INTEGER    NOT NULL CHECK (price_per_day >= 0),  -- VND
    description  TEXT
);

INSERT INTO zone_capacity_config (parking_zone, capacity, price_per_day, description)
VALUES
    ('ZONE_A', 3,  150000, 'Bãi đỗ xe khu A — gần tòa nhà, sức chứa nhỏ, giá cao'),
    ('ZONE_B', 10, 100000, 'Bãi đỗ xe khu B — xa hơn, sức chứa lớn, giá thấp hơn')
ON CONFLICT (parking_zone) DO UPDATE
    SET capacity      = EXCLUDED.capacity,
        price_per_day = EXCLUDED.price_per_day,
        description   = EXCLUDED.description;

-- =============================================================
-- Chủ sở hữu căn hộ — data seed từ ban quản lý chung cư.
-- Khi register_resident, hệ thống tra bảng này để verify quyền sở hữu.
-- Tên/id_number phải khớp với dữ liệu test (Lâm Thành Bảo / A1201)
-- để các test case hiện tại vẫn pass.
-- =============================================================

INSERT INTO apartment_owners (apartment_code, residential_area, owner_name, id_number)
VALUES
    ('A1201', 'Vinhomes Ocean Park', 'Lâm Thành Bảo',   '***1234'),
    ('B2305', 'Vinhomes Ocean Park', 'Trần Thị Bích',   '***5678'),
    ('C1801', 'Vinhomes Ocean Park', 'Nguyễn Văn Cường', '***9012'),
    ('D0502', 'Vinhomes Smart City', 'Lê Thị Dung',      '***3456'),
    ('E1101', 'Vinhomes Smart City', 'Phạm Minh Quân',   '***7890')
ON CONFLICT (apartment_code, residential_area) DO UPDATE
    SET owner_name  = EXCLUDED.owner_name,
        id_number   = EXCLUDED.id_number,
        verified_at = NOW();
