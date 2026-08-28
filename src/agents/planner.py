"""LLM Planner — mục tiêu ngôn ngữ tự nhiên thành canonical TaskPlan.

Ranh giới:
  - LLM đề xuất kế hoạch; nó KHÔNG thực thi gì cả.
  - Output thực thi được duy nhất là `TaskPlan` trong `src/common/task_plan.py`.
    Module này không định nghĩa schema kế hoạch riêng.
  - Planner KHÔNG thay thế `TaskPlanValidator`. Planner graph luôn đưa plan qua
    Validator trước khi tới execution boundary.
  - Planner không gọi tầng thực thi.

Bảo mật:
  - Không log goal, existing context hay raw LLM response.
  - Exception public không bao giờ echo nội dung LLM trả về hay dữ liệu người dùng.
  - Khi READY, `TaskPlan.goal` được đặt lại bằng goal gốc của caller — không tin
    LLM viết lại mục tiêu của người dùng.
  - Câu hỏi gửi người dùng do code dựng deterministic từ allowlist. Văn bản do
    LLM sinh không bao giờ đi thẳng ra ngoài, nên không thể echo PII hay secret.
  - `booking_id`/`amount`/`currency` của `pay_fee` chỉ được lấy từ InputRef trỏ
    tới `book_parking`, hoặc từ `existing_context` do backend dựng. Số tiền
    người dùng tự khai trong goal không phải nguồn authoritative.

Ranh giới HITL:
  `pay_fee` là action tài chính và PHẢI qua approval ở runtime trước khi
  Executor gọi Payment API. Planner chỉ lập kế hoạch; nó không phải là chỗ
  cưỡng chế approval. `PaymentApprovalBoundary` và payment-decision API thực
  hiện việc dừng/tiếp tục này ngoài quyền quyết định của LLM.
  Việc Planner trả READY cho một plan có `pay_fee` KHÔNG có nghĩa là được phép
  thanh toán ngay.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol

from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.agents.prompts.planner_prompt import (
    PLANNER_SYSTEM_PROMPT,
    build_planner_user_message,
)
from src.common.agent_tool_policy import AGENT_FORBIDDEN_TOOLS, AGENT_REACHABLE_TOOLS
from src.common.failure_messages import spoken_forms
from src.common.task_plan import InputRef, TaskPlan
from src.common.tool_contract import TOOL_CONTRACTS

logger = logging.getLogger(__name__)

# QUESTION: câu người dùng gõ là một CÂU HỎI về dịch vụ, không phải việc cần làm.
#
# Thiếu trạng thái này, mọi câu hỏi đều bị ép vào khuôn "lập kế hoạch hoặc là
# thiếu dữ liệu", và cái thứ hai hiện ra với người dùng thành "thông tin bạn
# cung cấp chưa hợp lệ" — đổ lỗi cho họ vì đã hỏi. Đã vá bằng từ khoá năm lần
# (hỏi năng lực, hỏi cách làm, xác minh căn hộ, hỏi ngày, hỏi quyền) và lần nào
# cũng chỉ bịt được đúng những cách hỏi mình nghĩ ra được.
#
# QUAN TRỌNG: `QUESTION` KHÔNG mang theo câu trả lời. Planner phân loại, không
# viết — Response Agent vẫn là nơi duy nhất soạn chữ cho người dùng, và nó đã có
# guard. Trộn hai vai lại là mở một đường cho LLM nói thẳng ra ngoài mà không ai kiểm.
PlannerStatus = Literal["READY", "NEEDS_INFORMATION", "QUESTION"]

# Dùng khi mục tiêu chứa việc nằm ngoài 6 tool được hỗ trợ. Đây là control
# value, không phải một field nghiệp vụ.
UNSUPPORTED_GOAL_FIELD = "supported_goal"

# Dùng khi thanh toán độc lập nhưng hệ thống chưa có báo phí tin cậy.
#
# Đây KHÔNG phải lời mời người dùng nhập số tiền. amount/currency là dữ liệu
# authoritative của Booking/Payment Provider; hỏi người dùng số tiền là mở
# đường cho họ tự khai giá trị thanh toán. Control value này nghĩa là "hệ thống
# chưa lấy được báo phí", không phải "thiếu input của người dùng".
PAYMENT_QUOTE_REQUIRED_FIELD = "payment_quote"

# Allowlist đóng cho `missing_fields`, kèm nhãn tiếng Việt để dựng câu hỏi.
# LLM chỉ được chọn tên trong đây; mọi thứ khác bị từ chối. Nhờ khớp chính xác
# nên chuỗi rỗng, whitespace, URL và credential marker đều tự động bị loại.
# KHÔNG có `full_name`/`apartment_code`/`residential_area`: chúng chỉ dùng để
# lập hồ sơ cư dân, việc nằm ngoài Agent. Giữ chúng ở đây nghĩa là Planner vẫn
# hỏi được, và giao diện thì không có ô nhập nào cho chúng.
MISSING_FIELD_LABELS: dict[str, str] = {
    # `residential_area`, `transaction_type`, `property_type`, `max_price` KHÔNG
    # có nhãn ở đây, và không phải vì quên: cả bốn chỉ thuộc `search_properties`
    # và `register_resident`, hai tool đã nằm ngoài `PLANNER_ALLOWED_TOOLS`.
    #
    # Một nhãn tồn tại nghĩa là có người định hiển thị nó cho người dùng. Giữ
    # chúng lại thì Planner vẫn HỎI được — và câu trả lời không đi đâu cả, vì
    # bộ đọc cho chúng đã bị gỡ cùng với dịch vụ. Đo được: workflow
    # clarification hỏi "ngân sách tối đa", người dùng đáp, và vòng lặp lặp lại
    # không có lối ra.
    # Nhãn nói "tên dự án", không nói "mã dự án": người dùng không biết PRJ-xxx.
    # Việc đổi tên field sang `project_name` cho client là việc của biên API
    # (`_to_public_missing_fields`); Planner không cần biết tới alias đó.
    "project_id": "tên dự án trong danh sách được hỗ trợ; tên khu vực chung chưa đủ",
    "viewing_date": "ngày muốn tham quan",
    "viewing_time": "giờ muốn tham quan",
    "interest_type": "nhu cầu mua, thuê hay chỉ nhận tư vấn",
    "preferred_contact_time": "giờ muốn được liên hệ",
    "consent": "đồng ý để bộ phận tư vấn liên hệ",
    "issue_type": "hạng mục cần bảo trì",
    "description": "mô tả sự cố",
    "location": "vị trí cần sửa chữa",
    "preferred_date": "ngày muốn bảo trì",
    "preferred_time": "giờ muốn bảo trì",
    "move_date": "ngày muốn chuyển nhà",
    "move_time": "giờ muốn chuyển nhà",
    "move_origin_id": "nơi bạn hiện đang ở (địa chỉ hoặc tên tòa nhà)",
    "move_destination_id": "nơi bạn muốn chuyển đến (địa chỉ hoặc tên tòa nhà)",
    "move_size": "quy mô đồ cần chuyển (ít, vừa hoặc nhiều)",
    "needs_elevator": "có cần đăng ký thang máy hay không",
    "needs_loading_support": "có cần hỗ trợ bốc dỡ hay không",
    "move_vehicle": "phương tiện chuyển nhà (none, van hoặc truck)",
    "plate_number": "biển số xe",
    "vehicle_type": "loại xe (ô tô hoặc xe máy)",
    "booking_date": "ngày muốn đặt chỗ",
    "parking_zone": "khu vực đỗ xe (Khu A hoặc Khu B)",
    # `resident_id`, `vehicle_id`, `booking_id`, `amount`, `currency` KHÔNG có
    # nhãn, và không phải vì quên. Một nhãn tồn tại nghĩa là có người định HIỆN
    # nó ra cho người dùng, và cả năm đều là dữ liệu của provider hoặc của phiên
    # đăng nhập. Đo được trước khi gỡ — bốn câu hỏi này dựng và hiển thị được:
    #
    #     "Mình cần thêm thông tin: mã cư dân."
    #     "Mình cần thêm thông tin: số tiền và loại tiền tệ."
    #
    # Câu thứ hai là mời chính người phải trả tiền khai số tiền phải trả.
    #
    # `vehicle_id` có đường riêng: nó được HẠ CẤP thành plate_number +
    # vehicle_type (xem `_DOWNGRADABLE_MISSING_FIELDS`), nên nó không bao giờ
    # cần một nhãn của riêng mình.
    "tour_date": "ngày muốn đặt xe tham quan",
    "passenger_count": "số người đi xe (tối thiểu 1, tối đa 30)",
}

# `Literal` không được kiểm tra lúc chạy — giữ bản runtime để `PlannerResult`
# và `Planner` cùng dựa vào một nguồn.
_VALID_STATUSES: frozenset[str] = frozenset({"READY", "NEEDS_INFORMATION", "QUESTION"})

# Hai tên cũ, giữ lại vì nhiều chỗ đã import chúng. Định nghĩa THẬT nằm ở
# `src/common/agent_tool_policy.py` — xem docstring ở đó cho lý do từng tool bị
# cấm, và vì sao chính sách không thể sống trong `agents/`.
PLANNER_FORBIDDEN_TOOLS: frozenset[str] = AGENT_FORBIDDEN_TOOLS
PLANNER_ALLOWED_TOOLS: frozenset[str] = AGENT_REACHABLE_TOOLS


def _fields_of_tools(tools: frozenset[str]) -> frozenset[str]:
    return frozenset(name for tool, contract in TOOL_CONTRACTS.items() if tool in tools for name in contract.inputs)


# Field Planner được phép NÊU là còn thiếu. Allowlist DƯƠNG, suy từ đúng những
# tool nó lập được — không từ `MISSING_FIELD_LABELS`, và không từ toàn bộ
# contract provider.
#
# Bản cũ lấy `frozenset(MISSING_FIELD_LABELS)`, tức hai bảng cùng trả lời một
# câu hỏi ở hai chỗ. Chúng lệch nhau ngay lần đầu có tool bị loại: kế hoạch
# không tạo được nữa, nhưng câu hỏi thì vẫn hỏi được.
#
# Hai field điều khiển đi kèm vì chúng không thuộc tool nào — chúng là cách
# Planner nói "mục tiêu ngoài phạm vi" / "cần báo giá trước".
# Dữ liệu CÓ THẨM QUYỀN. Model có thể nêu tên chúng — nó đọc bảng tool và thấy
# chúng là input thật — nhưng chúng không bao giờ đi ra tới người dùng.
#
# Nguồn của từng thứ, và vì sao câu trả lời của người dùng không được là nguồn:
#
#   resident_id            tài khoản đã xác minh
#   booking_id/amount/     `InputRef` từ `book_parking`, hoặc ngữ cảnh backend
#   currency               — hỏi khách số tiền là để người trả tiền tự khai
#   viewing_id             kết quả của `schedule_property_viewing`
AUTHORITATIVE_MISSING_FIELDS: frozenset[str] = frozenset(
    {"resident_id", "booking_id", "amount", "currency", "viewing_id", "owner_user_id", "workflow_id"}
)

# Alias nội bộ CÓ đường hạ cấp deterministic sang thứ người dùng hiểu được.
#
# Đây là lý do `RAW_MODEL_MISSING_FIELDS` tồn tại tách khỏi `PUBLIC_...`: model
# nêu một cái tên nội bộ, và code đổi nó thành câu hỏi đúng — chứ không phải
# từ chối rồi tiêu một lượt gọi.
_DOWNGRADABLE_MISSING_FIELDS: dict[str, tuple[str, ...]] = {
    "vehicle_id": ("plate_number", "vehicle_type"),
}

# Field ĐI RA tới người dùng. Hai điều kiện, cả hai đều cần:
#
#   với-tới-được  — thuộc input của một tool Agent lập được
#   có nhãn       — `build_question` ghép câu hỏi TỪ `MISSING_FIELD_LABELS`
#
# Giao của hai tập, không phải hợp. Trừ tiếp phần có thẩm quyền.
PUBLIC_MISSING_FIELDS: frozenset[str] = (
    (_fields_of_tools(AGENT_REACHABLE_TOOLS) & frozenset(MISSING_FIELD_LABELS)) - AUTHORITATIVE_MISSING_FIELDS
) | {
    UNSUPPORTED_GOAL_FIELD,
    PAYMENT_QUOTE_REQUIRED_FIELD,
}

# Tên model được phép NÊU. Rộng hơn `PUBLIC_MISSING_FIELDS` đúng hai phần, và cả
# hai đều có đường xử lý deterministic:
#
#   alias hạ cấp được    → đổi thành câu hỏi công khai
#   dữ liệu có thẩm quyền → đổi thành `payment_quote`, hoặc từ chối fail-closed
#
# Nhận chúng ở schema KHÔNG phải nới lỏng: nó là cách phân biệt "model nói một
# cái tên nội bộ" với "model bịa một cái tên", để cái thứ nhất được xử lý đúng
# thay vì tiêu một lượt sửa lỗi.
RAW_MODEL_MISSING_FIELDS: frozenset[str] = (
    PUBLIC_MISSING_FIELDS | frozenset(_DOWNGRADABLE_MISSING_FIELDS) | AUTHORITATIVE_MISSING_FIELDS
)

# Tên cũ, giữ cho các chỗ đã import. Nó là allowlist của SCHEMA.
_ALLOWED_MISSING_FIELDS: frozenset[str] = RAW_MODEL_MISSING_FIELDS

# Field có thẩm quyền thuộc PHÍA THANH TOÁN. Model nêu chúng nghĩa là nó thiếu
# báo giá — một sự cố phía hệ thống, không phải thiếu thông tin của khách.
_PAYMENT_CONTEXT_FIELDS: frozenset[str] = frozenset({"booking_id", "amount", "currency"})


# --- Trust boundary cho thanh toán ------------------------------------------
#
# `booking_id`, `amount`, `currency` của pay_fee là dữ liệu authoritative của
# Booking/Payment Provider. Chỉ đúng HAI nguồn hợp lệ:
#
#   a) InputRef trỏ tới một task book_parking trong cùng plan, hoặc
#   b) `existing_context` do backend cung cấp.
#
# Số tiền người dùng viết trong câu goal KHÔNG phải nguồn authoritative và
# không bao giờ được ghi đè trusted context — nếu không, người dùng chỉ cần
# viết "thanh toán 1 đồng" là tự đặt được giá trị giao dịch.
#
# Phân chia trách nhiệm: API boundary chịu trách nhiệm dựng `existing_context`
# tin cậy (tra booking từ Provider rồi mới đưa vào). Planner KHÔNG tự xác thực
# dict do caller tùy ý truyền — nó chỉ đảm bảo không có nguồn thứ ba nào lọt vào.
_TRUSTED_PAYMENT_FIELDS: tuple[str, ...] = ("booking_id", "amount", "currency")

# Câu này LIỆT KÊ những gì Agent làm được, nên nó phải khớp
# `PLANNER_ALLOWED_TOOLS` — không hơn một dịch vụ nào.
#
# "tìm nhà" và "đăng ký cư dân" đã bị gỡ khỏi câu: cả hai nằm ngoài phạm vi
# Agent (`PLANNER_FORBIDDEN_TOOLS`). Hứa một việc rồi từ chối chính việc ấy ở
# lượt sau là cách chắc chắn nhất để người dùng mất niềm tin vào phần còn lại.
_UNSUPPORTED_GOAL_QUESTION = (
    "Mục tiêu của bạn có phần nằm ngoài các dịch vụ mình hỗ trợ "
    "(đặt lịch xem nhà, đăng ký quan tâm dự án, đăng ký xe, "
    "đặt chỗ đậu xe, bảo trì, chuyển nhà, đặt xe tham quan, thanh toán phí). "
    "Bạn xác nhận chỉ thực hiện những dịch vụ này, hoặc mô tả lại mục tiêu giúp mình nhé?"
)

# Câu này KHÔNG đòi người dùng bất cứ thứ gì, và đó là toàn bộ điểm của nó.
#
# Bản cũ nói "Vui lòng kiểm tra lại mã đặt chỗ hoặc thử lại sau" — nó đẩy trách
# nhiệm `booking_id` sang người dùng, đúng thứ chính sách đã xác định là dữ liệu
# có thẩm quyền: mã ấy do `book_parking` sinh ra, người dùng không thấy, không
# tra được, và không làm gì được với lời khuyên đó.
#
# Thiếu báo phí là sự cố PHÍA HỆ THỐNG. Câu nói ra phải phản ánh đúng vậy —
# không nhắc số tiền, không nhắc mã, không mời bổ sung gì.
_PAYMENT_QUOTE_QUESTION = "Mình chưa lấy được báo phí từ hệ thống đặt chỗ. Bạn vui lòng thử lại sau."


class PlannerError(RuntimeError):
    """Planner không tạo được kết quả dùng được.

    Message luôn là mô tả chung. Không bao giờ chứa raw LLM output, goal hay
    dữ liệu người dùng — những thứ đó có thể mang thông tin nhạy cảm.
    """


# --- Corrective retry -------------------------------------------------------

# Đúng một lần sửa lỗi. Model không tất định: một mẫu sai không nên làm hỏng cả
# request, nhưng sai hai lần liên tiếp thì retry thêm chỉ tốn tiền và thời gian.
_MAX_CORRECTIVE_RETRIES = 1

# Hướng dẫn sửa lỗi theo LOẠI vi phạm. Đây là chuỗi cố định: không nội suy
# goal, existing_context, response cũ hay bất cứ giá trị nào model đã sinh —
# nếu không, retry sẽ trở thành đường tuồn PII/secret ngược lại vào prompt.
_CORRECTIVE_PREAMBLE = "Câu trả lời trước của bạn không hợp lệ. "

# Field mà BACKEND đã validate trước khi đưa vào context (qua form clarification
# hoặc qua chuẩn hoá câu trả lời). Chỉ những field này mới được coi là "đã có
# giá trị" khi kiểm tính nhất quán của model.
#
# Cố ý KHÔNG có `resident_id`, `vehicle_id`, `booking_id`, `amount`, `currency`,
# `owner_user_id`, `workflow_id`: chúng là dữ liệu có thẩm quyền của provider
# hoặc của phiên đăng nhập. Cho câu trả lời người dùng trở thành nguồn của
# chúng là mở lại đúng những lỗ hổng mà trust boundary sinh ra để chặn.
_BACKEND_VALIDATED_FIELDS: frozenset[str] = frozenset(
    {
        "plate_number",
        "vehicle_type",
        "booking_date",
        "parking_zone",
        "project_id",
        "viewing_date",
        "viewing_time",
        "interest_type",
        "preferred_contact_time",
        "consent",
        "issue_type",
        "description",
        "location",
        "preferred_date",
        "preferred_time",
        "move_date",
        "move_time",
        "move_vehicle",
        "needs_elevator",
        "needs_loading_support",
        "tour_date",
        "passenger_count",
    }
)


def _already_supplied(field: str, context: dict[str, Any]) -> bool:
    """Field đã có giá trị dùng được trong context chưa.

    Chuỗi rỗng và khoảng trắng KHÔNG tính là đã có — nếu tính, một giá trị rỗng
    lọt vào context sẽ khiến guard im lặng chấp nhận và người dùng không bao giờ
    được hỏi lại.
    """
    if field not in _BACKEND_VALIDATED_FIELDS or field not in context:
        return False
    value = context[field]
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


# ---------------------------------------------------------------------------
# Không gian kế hoạch của Agent
# ---------------------------------------------------------------------------
#

# Field CHỈ tồn tại để tạo hồ sơ cư dân. Planner hỏi chúng nghĩa là nó đang cố
# onboarding qua TaskPlan — việc mà kiến trúc đã đặt ra ngoài Agent.
#
# `residential_area` KHÔNG có ở đây dù nó cũng là input của `register_resident`:
# nó đồng thời là input BẮT BUỘC của `search_properties`. Chặn nó sẽ phá luồng
# tìm bất động sản hợp lệ — một field dùng chung giữa hai tool thì không thể
# cấm theo tên. Thứ chặn được vòng lặp là `PLANNER_FORBIDDEN_TOOLS`: không có
# `register_resident` trong kế hoạch thì `residential_area` chỉ còn ý nghĩa
# tìm kiếm.
PLANNER_FORBIDDEN_MISSING_FIELDS: frozenset[str] = frozenset({"full_name", "apartment_code"})


_CORRECTIVE_INSTRUCTIONS: dict[str, str] = {
    "FACT_WITHOUT_EVIDENCE": (
        "Trong explicit_facts, bạn nêu một kết luận mà phần 'evidence' không xuất hiện "
        "nguyên văn trong yêu cầu của người dùng. Mỗi mục phải trích đúng một đoạn có "
        "thật trong yêu cầu đó. Nếu không trích được, hãy BỎ mục ấy đi: người dùng chưa "
        "nói rõ, và việc còn thiếu sẽ được hỏi lại."
    ),
    "CONTRADICTORY_FACT": (
        "Trong explicit_facts, bạn đưa hai kết luận trái ngược nhau cho cùng một ô. Mỗi ô "
        "chỉ được xuất hiện MỘT lần. Nếu yêu cầu của người dùng nói nước đôi về ô đó, hãy "
        "bỏ nó khỏi explicit_facts để hệ thống hỏi lại."
    ),
    "FACT_AND_MISSING_CONFLICT": (
        "Bạn vừa nêu một ô trong explicit_facts vừa nêu chính ô đó trong missing_fields. "
        "Hai điều này loại trừ nhau: hoặc người dùng đã nói rõ, hoặc chưa. Chọn một."
    ),
    "FORBIDDEN_PLANNER_TOOL": (
        "Kế hoạch của bạn chứa một bước đăng ký/liên kết hồ sơ cư dân. Việc đó "
        "nằm NGOÀI phạm vi của bạn và do bộ phận quản lý thực hiện. Nếu phần "
        "'Dữ liệu đã có' có resident_id, hãy dùng thẳng giá trị đó. Nếu không có, "
        "đừng lập kế hoạch cho dịch vụ dành riêng cho cư dân."
    ),
    "FORBIDDEN_LINKING_CLARIFICATION": (
        "Bạn hỏi thông tin dùng để tạo hồ sơ cư dân. Không được hỏi những thông "
        "tin đó: hồ sơ cư dân do bộ phận quản lý lập, không thu thập trong hội "
        "thoại này. Chỉ hỏi dữ liệu cần cho chính dịch vụ người dùng yêu cầu."
    ),
    "MISSING_FIELD_NOT_ASKABLE": (
        "Bạn hỏi dữ liệu mà hệ thống tự biết: mã cư dân, mã đặt chỗ, mã lịch xem, "
        "số tiền, loại tiền tệ. Những giá trị này đến từ tài khoản đã xác minh "
        "hoặc từ kết quả của bước trước trong chính kế hoạch — không hỏi người "
        "dùng. Dùng InputRef để lấy chúng từ bước trước, hoặc chỉ nêu những field "
        "người dùng thật sự phải cung cấp."
    ),
    "MISSING_FIELD_ALREADY_PROVIDED": (
        "Bạn nêu field còn thiếu, nhưng những field đó ĐÃ có giá trị trong phần "
        "'Dữ liệu đã có'. Đọc lại phần đó trước khi kết luận thiếu dữ liệu. Nếu "
        "mọi field bắt buộc đều đã có, hãy trả status=READY kèm TaskPlan; chỉ nêu "
        "field thật sự chưa có giá trị nào."
    ),
    "READY_WITH_MISSING_FIELDS": (
        "Bạn trả status=READY nhưng vẫn nêu field còn thiếu. Hai trạng thái này "
        "loại trừ nhau. Rà lại 4 nguồn dữ liệu: field lấy được từ task trước phải "
        "dùng InputRef chứ không đưa vào missing_fields (hay gặp nhất là amount và "
        "currency khi plan đã có book_parking). Nếu lập được kế hoạch thì trả READY "
        "với missing_fields rỗng; nếu thật sự thiếu dữ liệu thì trả "
        "NEEDS_INFORMATION và plan=null."
    ),
    "READY_WITHOUT_PLAN": (
        "Bạn trả status=READY nhưng không kèm plan. READY bắt buộc phải có TaskPlan "
        "đầy đủ. Nếu chưa đủ dữ liệu để lập kế hoạch thì trả NEEDS_INFORMATION với "
        "plan=null và nêu tên field còn thiếu."
    ),
    "NEEDS_INFORMATION_WITH_PLAN": (
        "Bạn trả status=NEEDS_INFORMATION nhưng vẫn kèm plan. Khi thiếu dữ liệu thì "
        "plan phải là null — không được lập kế hoạch một phần với giá trị bịa ra."
    ),
    "NEEDS_INFORMATION_WITHOUT_FIELDS": (
        "Bạn trả status=NEEDS_INFORMATION nhưng không nêu field nào còn thiếu. Phải "
        "liệt kê ít nhất một tên field, hoặc chuyển sang READY nếu thật ra đã đủ dữ liệu."
    ),
    "UNTRUSTED_PAYMENT_VALUE": (
        "Task pay_fee dùng giá trị không đến từ nguồn tin cậy. booking_id, amount và "
        "currency của pay_fee CHỈ được lấy từ InputRef trỏ tới một task book_parking "
        "trong cùng plan, hoặc từ existing_context do hệ thống cung cấp. Số tiền người "
        "dùng tự ghi trong mục tiêu KHÔNG phải nguồn tin cậy và không được dùng. "
        'Nếu không có nguồn nào, trả NEEDS_INFORMATION với missing_fields = ["payment_quote"].'
    ),
    "MISSING_FIELD_NOT_ALLOWED": (
        "missing_fields chứa tên không nằm trong danh sách cho phép. Chỉ được dùng "
        "đúng các tên đã liệt kê trong system prompt, viết nguyên văn, không thêm mô "
        "tả và không đưa giá trị của người dùng vào."
    ),
    "SCHEMA_MISMATCH": (
        "Kết quả không khớp schema yêu cầu. Trả đúng một object có status, và kèm "
        "plan khi READY hoặc missing_fields khi NEEDS_INFORMATION, không thêm field lạ."
    ),
}


class _InconsistentResponseError(PlannerError):
    """Model trả kết quả sửa được — dùng nội bộ để kích hoạt corrective retry.

    Kế thừa `PlannerError` nên nếu thoát ra ngoài thì contract public vẫn đúng.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# Lỗi nghĩa là "model trả nội dung dùng không được" — hỏi lại một lần thì sửa
