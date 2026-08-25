"""Test cấu hình LLM provider; không gọi network và không cần API key thật."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from src.config import Settings
from src.services.llm import LLMConfigurationError, get_llm


def _secret_value(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None


def test_openai_is_default_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra default của Settings, không phụ thuộc cấu hình shell người chạy."""
    for variable in (
        "LLM_PROVIDER",
        "MODEL_NAME",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL_NAME",
        "OPENROUTER_BASE_URL",
    ):
        monkeypatch.delenv(variable, raising=False)

    settings = Settings(_env_file=None, openai_api_key="test-openai-key")

    llm = get_llm(settings)

    assert llm.model_name == "gpt-4o-mini"
    assert llm.openai_api_base is None
    assert _secret_value(llm.openai_api_key) == "test-openai-key"
    assert llm.temperature == 0.0


def test_openrouter_uses_openai_compatible_endpoint() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openrouter",
        openrouter_api_key="test-openrouter-key",
        openrouter_model_name="openrouter/free",
        llm_temperature=0,
    )

    llm = get_llm(settings)

    assert llm.model_name == "openrouter/free"
    assert str(llm.openai_api_base).rstrip("/") == "https://openrouter.ai/api/v1"
    assert _secret_value(llm.openai_api_key) == "test-openrouter-key"
    assert llm.temperature == 0.0


@pytest.mark.parametrize(
    ("provider", "expected_variable"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
    ],
)
def test_missing_provider_key_is_rejected_safely(provider: str, expected_variable: str) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider=provider,
        openai_api_key="",
        openrouter_api_key="",
    )

    with pytest.raises(LLMConfigurationError) as exc_info:
        get_llm(settings)

    assert expected_variable in str(exc_info.value)
    assert "sk-" not in str(exc_info.value)


def test_unknown_provider_is_rejected_by_settings() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="unknown")


# ---------------------------------------------------------------------------
# DeepSeek — Gate 2 chốt đúng một model
# ---------------------------------------------------------------------------

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _deepseek_settings(**overrides):
    from src.config import Settings

    base = {
        "llm_provider": "deepseek",
        "deepseek_api_key": "test-key-not-real",
        "deepseek_model_name": "deepseek-v4-flash",
        "deepseek_base_url": DEEPSEEK_BASE_URL,
    }
    base.update(overrides)
    return Settings(**base)


def test_deepseek_without_a_key_names_only_the_missing_variable() -> None:
    from src.services.llm import LLMConfigurationError, get_llm

    with pytest.raises(LLMConfigurationError) as exc_info:
        get_llm(_deepseek_settings(deepseek_api_key=""))

    message = str(exc_info.value)
    assert "DEEPSEEK_API_KEY" in message
    # Không nêu provider khác, không gợi ý fallback.
    assert "OPENAI" not in message.upper()
    assert "OPENROUTER" not in message.upper()


def test_deepseek_builds_a_client_on_the_official_base_url() -> None:
    from src.services.llm import get_llm

    model = get_llm(_deepseek_settings())

    assert model.model_name == "deepseek-v4-flash"
    assert str(model.openai_api_base).rstrip("/") == DEEPSEEK_BASE_URL


@pytest.mark.parametrize(
    "rejected_model",
    ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro", "deepseek-v3", "gpt-4o-mini", "", "   "],
)
def test_only_deepseek_v4_flash_is_accepted(rejected_model: str) -> None:
    """Một biến môi trường gõ nhầm không được lặng lẽ đổi model đang chạy."""
    from src.services.llm import LLMConfigurationError, get_llm

    with pytest.raises(LLMConfigurationError) as exc_info:
        get_llm(_deepseek_settings(deepseek_model_name=rejected_model))

    assert "DEEPSEEK_MODEL_NAME" in str(exc_info.value)
    assert "deepseek-v4-flash" in str(exc_info.value)


def test_deepseek_failure_never_falls_back_to_another_provider() -> None:
    """Thiếu key DeepSeek KHÔNG được âm thầm dùng key OpenAI/OpenRouter.

    Bản trước của `get_llm()` kết thúc bằng một `return ChatOpenAI(...)` không
    điều kiện, nên mọi provider không phải "openrouter" đều rơi xuống OpenAI —
    gõ nhầm tên provider sẽ gọi nhà cung cấp khác với key khác mà không ai biết.
    """
    from src.services.llm import LLMConfigurationError, get_llm

    settings = _deepseek_settings(
        deepseek_api_key="",
        openai_api_key="openai-key-should-not-be-used",
        openrouter_api_key="openrouter-key-should-not-be-used",  # secret-fixture
    )

    with pytest.raises(LLMConfigurationError):
        get_llm(settings)


