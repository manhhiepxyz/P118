from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI20K Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"

    # LLM
    # `Literal` là allowlist provider. Giá trị ngoài danh sách bị Pydantic từ
    # chối ngay lúc nạp cấu hình, không âm thầm rơi về một provider mặc định.
    llm_provider: Literal["openai", "openrouter", "deepseek"] = "openai"
    openai_api_key: str = ""
    model_name: str = "gpt-4o-mini"
    openrouter_api_key: str = ""
    openrouter_model_name: str = "openrouter/free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # DeepSeek dùng API tương thích OpenAI.
    #
    # Gate 2 CHỐT đúng một model. `deepseek-reasoner` bật chain-of-thought nên
    # structured output không ổn định; `deepseek-chat` và các bản pro là model
    # khác hẳn về giá lẫn hành vi. Khoá lại để một biến môi trường gõ nhầm
    # không lặng lẽ đổi model đang chạy production demo.
    deepseek_api_key: str = ""
    deepseek_model_name: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"

    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    # Database
    database_url: str = "sqlite:///./data/app.db"

    # Service connectors
    resident_service_url: str = "http://localhost:8001"
    transport_service_url: str = "http://localhost:8002"
    payment_service_url: str = "http://localhost:8003"
    property_service_url: str = "http://localhost:8005"
    resident_services_service_url: str = "http://localhost:8006"

    # Vector Store
    chroma_persist_dir: str = "./data/chroma"


@lru_cache
def get_settings() -> Settings:
    return Settings()