# được. KHÁC hẳn auth/rate-limit/network: những thứ đó hỏi lại cũng vô ích.
#
#   ValidationError      output đúng JSON nhưng sai schema
#   OutputParserException LangChain không parse nổi output
#   JSONDecodeError      output không phải JSON hợp lệ
#
# Hai loại sau từng KHÔNG có trong danh sách, và hậu quả đo được trên stack
# thật: 1/18 yêu cầu nhiều dịch vụ chết hẳn với "Planner không gọi được LLM
# (OutputParserException)" — đúng loại lỗi mà vòng corrective retry được viết
# ra để xử lý, lại bị đối xử như một lỗi xác thực và bỏ cuộc ngay.
_REPAIRABLE_LLM_ERRORS: tuple[type[BaseException], ...] = (
    ValidationError,
    OutputParserException,
    json.JSONDecodeError,
)


def _is_repairable_llm_error(exc: BaseException) -> bool:
    """Lỗi từ `ainvoke` có phải loại sửa được bằng cách hỏi lại không.

    Danh sách ở `_REPAIRABLE_LLM_ERRORS`. Auth, rate limit, network và
    configuration không thuộc loại nào trong đó, nên tự động rơi vào nhánh
    không retry — mặc định vẫn là fail-fast.

    LangChain có thể bọc lỗi parse trong exception riêng, nên dò cả chuỗi
    `__cause__`/`__context__` thay vì chỉ kiểm tra lớp ngoài cùng.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, _REPAIRABLE_LLM_ERRORS):
            return True
        current = current.__cause__ or current.__context__
    return False


class _StructuredLLM(Protocol):
    """Phần API của LangChain runnable mà Planner thực sự dùng.

    Khai báo tối thiểu như vậy để unit test inject fake được, không cần API key
    và không cần dựng nguyên `ChatOpenAI`.
    """

    async def ainvoke(self, input: Any) -> Any: ...


class _SupportsStructuredOutput(Protocol):
    def with_structured_output(self, schema: Any, **kwargs: Any) -> _StructuredLLM: ...


# Phụ lục chỉ dùng cho `json_mode`. Không mô tả schema ở đây: schema đã được
# truyền qua `with_structured_output`, và output vẫn được validate bằng
# `_PlannerResponse` như mọi provider khác.
_JSON_MODE_INSTRUCTION = (
    "Trả lời bằng một object JSON hợp lệ duy nhất, đúng schema đã cho. "
    "Không bọc trong code fence, không thêm chữ nào ngoài JSON."
)


# Ba ô boolean mà người dùng có thể nói rõ ngay trong goal. Danh sách này là
# `Literal` ở schema chứ không phải một lượt kiểm sau đó: một `_PlannerResponse`
# mang `resident_id` phải KHÔNG DỰNG ĐƯỢC, để mọi đường dùng nó — kể cả đường
# viết sau này — đều được chặn theo. Trust boundary bằng cấu trúc, không bằng
# danh sách chặn.
ExplicitFactField = Literal["consent", "needs_loading_support", "needs_elevator"]


class _ExplicitFact(BaseModel):
    """Một điều người dùng đã NÓI RÕ, kèm chỗ họ nói.

    `evidence` là phần khiến cấu trúc này khác một dict tự do: model phải TRÍCH
    DẪN được từ chính goal. Không trích được nghĩa là nó đang bịa, và code bắt
    được điều đó mà không cần hiểu tiếng Việt.
    """

    model_config = ConfigDict(extra="forbid")

    field: ExplicitFactField = Field(description="Tên ô. Chỉ ba giá trị này, không có ô nào khác.")
    value: bool = Field(
        strict=True,
        description="true hoặc false. Phải là boolean thật, không phải chuỗi.",
    )
    evidence: str = Field(
        min_length=1,
        description="Đoạn NGUYÊN VĂN trong yêu cầu của người dùng chứng minh kết luận này.",
    )

    @field_validator("evidence")
    @classmethod
    def _evidence_must_carry_text(cls, raw: str) -> str:
        """Khoảng trắng không phải một trích dẫn.

        `min_length=1` cho " " đi qua, và một chuỗi trắng thì khớp với mọi goal
        — tức là lớp kiểm trích dẫn bên dưới sẽ luôn xanh, và luôn vô nghĩa.
        """
        if not raw.strip():
            raise ValueError("evidence rỗng.")
        return raw


class _PlannerResponse(BaseModel):
    """Schema structured output mà LLM phải điền.

    Đây là wrapper riêng của Planner để biểu diễn hai kết quả. Field `plan` dùng
    đúng `TaskPlan` chính thức — không sao chép lại Task/TaskPlan/InputRef.

    Không có field `question`: LLM không soạn văn bản gửi người dùng.
    """

    model_config = ConfigDict(extra="forbid")

    status: PlannerStatus = Field(
        description=(
            "READY khi lập được kế hoạch; NEEDS_INFORMATION khi thiếu dữ liệu bắt buộc; "
            "QUESTION khi người dùng đang HỎI chứ không yêu cầu làm."
        ),
    )
    plan: TaskPlan | None = Field(
        default=None,
        description="TaskPlan khi status=READY. Phải là null khi status=NEEDS_INFORMATION.",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Khi status=NEEDS_INFORMATION: tên các field còn thiếu, chỉ lấy từ danh sách "
            "cho phép. Phải rỗng khi status=READY."
        ),
    )
    explicit_facts: list[_ExplicitFact] = Field(
        default_factory=list,
        description=(
            "Những ô consent/needs_loading_support/needs_elevator mà người dùng đã NÓI RÕ "
            "trong yêu cầu, kèm đoạn nguyên văn chứng minh. Bỏ trống nếu họ không nói, nói "
            "nước đôi, hoặc chỉ nhắc tới chủ đề mà không yêu cầu. Không suy diễn từ việc "
            "họ im lặng. Một ô đã nêu ở đây thì KHÔNG được nêu lại trong missing_fields."
        ),
    )

    @field_validator("missing_fields")
    @classmethod
    def _only_fields_the_agent_can_act_on(cls, raw: list[str]) -> list[str]:
        """Field còn thiếu phải nằm trong allowlist NGAY Ở SCHEMA.

        `_clean_missing_fields` cũng kiểm, nhưng nó chạy sau — và một
        `_PlannerResponse` hợp lệ mang field của một dịch vụ đã bị loại là một
        object hợp lệ mô tả một việc không làm được. Chặn ở chỗ object ra đời
        thì mọi đường dùng nó đều được chặn theo.

        Chỉ nêu VỊ TRÍ. Giá trị do LLM sinh và có thể mang theo nội dung người
        dùng gõ; đưa nó vào message là đưa nó vào log.
        """
        bad = [index for index, name in enumerate(raw) if name not in _ALLOWED_MISSING_FIELDS]
        if bad:
            raise ValueError(f"missing_fields có giá trị ngoài danh sách cho phép tại vị trí {bad}.")
        return raw

    @model_validator(mode="after")
    def _enforce_exclusive_states(self) -> _PlannerResponse:
        """Hai trạng thái loại trừ nhau ở TẦNG SCHEMA, không chỉ ở văn xuôi mô tả.

        Vì sao không dùng discriminated union: union buộc phải bọc thêm một tầng
        (`with_structured_output` cần một class, không nhận bare Union) và
        LangChain convert nó thành `oneOf` + `discriminator` — hai thứ mà strict
        structured-output mode cấm, và model yếu qua OpenRouter hay xử lý sai.
        Wire shape phẳng tương thích rộng hơn; ràng buộc loại trừ được cưỡng chế
        ở đây, cho cùng một hiệu lực: sai là `ValidationError` ngay lúc parse.
        """
        if self.status == "QUESTION":
            if self.plan is not None or self.missing_fields:
                raise ValueError("status=QUESTION phải không có plan và không có missing_fields.")
            return self

        if self.status == "READY":
            if self.plan is None:
                raise ValueError("status=READY phải kèm plan.")
            if self.missing_fields:
                raise ValueError("status=READY thì missing_fields phải rỗng.")
            return self

        if self.plan is not None:
            raise ValueError("status=NEEDS_INFORMATION thì plan phải null.")
        if not self.missing_fields:
            raise ValueError("status=NEEDS_INFORMATION phải nêu ít nhất một field còn thiếu.")
        return self


def _is_real_number(value: Any) -> bool:
    """Số thật, không phải bool.

    `bool` là subclass của `int` trong Python nên `True == 1` và
    `isinstance(True, int)` đều đúng. Không loại trừ tường minh thì một plan có
    `amount=True` sẽ khớp trusted `amount=1` và đi thẳng tới Payment API.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_trusted_value(field_name: str, value: Any, trusted: Any) -> bool:
    """Giá trị literal có khớp trusted context không, đã siết kiểu.

    Chính sách với `amount`: `150000` và `150000.0` được coi là TƯƠNG ĐƯƠNG.
    Lý do — ràng buộc an toàn ở đây là "đúng bằng số tiền hệ thống báo", và hai
    giá trị đó là cùng một số tiền; từ chối chỉ tạo một vòng retry vô ích cho
    thứ không phải lỗ hổng. Contract vẫn quy định amount là số nguyên, và
    `PayFeeRequest.amount: int` ở Payment Provider mới là chỗ chốt kiểu — đó là
    boundary đúng để ép kiểu, không phải Planner.

    Ngược lại, `bool` bị loại tuyệt đối: nó không phải "cùng một số tiền viết
    khác kiểu" mà là một kiểu dữ liệu khác lọt qua nhờ đặc thù của Python.

    Cả `value` lẫn `trusted` đều phải qua kiểm tra kiểu: nếu backend lỡ đưa
    `amount=True` vào context thì cũng không được dùng làm chuẩn so khớp.
    """
    if field_name == "amount":
        return _is_real_number(value) and _is_real_number(trusted) and value == trusted

    # booking_id và currency là định danh dạng chuỗi.
    return isinstance(value, str) and isinstance(trusted, str) and value == trusted


