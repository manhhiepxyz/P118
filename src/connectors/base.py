"""Connector base class cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/connectors/base.py
"""

from abc import ABC, abstractmethod
from typing import Any

from src.common.results import StandardResult


class Connector(ABC):
    """Abstract base class cho tất cả Connector.

    Connector là ranh giới duy nhất giữa hệ thống và API ngoài.
    Executor không gọi API trực tiếp.
    """

    @property
    @abstractmethod
    def tool_names(self) -> list[str]:
        """Danh sách tool_name mà connector này xử lý."""
        ...

    @abstractmethod
    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> StandardResult:
        """Thực thi tool và trả về StandardResult.

        Args:
            tool_name: Tên tool (ví dụ: "register_resident", "register_vehicle")
            input_data: Dữ liệu đầu vào theo internal contract

        Returns:
            StandardResult object (không bao giờ trả raw JSON)
        """
        ...

    def can_handle(self, tool_name: str) -> bool:
        """Kiểm tra connector có xử lý tool này không."""
        return tool_name in self.tool_names
