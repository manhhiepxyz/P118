"""Đường nhanh: một lượt gọi rẻ thay cho một lượt lập kế hoạch đắt.

Owner: Thành Bảo (Decision layer)
File: src/agents/fast_lane.py

VÌ SAO

Đo trên `llm_usage` của stack demo, 86 lượt Planner thật:

    plan  trung vị 32,98s   p90 78,28s   tổng 3390s   ← 89% toàn bộ thời gian model

Một workflow 5 dịch vụ của người dùng mất 146 giây, trong đó 101 giây là MỘT
lượt Planner sinh 13.593 token. Phần cấu trúc của kết quả ấy là cơ học —
`plan_assembly` dựng lại 38/38 đồ thị và 149/149 InputRef của các kế hoạch đã
ghi. Cái model thật sự cần trả lời chỉ là: yêu cầu này cần DỊCH VỤ nào, và
GIÁ TRỊ là gì.

Đo lượt gọi rẻ ấy trên 54 goal đã ghi:

    tập dịch vụ khớp   52/54 (96%)
    ô giá trị đúng     325/363 (90%)
    kế hoạch hoàn hảo  38/54 (70%)
    độ trễ             trung vị 1,56s · p90 1,83s · max 2,07s

`pay_fee` KHÔNG nằm trong thực đơn của model. Nó là hệ quả của `book_parking`
(37/37 kế hoạch, không ngoại lệ hai chiều), và để nó trên thực đơn làm độ chính
xác tụt 96% → 65%. `plan_assembly` thêm nó.

HỎNG THÌ CHẬM, KHÔNG HỎNG THÀNH SAI

`plan()` trả `None` ở MỌI nhánh không chắc chắn, và caller đi tiếp vào Planner
đầy đủ như hôm nay. Ca buộc phải như vậy, đo được nguyên văn:

    "cho mình xin cái chỗ để xe khu B từ 5/9 nhé, xe wave biển 51H-12345"
       → booking_date = "2023-09-05"

Model tự đoán năm và trượt ba năm. Đủ ô, đúng định dạng, lọt mọi phép kiểm
hình thức. Nên module này KHÔNG có cổng kiểm riêng: kế hoạch lắp xong đi qua
đúng `TaskPlanValidator` mà kế hoạch Planner đi qua, và nó chặn bằng "has
booking_date in the past".

Trên 14 câu khó cố ý (Anh, khẩu ngữ, trộn ngôn ngữ, ngoài phạm vi, mơ hồ): 3
câu đi đường nhanh, cả 3 đúng, 0 kế hoạch sai lọt tới thực thi. Bốn câu ngoài
phạm vi đều trả `tools` rỗng.

MODULE NÀY KHÔNG BAO GIỜ TỪ CHỐI

Bộ phân loại rẻ không biết ranh giới phạm vi — đo được "tôi muốn ký hợp đồng
thuê căn p5" bị xếp thành `register_property_interest`, dù Agent không ký hợp
đồng. Vô hại vì thiếu ô nên rơi về Planner, nhưng nó là lý do `None` ở đây chỉ
có nghĩa "không xử lý được", KHÔNG BAO GIỜ có nghĩa "từ chối". Việc từ chối
thuộc về lane cũ.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from src.agents.plan_assembly import assemble_plan
from src.agents.validator import TaskPlanValidator
from src.common.projects import resolve_project_id
from src.common.task_plan import TaskPlan
from src.monitoring.usage_tracker import (
    current_usage_context,
    reset_usage_context,
    usage_context,
)

logger = logging.getLogger(__name__)

# Thực đơn model được chọn. `pay_fee` KHÔNG có mặt — xem docstring module.
MENU = (
    "schedule_property_viewing",
    "register_property_interest",
    "create_maintenance_request",
    "schedule_move",
    "register_vehicle",
    "book_parking",
    "book_shuttle",
)


class _DuDoan(BaseModel):
    """Output của model. Enum đóng ở mọi ô có tập giá trị hữu hạn."""

    model_config = ConfigDict(extra="forbid")

    tools: list[Literal[MENU]] = []
    project_name: str | None = None
    viewing_date: str | None = None
    viewing_time: str | None = None
    plate_number: str | None = None
    vehicle_type: Literal["car", "motorcycle"] | None = None
    booking_date: str | None = None
    parking_zone: Literal["ZONE_A", "ZONE_B"] | None = None
    issue_type: Literal["air_conditioning", "plumbing", "electrical", "other"] | None = None
    description: str | None = None
    location: str | None = None
    preferred_date: str | None = None
    preferred_time: str | None = None
    interest_type: Literal["rent", "buy"] | None = None
    preferred_contact_time: str | None = None
    consent: bool | None = None
    move_date: str | None = None
    move_time: str | None = None
    move_vehicle: Literal["van", "truck"] | None = None
    needs_elevator: bool | None = None
    needs_loading_support: bool | None = None
    tour_date: str | None = None
    passenger_count: int | None = None


# `json_mode` KHÔNG gửi schema cho model — chỉ nói "trả JSON". Đo được: bỏ
# khung ra khỏi prompt thì model tự bịa tên trường (`{"service": ..., "date":
# ...}`) và 54/54 lượt đều trượt schema. Khung phải nằm trong prompt.
HUONG_DAN = """Đọc yêu cầu của cư dân, chọn DỊCH VỤ và trích GIÁ TRỊ. KHÔNG lập kế hoạch, KHÔNG sắp thứ tự, KHÔNG lo phí.