def build_question(missing_fields: tuple[str, ...]) -> str:
    """Dựng câu hỏi gửi người dùng từ danh sách field còn thiếu.

    Deterministic và chỉ ghép từ nhãn cố định trong `MISSING_FIELD_LABELS`, nên
    không có đường nào để văn bản do LLM sinh (hay dữ liệu người dùng) lọt ra.
    """
    # Control value được xét trước: chúng mô tả tình huống, không phải một ô
    # dữ liệu người dùng cần điền, nên không ghép được vào câu "bổ sung giúp mình".
    if UNSUPPORTED_GOAL_FIELD in missing_fields:
        return _UNSUPPORTED_GOAL_QUESTION
    if PAYMENT_QUOTE_REQUIRED_FIELD in missing_fields:
        return _PAYMENT_QUOTE_QUESTION

    labels = [MISSING_FIELD_LABELS[name] for name in missing_fields]
    if len(labels) == 1:
        needed = labels[0]
    else:
        needed = ", ".join(labels[:-1]) + f" và {labels[-1]}"

    return f"Mình cần thêm thông tin để lập kế hoạch: {needed}. Bạn bổ sung giúp mình nhé?"


@dataclass(frozen=True)
class ExplicitFact:
    """Một fact ĐÃ QUA KIỂM, sẵn sàng đi vào ngữ cảnh.

    Kiểu riêng chứ không phải `dict`: một dict đi qua bốn tầng thì tầng thứ tư
    không có cách nào biết nó đã được kiểm hay chưa. Object này chỉ ra đời từ
    `Planner._accept_explicit_facts`, nên sự tồn tại của nó LÀ bằng chứng.

    `evidence` KHÔNG được mang theo: nó là nguyên văn lời người dùng, và mọi
    tầng dưới đây (state, clarification, log) đều không phải chỗ cho nó. Nó đã
    làm xong việc của mình ở tầng kiểm.
    """

    field: ExplicitFactField
    value: bool


