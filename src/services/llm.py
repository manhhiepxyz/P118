from langchain_openai import ChatOpenAI

from src.config import Settings, get_settings


class LLMConfigurationError(RuntimeError):
    """Cấu hình provider LLM chưa đủ để tạo client.

    Message chỉ nêu tên biến môi trường còn thiếu, không bao giờ chứa key.
    """


def _require_key(value: str, variable_name: str) -> str:
    if not value.strip():
        raise LLMConfigurationError(f"Thiếu biến môi trường {variable_name}.")
    return value


def get_llm(settings: Settings | None = None) -> ChatOpenAI:
    """Tạo LangChain chat model theo provider đã cấu hình.

    OpenRouter dùng API tương thích OpenAI, nên tiếp tục dùng ``ChatOpenAI``
    với base URL chính thức của OpenRouter. Không provider nào được tạo ở
    import time; caller chủ động gọi factory này khi cần.
    """
    settings = settings or get_settings()

    if settings.llm_provider == "openrouter":
        return ChatOpenAI(
            model=settings.openrouter_model_name,
            api_key=_require_key(settings.openrouter_api_key, "OPENROUTER_API_KEY"),
            base_url=settings.openrouter_base_url,
            temperature=settings.llm_temperature,
        )

    return ChatOpenAI(
        model=settings.model_name,
        api_key=_require_key(settings.openai_api_key, "OPENAI_API_KEY"),
        temperature=settings.llm_temperature,
    )
