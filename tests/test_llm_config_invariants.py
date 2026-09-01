"""Cấu hình LLM phải sai là biết ngay, không phải sai lúc người dùng bấm nút.

Sự cố thật: Docker Compose báo mọi service healthy, backend chạy với một
provider không có key tương ứng, và lỗi chỉ lộ ra khi Planner chạy — sau khi
người dùng đã gửi mục tiêu và workflow đã được tạo.

`get_llm()` là factory lazy: nó chỉ raise khi có người gọi. Vì vậy cần một hàm
kiểm riêng, chạy được lúc khởi động và trong `/ready`, không gọi mạng.
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.services.llm import (
    LLMConfigurationError,
    check_llm_configuration,
    structured_output_method,
)


def _settings(**overrides) -> Settings:
    base = {
        "llm_provider": "deepseek",
        "deepseek_api_key": "khoa-gia-cho-test",
        "deepseek_model_name": "deepseek-v4-flash",
        "openrouter_api_key": "",
        "openrouter_model_name": "openrouter/free",
        "openai_api_key": "",
        "model_name": "gpt-4o-mini",
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Cấu hình hợp lệ
# ---------------------------------------------------------------------------


def test_a_valid_deepseek_setup_passes():
    check_llm_configuration(_settings())


def test_a_valid_openrouter_setup_passes():
    check_llm_configuration(
        _settings(llm_provider="openrouter", openrouter_api_key="khoa-gia", openrouter_model_name="x/y")
    )


def test_a_valid_openai_setup_passes():
    check_llm_configuration(_settings(llm_provider="openai", openai_api_key="khoa-gia"))


# ---------------------------------------------------------------------------
# Đúng sự cố đã xảy ra: provider này, key của provider kia
# ---------------------------------------------------------------------------


def test_openrouter_with_only_a_deepseek_key_is_rejected():
    """Đây chính xác là cấu hình đã chạy trên Docker và báo healthy."""
    with pytest.raises(LLMConfigurationError) as exc:
        check_llm_configuration(
            _settings(llm_provider="openrouter", deepseek_api_key="khoa-gia", openrouter_api_key="")
        )
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_deepseek_without_its_key_is_rejected():
    with pytest.raises(LLMConfigurationError) as exc:
        check_llm_configuration(_settings(deepseek_api_key="   "))
    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_openai_without_its_key_is_rejected():
    with pytest.raises(LLMConfigurationError) as exc:
        check_llm_configuration(_settings(llm_provider="openai", openai_api_key=""))
    assert "OPENAI_API_KEY" in str(exc.value)


def test_deepseek_pinned_to_one_model():
    with pytest.raises(LLMConfigurationError) as exc:
        check_llm_configuration(_settings(deepseek_model_name="deepseek-chat"))
    assert "DEEPSEEK_MODEL_NAME" in str(exc.value)


def test_openrouter_needs_a_real_model_name():
    """Model rỗng thì lỗi phải nói ra ngay, không để provider từ chối lúc chạy."""
    with pytest.raises(LLMConfigurationError) as exc:
        check_llm_configuration(
            _settings(llm_provider="openrouter", openrouter_api_key="khoa-gia", openrouter_model_name="  ")
        )
    assert "OPENROUTER_MODEL_NAME" in str(exc.value)


def test_a_provider_may_not_borrow_another_providers_model():
    """`openrouter` + model của DeepSeek là cấu hình gõ nhầm, không phải hợp lệ."""
    with pytest.raises(LLMConfigurationError):
        check_llm_configuration(
            _settings(
                llm_provider="openrouter",
                openrouter_api_key="khoa-gia",
                openrouter_model_name="deepseek-v4-flash",
            )
        )


# ---------------------------------------------------------------------------
# Không rò rỉ
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_provider": "openrouter", "openrouter_api_key": ""},
        {"deepseek_api_key": ""},
        {"deepseek_model_name": "deepseek-chat"},
        {"llm_provider": "openai", "openai_api_key": ""},
    ],
)
def test_the_error_message_never_carries_a_key_or_a_credentialed_url(overrides):
    # Canary không mang dạng key thật, và biến không tên là `secret`.
    #
    # Repo có bộ quét secret chạy trên mọi file được track
    # (`tests/test_no_committed_secrets.py`). Nó bắt cả tiền tố `sk-` lẫn mẫu
    # `SECRET = "<32 ký tự trở lên>"`, và cố ý KHÔNG phân biệt key thật với key
    # giả — đúng như nó nên thế. Một canary trông giống key thật sẽ làm guard
    # đó đỏ vĩnh viễn, và một guard luôn đỏ là một guard đã bị tắt.
    #
    # Hình dạng canary không quan trọng ở đây: điều cần chứng minh là GIÁ TRỊ
    # của key không bao giờ được lặp lại trong message. Tiền tố `sk-` vẫn được
    # kiểm riêng ở assert phía dưới.
    planted = "canary-khong-duoc-xuat-hien-0123456789"
    settings = _settings(
        **{
            "deepseek_api_key": planted,
            "openrouter_api_key": planted,
            "openai_api_key": planted,
            **overrides,
        }
    )
    with pytest.raises(LLMConfigurationError) as exc:
        check_llm_configuration(settings)

    message = str(exc.value)
    assert planted not in message
    assert "sk-" not in message
    assert "://" not in message, "message chứa URL — URL là chỗ credential hay đi nhờ"


# ---------------------------------------------------------------------------
# Cùng một nguồn sự thật với factory
# ---------------------------------------------------------------------------


def test_whatever_the_check_accepts_the_factory_can_build(monkeypatch):
    """Hai đường không được lệch nhau: kiểm xanh mà tạo client vẫn hỏng là vô nghĩa."""
    from src.services import llm

    settings = _settings()
    check_llm_configuration(settings)
    client = llm.get_llm(settings)
    assert client is not None


def test_whatever_the_check_rejects_the_factory_also_rejects():
    from src.services import llm

    settings = _settings(llm_provider="openrouter", openrouter_api_key="")
    with pytest.raises(LLMConfigurationError):
        check_llm_configuration(settings)
    with pytest.raises(LLMConfigurationError):
        llm.get_llm(settings)


def test_structured_output_stays_json_mode_for_deepseek():
    assert structured_output_method(_settings()) == "json_mode"
    assert structured_output_method(_settings(llm_provider="openai", openai_api_key="k")) == "function_calling"
