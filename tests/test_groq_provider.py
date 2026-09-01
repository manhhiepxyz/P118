"""Groq là provider thứ tư — cùng khuôn với ba cái đã có."""

from __future__ import annotations

import pytest

from src.config import Settings
from src.services.llm import (
    LLMConfigurationError,
    check_llm_configuration,
    get_llm,
    structured_output_method,
)


def _settings(**over) -> Settings:
    base = {
        "llm_provider": "groq",
        "groq_api_key": "khoa-gia-cho-test",
        "groq_model_name": "openai/gpt-oss-20b",
    }
    base.update(over)
    return Settings(**base)


def test_a_configured_groq_passes_the_startup_check():
    check_llm_configuration(_settings())


def test_a_missing_key_is_refused_at_startup_not_at_click_time():
    with pytest.raises(LLMConfigurationError, match="GROQ_API_KEY"):
        check_llm_configuration(_settings(groq_api_key=""))


def test_a_deepseek_model_name_left_behind_is_caught():
    """Đúng lỗi đã xảy ra với OpenRouter: đổi provider mà quên đổi tên model.

    Không chặn ở đây thì Groq từ chối lúc GỌI — lỗi nổ ra khi người dùng đã bấm
    nút, thay vì lúc khởi động.
    """
    with pytest.raises(LLMConfigurationError, match="DeepSeek"):
        check_llm_configuration(_settings(groq_model_name="deepseek-v4-flash"))


def test_the_client_points_at_groq():
    client = get_llm(_settings())
    assert client.model_name == "openai/gpt-oss-20b"
    assert "groq.com" in str(client.openai_api_base)


def test_fast_mode_never_sends_reasoning_effort_to_groq():
    """Groq trả 400 cho `reasoning_effort` trên model không suy luận.

    Và 400 đó xảy ra ở ĐÚNG lớp trả lời cho người dùng (`fast=True` chỉ dùng ở
    Response Agent), nên mọi câu trả lời sẽ hỏng — không phải một tính năng
    kém đi, mà là hỏng hẳn.
    """
    client = get_llm(_settings(), fast=True)
    kwargs = getattr(client, "model_kwargs", {}) or {}
    assert "reasoning_effort" not in kwargs
    assert getattr(client, "reasoning_effort", None) is None


def test_groq_uses_function_calling_for_structured_output():
    assert structured_output_method(_settings()) == "function_calling"


def test_deepseek_still_disables_reasoning_in_fast_mode():
    """Chốt ngược: thêm Groq KHÔNG được làm mất tối ưu đã đo được của DeepSeek."""
    client = get_llm(
        Settings(llm_provider="deepseek", deepseek_api_key="khoa-gia", deepseek_model_name="deepseek-v4-flash"),
        fast=True,
    )
    kwargs = getattr(client, "model_kwargs", {}) or {}
    assert kwargs.get("reasoning_effort") == "none" or getattr(client, "reasoning_effort", None) == "none"