@dataclass(frozen=True)
class PlannerResult:
    """Kết quả Planner trả cho caller.

    Đúng một trong hai trạng thái, và không thể dựng được trạng thái lai:

      - READY             -> `plan` là TaskPlan; `missing_fields` rỗng.
      - NEEDS_INFORMATION -> `plan` None; `missing_fields` khác rỗng, hợp lệ.
      - QUESTION          -> `plan` None; `missing_fields` rỗng. Câu hỏi, không
                             phải yêu cầu. Không mang theo câu trả lời.

    `question` KHÔNG phải field khởi tạo — nó là property dựng từ
    `missing_fields`. Caller không có chỗ nào để chèn văn bản tự do vào câu hỏi
    hiển thị cho người dùng.

    `__post_init__` chặn mọi tổ hợp khác, kể cả khi caller tự dựng trực tiếp.
    Thông báo lỗi chỉ nêu vị trí, không echo giá trị — `missing_fields` có thể
    đến từ LLM và mang dữ liệu người dùng.
    """

    status: PlannerStatus
    plan: TaskPlan | None = None
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    # Đi kèm MỌI trạng thái, kể cả READY: người dùng có thể nói rõ cả ba ô và
    # vẫn thiếu một thứ khác, hoặc không thiếu gì cả. Gắn fact vào riêng nhánh
    # NEEDS_INFORMATION sẽ làm mất chúng đúng lúc kế hoạch chạy được.
    explicit_facts: tuple[ExplicitFact, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # `Literal` chỉ có tác dụng khi type-check tĩnh; dataclass không kiểm tra
        # lúc chạy, nên phải tự chặn ở đây.
        if self.status not in _VALID_STATUSES:
            raise ValueError("PlannerResult.status không hợp lệ.")

        # QUESTION là trạng thái RỖNG: không kế hoạch, không field thiếu. Cho
        # phép kèm `missing_fields` sẽ dựng ra một trạng thái lai — vừa hỏi lại
        # vừa trả lời — và giao diện không biết hiện cái nào.
        if self.status == "QUESTION" and (self.plan is not None or self.missing_fields):
            raise ValueError("PlannerResult.QUESTION phải không có plan và không có missing_fields.")

        # List/set/str đều lọt qua các vòng lặp bên dưới nhưng phá bất biến
        # frozen + hashable của dataclass. Yêu cầu đúng tuple.
        if not isinstance(self.missing_fields, tuple):
            raise ValueError("PlannerResult.missing_fields phải là tuple.")

        bad_positions = [index for index, name in enumerate(self.missing_fields) if name not in PUBLIC_MISSING_FIELDS]
        if bad_positions:
            raise ValueError(f"PlannerResult.missing_fields có giá trị không hợp lệ tại vị trí {bad_positions}.")

        duplicate_positions = [
            index for index, name in enumerate(self.missing_fields) if name in self.missing_fields[:index]
        ]
        if duplicate_positions:
            raise ValueError(f"PlannerResult.missing_fields có giá trị trùng tại vị trí {duplicate_positions}.")

        if self.status == "READY":
            if self.plan is None:
                raise ValueError("PlannerResult READY phải có plan.")
            if self.missing_fields:
                raise ValueError("PlannerResult READY không được có missing_fields.")
            return

        # Nhánh QUESTION đã kiểm xong ở trên — nó rỗng cả hai phía.
        if self.status == "QUESTION":
            return

        if self.plan is not None:
            raise ValueError("PlannerResult NEEDS_INFORMATION không được có plan.")
        if not self.missing_fields:
            raise ValueError("PlannerResult NEEDS_INFORMATION phải có missing_fields.")

    @property
    def is_ready(self) -> bool:
        return self.status == "READY"

    @property
    def question(self) -> str | None:
        """Câu hỏi gửi người dùng — luôn do code dựng, không ai truyền vào được.

        READY không có gì để hỏi nên trả None.
        """
        if self.status == "READY":
            return None
        return build_question(self.missing_fields)


class Planner:
    """Chuyển mục tiêu ngôn ngữ tự nhiên thành canonical TaskPlan.

    LLM được inject qua constructor, nên unit test chạy được với fake runnable —
    không cần network và không cần API key. Module này cũng không khởi tạo
    `ChatOpenAI` hay đọc API key ở import time.

        planner = Planner(get_llm())
        result = await planner.plan(goal, existing_context={})
    """

    def __init__(
        self,
        llm: _SupportsStructuredOutput,
        *,
        structured_output_method: str | None = None,
    ) -> None:
        # `with_structured_output` buộc LLM trả object đúng schema: không cần
        # code fence, không tự json.loads(), không parse text thủ công. Dù đi
        # đường nào, output CUỐI CÙNG vẫn được validate bằng `_PlannerResponse`
        # — không có nhánh nào bỏ qua Pydantic để dễ chạy hơn.
        #
        # `structured_output_method` cho caller chọn cơ chế khi provider không
        # hỗ trợ mặc định. DeepSeek V4 Flash chạy thinking mode nên từ chối
        # forced `tool_choice` ("Thinking mode does not support this
        # tool_choice"), và `json_schema` thì báo "response_format type is
        # unavailable now"; `json_mode` là đường còn lại và vẫn parse qua
        # Pydantic như mọi provider khác.
        # `json_mode` của OpenAI-compatible API từ chối request nếu prompt
        # không chứa chữ "json": "Prompt must contain the word 'json' in some
        # form to use 'response_format' of type 'json_object'". Chỉ thêm chỉ
        # dẫn cho đúng nhánh này để prompt của các provider khác không đổi.
        self._json_mode_hint = structured_output_method == "json_mode"

        if structured_output_method is None:
            self._structured_llm = llm.with_structured_output(_PlannerResponse)
        else:
            self._structured_llm = llm.with_structured_output(_PlannerResponse, method=structured_output_method)

    async def plan(
        self,
        goal: str,
        existing_context: dict[str, Any] | None = None,
        recalled: list[dict[str, Any]] | None = None,
    ) -> PlannerResult:
        """Lập kế hoạch cho `goal`, có tính đến dữ liệu đã có trong `existing_context`.

        Raises:
            PlannerError: goal rỗng, context không serialize được, LLM lỗi, hoặc
                LLM trả kết quả không nhất quán.
        """
        if not goal or not goal.strip():
            # Chặn trước khi gọi LLM: goal rỗng thì không có gì để lập kế hoạch,
            # và cũng không nên tốn một lượt gọi model.
            raise PlannerError("Planner cần một mục tiêu không rỗng.")

        context = existing_context or {}
        system_prompt = PLANNER_SYSTEM_PROMPT
        if self._json_mode_hint:
            system_prompt = f"{PLANNER_SYSTEM_PROMPT}\n\n{_JSON_MODE_INSTRUCTION}"
        messages = [
            ("system", system_prompt),
            ("human", self._build_user_message(goal, context, recalled)),
        ]

        for attempt in range(_MAX_CORRECTIVE_RETRIES + 1):
            is_last_attempt = attempt == _MAX_CORRECTIVE_RETRIES

            try:
                response = await self._structured_llm.ainvoke(messages)
            except Exception as exc:  # noqa: BLE001 — mọi lỗi LLM đều quy về một loại
                if _is_repairable_llm_error(exc) and not is_last_attempt:
                    # Model trả nội dung dùng không được — hỏi lại một lần.
                    #
                    # GHI LẠI dù lần hỏi lại này thường thành công: một lượt
                    # retry im lặng nghĩa là không ai biết tần suất model trả
                    # output hỏng, và cũng không có cách nào chứng minh vòng
                    # retry đang thật sự chạy. `warning` chứ không `info`: log
                    # ứng dụng lọc dưới mức đó, và một dòng không đọc được thì
                    # bằng không có.
                    logger.warning("planner hỏi lại sau output không dùng được (%s)", type(exc).__name__)
                    messages = self._with_correction(messages, "SCHEMA_MISMATCH")
                    continue
                # Auth, rate limit, network, configuration: hỏi lại cũng vô ích.
                # Chỉ giữ tên loại exception — message gốc có thể chứa prompt đã
                # gửi, đoạn response, hoặc header xác thực.
                raise PlannerError(f"Planner không gọi được LLM ({type(exc).__name__}).") from None

            try:
                return self._to_result(response, goal, context, recalled)
            except _InconsistentResponseError as exc:
                if is_last_attempt:
                    # Sai cả hai lần: dừng, không retry thêm.
                    raise PlannerError(str(exc)) from None
                messages = self._with_correction(messages, exc.kind)

        # Vòng lặp luôn return hoặc raise ở trên; nhánh này không đạt tới được.
        raise PlannerError("Planner không tạo được kết quả dùng được.")

    @staticmethod
    def _with_correction(messages: list[tuple[str, str]], kind: str) -> list[tuple[str, str]]:
        """Thêm hướng dẫn sửa lỗi vào cuối hội thoại.

        Chỉ ghép chuỗi cố định theo loại vi phạm. Response cũ KHÔNG được đính
        kèm: nó có thể chứa dữ liệu người dùng, và gửi lại vào prompt sẽ biến
        retry thành đường rò rỉ.
        """
        instruction = _CORRECTIVE_INSTRUCTIONS.get(kind, _CORRECTIVE_INSTRUCTIONS["SCHEMA_MISMATCH"])
        return [*messages, ("human", _CORRECTIVE_PREAMBLE + instruction)]

    @staticmethod
    def _build_user_message(
        goal: str,
        existing_context: dict[str, Any],
        recalled: list[dict[str, Any]] | None = None,
    ) -> str:
        try:
            # Ngày hôm nay lấy tại ĐÂY, không truyền từ ngoài vào: mọi lời gọi
            # planner đều phải thấy cùng một "hôm nay" với `TaskPlanValidator`,
            # vốn cũng dùng `date.today()`. Hai nguồn ngày khác nhau thì kế
            # hoạch hợp lệ lúc dựng có thể thành quá khứ lúc kiểm.
            return build_planner_user_message(goal, existing_context, today=date.today().isoformat(), recalled=recalled)
        except (TypeError, ValueError) as exc:
            # Không echo context: nó có thể chứa dữ liệu cư dân.
            raise PlannerError(f"existing_context không serialize được sang JSON ({type(exc).__name__}).") from None

    @staticmethod
    def _fields_taken_from_recall(
        plan: TaskPlan,
        recalled: list[dict[str, Any]],
        existing_context: dict[str, Any],
        goal: str,
    ) -> list[str]:
        """Field nào trong plan lấy giá trị TỪ CHUYỆN CŨ mà lần này chưa ai xác nhận.

        Cưỡng chế bằng CODE, không chỉ bằng prompt — cùng lý do với
        `_reject_untrusted_payment_values`. Prompt là lời khuyên: model đọc
        "`nho_lai` không phải một nguồn" rồi vẫn có thể điền, và khi nó điền thì
        hành động xảy ra thật. Không có gì ở giữa bắt lại.

        Cái giá của một lần đoán sai không đối xứng. Hỏi thừa một câu: người
        dùng gõ thêm ba chữ. Đặt nhầm khu vì "lần trước khu A": họ tới nơi mới
        biết, chỗ đã bị giữ, và phải huỷ rồi đặt lại.

        Một giá trị bị coi là "lấy từ chuyện cũ" khi nó xuất hiện trong
        `nho_lai` mà KHÔNG xuất hiện trong `existing_context` (dữ kiện lần này)
        và KHÔNG xuất hiện trong chính câu người dùng vừa nói.

        So sánh trên chuỗi đã chuẩn hoá: model thường viết lại giá trị theo dạng
        chuẩn ("ZONE_A") trong khi chuyện cũ lưu dạng người nói ("khu A"). So
        thô sẽ bỏ lọt đúng những ca cần bắt.
        """
        if not recalled:
            return []

        def norm(value: Any) -> str:
            return str(value).strip().casefold().replace("_", " ")

        goal_text = norm(goal)
        confirmed = {norm(v) for v in existing_context.values() if v is not None}
        remembered: set[str] = set()
        for turn in recalled:
            for value in turn.values():
                if value is not None:
                    remembered.add(norm(value))

        offending: list[str] = []
        for task in plan.tasks:
            for field_name, value in (task.input or {}).items():
                # InputRef (dict) là output của task trước — nguồn hợp lệ.
                if not isinstance(value, (str, int, float)):
                    continue
                # So bằng MỌI cách giá trị đó có thể đã được nói ra: model viết
                # `ZONE_A`, người dùng nói "khu A". Xem `spoken_forms`.
                forms = [norm(f) for f in spoken_forms(str(value))]
                if any(f in confirmed or f in goal_text for f in forms if f):
                    continue
                if any(f and (f in memory or memory in f) for f in forms for memory in remembered if memory):
                    if field_name not in offending:
                        offending.append(field_name)
        return offending

    @staticmethod
    def _reject_untrusted_payment_values(plan: TaskPlan, existing_context: dict[str, Any]) -> None:
        """Chặn pay_fee dùng số tiền không đến từ nguồn authoritative.

        Đây là cưỡng chế bằng code, không chỉ bằng prompt: model có thể lấy số
        tiền từ câu goal của người dùng, và nếu chỉ dựa vào prompt thì một câu
        như "thanh toán 1 đồng cho BOOK-001" sẽ thành plan trả 1 đồng thật.

        Chỉ kiểm field CÓ MẶT trong input. Field vắng mặt là lỗi thiếu required
        input — việc của `TaskPlanValidator`, không phải của kiểm tra này.

        Message lỗi chỉ nêu tên field và tên tool (đều thuộc tập cố định), không
        bao giờ echo giá trị: giá trị đó do LLM sinh và có thể mang dữ liệu người dùng.
        """
        tasks_by_id = {task.task_id: task for task in plan.tasks}

        for task in plan.tasks:
            if task.tool != "pay_fee":
                continue

            present = [name for name in _TRUSTED_PAYMENT_FIELDS if name in task.input]
            if len(present) < len(_TRUSTED_PAYMENT_FIELDS):
                # Thiếu field: `TaskPlanValidator` từ chối vì thiếu required
                # input, và graph không cho execute khi Validator chưa duyệt.
                # Không kiểm provenance trên bộ ba khuyết.
                continue

            values = {name: task.input[name] for name in _TRUSTED_PAYMENT_FIELDS}
            reference_count = sum(isinstance(value, InputRef) for value in values.values())

            if reference_count == len(values):
                Planner._check_single_booking_provenance(values, tasks_by_id)
            elif reference_count == 0:
                Planner._check_trusted_context_provenance(values, existing_context)
            else:
                # Trộn hai nguồn: booking_id literal từ context nhưng amount lấy
                # từ một booking khác trong plan (hoặc ngược lại) sẽ thanh toán
                # sai số tiền cho sai đơn.
                raise _InconsistentResponseError(
                    "UNTRUSTED_PAYMENT_VALUE",
                    "pay_fee trộn InputRef và giá trị literal cho ba field thanh toán.",
                )

    @staticmethod
    def _check_single_booking_provenance(
        values: dict[str, Any],
        tasks_by_id: dict[str, Any],
    ) -> None:
        """Mode A — cả ba field đến từ CÙNG MỘT task book_parking.

        Kiểm từng field riêng lẻ là chưa đủ: `booking_id` trỏ booking T1 còn
        `amount` trỏ booking T2 thì mỗi field đều "hợp lệ", nhưng plan sẽ thanh
        toán phí của đơn này cho đơn kia.
        """
        source_ids = {value.from_task for value in values.values()}
        if len(source_ids) != 1:
            # Không nêu task ID: chúng do LLM sinh ra.
            raise _InconsistentResponseError(
                "UNTRUSTED_PAYMENT_VALUE",
                "pay_fee lấy ba field thanh toán từ nhiều task khác nhau.",
            )

        source = tasks_by_id.get(next(iter(source_ids)))
        if source is None or source.tool != "book_parking":
            raise _InconsistentResponseError(
                "UNTRUSTED_PAYMENT_VALUE",
                "pay_fee tham chiếu task không phải book_parking.",
            )

        # Mapping 1:1 vì book_parking đặt tên output trùng tên input của pay_fee.
        # Thiếu kiểm tra này thì `amount = InputRef(T3, "booking_id")` sẽ trả số
        # tiền bằng một chuỗi mã đặt chỗ.
        for field_name, value in values.items():
            if value.field != field_name:
                raise _InconsistentResponseError(
                    "UNTRUSTED_PAYMENT_VALUE",
                    f"pay_fee lấy '{field_name}' từ output field không tương ứng của book_parking.",
                )

    @staticmethod
    def _check_trusted_context_provenance(
        values: dict[str, Any],
        existing_context: dict[str, Any],
    ) -> None:
        """Mode B — cả ba field đều là literal khớp trusted context.

        Đòi đủ cả ba: nếu chỉ một hai field khớp context còn field kia do model
        tự điền thì bộ ba không còn mô tả cùng một đơn đặt chỗ.
        """
        for field_name, value in values.items():
            trusted = existing_context.get(field_name)
            if trusted is None or not _matches_trusted_value(field_name, value, trusted):
                raise _InconsistentResponseError(
                    "UNTRUSTED_PAYMENT_VALUE",
                    f"pay_fee dùng '{field_name}' không đến từ book_parking hay existing_context.",
                )

    @staticmethod
    def _normalise_for_quote(text: str) -> str:
        """Chuẩn hoá tối thiểu để so trích dẫn: thường hoá + gộp khoảng trắng.

        CỐ Ý không bỏ dấu. `evidence` phải là một trích dẫn gần như nguyên văn;
        một model viết lại câu không dấu thì nó đang diễn giải chứ không trích,
        và đó chính là thứ lớp kiểm này tồn tại để bắt. Chỉ tha thứ hai khác
        biệt vô hại: hoa/thường, và khoảng trắng thừa do xuống dòng.
        """
        return " ".join(text.casefold().split())

    def _accept_explicit_facts(self, response: _PlannerResponse, goal: str) -> tuple[ExplicitFact, ...]:
        """Nhận những fact CHỨNG MINH ĐƯỢC, từ chối cả response nếu có cái không.

        Ba cửa, và cửa nào cũng ném `_InconsistentResponseError` — tức là đi vào
        đúng vòng sửa một lần đã có sẵn, không dựng cơ chế thứ hai:

          trích dẫn không có thật     model đang bịa một điều người dùng chưa nói
          hai kết luận trái nhau      response tự mâu thuẫn với chính nó
          vừa nhận vừa hỏi lại        cũng vậy, chỉ ở một trục khác

        KHÔNG lặng lẽ bỏ mục hỏng rồi giữ phần còn lại: một response đã sai ở
        một chỗ thì không có cơ sở nào để tin phần còn lại của nó, và bỏ im
        lặng nghĩa là không ai đo được model sai bao nhiêu lần.

        Từ chối là AN TOÀN theo đúng chiều cần thiết: mất một fact thì hệ thống
        hỏi lại và người dùng gõ thêm vài chữ; nhận một fact bịa thì nó gọi điện
        cho người vừa từ chối, và không màn hình nào nói ra điều đó.
        """
        if not response.explicit_facts:
            return ()

        goal_text = self._normalise_for_quote(goal)
        ket_luan: dict[str, bool] = {}
        for fact in response.explicit_facts:
            trich = self._normalise_for_quote(fact.evidence)
            if not trich or trich not in goal_text:
                # Chỉ nêu TÊN Ô. `evidence` là nguyên văn lời người dùng, và
                # message này đi vào log lẫn prompt sửa lỗi.
                raise _InconsistentResponseError(
                    "FACT_WITHOUT_EVIDENCE",
                    f"Planner nêu kết luận không trích dẫn được cho ô: {fact.field}.",
                )
            if fact.field in ket_luan and ket_luan[fact.field] != fact.value:
                raise _InconsistentResponseError(
                    "CONTRADICTORY_FACT",
                    f"Planner đưa hai kết luận trái nhau cho ô: {fact.field}.",
                )
            ket_luan[fact.field] = fact.value

        trung = sorted(set(ket_luan) & set(response.missing_fields))
        if trung:
            raise _InconsistentResponseError(
                "FACT_AND_MISSING_CONFLICT",
                "Planner vừa kết luận vừa hỏi lại cùng một ô: " + ", ".join(trung) + ".",
            )

        return tuple(ExplicitFact(field=name, value=value) for name, value in ket_luan.items())

    def _to_result(
        self,
        response: Any,
        goal: str,
        existing_context: dict[str, Any],
        recalled: list[dict[str, Any]] | None = None,
    ) -> PlannerResult:
        """Kiểm tra tính nhất quán rồi chuyển sang kết quả public."""
        if not isinstance(response, _PlannerResponse):
            raise _InconsistentResponseError("SCHEMA_MISMATCH", "Planner nhận được kết quả sai schema từ LLM.")

        # Kiểm fact TRƯỚC khi rẽ nhánh trạng thái: chúng đi kèm cả ba trạng
        # thái, và một response bịa trích dẫn thì không được đi tiếp dù nó có
        # kèm kế hoạch hợp lệ hay không.
        facts = self._accept_explicit_facts(response, goal)

        if response.status == "READY":
            # `_PlannerResponse._enforce_exclusive_states` đã chặn hai trường hợp
            # dưới ở tầng schema. Giữ lại làm phòng thủ nhiều lớp: nếu sau này ai
            # đó nới validator hoặc dựng object bằng đường khác, execution vẫn
            # không được đi tiếp với state mâu thuẫn.
            if response.plan is None:
                raise _InconsistentResponseError("READY_WITHOUT_PLAN", "Planner trả READY nhưng không kèm kế hoạch.")
            if response.missing_fields:
                raise _InconsistentResponseError(
                    "READY_WITH_MISSING_FIELDS",
                    "Planner trả READY nhưng vẫn nêu field còn thiếu.",
                )

            # Goal luôn lấy từ caller: LLM không được viết lại mục tiêu người dùng.
            # Dựng lại TaskPlan để Pydantic validate đầy đủ, thay vì model_copy
            # (bỏ qua validation) hay model_construct (bỏ qua hoàn toàn).
            plan = TaskPlan(goal=goal, tasks=response.plan.tasks)

            # Tool nằm ngoài không gian kế hoạch của Agent → response mâu thuẫn.
            #
            # KHÔNG xoá task rồi chạy phần còn lại: xoá một task làm đổi
            # dependency của các task sau, và kế hoạch còn lại không còn là kế
            # hoạch model đã lập. Từ chối và hỏi lại một lần.
            forbidden = sorted({task.tool for task in plan.tasks if task.tool in PLANNER_FORBIDDEN_TOOLS})
            if forbidden:
                # Chỉ nêu TÊN TOOL — input của task chứa dữ liệu người dùng.
                raise _InconsistentResponseError(
                    "FORBIDDEN_PLANNER_TOOL",
                    "Kế hoạch chứa bước nằm ngoài phạm vi Agent: " + ", ".join(forbidden) + ".",
                )

            # Trust boundary: chặn trước khi plan rời khỏi Planner.
            self._reject_untrusted_payment_values(plan, existing_context)

            # Giá trị lấy từ chuyện cũ → HỎI LẠI, không phải báo lỗi.
            #
            # Báo lỗi là trừng phạt người dùng vì model đoán ẩu; hỏi lại đúng
            # là thứ lẽ ra phải xảy ra. Và nó giữ được giá trị của `nho_lai`:
            # model đã hiểu "như lần trước" nghĩa là gì, chỉ là nó phải xác
            # nhận trước khi biến điều đó thành hành động.
            recalled_fields = self._fields_taken_from_recall(plan, recalled or [], existing_context, goal)
            if recalled_fields:
                logger.info("planner: %d field lấy từ nho_lai → hỏi lại", len(recalled_fields))
                return PlannerResult(
                    status="NEEDS_INFORMATION", missing_fields=tuple(recalled_fields), explicit_facts=facts
                )

            # Fact đi kèm kế hoạch, nhưng KHÔNG đụng vào nó. Kế hoạch đã qua
            # Validator; một fact không được thêm, bớt hay sửa task nào. Nó chỉ
            # là ngữ cảnh cho lượt sau.
            return PlannerResult(status="READY", plan=plan, explicit_facts=facts)

        if response.status == "QUESTION":
            # Câu hỏi: không kế hoạch, không field thiếu, không câu chữ.
            #
            # Phải chặn ở ĐÂY chứ không để rơi xuống nhánh NEEDS_INFORMATION bên
            # dưới. Nhánh đó gọi `_clean_missing_fields`, và danh sách rỗng thì
            # nó ném `NEEDS_INFORMATION_WITHOUT_FIELDS` — đo được: cả 5 câu hỏi
            # thử nghiệm đều chết ở đúng chỗ này, dù model đã trả `QUESTION`
            # hoàn toàn đúng.
            if response.plan is not None or response.missing_fields:
                raise _InconsistentResponseError(
                    "QUESTION_WITH_PAYLOAD",
                    "Planner trả QUESTION nhưng vẫn kèm kế hoạch hoặc field thiếu.",
                )
            return PlannerResult(status="QUESTION", explicit_facts=facts)

        # NEEDS_INFORMATION — cũng đã được validator chặn, giữ làm lớp phòng thủ.
        if response.plan is not None:
            raise _InconsistentResponseError(
                "NEEDS_INFORMATION_WITH_PLAN",
                "Planner trả NEEDS_INFORMATION nhưng vẫn kèm kế hoạch.",
            )

        cleaned = self._clean_missing_fields(response.missing_fields)

        # Model hỏi lại field đã có giá trị trong context → response mâu thuẫn.
        #
        # Đây là vòng lặp chết nhìn từ phía người dùng: họ trả lời biển số, hệ
        # thống hỏi lại biển số, và không có gì họ gõ thêm thoát ra được. Sửa ở
        # tầng Planner chứ không phải bằng if/else ở giao diện: giao diện lọc đi
        # thì backend vẫn tin là đang thiếu, và bước thực thi vẫn không chạy.
        #
        # KHÔNG tự ý bỏ field ra khỏi danh sách rồi dựng plan: làm vậy là đoán
        # thay model một kế hoạch mà nó chưa từng lập.
        # Hỏi thông tin để tạo hồ sơ cư dân = đang onboarding qua TaskPlan.
        # Giao diện không có ô nhập cho chúng, nên câu hỏi này không thể trả lời.
        linking = [name for name in cleaned if name in PLANNER_FORBIDDEN_MISSING_FIELDS]
        if linking:
            raise _InconsistentResponseError(
                "FORBIDDEN_LINKING_CLARIFICATION",
                "Planner hỏi thông tin lập hồ sơ cư dân: " + ", ".join(sorted(linking)) + ".",
            )

        redundant = [name for name in cleaned if _already_supplied(name, existing_context)]
        if redundant:
            # Message chỉ có TÊN field. Giá trị (biển số, ngày, khu đỗ) là dữ
            # liệu người dùng và không được đi vào log hay prompt retry.
            raise _InconsistentResponseError(
                "MISSING_FIELD_ALREADY_PROVIDED",
                "Planner hỏi lại field đã có giá trị: " + ", ".join(sorted(redundant)) + ".",
            )

        # `question` không truyền vào — `PlannerResult` tự dựng từ missing_fields.
        return PlannerResult(status="NEEDS_INFORMATION", plan=None, missing_fields=cleaned, explicit_facts=facts)

    @staticmethod
    def _clean_missing_fields(raw_fields: list[str]) -> tuple[str, ...]:
        """Lọc `missing_fields` về những gì HỎI ĐƯỢC, giữ thứ tự và bỏ trùng.

        Ba nhóm, ba cách xử lý:

          alias hạ cấp được   `vehicle_id` → `plate_number` + `vehicle_type`.
                              Model nêu một ID nội bộ; người dùng biết biển số.

          thuộc thanh toán    `booking_id`/`amount`/`currency` → `payment_quote`.
                              Model nêu chúng nghĩa là nó thiếu BÁO GIÁ — sự cố
                              phía hệ thống, không phải thiếu thông tin của
                              khách. Hỏi khách số tiền là mời chính người phải
                              trả tự khai số phải trả.

          có thẩm quyền khác  `resident_id`, `viewing_id`... → từ chối, lý do cố
                              định. Chúng đến từ tài khoản đã xác minh hoặc từ
                              kết quả một bước trước; không có câu hỏi nào đúng
                              để đặt ra.

        Giá trị không hợp lệ KHÔNG vào message: chúng do LLM sinh và có thể mang
        dữ liệu người dùng. Chỉ báo vị trí.

        Ở đây bỏ trùng thay vì từ chối: output LLM là dữ liệu nhiễu cần chuẩn
        hoá. `PlannerResult` thì ngược lại — nó từ chối trùng, vì caller dựng
        trực tiếp phải truyền dữ liệu đã sạch.
        """
        bad_positions = [index for index, name in enumerate(raw_fields) if name not in RAW_MODEL_MISSING_FIELDS]
        if bad_positions:
            raise _InconsistentResponseError(
                "MISSING_FIELD_NOT_ALLOWED",
                f"Planner nêu field còn thiếu không hợp lệ tại vị trí {bad_positions} (ngoài danh sách cho phép).",
            )

        # Thiếu ngữ cảnh thanh toán thì CẢ danh sách quy về một control field:
        # ghép nó với các câu hỏi khác sẽ dựng ra một màn vừa hỏi vừa báo lỗi.
        if any(name in _PAYMENT_CONTEXT_FIELDS for name in raw_fields):
            return (PAYMENT_QUOTE_REQUIRED_FIELD,)

        blocked = [
            index
            for index, name in enumerate(raw_fields)
            if name in AUTHORITATIVE_MISSING_FIELDS and name not in _PAYMENT_CONTEXT_FIELDS
        ]
        if blocked:
            raise _InconsistentResponseError(
                "MISSING_FIELD_NOT_ASKABLE",
                f"Planner hỏi dữ liệu hệ thống tự biết tại vị trí {blocked}; dữ liệu này không hỏi người dùng.",
            )

        seen: set[str] = set()
        cleaned: list[str] = []
        for name in raw_fields:
            if name in seen:
                continue
            seen.add(name)
            for replacement in _DOWNGRADABLE_MISSING_FIELDS.get(name, (name,)):
                if replacement not in cleaned:
                    cleaned.append(replacement)

        if not cleaned:
            raise _InconsistentResponseError(
                "NEEDS_INFORMATION_WITHOUT_FIELDS",
                "Planner trả NEEDS_INFORMATION nhưng không nêu thiếu gì.",
            )

        return tuple(cleaned)
