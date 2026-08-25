"""Lắp kế hoạch từ danh sách dịch vụ. Không gọi model, không đoán.

Owner: Thành Bảo (Decision layer)
File: src/agents/plan_assembly.py

VÌ SAO MODULE NÀY TỒN TẠI

Đo trên `llm_usage` của stack demo, 86 lượt Planner thật của người dùng:

    plan     trung vị 32,98s   p90 78,28s   tổng 3390s   ← 89% thời gian gọi model
    respond  trung vị  1,55s   p90  1,93s   tổng  425s

    latency_ms = 161 + 7,71 × token_model_sinh          R² = 0,988

161 mili-giây là TẤT CẢ phần không phải model: mạng, tầng HTTP, database, và
xử lý một prompt 12.000 token. Prompt gần như miễn phí — lượt nhanh nhất nạp
12.708 token vào và xong trong 1,38 s. Đắt là lượt model NGHĨ, 7,71 ms một
token, từ 28 tới 20.593 token.

Và nó nghĩ lại những thứ không bao giờ đổi. Một hình dạng kế hoạch xuất hiện
19 lần và tốn 738 giây; trong 54 kế hoạch chỉ có 6 hình dạng là duy nhất. 24
workflow cùng hình dạng chỉ khác nhau ở THỨ TỰ NHÃN — cùng một đồ thị, xáo T1
với T2.

BẢNG PHỤ THUỘC KHÔNG PHẢI DO TÔI ĐẶT RA

Nó suy ra từ bảng input→output trong `planner_prompt.py`: một tool phụ thuộc
tool nào sinh ra input bắt buộc của nó. Đúng ba cạnh, không hơn.

Dựng lại 54 kế hoạch đã ghi bằng bảng ấy:

    thứ tự các bước   38/38      depends_on   38/38      InputRef   149/149

Đối chứng âm, để biết phép so sánh không rỗng:

    xáo thứ tự tool trước khi lắp   →  3/38 khớp
    bỏ bảng, trả đồ thị rỗng        →  0/38 khớp

16/54 kế hoạch có đồ thị rỗng; khớp với chúng là tầm thường nên đã tách khỏi
con số trên.

MODULE NÀY KHÔNG QUYẾT ĐỊNH CÁI GÌ ĐƯỢC CHẠY

Nó chỉ lắp. Kế hoạch lắp xong đi qua đúng `TaskPlanValidator` mà kế hoạch của
Planner đi qua — cùng một cửa, cùng luật: tool được phép, ô bắt buộc, ngày quá
khứ, chân trời, enum, khoá cấm. Đo được một ca thật: model đọc "từ 5/9" thành
`2023-09-05`, đủ ô và đúng định dạng nên lọt mọi phép kiểm hình thức, và
`TaskPlanValidator` chặn bằng "has booking_date in the past". Đó là lý do
module này không được có cổng kiểm riêng.
"""

from __future__ import annotations

from typing import Any

from src.common.task_plan import InputRef, Task, TaskPlan

# Tool nào sinh ra field nào — CHỈ những field mà một tool khác dùng làm input.
# Chép từ cột "output" của bảng trong `planner_prompt.py`.
SINH_RA: dict[str, frozenset[str]] = {
    "register_vehicle": frozenset({"vehicle_id"}),
    "book_parking": frozenset({"booking_id", "amount", "currency"}),
    "schedule_property_viewing": frozenset({"viewing_id"}),
}

# Tool nào CẦN field nào do tool khác sinh ra. Chép từ cột "input bắt buộc".
CAN_TU_BUOC_TRUOC: dict[str, frozenset[str]] = {
    "book_parking": frozenset({"vehicle_id"}),
    "pay_fee": frozenset({"booking_id", "amount", "currency"}),
    "book_shuttle": frozenset({"viewing_id"}),
}

# Ô người dùng phải cung cấp, theo tool. Chép từ `TaskPlanValidator.REQUIRED_INPUTS`
# trừ những ô đến từ bước trước (đã nằm ở `CAN_TU_BUOC_TRUOC`) và `resident_id`
# — cái đó đến từ tài khoản, không từ câu người dùng gõ.
O_NGUOI_DUNG: dict[str, tuple[str, ...]] = {
    "schedule_property_viewing": ("project_id", "viewing_date", "viewing_time"),
    "register_property_interest": (
        "project_id",
        "interest_type",
        "preferred_contact_time",
        "consent",
    ),
    "create_maintenance_request": (
        "issue_type",
        "description",
        "location",
        "preferred_date",
        "preferred_time",
    ),
    "schedule_move": (
        "move_date",
        "move_time",
        "needs_elevator",
        "needs_loading_support",
        "move_vehicle",
    ),
    "register_vehicle": ("resident_id", "plate_number", "vehicle_type"),
    "book_parking": ("booking_date", "parking_zone"),
    "book_shuttle": ("tour_date", "passenger_count"),
    "pay_fee": (),
}

# Hệ quả bắt buộc — code quyết định, KHÔNG hỏi model.
#
# Đo trên 54 kế hoạch đã ghi: 37 kế hoạch có `book_parking`, cả 37 đều có
# `pay_fee`, và không có `pay_fee` nào đứng một mình. Không ngoại lệ nào theo
# cả hai chiều.
#
# Để `pay_fee` trên thực đơn của model thì độ chính xác chọn dịch vụ tụt từ
# 96% xuống 65%: bảy trên tám ca lệch là cùng một lỗi, model quên nó. Ba mươi
# mốt điểm phần trăm sai số do ta tự tạo ra khi hỏi model thứ code đã biết chắc.
HE_QUA: dict[str, tuple[str, ...]] = {
    "book_parking": ("register_vehicle", "pay_fee"),
}

