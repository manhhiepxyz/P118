from typing import Any

from langchain_openai import ChatOpenAI

from src.config import Settings, get_settings


class LLMConfigurationError(RuntimeError):
    """Cấu hình provider LLM chưa đủ để tạo client.

    Message chỉ nêu TÊN biến môi trường còn thiếu hoặc giá trị hợp lệ được
    phép — KHÔNG chứa API key, và không chứa URL: URL là chỗ credential hay đi
    nhờ dưới dạng `https://user:pass@host`.
    """


class LLMAuthenticationError(RuntimeError):
    """Nhà cung cấp từ chối khoá: hết hạn, bị thu hồi, hoặc sai tài khoản.

    Khác `LLMConfigurationError`: ở đây cấu hình ĐÚNG hình dạng, chỉ là khoá
    không còn dùng được. Cả hai đều không thử lại được, nhưng người xử lý khác
    nhau — một bên sửa biến môi trường, một bên xin khoá mới.
    """


class LLMRateLimitedError(RuntimeError):
    """Vượt hạn mức. Lỗi TẠM THỜI — thử lại sau là hợp lý."""


# Gate 2 chốt đúng một model DeepSeek.
#
# `deepseek-reasoner` bật chain-of-thought nên structured output không ổn định:
# Planner cần một schema Pydantic parse được mọi lần, không cần suy luận dài.
# `deepseek-chat` và các bản pro là model khác hẳn về giá lẫn hành vi. Khoá
# cứng ở đây để một biến môi trường gõ nhầm không lặng lẽ đổi model đang chạy.
DEEPSEEK_ALLOWED_MODEL = "deepseek-v4-flash"


def _require_key(value: str, variable_name: str) -> str:
    if not value.strip():
        raise LLMConfigurationError(f"Thiếu biến môi trường {variable_name}.")
    return value


def _require_deepseek_model(value: str) -> str:
    """Chốt model DeepSeek. Sai model là lỗi cấu hình, không phải cảnh báo."""
    if value.strip() != DEEPSEEK_ALLOWED_MODEL:
        raise LLMConfigurationError(f"DEEPSEEK_MODEL_NAME phải đúng '{DEEPSEEK_ALLOWED_MODEL}' trong Gate 2.")
    return value.strip()


def _require_openrouter_model(value: str) -> str:
    """OpenRouter định tuyến theo `nhà-cung-cấp/model`, nên tên phải có dấu `/`.

    Quy tắc này cũng chặn đúng kiểu gõ nhầm đã gây sự cố: đổi `LLM_PROVIDER`
    sang openrouter nhưng để nguyên tên model của DeepSeek. Không có dấu `/`
    thì OpenRouter sẽ từ chối lúc gọi — tức là lỗi nổ ra khi người dùng đã bấm
    nút, thay vì lúc khởi động.
    """
    model = value.strip()
    if not model:
        raise LLMConfigurationError("Thiếu biến môi trường OPENROUTER_MODEL_NAME.")
    if "/" not in model:
        raise LLMConfigurationError(
            "OPENROUTER_MODEL_NAME phải có dạng 'nhà-cung-cấp/model'; "
            "tên model của provider khác không dùng được ở đây."
        )
    return model


