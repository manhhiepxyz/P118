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

    # Rate limiter — CHỈ áp cho POST route tiêu thụ LLM; GET polling miễn trừ.
    rate_limit_per_minute: int = Field(default=20, ge=1)
    rate_limit_burst: int = Field(default=10, ge=1)
    rate_limit_enabled: bool = True

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
    # Mock ownership provider — xác thực căn hộ + verification_records (provider duyệt).
    ownership_service_url: str = "http://localhost:8004"
    # Mỗi tool một service, mỗi service một cổng. Trước đây PropertyConnector và
    # TourConnector cùng trỏ 8005 nhưng mock-tour không có /api/properties/search,
    # còn ResidentServicesConnector trỏ 8006 nơi Docker đang chạy shuttle — cả hai
    # là 404 lúc chạy thật mà test in-process không thấy.
    tour_service_url: str = "http://localhost:8005"
    consultation_service_url: str = "http://localhost:8007"
    # Đặt xe đưa đón tham quan — tool `book_shuttle`, chạy mặc định (compose
    # service mock-shuttle đã bỏ profile experimental).
    shuttle_service_url: str = "http://localhost:8009"

    # Auth (JWT-like HMAC token — stdlib only, xem src/api/auth.py)
    # Rỗng thì tạo token phải 500: fail-closed là hành vi đúng, không được nới.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24h — demo, không cần refresh token

    # Vector Store
    chroma_persist_dir: str = "./data/chroma"

    # Reconciliation (release-on-failure + zombie sweep) — Phase B
    # `payment_approval_ttl_hours`: yêu cầu thanh toán không được quyết định
    # trong thời gian này sẽ bị coi là hết hạn (expire → CANCELLED + release).
    # `zombie_sweep_*`: quét workflow mồ côi (RUNNING/PENDING không còn process
    # sống) khi poll danh sách; tắt trong test để tránh sweep đụng DB thật.
    payment_approval_ttl_hours: int = 24
    zombie_sweep_enabled: bool = True
    zombie_sweep_interval_seconds: int = 300
    zombie_running_ttl_hours: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
