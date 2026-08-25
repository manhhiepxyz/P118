"""Bằng chứng một task đã rời hệ thống tới provider hay chưa.

Owner: Thành Bảo (Decision layer)
File: src/common/submission.py

Vì sao không phải một boolean
-----------------------------
Câu hỏi "đã gửi chưa" có BA câu trả lời, và câu thứ ba là câu quan trọng nhất:

    NOT_SUBMITTED   chứng minh được request CHƯA rời hệ thống
    SUBMITTING      đã bắt đầu gửi, chưa biết kết quả
    ACKNOWLEDGED    provider đã xác nhận, và ta giữ được ID của nó
    UNKNOWN         KHÔNG chứng minh được — timeout, mất response, dữ liệu cũ

Một boolean buộc `UNKNOWN` phải nói dối theo một trong hai chiều. Chọn `False`
thì hệ thống gửi lần hai và khách có hai lịch hẹn. Chọn `True` thì nó bỏ qua
một việc chưa ai làm. Cả hai đều hỏng trong im lặng.

Trước file này, bốn thứ từng được dùng làm proxy, và cả bốn đều sai:
`workflows.status` (vòng đời workflow), `task.status == SUCCESS` (nói kết quả,
không nói thời điểm rời hệ thống), `service_approvals` (hàng đợi QUYẾT ĐỊNH nội
bộ — ai đó còn phải bấm), và "có `result_data`" (một `fail` cũng có data).

Không bịa ID
------------
`EXTERNAL_ID_FIELD_BY_TOOL` khai báo ô nào trong output của provider LÀ tham
chiếu có thẩm quyền. Tool không trả về được ô ấy thì trạng thái là `UNKNOWN`,
không phải `ACKNOWLEDGED` với ID rỗng: một bằng chứng không đối chiếu lại được
thì không phải bằng chứng.

`search_properties` chỉ đọc — nó không tạo cam kết nào và provider không trả ID
nào. Nó không bao giờ mang bằng chứng gửi đi.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from src.common.enums import ErrorCode


class SubmissionStatus(StrEnum):
    NOT_SUBMITTED = "NOT_SUBMITTED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"


# Ô trong output của provider mang tham chiếu CÓ THẨM QUYỀN cho từng tool.
# Kiểm bằng test đối chiếu với `TOOL_CONTRACTS.outputs`: thiếu một khai báo là
# thiếu bằng chứng cho MỌI lần tool ấy chạy.
EXTERNAL_ID_FIELD_BY_TOOL: dict[str, str] = {
    "book_parking": "booking_id",
    "book_shuttle": "shuttle_id",
    "create_maintenance_request": "maintenance_id",
    "pay_fee": "payment_id",
    "register_property_interest": "interest_id",
    "register_resident": "resident_id",
    "register_vehicle": "vehicle_id",
    "schedule_move": "move_request_id",
    "schedule_property_viewing": "viewing_id",
}

# Tool KHÔNG tạo cam kết nào ở phía provider.
READ_ONLY_TOOLS = frozenset({"search_properties"})

# Mã lỗi CHỨNG MINH được request chưa rời hệ thống.
#
# Cả hai xảy ra TRƯỚC lời gọi connector: `_resolve_input` không giải được đầu
# vào, hoặc không có connector nào phục vụ tool. Không có gói tin nào được gửi.
#
# Danh sách này cố ý rất hẹp. Mọi mã khác — kể cả lỗi nghiệp vụ rõ ràng như
# "hết chỗ" — đều nghĩa là provider ĐÃ nhận và ĐÃ xử lý.
_PROVES_NOT_SENT = frozenset({ErrorCode.DEPENDENCY_ERROR.value, ErrorCode.UNKNOWN_TOOL.value})


def evidence_from_result(tool: str, result: Any) -> tuple[SubmissionStatus, str | None]:
    """Trạng thái gửi và ID có thẩm quyền, suy từ MỘT kết quả connector.

    Fail-closed: nhánh mặc định là `UNKNOWN`, không phải `NOT_SUBMITTED`. Gọi
    một lần gửi không rõ kết quả là "chưa gửi" nghĩa là lần sau gửi lại — và
    provider tạo record rồi mới mất response ở đường về là chuyện hoàn toàn
    bình thường, Executor không có cách nào phân biệt.
    """
    if tool in READ_ONLY_TOOLS:
        return SubmissionStatus.NOT_SUBMITTED, None

    if getattr(result, "success", False):
        field = EXTERNAL_ID_FIELD_BY_TOOL.get(tool)
        data = getattr(result, "data", None) or {}
        value = data.get(field) if field and isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            return SubmissionStatus.ACKNOWLEDGED, value.strip()
        # Provider báo xong nhưng không đưa tham chiếu nào. Không dựng bằng
        # chứng rỗng — lần sau muốn hỏi "cái đó thế nào rồi" thì không có gì để hỏi.
        return SubmissionStatus.UNKNOWN, None

    code = getattr(result, "error_code", None)
    code = getattr(code, "value", code)
    if code in _PROVES_NOT_SENT:
        return SubmissionStatus.NOT_SUBMITTED, None
    return SubmissionStatus.UNKNOWN, None


# Trạng thái KHÔNG bao giờ được rời khỏi.
#
# `UNKNOWN` là kết luận "không chứng minh được", và không có quan sát nào về sau
# làm nó chứng minh được lại. Cho phép `UNKNOWN → NOT_SUBMITTED` là mở lại đúng
# đường gửi trùng. `ACKNOWLEDGED` cũng vậy: ID đã cầm rồi thì không mất đi.
TERMINAL_SUBMISSION_STATUSES = frozenset({SubmissionStatus.ACKNOWLEDGED.value, SubmissionStatus.UNKNOWN.value})