def check_llm_configuration(settings: Settings | None = None) -> None:
    """Kiểm cấu hình LLM mà KHÔNG gọi mạng. Sai thì raise.

    Vì sao cần một hàm riêng: `get_llm()` là factory lazy — nó chỉ raise khi có
    người gọi, mà người gọi đầu tiên là Planner, tức là sau khi người dùng đã
    gửi mục tiêu và workflow đã được tạo. Trên Docker, container vẫn báo
    healthy suốt thời gian đó.

    Hàm này chạy được lúc khởi động và trong `/ready`, nên cấu hình sai bị chặn
    trước khi hệ thống nhận việc. Nó dùng CHUNG các hàm kiểm với `get_llm()`,
    để không có chuyện kiểm xanh mà tạo client vẫn hỏng.

    Cố ý KHÔNG gọi thử một request tới nhà cung cấp: healthcheck lặp mỗi 30
    giây sẽ đốt tiền và tự tạo rate limit. Kiểm khoá thật là việc của một lệnh
    smoke chạy một lần khi deploy.
    """
    settings = settings or get_settings()
    provider = settings.llm_provider

    if provider == "deepseek":
        _require_key(settings.deepseek_api_key, "DEEPSEEK_API_KEY")
        _require_deepseek_model(settings.deepseek_model_name)
        return

    if provider == "openrouter":
        _require_key(settings.openrouter_api_key, "OPENROUTER_API_KEY")
        _require_openrouter_model(settings.openrouter_model_name)
        return

    if provider == "openai":
        _require_key(settings.openai_api_key, "OPENAI_API_KEY")
        if not settings.model_name.strip():
            raise LLMConfigurationError("Thiếu biến môi trường MODEL_NAME.")
        return

    raise LLMConfigurationError("LLM_PROVIDER không hợp lệ; chỉ chấp nhận: openai, openrouter, deepseek.")


def get_llm(settings: Settings | None = None, *, callbacks: list[Any] | None = None) -> ChatOpenAI:
    """Tạo LangChain chat model theo provider đã cấu hình.

    OpenRouter và DeepSeek đều dùng API tương thích OpenAI, nên cùng dùng
    ``ChatOpenAI`` với base URL riêng. Không provider nào được tạo ở import
    time; caller chủ động gọi factory này khi cần.

    `callbacks` (kw-only, backward-compatible): langchain callback để theo dõi
    usage (Phase D — `LlmUsageLogger`). Mọi caller cũ dùng positional settings
    nên không bị ảnh hưởng.

    KHÔNG có fallback. Trước đây mọi provider không phải "openrouter" đều rơi
    xuống nhánh OpenAI ở cuối hàm — nghĩa là gõ nhầm tên provider sẽ âm thầm
    gọi một nhà cung cấp khác, với một key khác, và không ai biết. Giờ mỗi
    provider có nhánh tường minh và giá trị lạ bị từ chối.
    """
    settings = settings or get_settings()
    provider = settings.llm_provider

    if provider == "deepseek":
        return ChatOpenAI(
            model=_require_deepseek_model(settings.deepseek_model_name),
            api_key=_require_key(settings.deepseek_api_key, "DEEPSEEK_API_KEY"),
            base_url=settings.deepseek_base_url,
            temperature=settings.llm_temperature,
            callbacks=callbacks,
        )

    if provider == "openrouter":
        return ChatOpenAI(
            model=_require_openrouter_model(settings.openrouter_model_name),
            api_key=_require_key(settings.openrouter_api_key, "OPENROUTER_API_KEY"),
            base_url=settings.openrouter_base_url,
            temperature=settings.llm_temperature,
            callbacks=callbacks,
        )

    if provider == "openai":
        return ChatOpenAI(
            model=settings.model_name,
            api_key=_require_key(settings.openai_api_key, "OPENAI_API_KEY"),
            temperature=settings.llm_temperature,
            callbacks=callbacks,
        )

    # `Settings.llm_provider` là Literal nên Pydantic đã chặn giá trị lạ. Nhánh
    # này bắt trường hợp caller tự dựng một Settings-like object bỏ qua
    # validation — vẫn phải từ chối, không được đoán provider thay họ.
    raise LLMConfigurationError("LLM_PROVIDER không hợp lệ; chỉ chấp nhận: openai, openrouter, deepseek.")


def structured_output_method(settings: Settings | None = None) -> str | None:
    """Cơ chế structured output phù hợp với provider đang dùng.

    Trả None nghĩa là dùng mặc định của LangChain (function calling).

    DeepSeek V4 Flash chạy thinking mode: nó từ chối forced `tool_choice`
    ("Thinking mode does not support this tool_choice") và chưa mở
    `response_format: json_schema` ("This response_format type is unavailable
    now"). `json_mode` là đường còn lại. Output vẫn được validate bằng cùng một
    Pydantic model — đổi cơ chế truyền tải, không nới lỏng kiểm tra.
    """
    settings = settings or get_settings()
    return "json_mode" if settings.llm_provider == "deepseek" else None
