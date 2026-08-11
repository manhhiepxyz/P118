"""Test cấu hình LLM provider; không gọi network và không cần API key thật."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from src.config import Settings
from src.services.llm import LLMConfigurationError, get_llm


def _secret_value(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None


def test_openai_is_default_provider() -> None:
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