| dịch vụ | dùng khi |
|---|---|
| schedule_property_viewing | đặt lịch tham quan / xem căn |
| register_property_interest | đăng ký quan tâm, nhận tư vấn, muốn được liên hệ |
| create_maintenance_request | báo hỏng, sửa chữa, bảo trì |
| schedule_move | đặt lịch chuyển nhà |
| register_vehicle | đăng ký xe theo biển số |
| book_parking | giữ/đặt chỗ đỗ xe (luôn kèm register_vehicle) |
| book_shuttle | đặt xe đưa đón đi tham quan |

Yêu cầu không thuộc bảng trên thì trả "tools": [] — KHÔNG chọn bừa một dịch vụ gần giống.
CHỈ điền ô nào yêu cầu NÓI RÕ. Không suy đoán, không điền mặc định. Ô không có thì để null.
Ngày YYYY-MM-DD, và chỉ điền khi yêu cầu ghi rõ năm. Giờ HH:MM.

Trả về ĐÚNG khung JSON này, đủ mọi khoá:
{
 "tools": [], "project_name": null,
 "viewing_date": null, "viewing_time": null,
 "plate_number": null, "vehicle_type": null,
 "booking_date": null, "parking_zone": null,
 "issue_type": null, "description": null, "location": null,
 "preferred_date": null, "preferred_time": null,
 "interest_type": null, "preferred_contact_time": null, "consent": null,
 "move_date": null, "move_time": null, "move_vehicle": null,
 "needs_elevator": null, "needs_loading_support": null,
 "tour_date": null, "passenger_count": null
}
vehicle_type: "car" | "motorcycle".  parking_zone: "ZONE_A" | "ZONE_B" ("Khu A"->ZONE_A).
issue_type: "air_conditioning" | "plumbing" | "electrical" | "other".
interest_type: "rent" ("Thuê") | "buy" ("Mua").  move_vehicle: "van" | "truck"."""

# Ô lấy từ TÀI KHOẢN, không từ câu người dùng gõ. Model không được bịa ra chúng
# — `resident_id` là danh tính cư dân, và một chuỗi model tự nghĩ ra sẽ đăng ký
# xe cho người khác.
O_TU_NGU_CANH = ("resident_id",)


class FastLane:
    """Thử lập kế hoạch bằng một lượt gọi rẻ. `None` = không chắc, dùng Planner."""

    def __init__(self, llm: Any, *, structured_output_method: str | None = None) -> None:
        # Buộc schema MUỘN, không phải trong `__init__`.
        #
        # Composition root dựng đối tượng này cho MỌI workflow, kể cả những
        # workflow không bao giờ tới tầng lập kế hoạch. Làm việc có thể hỏng ở
        # đây nghĩa là một thay đổi ở tầng LLM làm sập cả những luồng không
        # dùng tới nó — `tests/test_demo_llm_runtime.py` bắt đúng chuyện này.
        self._llm_tho = llm
        self._method = structured_output_method
        self._llm: Any = None

    def _buoc(self) -> Any:
        if self._llm is None:
            self._llm = self._llm_tho.with_structured_output(_DuDoan, method=self._method)
        return self._llm

    async def plan(
        self,
        goal: str,
        existing_context: dict[str, Any] | None = None,
        user_answers: dict[str, Any] | None = None,
    ) -> TaskPlan | None:
        if not (goal or "").strip():
            return None
        # Ghi usage dưới stage RIÊNG.
        #
        # Không có dòng này thì lượt gọi rẻ rơi vào `stage='plan'` và trộn lẫn
        # với lượt Planner thật — làm hỏng đúng phép đo đã dẫn tới module này
        # (trung vị 32,98s, p90 78,28s). Tách ra thì đo được cả tỉ lệ đi đường
        # nhanh lẫn chi phí thật của nó ngay trên dữ liệu production.
        hien_tai = current_usage_context() or {}
        token = usage_context(
            workflow_id=hien_tai.get("workflow_id"),
            stage="fast_plan",
            run_id=hien_tai.get("run_id"),
        )
        try:
            du_doan = await self._buoc().ainvoke([("system", HUONG_DAN), ("human", goal)])
        except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
            # Không để lỗi provider thoát lên người dùng: đường cũ vẫn chạy được.
            logger.warning("fast lane: không phân loại được (%s)", type(exc).__name__)
            return None
        finally:
            reset_usage_context(token)

        tools = list(du_doan.tools)
        if not tools:
            return None

        values: dict[str, Any] = {k: v for k, v in du_doan.model_dump().items() if k != "tools" and v is not None}
        ten_du_an = values.pop("project_name", None)
        if ten_du_an:
            ma = resolve_project_id(str(ten_du_an))
            # Tên lạ KHÔNG được ánh xạ về một dự án mặc định. Thiếu `project_id`
            # thì kế hoạch trượt Validator và rơi về Planner — đúng ý muốn.
            if ma:
                values["project_id"] = ma

        for o in O_TU_NGU_CANH:
            gia_tri = (existing_context or {}).get(o)
            if gia_tri is not None:
                values[o] = gia_tri

        # Câu trả lời người dùng VỪA gõ ở lượt hỏi lại, đặt SAU cùng nên nó
        # THẮNG giá trị model đọc được từ goal.
        #
        # `/continue` giữ NGUYÊN goal cũ và để câu trả lời mới ở `user_answers`
        # (xem `routes.py`), vì goal là điều họ nói LÚC ĐẦU còn `user_answers`
        # là điều họ nói SAU KHI biết còn thiếu gì. Không đọc nó ở đây thì
        # đường nhanh luôn thiếu đúng cái ô người dùng vừa điền — kế hoạch
        # trượt Validator và rơi về Planner đầy đủ, MỌI LẦN.
        #
        # Đo được trên `llm_usage` trước khi sửa:
        #
        #     workflow gốc (/start)      53 lượt → 20 về đích (38%)
        #     workflow con (/continue)    4 lượt →  0 về đích (0%), 44,1s
        #
        # Lượt hỏi lại — đúng lúc hệ thống đã biết CHÍNH XÁC thiếu ô nào và
        # người dùng vừa điền nốt — lại là lượt chậm nhất.
        #
        # Giá trị `None` KHÔNG ghi đè: một ô chưa trả lời không được phép xoá
        # giá trị goal đã nói rõ. Và mọi thứ vẫn đi qua `TaskPlanValidator` ở
        # dưới — đây là thêm một NGUỒN giá trị, không phải nới một cổng kiểm.
        for o, gia_tri in (user_answers or {}).items():
            if gia_tri is not None:
                values[o] = gia_tri

        try:
            ke_hoach = assemble_plan(goal, tools, values)
            TaskPlanValidator.validate(ke_hoach)
        except Exception as exc:  # noqa: BLE001 - mọi lý do đều dẫn về Planner
            logger.info("fast lane: nhường Planner (%s)", type(exc).__name__)
            return None
        return ke_hoach
