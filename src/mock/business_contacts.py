"""Đầu mối tư vấn của từng dự án — dữ liệu do provider sở hữu.

`contact_name`/`contact_phone` trong output của `schedule_property_viewing` là
người sẽ đón khách xem nhà: nhân viên tư vấn hoặc ban quản lý dự án. KHÔNG phải
tên và số điện thoại của chính người đặt lịch.

Hai lý do phải tách bạch:

  - Trả lại thông tin của user cho user không nói cho họ biết điều gì mới, mà
    lại đẩy PII đi qua state của Agent, qua log và qua DB workflow.
  - Khách chưa phải cư dân (prospect) thì không có hồ sơ để tra, nên cách làm
    cũ trả `None` — và đó chính là nhóm người dùng chủ yếu của tính năng xem
    nhà. Đầu mối phải luôn có, kể cả khi người đặt hoàn toàn ẩn danh.

Đây là dữ liệu mock cho Gate 2. Khi có nguồn nhân sự thật, thay implementation
của `contact_for_project()`; chữ ký giữ nguyên.
"""

from __future__ import annotations

from typing import NamedTuple


class BusinessContact(NamedTuple):
    contact_name: str
    contact_phone: str


# Đầu mối mặc định của trung tâm dịch vụ khách hàng. Dùng khi dự án chưa có
# nhân viên riêng — vẫn là đầu mối thật, không phải chuỗi rỗng.
DEFAULT_CONTACT = BusinessContact("Trung tâm Dịch vụ Khách hàng", "1900-1234")

_CONTACT_BY_PROJECT: dict[str, BusinessContact] = {
    "PRJ-001": BusinessContact("Nguyễn Thu Hà", "0901-234-101"),
    "PRJ-002": BusinessContact("Trần Minh Quân", "0901-234-102"),
    "PRJ-003": BusinessContact("Lê Hoàng Yến", "0901-234-103"),
    "PRJ-004": BusinessContact("Phạm Đức Anh", "0901-234-104"),
    "PRJ-005": BusinessContact("Vũ Thanh Mai", "0901-234-105"),
    "PRJ-006": BusinessContact("Đỗ Quốc Bảo", "0901-234-106"),
    "PRJ-007": BusinessContact("Ngô Kim Chi", "0901-234-107"),
}


def contact_for_project(project_id: str | None) -> BusinessContact:
    """Đầu mối tư vấn của dự án. Luôn trả giá trị dùng được, không bao giờ rỗng."""
    if not project_id:
        return DEFAULT_CONTACT
    return _CONTACT_BY_PROJECT.get(project_id.strip().upper(), DEFAULT_CONTACT)


# Nguồn gốc của thông tin liên hệ mà provider dùng để gọi lại người đăng ký.
# `register_property_interest` trả giá trị này thay vì trả tên/số điện thoại:
# Agent chỉ cần biết provider sẽ liên hệ QUA ĐÂU, không cần cầm PII.
CONTACT_CHANNEL_VERIFIED_ACCOUNT = "VERIFIED_ACCOUNT_CONTACT"
