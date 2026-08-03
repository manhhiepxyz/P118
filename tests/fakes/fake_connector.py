"""FakeConnector cho testing.

Owner: Mạnh Hiệp (Executor layer)
File: tests/fakes/fake_connector.py
"""

from typing import Any, Callable
from src.connectors.base import Connector
from src.common.results import StandardResult
from src.common.enums import ErrorCode


class FakeConnector(Connector):
    """Fake Connector cho unit test.

    Có thể cấu hình trả về kết quả khác nhau cho từng tool.
    """

    def __init__(
        self,
        responses: dict[str, StandardResult] | None = None,
        default_response: StandardResult | None = None,
        side_effect: Callable[[str, dict[str, Any]], StandardResult] | None = None,
    ):
        """Khởi tạo FakeConnector.

        Args:
            responses: Dict mapping tool_name -> StandardResult
            default_response: Response mặc định nếu tool không có trong responses
            side_effect: Function tùy chỉnh nhận (tool_name, input_data) -> StandardResult
        """
        self._responses = responses or {}
        self._default_response = default_response or StandardResult.ok(data={"mock": True})
        self._side_effect = side_effect
        self._call_history: list[dict[str, Any]] = []

    @property
    def tool_names(self) -> list[str]:
        return list(self._responses.keys()) + ["default"]

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> StandardResult:
        self._call_history.append({"tool_name": tool_name, "input_data": input_data})

        if self._side_effect:
            return self._side_effect(tool_name, input_data)

        return self._responses.get(tool_name, self._default_response)

    def set_response(self, tool_name: str, response: StandardResult) -> None:
        """Cấu hình response cho một tool cụ thể."""
        self._responses[tool_name] = response

    def set_default_response(self, response: StandardResult) -> None:
        """Cấu hình response mặc định."""
        self._default_response = response

    def set_side_effect(
        self,
        side_effect: Callable[[str, dict[str, Any]], StandardResult],
    ) -> None:
        """Cấu hình function tùy chỉnh."""
        self._side_effect = side_effect

    @property
    def call_history(self) -> list[dict[str, Any]]:
        """Lịch sử các lần gọi execute."""
        return self._call_history.copy()

    def clear_history(self) -> None:
        """Xóa lịch sử gọi."""
        self._call_history.clear()

    def was_called_with(self, tool_name: str, input_data: dict | None = None) -> bool:
        """Kiểm tra tool có được gọi với input_data cụ thể không."""
        for call in self._call_history:
            if call["tool_name"] == tool_name:
                if input_data is None:
                    return True
                if call["input_data"] == input_data:
                    return True
        return False

    def get_call_count(self, tool_name: str) -> int:
        """Đếm số lần tool được gọi."""
        return sum(1 for call in self._call_history if call["tool_name"] == tool_name)


# Pre-configured responses cho các scenario phổ biến
def create_success_response(data: dict) -> StandardResult:
    """Tạo response thành công."""
    return StandardResult.ok(data=data)


def create_no_availability_response(message: str = "Không có chỗ trống") -> StandardResult:
    """Tạo response NO_AVAILABILITY."""
    return StandardResult.fail(
        error_code=ErrorCode.NO_AVAILABILITY,
        error_message=message,
        retryable=False,
    )


def create_service_timeout_response(message: str = "Service timeout") -> StandardResult:
    """Tạo response SERVICE_TIMEOUT (retryable)."""
    return StandardResult.fail(
        error_code=ErrorCode.SERVICE_TIMEOUT,
        error_message=message,
        retryable=True,
    )


def create_validation_error_response(message: str = "Dữ liệu không hợp lệ") -> StandardResult:
    """Tạo response VALIDATION_ERROR."""
    return StandardResult.fail(
        error_code=ErrorCode.VALIDATION_ERROR,
        error_message=message,
        retryable=False,
    )