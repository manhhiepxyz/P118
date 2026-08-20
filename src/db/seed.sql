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
    -- `>= 0`, không phải `> 0`.
    --
    -- Sức chứa 0 nghĩa là khu KHÔNG còn nhận đăng ký — một trạng thái nghiệp vụ
    -- có thật (bãi đã kín dài hạn), và `check_and_reserve_capacity` xử lý nó
    -- đúng ngay: `booked_count >= capacity_limit` thành `0 >= 0`, trả
    -- NO_AVAILABILITY. Cách này áp cho MỌI ngày mà không cần gieo booking giả —
    -- booking giả vừa sai sự thật vừa phải gieo lại cho từng ngày.
    capacity     INTEGER     NOT NULL CHECK (capacity >= 0),
    price_per_day INTEGER    NOT NULL CHECK (price_per_day >= 0),  -- VND
    description  TEXT
);

INSERT INTO zone_capacity_config (parking_zone, capacity, price_per_day, description)
VALUES
    -- Khu A KÍN, khu B luôn còn chỗ — kịch bản demo cố định.
    --
    -- Luồng đáng xem nhất của sản phẩm là: chọn khu A → hết chỗ → hệ thống nêu
    -- lý do và gợi ý khu B → người dùng đổi → chạy tiếp. Muốn diễn lại được
    -- luồng ấy thì kết quả phải ĐOÁN TRƯỚC ĐƯỢC, không phụ thuộc hôm nay đã có
    -- ai đặt chưa.
    ('ZONE_A', 0,   150000, 'Bãi đỗ xe khu A — gần tòa nhà, hiện đã kín'),
    ('ZONE_B', 100, 100000, 'Bãi đỗ xe khu B — xa hơn, sức chứa lớn, giá thấp hơn')
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

-- =============================================================
-- Sức chứa slot tham quan dự án (demo — v0.5.0).
-- Khớp DEFAULT_TOUR_SLOTS trong src/mock/store.py.
-- Service layer dùng ON CONFLICT DO NOTHING khi tạo tour_capacity per-date.
-- =============================================================

INSERT INTO tour_slot_config (residential_area, tour_slot, capacity)
VALUES
    ('Vinhomes Ocean Park', 'MORNING',   3),
    ('Vinhomes Ocean Park', 'AFTERNOON', 3),
    ('Vinhomes Smart City', 'MORNING',   3),
    ('Vinhomes Smart City', 'AFTERNOON', 3)
ON CONFLICT (residential_area, tour_slot) DO UPDATE
    SET capacity = EXCLUDED.capacity;


-- Đồng bộ các ngày ĐÃ vật hoá sang cấu hình vừa cập nhật ở TRÊN.
--
-- PHẢI nằm ở seed.sql, không phải schema_migrations.sql: thứ tự chạy là
-- schema → schema_migrations → seed, nên đặt ở file giữa thì lúc UPDATE bảng
-- cấu hình vẫn còn giá trị CŨ và câu lệnh không đổi gì cả. Đo được: config
-- thành 0/100 mà các ngày vẫn 3/10.
--
-- `parking_capacity` sinh một dòng cho mỗi (khu, ngày) ở lần dùng đầu, chép
-- sức chứa TẠI THỜI ĐIỂM ĐÓ. Không đụng tới chúng thì những ngày đã chạm giữ
-- số cũ, và kịch bản demo hỏng đúng vào ngày hay dùng nhất.
--
-- Chỉ sửa từ HÔM NAY trở đi: ngày đã qua là bằng chứng chuyện đã xảy ra.
UPDATE parking_capacity c
   SET capacity = z.capacity
  FROM zone_capacity_config z
 WHERE c.parking_zone = z.parking_zone
   AND c.booking_date >= CURRENT_DATE
   AND c.capacity <> z.capacity;
