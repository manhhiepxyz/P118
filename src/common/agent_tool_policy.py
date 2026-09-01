"""Ba tập tool, và chỗ duy nhất định nghĩa chúng.

Owner: Thành Bảo (Decision layer)
File: src/common/agent_tool_policy.py

    PROVIDER_TOOLS         mọi thứ hệ thống có connector phục vụ
    AGENT_FORBIDDEN_TOOLS  có connector, nhưng Agent KHÔNG được chạm
    AGENT_REACHABLE_TOOLS  phần còn lại — Planner lập được, Patch sửa được

Vì sao ở `common` chứ không ở `agents/planner.py`
------------------------------------------------
`field_parsers.py` từng `import src.agents.planner` chỉ để lấy danh sách này.
Chiều ấy sai: `common` là tầng dưới cùng, còn `agents`/`api`/`orchestration`
dựng trên nó. Một import ngược tạo vòng tiềm tàng, và nó buộc mọi thứ chạm
`field_parsers` phải kéo theo cả Planner — kể cả những chỗ chỉ cần đọc một
chuỗi ngày.

Đây là CHÍNH SÁCH, không phải hạ tầng: nó trả lời "Agent được phép làm gì", một
câu hỏi mà cả bốn tầng đều hỏi. Câu hỏi ấy phải có đúng một câu trả lời, ở một
chỗ ai cũng với tới được mà không phải với ngược lên.

Vì sao hai tool bị cấm
----------------------
`register_resident` — đăng ký / liên kết / xác minh hồ sơ cư dân xảy ra NGOÀI
Agent (đường admin/provider). Planner tự thêm nó thì nó sẽ hỏi
`full_name`/`apartment_code`/`residential_area`, ba ô mà giao diện không có chỗ
nhập; người dùng nhận một câu hỏi không có câu trả lời hợp lệ.

`search_properties` — quyết định sản phẩm: tìm kiếm / listing là chức năng
marketplace thông thường, không phải tác vụ của Agent.

Cả hai VẪN CÒN connector và provider — không xoá, vì xoá là một thay đổi rộng
hơn hẳn và không cần thiết. Thứ bị đóng là ĐƯỜNG TỚI chúng từ Agent.

Ràng buộc nằm ở đây, không ở prompt: đã quan sát trên model thật — nó tự thêm
một tool mà prompt đã dặn không dùng.
"""

from __future__ import annotations

from typing import get_args

from src.common.task_plan import AllowedTool

PROVIDER_TOOLS: frozenset[str] = frozenset(get_args(AllowedTool))

# `change_parking_zone` nằm đây vì nó KHÔNG phải một dịch vụ người dùng yêu cầu
# từ đầu — nó là thao tác SỬA trên một chỗ đã giữ, chỉ có nghĩa khi đã tồn tại
# `booking_id` thật từ một bước trước.
#
# Cho Planner lập kế hoạch với nó nghĩa là model được phép tự viết ra một
# `booking_id` literal — đúng loại giá trị mà trust boundary tồn tại để chặn.
# Đường sửa lỗi dựng task này từ kết quả ĐÃ CHẠY, không từ câu người dùng gõ.
AGENT_FORBIDDEN_TOOLS: frozenset[str] = frozenset(
    {
        "register_resident",
        "search_properties",
        "change_parking_zone",
        "cancel_property_viewing",
        "cancel_parking",
        "cancel_maintenance",
        "cancel_move",
        "cancel_shuttle",
    }
)

AGENT_REACHABLE_TOOLS: frozenset[str] = PROVIDER_TOOLS - AGENT_FORBIDDEN_TOOLS
