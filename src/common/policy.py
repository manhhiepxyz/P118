"""Contract có kiểu cho các guard deterministic dừng việc thực thi.

Trước file này, `graph.py` bắt `PermissionError` rồi chỉ giữ `type(exc).__name__`.
Cách đó làm mất `workflow_id`, `partial_results` và báo giá — nên tầng API không
có gì để hiển thị ngoài "cần xác nhận thanh toán", không kèm số tiền. Người dùng
được hỏi có đồng ý trả một khoản mà họ không nhìn thấy.

Đặt ở `src/common/` để `src/agents/**` dùng được mà không phải import
`src/orchestration/**` — chiều phụ thuộc phải là orchestration → agents.

`code` là định danh ổn định cho tầng API map sang câu nghiệp vụ. Không dùng tên
class làm khoá: đổi tên class là im lặng phá vỡ mọi nơi đang so chuỗi.
"""

from __future__ import annotations

from typing import Any

from src.common.results import StandardResult


class PolicyInterruptionError(PermissionError):
    """Guard đã chặn TRƯỚC khi tầng thực thi được gọi.

    Khi exception này được ném, chưa có lời gọi dịch vụ nào cho phần bị chặn.
    Các bước đã hoàn tất trước đó nằm trong `partial_results`.
    """

    code: str = "POLICY_DENIED"

    def __init__(
        self,
        message: str,
        *,
        workflow_id: str | None = None,
        partial_results: dict[str, StandardResult] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.workflow_id = workflow_id
        self.partial_results = partial_results or {}
        # Dữ liệu phụ đã được làm sạch, an toàn để đi tiếp lên tầng API.
        # KHÔNG chứa payload provider, connection string hay message gốc.
        self.context = context or {}
