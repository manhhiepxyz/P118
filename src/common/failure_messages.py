"""Thông báo lỗi nghiệp vụ dùng chung giữa API và Repair Loop.

Owner: Thành Bảo (Decision layer)
File: src/common/failure_messages.py

Một nguồn DUY NHẤT cho message lỗi nghiệp vụ — API (`_demo_response`),
polling task views và Repair Loop cùng dùng hàm này để hai nơi không bao giờ
lệch nhau. Chỉ nhận mã lỗi đã chuẩn hoá (`ErrorCode`), không bao giờ đưa raw
exception / payload / connection detail ra ngoài.

`task` là object có `.tool` và `.input` (TaskPlan task hoặc row dict). Hàm chỉ
đọc input để lấy thông tin nghiệp vụ đã được allowlist (`apartment_code`,
`plate_number`, `viewing_date`...), không echo dữ liệu không hợp lệ.
"""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str | None:
    """Chỉ nhận scalar để presentation layer không phát tán raw object."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def task_failure_message(task: Any, title: str, code: str) -> str:
    """Đổi mã lỗi provider thành thông báo nghiệp vụ, không lộ raw exception."""
    inputs = task.input
    if code == "RESIDENT_ALREADY_EXISTS":
        apartment = _text(inputs.get("apartment_code"))
        subject = f"Căn hộ {apartment}" if apartment else "Căn hộ này"
        return f"{subject} đã có hồ sơ cư dân. Hãy sử dụng tài khoản cư dân đã liên kết."
    if code == "VEHICLE_ALREADY_EXISTS":
        plate = _text(inputs.get("plate_number"))
        subject = f"Biển số {plate}" if plate else "Biển số này"
        return f"{subject} đã được đăng ký. Hãy sử dụng phương tiện đã liên kết hoặc kiểm tra lại biển số."
    if code == "NO_AVAILABILITY":
        if task.tool == "schedule_property_viewing":
            viewing_date = _text(inputs.get("viewing_date"))
            viewing_time = _text(inputs.get("viewing_time"))
            slot = " ".join(value for value in (viewing_date, viewing_time) if value)
            suffix = f" {slot}" if slot else " này"
            return f"Khung giờ tham quan{suffix} không còn trống. Hãy chọn thời gian khác."
        booking_date = _text(inputs.get("booking_date"))
        suffix = f" cho ngày {booking_date}" if booking_date else ""
        return f"Khu vực đỗ xe đã hết chỗ{suffix}. Hãy chọn ngày hoặc khu vực khác."
    if code == "BOOKING_ALREADY_EXISTS":
        return "Phương tiện này đã có chỗ đỗ trong ngày được chọn."
    if code == "DEPENDENCY_ERROR":
        return f"Bước “{title}” chưa được thực hiện vì bước trước đó không thành công."
    if code == "INVALID_INPUT":
        return f"Thông tin của bước “{title}” chưa hợp lệ. Hãy kiểm tra lại dữ liệu đã nhập."
    if code == "PROJECT_NOT_FOUND":
        project = _text(inputs.get("project_name"))
        subject = f"Dự án “{project}”" if project else "Dự án đã chọn"
        return f"{subject} không có trong danh mục. Hãy chọn một dự án trong danh sách được hỗ trợ."
    if code == "VIEWING_ALREADY_BOOKED":
        viewing_date = _text(inputs.get("viewing_date"))
        viewing_time = _text(inputs.get("viewing_time"))
        slot = " ".join(value for value in (viewing_date, viewing_time) if value)
        suffix = f" {slot}" if slot else " này"
        return f"Khung giờ{suffix} đã có người đặt. Hãy chọn một khung giờ khác."
    if code == "INTEREST_ALREADY_EXISTS":
        project = _text(inputs.get("project_name"))
        subject = f"dự án “{project}”" if project else "dự án này"
        return f"Bạn đã đăng ký quan tâm {subject} rồi. Bộ phận tư vấn sẽ liên hệ với bạn."
    if code in {"SERVICE_UNAVAILABLE", "SERVICE_TIMEOUT"}:
        return f"Dịch vụ cho bước “{title}” đang tạm gián đoạn. Bạn thử lại sau ít phút giúp mình nhé."

    # Mã CHƯA được phân loại.
    #
    # Câu này cố ý KHÔNG nói "vui lòng thử lại". "Thử lại" là một lời hứa rằng
    # lần sau sẽ khác — với một mã chưa ai phân loại, ta không biết điều đó có
    # đúng không. Thực tế đã xảy ra: dự án không tồn tại rơi vào nhánh này, và
    # người dùng được mời bấm lại một việc không bao giờ chạy được.
    return f"Bước “{title}” chưa thực hiện được. Bạn kiểm tra lại thông tin hoặc liên hệ hỗ trợ giúp mình nhé."
