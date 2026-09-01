"""Phân loại lỗi workflow thành mã ổn định + câu tiếng Việt cho người dùng.

Vì sao cần: trước đây mọi exception thoát ra khỏi background job đều thành
`EXECUTION_ERROR` với câu "Workflow không thể hoàn thành do lỗi dịch vụ hoặc
cấu hình." Câu đó đúng nhưng vô dụng — nó gộp "sai biến môi trường" (gọi lại
bao nhiêu lần cũng hỏng, cần người vận hành) với "provider đang bận" (chờ chút
rồi thử lại là xong). Người dùng không biết nên làm gì, còn người trực không
biết bắt đầu từ đâu.

Nguyên tắc:

  - mã lỗi ỔN ĐỊNH, dùng để đối chiếu log và cho admin đọc; không đổi theo câu chữ
  - `retryable` nói thẳng thử lại có ích không
  - câu công khai bằng tiếng Việt, nói việc NÊN LÀM, không nói tên class,
    không nói tên biến môi trường, không kèm SQL/DSN/traceback
"""

from __future__ import annotations

from typing import NamedTuple


class FailureKind(NamedTuple):
    """Một loại lỗi: mã, có thử lại được không, và câu nói với người dùng."""

    code: str
    retryable: bool
    message: str


# Hai câu cho hai hành động khác nhau. Người dùng chỉ có đúng hai lựa chọn —
# thử lại, hoặc báo cho ai đó — nên câu chữ phải rơi vào đúng một trong hai.
_CONTACT_SUPPORT = (
    "Hệ thống đang có sự cố cấu hình. Bạn liên hệ bộ phận hỗ trợ giúp mình nhé, thử lại lúc này chưa có tác dụng."
)
_TRY_AGAIN = "Hệ thống đang bận. Bạn thử lại sau ít phút giúp mình nhé."
_TRY_AGAIN_SERVICE = "Dịch vụ liên quan đang tạm gián đoạn. Bạn thử lại sau ít phút giúp mình nhé."
_TRY_AGAIN_DB = "Hệ thống đang tạm thời không lưu được dữ liệu. Bạn thử lại sau ít phút giúp mình nhé."

LLM_CONFIGURATION_ERROR = FailureKind("LLM_CONFIGURATION_ERROR", False, _CONTACT_SUPPORT)
LLM_AUTHENTICATION_ERROR = FailureKind("LLM_AUTHENTICATION_ERROR", False, _CONTACT_SUPPORT)
LLM_RATE_LIMITED = FailureKind("LLM_RATE_LIMITED", True, _TRY_AGAIN)
PROVIDER_UNAVAILABLE = FailureKind("PROVIDER_UNAVAILABLE", True, _TRY_AGAIN_SERVICE)
DATABASE_UNAVAILABLE = FailureKind("DATABASE_UNAVAILABLE", True, _TRY_AGAIN_DB)
VALIDATION_ERROR = FailureKind(
    "VALIDATION_ERROR",
    False,
    "Yêu cầu chưa đủ điều kiện để thực hiện. Bạn kiểm tra lại thông tin vừa nhập giúp mình nhé.",
)
EXECUTION_ERROR = FailureKind("EXECUTION_ERROR", True, _TRY_AGAIN_SERVICE)
SCHEDULE_CONFLICT_PERSISTENCE_ERROR = FailureKind(
    "SCHEDULE_CONFLICT_PERSISTENCE_ERROR",
    False,
    "Hệ thống tạm thời không lưu được kiểm tra xung đột lịch. Yêu cầu chưa được thực hiện, bạn liên hệ bộ phận hỗ trợ giúp mình nhé.",
)


_BY_CODE: dict[str, FailureKind] = {
    kind.code: kind
    for kind in (
        LLM_CONFIGURATION_ERROR,
        LLM_AUTHENTICATION_ERROR,
        LLM_RATE_LIMITED,
        PROVIDER_UNAVAILABLE,
        DATABASE_UNAVAILABLE,
        VALIDATION_ERROR,
        EXECUTION_ERROR,
        SCHEDULE_CONFLICT_PERSISTENCE_ERROR,
    )
}


def failure_for_code(code: str | None) -> FailureKind | None:
    """Dựng lại loại lỗi từ mã đã ghim trong PostgreSQL.

    Đây là đường đọc sau restart: cache trong tiến trình đã mất, chỉ còn mã.
    Mã lạ (ghi bởi một phiên bản cũ hơn) trả None thay vì đoán — đoán sai
    `retryable` sẽ mời người dùng thử lại một việc không bao giờ chạy được.
    """
    if not code:
        return None
    return _BY_CODE.get(code)


def classify_failure(exc: BaseException) -> FailureKind:
    """Map một exception sang loại lỗi công khai.

    Phân loại theo LOẠI exception, không theo nội dung message: message đến từ
    thư viện bên thứ ba và đổi bất cứ lúc nào, còn so chuỗi thì hỏng lặng lẽ.

    Import cục bộ để module này không kéo theo LangChain/asyncpg/httpx — nó
    được dùng cả ở những chỗ chỉ cần bảng mã.
    """
    from src.services.llm import (
        LLMAuthenticationError,
        LLMConfigurationError,
        LLMRateLimitedError,
    )

    if isinstance(exc, LLMConfigurationError):
        return LLM_CONFIGURATION_ERROR
    if isinstance(exc, LLMAuthenticationError):
        return LLM_AUTHENTICATION_ERROR
    if isinstance(exc, LLMRateLimitedError):
        return LLM_RATE_LIMITED

    # OpenAI SDK (dùng chung cho DeepSeek/OpenRouter vì API tương thích) có cây
    # exception riêng. Không import ở top-level: nó là dependency của tầng LLM,
    # còn module này thì không.
    try:
        from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError

        if isinstance(exc, AuthenticationError):
            return LLM_AUTHENTICATION_ERROR
        if isinstance(exc, RateLimitError):
            return LLM_RATE_LIMITED
        if isinstance(exc, APIConnectionError):
            return PROVIDER_UNAVAILABLE
        if isinstance(exc, APIStatusError):
            # 4xx của nhà cung cấp thường là sai cấu hình/quyền — thử lại vô ích.
            status = getattr(exc, "status_code", None)
            if isinstance(status, int) and 400 <= status < 500 and status != 429:
                return LLM_CONFIGURATION_ERROR
            return PROVIDER_UNAVAILABLE
    except ImportError:  # pragma: no cover - openai luôn có trong runtime
        pass

    try:
        import asyncpg

        if isinstance(exc, asyncpg.PostgresConnectionError | asyncpg.PostgresError):
            return DATABASE_UNAVAILABLE
    except ImportError:  # pragma: no cover
        pass

    try:
        import httpx

        if isinstance(exc, httpx.HTTPError):
            return PROVIDER_UNAVAILABLE
    except ImportError:  # pragma: no cover
        pass

    if isinstance(exc, ConnectionError | TimeoutError):
        return PROVIDER_UNAVAILABLE

    # Chưa phân loại được thì coi là lỗi thực thi và CHO PHÉP thử lại. Mặc định
    # ngược lại — chặn thử lại — sẽ biến một trục trặc thoáng qua thành hỏng
    # vĩnh viễn dưới mắt người dùng.
    return EXECUTION_ERROR
