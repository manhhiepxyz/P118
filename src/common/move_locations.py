"""Danh mục địa điểm chuyển nhà nội khu mà bản demo thực sự phục vụ.

Đây là dữ liệu mock có chủ ý, không phải geocoder. Người dùng có thể nói tên
có/không dấu; mã nội bộ chỉ được sinh khi tên khớp duy nhất trong danh mục.
Ngoài danh mục trả ``None`` để tầng trên nói chưa hỗ trợ, tuyệt đối không ước
lượng khoảng cách rồi dựng giá.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

DistanceBand = Literal["SAME_BUILDING", "SAME_WARD", "SAME_DISTRICT"]


@dataclass(frozen=True)
class MoveLocation:
    location_id: str
    name: str
    ward: str
    district: str


MOVE_LOCATIONS: tuple[MoveLocation, ...] = (
    MoveLocation("MOVE-Q7-A1", "Tòa A1 Riverside", "Tân Phú", "Quận 7"),
    MoveLocation("MOVE-Q7-A2", "Tòa A2 Riverside", "Tân Phú", "Quận 7"),
    MoveLocation("MOVE-Q7-B1", "Tòa B1 Green View", "Tân Phong", "Quận 7"),
    MoveLocation("MOVE-Q7-B2", "Tòa B2 Green View", "Tân Phong", "Quận 7"),
    MoveLocation("MOVE-Q7-C1", "Tòa C1 Sunrise", "Tân Hưng", "Quận 7"),
    MoveLocation("MOVE-Q7-C2", "Tòa C2 Sunrise", "Tân Hưng", "Quận 7"),
)


def _fold(text: str) -> str:
    swapped = text.casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", swapped)
    return " ".join("".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn").split())


_BY_ID = {item.location_id: item for item in MOVE_LOCATIONS}
_BY_NAME = {_fold(item.name): item.location_id for item in MOVE_LOCATIONS}


def resolve_move_location_id(value: str) -> str | None:
    """Mã canonical cho đúng một ID/tên; không fuzzy-match địa chỉ lạ."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if cleaned.upper() in _BY_ID:
        return cleaned.upper()
    return _BY_NAME.get(_fold(cleaned))


def find_move_location_id(text: str) -> str | None:
    """Tìm đúng một tên danh mục trong một câu tự nhiên; nhiều tên → mơ hồ."""
    folded = _fold(text)
    found = {location_id for name, location_id in _BY_NAME.items() if name in folded}
    return next(iter(found)) if len(found) == 1 else None


def move_location_name(location_id: str) -> str | None:
    item = _BY_ID.get((location_id or "").upper())
    return item.name if item else None


def distance_band(origin_id: str, destination_id: str) -> DistanceBand | None:
    """Nhóm quãng đường được mock provider hỗ trợ, hoặc ``None`` ngoài vùng."""
    origin = _BY_ID.get((origin_id or "").upper())
    destination = _BY_ID.get((destination_id or "").upper())
    if origin is None or destination is None:
        return None
    if origin.location_id == destination.location_id:
        return "SAME_BUILDING"
    if (origin.ward, origin.district) == (destination.ward, destination.district):
        return "SAME_WARD"
    if origin.district == destination.district:
        return "SAME_DISTRICT"
    return None
