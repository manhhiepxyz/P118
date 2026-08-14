"""Lưu trữ tạm in-memory cho mock service.

Tuần 1 dùng dict trong RAM cho đơn giản; tuần sau thay bằng PostgreSQL.
Singleton ``store`` dùng chung cho cả 4 service để mô phỏng dữ liệu nối nhau
(ví dụ Vehicle kiểm tra resident_id tồn tại trong cùng cửa hàng dữ liệu).
"""

from dataclasses import dataclass, field
from threading import RLock

from src.common.projects import PROJECTS as _CANONICAL_PROJECTS

# Dữ liệu chủ sở hữu căn hộ — seed từ ban quản lý chung cư.
# Dùng verify quyền sở hữu khi register_resident.
# Phải khớp với dữ liệu test (Lâm Thành Bảo / A1201 / Vinhomes Ocean Park).
DEFAULT_APARTMENT_OWNERS = [
    {
        "apartment_code": "A1201",
        "residential_area": "Vinhomes Ocean Park",
        "owner_name": "Lâm Thành Bảo",
        "id_number": "***1234",
    },
    {
        "apartment_code": "B2305",
        "residential_area": "Vinhomes Ocean Park",
        "owner_name": "Trần Thị Bích",
        "id_number": "***5678",
    },
    {
        "apartment_code": "C1801",
        "residential_area": "Vinhomes Ocean Park",
        "owner_name": "Nguyễn Văn Cường",
        "id_number": "***9012",
    },
    {
        "apartment_code": "D0502",
        "residential_area": "Vinhomes Smart City",
        "owner_name": "Lê Thị Dung",
        "id_number": "***3456",
    },
    {
        "apartment_code": "E1101",
        "residential_area": "Vinhomes Smart City",
        "owner_name": "Phạm Minh Quân",
        "id_number": "***7890",
    },
    # Test data cho capacity tests (test_book_parking_no_availability_zone_a)
    # Một người có thể sở hữu nhiều căn hộ
    {
        "apartment_code": "B0",
        "residential_area": "Vinhomes Ocean Park",
        "owner_name": "Lâm Thành Bảo",
        "id_number": "***T000",
    },
    {
        "apartment_code": "B1",
        "residential_area": "Vinhomes Ocean Park",
        "owner_name": "Lâm Thành Bảo",
        "id_number": "***T001",
    },
    {
        "apartment_code": "B2",
        "residential_area": "Vinhomes Ocean Park",
        "owner_name": "Lâm Thành Bảo",
        "id_number": "***T002",
    },
    {
        "apartment_code": "B3",
        "residential_area": "Vinhomes Ocean Park",
        "owner_name": "Lâm Thành Bảo",
        "id_number": "***T003",
    },
]

# Sức chứa slot tham quan theo (residential_area, tour_slot) — seed như
# apartment_owners. Dùng cho NO_AVAILABILITY khi slot đã kín.
# Sức chứa được sinh từ danh mục dự án canonical, không liệt kê tay: mọi dự án
# `search_properties` trả về đều phải đặt lịch xem được. Danh sách viết tay
# trước đây chỉ phủ 2 khu, nên 5 dự án còn lại search ra rồi đặt là 404.
DEFAULT_TOUR_SLOTS = [
    (_project["project_name"], slot, 3)
    for _project in _CANONICAL_PROJECTS
    for slot in ("MORNING", "AFTERNOON")
]


@dataclass
class Store:
    """Kho dữ liệu dùng chung cho các mock service."""

    residents: dict = field(default_factory=dict)
    vehicles: dict = field(default_factory=dict)
    bookings: dict = field(default_factory=dict)
    payments: dict = field(default_factory=dict)
    # Số chỗ đã đặt cho (zone, booking_date) — dùng cho NO_AVAILABILITY.
    parking_load: dict = field(default_factory=dict)
    # Chủ sở hữu căn hộ — key: (apartment_code, residential_area) → value: record
    apartment_owners: dict = field(default_factory=dict)
    # Đặt lịch tham quan dự án (book_tour).
    tour_bookings: dict = field(default_factory=dict)
    # Số lượt đã đặt cho (residential_area, tour_date, tour_slot) — NO_AVAILABILITY.
    tour_load: dict = field(default_factory=dict)
    # Sức chứa slot tham quan — key: (residential_area, tour_slot) → capacity
    tour_slots: dict = field(default_factory=dict)
    # Đặt xe tham quan (book_shuttle).
    shuttle_bookings: dict = field(default_factory=dict)
    # Tổng số khách đã đặt xe cho một ngày — key: tour_date (ISO string)
    shuttle_load: dict = field(default_factory=dict)
    # Đăng ký tư vấn (register_consultation).
    consultations: dict = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock, repr=False)

    def __post_init__(self) -> None:
        """Seed apartment_owners + tour_slots từ dữ liệu mặc định."""
        self._seed_apartment_owners()
        self._seed_tour_slots()

    def _seed_apartment_owners(self) -> None:
        """Nạp dữ liệu chủ sở hữu mặc định."""
        for owner in DEFAULT_APARTMENT_OWNERS:
            key = (owner["apartment_code"], owner["residential_area"])
            self.apartment_owners[key] = {
                "apartment_code": owner["apartment_code"],
                "residential_area": owner["residential_area"],
                "owner_name": owner["owner_name"],
                "id_number": owner.get("id_number"),
            }

    def _seed_tour_slots(self) -> None:
        """Nạp sức chứa slot tham quan mặc định."""
        for area, slot, capacity in DEFAULT_TOUR_SLOTS:
            self.tour_slots[(area, slot)] = capacity

    def reset(self) -> None:
        """Xóa toàn bộ dữ liệu (dùng cho test isolation), rồi re-seed."""
        with self._lock:
            self.residents.clear()
            self.vehicles.clear()
            self.bookings.clear()
            self.payments.clear()
            self.parking_load.clear()
            self.apartment_owners.clear()
            self.tour_bookings.clear()
            self.tour_load.clear()
            self.tour_slots.clear()
            self.shuttle_bookings.clear()
            self.shuttle_load.clear()
            self.consultations.clear()
            self._seed_apartment_owners()
            self._seed_tour_slots()


store = Store()
