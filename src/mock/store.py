"""Lưu trữ tạm in-memory cho mock service.

Tuần 1 dùng dict trong RAM cho đơn giản; tuần sau thay bằng PostgreSQL.
Singleton ``store`` dùng chung cho cả 4 service để mô phỏng dữ liệu nối nhau
(ví dụ Vehicle kiểm tra resident_id tồn tại trong cùng cửa hàng dữ liệu).
"""

from dataclasses import dataclass, field
from threading import RLock

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
    _lock: RLock = field(default_factory=RLock, repr=False)

    def __post_init__(self) -> None:
        """Seed apartment_owners từ DEFAULT_APARTMENT_OWNERS."""
        self._seed_apartment_owners()

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

    def reset(self) -> None:
        """Xóa toàn bộ dữ liệu (dùng cho test isolation), rồi re-seed."""
        with self._lock:
            self.residents.clear()
            self.vehicles.clear()
            self.bookings.clear()
            self.payments.clear()
            self.parking_load.clear()
            self.apartment_owners.clear()
            self._seed_apartment_owners()


store = Store()
