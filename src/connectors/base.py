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
from dataclasses import dataclass
from typing import Any, NamedTuple

from src.common.enums import ErrorCode
from src.common.results import StandardResult


class EnvelopeError(NamedTuple):
    """Thông tin lỗi trích từ Mock API envelope.

    Connector dùng struct này để dựng StandardResult.fail():
      - error_code: string thô từ API, caller phải map qua _map_error_code().
      - message   : mô tả lỗi từ API (không chứa input payload của user).
      - retryable : cờ retryable do API khai báo.
    """

    error_code: str
    message: str
    retryable: bool


@dataclass(frozen=True)
class ProviderCallContext:
    """Dữ liệu CỦA MỘT LẦN GỌI, do orchestration cấp, bất biến.

    Khoá idempotency là thuộc tính của một lần gửi, không phải state của
    connector. Đặt nó lên connector — thứ được dựng MỘT lần cho cả workflow và
    dùng chung cho mọi task — thì hai lần gọi song song ghi đè lên nhau, và một
    khoản tiền đi ra mang khoá của khoản kia.

    Không nhận `SubmissionPermit`: permit là khái niệm PERSISTENCE (được phép
    gửi hay không, vì sao). Connector không cần biết điều đó; nó chỉ cần biết
    phải gắn khoá nào. Kiểu đóng ở đây giữ đúng ranh giới ấy.
    """

    idempotency_key: str | None = None


class Connector(ABC):
    """Abstract base class cho tất cả Connector.

    Connector là ranh giới duy nhất giữa hệ thống và API ngoài.
    Executor không gọi API trực tiếp.

    Subclass hiện tại:
      - ResidentConnector  → POST /api/residents         (port 8001)
      - TransportConnector → POST /api/vehicles          (port 8002)
                           → POST /api/parking/bookings  (port 8002)
      - PaymentConnector   → POST /api/payments          (port 8003)
      - PropertyConnector  → POST /api/properties/* và /api/projects/* (port 8005)
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

    def idempotency_key_for(
        self, workflow_id: str, task_id: str, tool_name: str, resolved_input: dict[str, Any]
    ) -> str | None:
        """Khoá ĐỀ XUẤT cho lần gọi này, tính deterministic từ tham số.

        Không đọc state nào của connector: cùng bộ tham số phải ra cùng khoá ở
        mọi process, kể cả sau restart. Đó là điều kiện để khoá đã lưu và khoá
        vừa tính so được với nhau.

        `None` nghĩa là tool này không có khoá — và khi đó nó không bao giờ được
        gửi lại sau một lần gửi dở dang.
        """
        return None

    def is_retry_safe(self, tool_name: str) -> bool:
        """Gọi lại tool này sau timeout có an toàn không?

        Mặc định **False** — fail-closed. Đây là chủ ý: một tool ghi dữ liệu mà
        chưa có idempotency key thì retry sau timeout có thể tạo bản ghi THỨ HAI.
        Provider đã tạo record rồi mới timeout ở đường về là tình huống hoàn
        toàn bình thường; Executor không có cách nào phân biệt "chưa chạy" với
        "đã chạy nhưng mất response".

        Subclass chỉ được trả True khi chứng minh được một trong hai điều:

          1. Tool là read-only (gọi lại không đổi trạng thái gì).
          2. Tool mang idempotency key thật, và provider dùng key đó để trả lại
             kết quả cũ thay vì tạo mới.

        Executor kết hợp cờ này VỚI `StandardResult.is_retryable`: lỗi phải vừa
        transient vừa nằm trên một tool an toàn thì mới được thử lại.
        """
        return False

    @abstractmethod
    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        *,
        context: ProviderCallContext | None = None,
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

    @staticmethod
    def _extract_payload(body: Any) -> tuple[dict[str, Any] | None, EnvelopeError | None]:
        """Lấy canonical payload từ Mock API envelope.

        Mock Provider trả envelope chuẩn (mục 6 shared_contracts.md)::

            {"success": true, "data": {...}, "error_code": null,
             "message": "...", "retryable": false}

        Canonical field nằm trong ``data``, KHÔNG ở top level. Helper này là
        nơi DUY NHẤT bóc envelope — subclass không được tự đọc ``body[...]``.

        Quy tắc:
          - body là dict CÓ key "success" → là envelope:
              • success=False → (None, EnvelopeError(...)) để caller dựng
                StandardResult.fail() với error_code đã map + retryable.
              • success=True  → (body["data"] or {}, None).
          - body là dict KHÔNG có "success" → provider ngoài trả flat response
            (fallback được dung thứ) → (body, None).
          - body không phải dict → (None, EnvelopeError(...)).

        Returns:
            (payload, None) khi thành công; (None, EnvelopeError) khi lỗi.
            KHÔNG bao giờ trả envelope thô cho caller.
        """
        if not isinstance(body, dict):
            return None, EnvelopeError(
                error_code="UNKNOWN_EXTERNAL_ERROR",
                message="Response body không phải JSON object",
                retryable=False,
            )

        # Flat response từ provider không dùng envelope — tolerated fallback.
        if "success" not in body:
            return body, None

        if body.get("success"):
            data = body.get("data")
            # data=null hoặc không phải dict → coi như payload rỗng; caller sẽ
            # bắt lỗi thiếu required field và trả UNKNOWN_EXTERNAL_ERROR.
            return (data if isinstance(data, dict) else {}), None

        # HTTP 2xx nhưng envelope báo lỗi → caller phải trả failure.
        return None, EnvelopeError(
            error_code=str(body.get("error_code") or "UNKNOWN_EXTERNAL_ERROR"),
            message=str(body.get("message") or "Unknown error"),
            retryable=bool(body.get("retryable", False)),
        )

    def _map_error_code(self, code: str) -> ErrorCode:
        """Map error code string từ API sang ErrorCode nội bộ.

        Default: khớp trực tiếp với enum, không khớp → UNKNOWN_EXTERNAL_ERROR.
        Subclass override để bổ sung bảng mapping riêng của từng service.
        """
        try:
            return ErrorCode(code)
        except ValueError:
            return ErrorCode.UNKNOWN_EXTERNAL_ERROR

    def _build_envelope_failure(self, env_error: EnvelopeError) -> StandardResult:
        """Dựng StandardResult.fail() từ EnvelopeError của _extract_payload().

        Dùng khi HTTP 2xx nhưng envelope có success=false: error_code thô được
        map qua _map_error_code() của subclass, message giữ nguyên từ API
        (không nội suy input payload để tránh lộ dữ liệu nhạy cảm).
        """
        error_code = self._map_error_code(env_error.error_code)
        return StandardResult.fail(
            error_code=error_code,
            message=env_error.message,
            retryable=env_error.retryable or error_code.is_retryable,
        )

    def can_handle(self, tool_name: str) -> bool:
        """Kiểm tra connector có xử lý tool này không.

        Tiện ích để kiểm tra nhanh mà không cần tra bảng Executor.
        Executor dùng _connector_map thay vì method này trong runtime,
        nhưng can_handle() hữu ích trong test và validation.
        """
        return tool_name in self.tool_names