def test_unknown_provider_is_also_rejected_by_the_factory() -> None:
    """Phòng thủ khi caller tự dựng object bỏ qua validation của Pydantic."""
    from types import SimpleNamespace

    from src.services.llm import LLMConfigurationError, get_llm

    fake = SimpleNamespace(
        llm_provider="anthropic",
        openai_api_key="k",
        model_name="gpt-4o-mini",
        openrouter_api_key="k",
        openrouter_model_name="m",
        openrouter_base_url="u",
        deepseek_api_key="k",
        deepseek_model_name="deepseek-v4-flash",
        deepseek_base_url=DEEPSEEK_BASE_URL,
        llm_temperature=0.0,
    )

    with pytest.raises(LLMConfigurationError):
        get_llm(fake)


def test_error_messages_never_contain_the_key() -> None:
    from src.services.llm import LLMConfigurationError, get_llm

    secret = "sk-deepseek-super-secret-value"  # secret-fixture
    with pytest.raises(LLMConfigurationError) as exc_info:
        get_llm(_deepseek_settings(deepseek_api_key=secret, deepseek_model_name="deepseek-chat"))

    assert secret not in str(exc_info.value)


def test_importing_the_module_needs_no_api_key() -> None:
    """Import không được tạo client: test/CI phải chạy được khi chưa có key."""
    import importlib

    module = importlib.import_module("src.services.llm")

    assert hasattr(module, "get_llm")
    assert module.DEEPSEEK_ALLOWED_MODEL == "deepseek-v4-flash"


def test_no_real_api_key_lives_in_a_tracked_file() -> None:
    """Key chỉ được sống trong `.env` (đã gitignore).

    File được commit chỉ được chứa placeholder, giá trị rỗng hoặc tham chiếu
    môi trường. Nhận diện placeholder bằng DẤU HIỆU chứ không bằng độ dài:
    `.env.example` có quyền dùng "sk-or-v1-your-key-here" làm ví dụ minh hoạ,
    và nó dài hơn nhiều key thật ngắn.
    """
    import re

    root = Path(__file__).parents[1]
    tracked = ("docker-compose.yml", ".env.example", "src/services/llm.py", "src/config.py", "README.md")
    # `[^\S\n]*` thay vì `\s*`: `\s` nuốt cả xuống dòng, nên một dòng
    # `OPENROUTER_API_KEY=` (giá trị rỗng) sẽ bắt nhầm token của dòng kế tiếp.
    assignment = re.compile(r"([A-Z_]*API_KEY)[^\S\n]*[:=][^\S\n]*(\S*)")
    placeholder_markers = ("your", "here", "...", "xxx", "change", "<", "example", "not-real", "test-key")

    for relative in tracked:
        path = root / relative
        if not path.exists():
            continue
        for name, value in assignment.findall(path.read_text(encoding="utf-8")):
            cleaned = value.strip().strip("\"'")
            lowered = cleaned.lower()
            harmless = (
                not cleaned or cleaned.startswith("${") or any(marker in lowered for marker in placeholder_markers)
            )
            assert harmless, f"{relative}: {name} có vẻ chứa key thật"


def test_fast_mode_turns_reasoning_off_and_normal_mode_leaves_it_alone(monkeypatch) -> None:
    """`fast=True` chỉ dành cho tầng DIỄN ĐẠT, không cho tầng quyết định.

    Đo trên `deepseek-v4-flash`, cùng tải thật:

        Response Agent  có suy luận  trung vị 3.6s  p90 11.8s  đúng 10/10
                        TẮT          trung vị 1.3s  p90  1.6s  đúng 10/10
        Planner         có suy luận  trung vị 5.8s  max 40.1s  đúng  5/6
                        TẮT          trung vị 1.1s  max  1.2s  đúng  4/6

    Tắt ở Planner là mất một kế hoạch đúng. Test này giữ ranh giới đó: mặc định
    KHÔNG được lặng lẽ thành `fast`.
    """
    from src.config import Settings
    from src.services.llm import get_llm

    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="khoa-gia-cho-test",
        deepseek_model_name="deepseek-v4-flash",
    )

    normal = get_llm(settings)
    fast = get_llm(settings, fast=True)

    assert getattr(fast, "reasoning_effort", None) == "none"
    # Mặc định phải giữ nguyên hành vi model — Planner đi qua đường này.
    assert getattr(normal, "reasoning_effort", None) != "none"
