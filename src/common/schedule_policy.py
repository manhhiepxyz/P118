"""Trần thời gian và khung giờ mở cửa — chính sách, ở tầng dưới cùng.

Owner: Thành Bảo (Decision layer)
File: src/common/schedule_policy.py

Cùng lý do với `agent_tool_policy.py`: đây là CHÍNH SÁCH mà nhiều tầng cùng
hỏi — Validator hỏi để chặn một kế hoạch, `field_parsers` hỏi để chặn một giá
trị người dùng gõ. Câu hỏi ấy phải có đúng một câu trả lời.

Trước đây `field_parsers.py` (tầng `common`) phải `import src.agents.validator`
để lấy hai bảng này. Chiều phụ thuộc ấy sai, và nó buộc mọi thứ chạm bộ đọc một
chuỗi ngày phải kéo theo cả Validator.

Vì sao 5 năm chứ không phải 1–2
-------------------------------
Yêu cầu thật là chặn ngày VÔ LÝ (2050, 2199) — những ngày mà mọi lớp kiểm cũ đều
cho qua vì chúng không nằm trong quá khứ. 5 năm làm được đúng việc đó.

Phần chủ quan, nói thẳng: bộ test hiện dùng nhiều ngày cố định năm 2030 làm
"ngày an toàn trong tương lai". Trần 2 năm đúng hơn về nghiệp vụ nhưng buộc sửa
hàng chục chỗ và rút tuổi thọ bộ test xuống ~1 năm. Chọn trần rộng và ghi lại
đánh đổi, thay vì âm thầm siết luật nghiệp vụ cho vừa fixture.
"""

from __future__ import annotations

from datetime import time

MAX_HORIZON_DAYS: int = 1825

# Khung giờ mở cửa theo TOOL. Giờ hẹn liên hệ nằm trong giờ làm việc của bộ phận
# tư vấn; giờ chuyển nhà rộng hơn vì xe tải vào ra ngoài giờ hành chính.
TIME_INPUTS: dict[str, tuple[str, time, time]] = {
    "schedule_property_viewing": ("viewing_time", time(8, 0), time(17, 30)),
    "create_maintenance_request": ("preferred_time", time(8, 0), time(18, 0)),
    "schedule_move": ("move_time", time(7, 0), time(20, 0)),
    "register_property_interest": ("preferred_contact_time", time(8, 0), time(18, 0)),
}

# Cùng luật, tra theo TÊN Ô. Dựng TỪ bảng trên, không chép tay: bản chép tay
# trước đây thiếu `preferred_contact_time`, nên người dùng trả lời "19:00" thì
# bộ lọc cho qua và Validator mới chặn — họ nhận VALIDATION_ERROR chung chung
# thay vì một câu bảo chọn lại giờ.
TIME_WINDOWS: dict[str, tuple[time, time]] = {field: (opens, closes) for field, opens, closes in TIME_INPUTS.values()}
