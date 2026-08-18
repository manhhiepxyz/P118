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

from src.common.projects import PROJECTS


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
        if task.tool == "book_shuttle":
            tour_date = _text(inputs.get("tour_date"))
            suffix = f" ngày {tour_date}" if tour_date else ""
            return f"Xe tham quan đã hết chỗ{suffix}. Hãy chọn ngày khác."
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
        # Đọc cả `project_id`, không chỉ `project_name`.
        #
        # Trong input thật, tên người dùng gõ nằm ở `project_id`: Planner điền
        # tên vào đó, và Validator chỉ đổi được sang mã khi tên CÓ trong danh
        # mục — đúng những lần hỏng thì nó không đổi được. Chỉ đọc
        # `project_name` nên câu trả lời thành "Dự án đã chọn không có trong
        # danh mục", tức là không nói được cái tên mà người dùng vừa gõ.
        #
        # Mã nội bộ (`PRJ-007`) thì KHÔNG đưa ra: người dùng không gõ nó và
        # không cần biết nó.
        raw = _text(inputs.get("project_name")) or _text(inputs.get("project_id"))
        project = raw if raw and not raw.upper().startswith("PRJ-") else None
        subject = f"Dự án “{project}”" if project else "Dự án đã chọn"
        # Liệt kê luôn danh mục, giống hệt câu ở nhánh hỏi-bổ-sung.
        #
        # Hai đường đều dẫn tới "dự án không có" nhưng trước đây nói khác nhau:
        # một bên liệt kê 7 dự án, một bên chỉ bảo "chọn trong danh sách được
        # hỗ trợ" — trong hội thoại thì không có danh sách nào để nhìn. Người
        # dùng gặp đường nào là may rủi, và đường kém hơn để họ đoán tiếp.
        catalogue = ", ".join(project["project_name"] for project in PROJECTS)
        return f"{subject} không có trong danh mục. Hãy chọn một trong các dự án đang có: {catalogue}."
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
    if code == "SHUTTLE_ALREADY_BOOKED":
        return "Lịch xem nhà này đã được đặt xe đưa đón rồi. Bạn hãy đặt xe cho một lịch tham quan khác."
    if code == "VIEWING_NOT_FOUND":
        return "Không tìm thấy lịch tham quan để đặt xe đưa đón. Bạn hãy đặt lịch tham quan trước."
    if code in {"SERVICE_UNAVAILABLE", "SERVICE_TIMEOUT"}:
        return f"Dịch vụ cho bước “{title}” đang tạm gián đoạn. Bạn thử lại sau ít phút giúp mình nhé."

    # Mã CHƯA được phân loại.
    #
    # Câu này cố ý KHÔNG nói "vui lòng thử lại". "Thử lại" là một lời hứa rằng
    # lần sau sẽ khác — với một mã chưa ai phân loại, ta không biết điều đó có
    # đúng không. Thực tế đã xảy ra: dự án không tồn tại rơi vào nhánh này, và
    # người dùng được mời bấm lại một việc không bao giờ chạy được.
    return f"Bước “{title}” chưa thực hiện được. Bạn kiểm tra lại thông tin hoặc liên hệ hỗ trợ giúp mình nhé."


# Nhãn công khai của khu đỗ xe. Người dùng không bao giờ nhìn thấy "ZONE_A".
_ZONE_LABELS = {"ZONE_A": "Khu A", "ZONE_B": "Khu B"}

# Giá trị chuẩn của contract ↔ cách người Việt thật sự nói ra nó.
#
# Cần cho phép so "giá trị này có phải lấy từ chuyện cũ không": model viết lại
# câu nói thành dạng chuẩn (`ZONE_A`), còn ký ức lưu nguyên văn ("khu A"). So
# thô sẽ bỏ lọt đúng những ca cần bắt — và ca bị bỏ lọt là ca hệ thống lặng lẽ
# đặt lại chỗ cũ mà không hỏi ai.
#
# Danh sách enum lấy từ `TOOL_CONTRACTS`; thêm enum mới mà quên ở đây thì guard
# yếu đi chứ không sai — nó chỉ hết bắt được dạng tiếng Việt của enum đó.
SPOKEN_FORMS: dict[str, tuple[str, ...]] = {
    "ZONE_A": ("khu a", "zone a"),
    "ZONE_B": ("khu b", "zone b"),
    "car": ("ô tô", "oto", "xe hơi", "xe con"),
    "motorcycle": ("xe máy", "xe may", "mô tô"),
    "buy": ("mua",),
    "rent": ("thuê", "thue"),
    "consultation": ("tư vấn", "tu van"),
    "apartment": ("căn hộ", "can ho"),
    "room": ("phòng", "phong"),
    "plumbing": ("nước", "ống nước", "vòi nước"),
    "electrical": ("điện",),
    "air_conditioning": ("điều hoà", "máy lạnh"),
    "truck": ("xe tải",),
    "van": ("xe van",),
}


