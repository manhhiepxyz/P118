from langchain_openai import ChatOpenAI

from src.config import Settings, get_settings


class LLMConfigurationError(RuntimeError):
    """Cấu hình provider LLM chưa đủ để tạo client.

    Message chỉ nêu tên biến môi trường còn thiếu hoặc giá trị hợp lệ được
    phép, KHÔNG bao giờ chứa API key.
    """


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


def get_llm(settings: Settings | None = None) -> ChatOpenAI:
    """Tạo LangChain chat model theo provider đã cấu hình.

    OpenRouter và DeepSeek đều dùng API tương thích OpenAI, nên cùng dùng
    ``ChatOpenAI`` với base URL riêng. Không provider nào được tạo ở import
    time; caller chủ động gọi factory này khi cần.

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
        )

    if provider == "openrouter":
        return ChatOpenAI(
            model=settings.openrouter_model_name,
            api_key=_require_key(settings.openrouter_api_key, "OPENROUTER_API_KEY"),
            base_url=settings.openrouter_base_url,
            temperature=settings.llm_temperature,
        )

    if provider == "openai":
        return ChatOpenAI(
            model=settings.model_name,
            api_key=_require_key(settings.openai_api_key, "OPENAI_API_KEY"),
            temperature=settings.llm_temperature,
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