# Ô SAO CHÉP từ một bước khác — giá trị hiển nhiên tới mức không ai nói ra.
#
# `(tool, ô)  ->  (tool nguồn, ô nguồn)`
#
# Khác `CAN_TU_BUOC_TRUOC`: ở đó giá trị là KẾT QUẢ bước trước sinh ra lúc chạy
# (`InputRef`). Ở đây là một giá trị NGƯỜI DÙNG đã cung cấp cho bước khác, chép
# sang lúc lắp kế hoạch.
#
# Ca thật, workflow 8d4cd25c: "Đặt lịch tham quan … ngày 2026-08-27 lúc 11:30 xe
# đưa đón cho 1 khách". Không câu nào nói ngày xe đón — vì nó hiển nhiên là ngày
# đi xem. Đường nhanh bị cấm suy đoán nên để trống, kế hoạch trượt Validator, và
# người dùng chờ `plan 75,13s` cho một thứ suy ra được bằng một phép gán.
#
# Kiểm trên mọi kế hoạch đã ghi có cả hai bước: 4/4 lần `tour_date` bằng đúng
# `viewing_date`, không lần nào lệch.
SAO_CHEP_TU: dict[tuple[str, str], tuple[str, str]] = {
    ("book_shuttle", "tour_date"): ("schedule_property_viewing", "viewing_date"),
}


def _them_he_qua(tools: list[str]) -> list[str]:
    ra = list(dict.fromkeys(tools))
    for tool in list(ra):
        for keo_theo in HE_QUA.get(tool, ()):
            if keo_theo not in ra:
                ra.append(keo_theo)
    return ra


def _sap_topo(tools: list[str]) -> list[str]:
    """Tool sinh ra field phải đứng TRƯỚC tool cần field đó.

    Nhờ bước này, tầng chọn dịch vụ chỉ cần trả về một TẬP — không phải trả về
    thứ tự. Đo được: cho tập không thứ tự, code sắp ra đúng 38/38 đồ thị Planner
    đã sinh; xáo thứ tự rồi bỏ qua bước sắp thì chỉ còn 3/38.
    """
    con = list(tools)
    ra: list[str] = []
    da_co: set[str] = set()
    while con:
        for tool in con:
            if not (CAN_TU_BUOC_TRUOC.get(tool, frozenset()) - da_co):
                ra.append(tool)
                da_co |= SINH_RA.get(tool, frozenset())
                con.remove(tool)
                break
        else:
            # Chu trình hoặc phụ thuộc không thoả được. Không tự gỡ — lấy phần
            # tử đầu và để `TaskPlanValidator` từ chối kế hoạch ở cửa chung.
            tool = con.pop(0)
            ra.append(tool)
            da_co |= SINH_RA.get(tool, frozenset())
    return ra


def assemble_plan(goal: str, tools: list[str], values: dict[str, Any]) -> TaskPlan:
    """Kế hoạch cho `tools`, giá trị lấy từ `values`. Không gọi model.

    `values` là phẳng: tên ô → giá trị. Mỗi task chỉ nhận những ô THUỘC VỀ NÓ
    theo `O_NGUOI_DUNG` — một `parking_zone` lạc trong `values` không được rơi
    vào task tham quan.

    Ô thiếu thì để trống, KHÔNG điền mặc định. Kế hoạch thiếu ô sẽ trượt
    `TaskPlanValidator` ở cửa chung và caller rơi về Planner đầy đủ. Chế độ
    hỏng là CHẬM, không phải SAI — đó là điều kiện để module này tồn tại.
    """
    thu_tu = _sap_topo(_them_he_qua(list(tools)))
    nhan = {tool: f"T{i + 1}" for i, tool in enumerate(thu_tu)}

    tasks: list[Task] = []
    for tool in thu_tu:
        can = CAN_TU_BUOC_TRUOC.get(tool, frozenset())
        phu_thuoc: dict[str, str] = {}
        for field in can:
            nguon = [t for t in thu_tu[: thu_tu.index(tool)] if field in SINH_RA.get(t, frozenset())]
            if nguon:
                phu_thuoc[field] = nguon[-1]

        dau_vao: dict[str, Any] = {
            field: InputRef(from_task=nhan[nguon], field=field)
            for field, nguon in phu_thuoc.items()
        }
        for o in O_NGUOI_DUNG.get(tool, ()):
            if values.get(o) is not None:
                dau_vao[o] = values[o]
                continue
            # Người dùng KHÔNG nói thì mới chép. Nói rõ một ngày khác cho xe đón
            # là chuyện hợp lệ, và tự sửa lời họ thì tệ hơn để trống.
            nguon = SAO_CHEP_TU.get((tool, o))
            if nguon and nguon[0] in thu_tu and values.get(nguon[1]) is not None:
                dau_vao[o] = values[nguon[1]]

        tasks.append(
            Task(
                task_id=nhan[tool],
                tool=tool,
                depends_on=sorted({nhan[n] for n in phu_thuoc.values()}),
                input=dau_vao,
            )
        )
    return TaskPlan(goal=goal, tasks=tasks)