def spoken_forms(value: str) -> tuple[str, ...]:
    """Mọi cách một giá trị chuẩn có thể đã được NÓI RA, kèm chính nó."""
    return (value,) + SPOKEN_FORMS.get(value, ())
_OTHER_ZONE = {"ZONE_A": "Khu B", "ZONE_B": "Khu A"}


def repair_question(task_tool: str, code: str, task_input: dict | None) -> str | None:
    """Câu hỏi lại SAU khi một bước hỏng, có nêu lý do. None nếu chưa có câu riêng.

    Vì sao không dùng chung câu với lúc thiếu thông tin:

    Sự cố thật — người dùng gõ đầy đủ "đặt chỗ đỗ xe tại Khu A ngày 2026-08-22",
    plan chạy với `parking_zone="ZONE_A"`, provider trả `NO_AVAILABILITY`
    ("Parking zone is full for that date"). Hệ thống hỏi lại `parking_zone` —
    đúng ý định, nhưng dùng câu của nhánh THIẾU THÔNG TIN: "Mình cần bạn xác
    nhận khu vực đỗ xe là Khu A hay Khu B". Người dùng đã nói Khu A rồi, nên
    họ đọc được một câu vô lý và không hề biết Khu A đã kín.

    Thông tin không thiếu — nó hợp lệ nhưng không đáp ứng được. Hai tình huống
    khác nhau thì phải nói khác nhau, và câu nói phải mang theo lý do, nếu
    không người dùng sẽ trả lời đúng cái giá trị vừa bị từ chối.
    """
    inputs = task_input or {}

    if code == "NO_AVAILABILITY":
        if task_tool == "book_shuttle":
            date = _text(inputs.get("tour_date"))
            when = f" ngày {date}" if date else ""
            return f"Xe tham quan đã hết chỗ{when}. Bạn chọn ngày khác giúp mình nhé."
        if task_tool == "book_parking":
            zone = str(inputs.get("parking_zone") or "")
            label = _ZONE_LABELS.get(zone, "Khu vực bạn chọn")
            date = _text(inputs.get("booking_date"))
            when = f" ngày {date}" if date else ""
            alternative = _OTHER_ZONE.get(zone)
            suggestion = f"Bạn thử {alternative}" if alternative else "Bạn thử khu vực khác"
            return f"{label} đã hết chỗ{when}. {suggestion} hoặc chọn ngày khác giúp mình nhé."
        if task_tool == "schedule_property_viewing":
            time_text = _text(inputs.get("viewing_time"))
            date = _text(inputs.get("viewing_date"))
            slot = " ".join(part for part in (time_text, f"ngày {date}" if date else "") if part)
            subject = f"Khung giờ {slot}" if slot else "Khung giờ bạn chọn"
            return f"{subject} đã kín lịch. Bạn chọn giờ hoặc ngày khác giúp mình nhé."

    if code == "VEHICLE_ALREADY_EXISTS":
        plate = _text(inputs.get("plate_number"))
        subject = f"Biển số {plate}" if plate else "Biển số này"
        return f"{subject} đã được đăng ký trước đó. Bạn kiểm tra lại hoặc nhập biển số khác giúp mình nhé."

    if code == "BOOKING_ALREADY_EXISTS":
        date = _text(inputs.get("booking_date"))
        when = f" ngày {date}" if date else ""
        return f"Bạn đã có chỗ đỗ xe{when} rồi. Bạn chọn ngày khác giúp mình nhé."

    if code == "RESIDENT_ALREADY_EXISTS":
        return "Căn hộ này đã được đăng ký. Bạn kiểm tra lại mã căn hộ giúp mình nhé."

    if code == "SHUTTLE_ALREADY_BOOKED":
        return "Lịch tham quan này đã có xe đưa đón rồi. Bạn đặt xe cho một lịch tham quan khác giúp mình nhé."

    return None
