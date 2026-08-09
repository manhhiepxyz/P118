"""Connector base class cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/connectors/base.py

Vai trò trong kiến trúc:
  Connector là ranh giới DUY NHẤT giữa hệ thống nội bộ và API bên ngoài.
  Executor KHÔNG ĐƯỢC gọi API trực tiếp — mọi lời gọi HTTP phải đi qua
  một Connector cụ thể.

  Luồng chuẩn:
    Executor.execute()
      → _get_connector(tool_name)       # tra bảng tool→Connector
      → connector.execute(tool, input)  # Connector gọi HTTP
      → Mock API trả raw JSON
      → Connector map → StandardResult  # lọc field, normalize, map error
      → StandardResult về Executor      # Executor chỉ thấy StandardResult

Quy tắc triển khai (mọi subclass phải tuân thủ):
  1. Khai báo tool_names property với danh sách tool mình xử lý.
  2. execute() phải trả StandardResult, KHÔNG raise exception ra ngoài.
  3. Chỉ trả canonical output field theo shared_contracts.md — lọc bỏ
     mọi extra field mà API trả thêm.
  4. Map tất cả HTTP error / network exception sang ErrorCode chuẩn.
  5. Client httpx được inject từ bên ngoài (để test có thể mock) —
     nếu không inject thì tự tạo và tự đóng sau mỗi request.
"""

from abc import ABC, abstractmethod
from typing import Any

from src.common.results import StandardResult


class Connector(ABC):
    """Abstract base class cho tất cả Connector.

    Connector là ranh giới duy nhất giữa hệ thống và API ngoài.
    Executor không gọi API trực tiếp.

    Subclass hiện tại:
      - ResidentConnector  → POST /api/residents         (port 8001)
      - TransportConnector → POST /api/vehicles          (port 8002)
                           → POST /api/parking/bookings  (port 8002)
      - PaymentConnector   → POST /api/payments          (port 8003)
    """

    @property
    @abstractmethod
    def tool_names(self) -> list[str]:
        """Danh sách tool_name mà connector này xử lý.

        Executor dùng list này để xây dựng bảng tra tool→Connector khi
        khởi tạo. Mỗi tool_name phải khớp với allowlist trong TaskPlan.

        Ví dụ:
            ResidentConnector  → ["register_resident"]
            TransportConnector → ["register_vehicle", "book_parking"]
            PaymentConnector   → ["pay_fee"]
        """
        ...

    @abstractmethod
    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> StandardResult:
        """Thực thi tool và trả về StandardResult.

        Executor gọi method này sau khi:
          1. _check_dependencies() pass (tất cả dep đã SUCCESS)
          2. _resolve_input() đã thay InputRef bằng giá trị thật
          3. _get_connector() tìm được đúng Connector

        Subclass phải:
          - POST input_data tới đúng endpoint.
          - Nếu response.is_success:
              • Kiểm tra required output fields có đủ không.
              • Lọc chỉ giữ canonical fields (bỏ extra).
              • Với PaymentConnector: normalize payment_status về allowlist.
              • Trả StandardResult.ok(data={...canonical...}).
          - Nếu response.is_error:
              • Parse error_code từ body → map sang ErrorCode chuẩn.
              • Trả StandardResult.fail(error_code=..., message=...).
          - Nếu httpx raise exception:
              • TimeoutException → SERVICE_TIMEOUT, retryable=True
              • ConnectError     → SERVICE_UNAVAILABLE, retryable=True
              • Exception khác   → INTERNAL_SERVICE_ERROR, retryable=False

        KHÔNG để exception lan ra ngoài method này.

        Args:
            tool_name  : Tên tool (ví dụ: "register_resident", "pay_fee").
                         Subclass kiểm tra và trả INVALID_INPUT nếu sai.
            input_data : Dict đã resolve InputRef, sẵn sàng POST làm JSON body.

        Returns:
            StandardResult object. KHÔNG trả raw JSON.
        """
        ...

    def can_handle(self, tool_name: str) -> bool:
        """Kiểm tra connector có xử lý tool này không.

        Tiện ích để kiểm tra nhanh mà không cần tra bảng Executor.
        Executor dùng _connector_map thay vì method này trong runtime,
        nhưng can_handle() hữu ích trong test và validation.
        """
        return tool_name in self.tool_names
