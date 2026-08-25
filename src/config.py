from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic import AliasChoices, Field
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
    llm_provider: Literal["openai", "openrouter", "deepseek", "groq"] = "openai"
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

    # Groq — API tương thích OpenAI, chạy trên phần cứng riêng nên nhanh hơn
    # hẳn ở cùng một model. KHÔNG khoá cứng tên model như DeepSeek: danh mục
    # Groq đổi thường xuyên và họ gỡ model cũ, nên khoá lại sẽ biến một lần dọn
    # danh mục bên họ thành sự cố bên mình.
    groq_api_key: str = ""
    groq_model_name: str = "openai/gpt-oss-20b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
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

    # `auto_approve_viewing_seconds`: tự duyệt lịch tham quan sau N giây.
    #
    # 0 = TẮT, và đó là mặc định. Đây thuần tuý là tiện ích DEMO: khi trình bày
    # một mình thì không có ai ngồi ở cổng /review để bấm nút, và dừng lại giải
    # thích "giờ tôi đăng nhập bằng tài khoản khác" làm đứt mạch.
    #
    # Mặc định phải là tắt vì bật nó lên nghĩa là MỌI lịch tham quan đều được
    # chấp thuận mà không ai xem — trong một hệ thống thật thì đó là bỏ hẳn
    # bước kiểm soát, không phải một tuỳ chọn tiện lợi.
    #
    # Cổng /review vẫn hoạt động bình thường khi bật: ai bấm trước thì tính,
    # vì cả hai đường đi qua cùng `resume_viewing_after_approval` và cùng khoá
    # `WHERE status='AWAITING'`.
    #
    # Nhận cả `P118_AUTO_APPROVE_VIEWING_SECONDS` lẫn tên trần: settings ở đây
    # không có `env_prefix`, nên đặt tên có tiền tố P118_ (cho khớp
    # `P118_LLM_TRACE`) sẽ KHÔNG được đọc nếu không khai alias — và một biến
    # môi trường bị bỏ qua trong im lặng là kiểu lỗi mất nhiều thời gian nhất
    # để nhận ra: log không nói gì, tính năng chỉ đơn giản không chạy.
    auto_approve_viewing_seconds: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "P118_AUTO_APPROVE_VIEWING_SECONDS",
            "AUTO_APPROVE_VIEWING_SECONDS",
        ),
    )
    zombie_sweep_interval_seconds: int = 300
    zombie_running_ttl_hours: float = 0.5
    # Số yêu cầu ĐÃ KẾT THÚC giữ lại trong lịch sử mỗi người. Cũ hơn thì tự ẩn
    # (xoá mềm bằng `archived_at`, xem `trim_history_for_owner`). 0 = không cắt.
    history_keep_per_user: int = 15
    # Hạn ngạch NGÀY theo NGƯỜI DÙNG — thứ duy nhất thật sự chặn dùng vô hạn.
    #
    # Rate limit 20/phút (`rate_limit_per_minute`) chặn bùng phát tức thời,
    # nhưng nó khoá theo ĐỊA CHỈ IP: đổi mạng là reset, và 20/phút vẫn cho phép
    # 28.800 request/ngày. Cắt lượt trong một cuộc trò chuyện cũng không chặn —
    # người dùng chỉ cần mở cuộc mới.
    #
    # Đo được: mỗi workflow ~12.264 token ≈ $0,00365. 50/ngày là trần
    # $0,18/người/ngày. Người dùng thật nhiều nhất hiện tại mới 112 lượt gọi LLM
    # TỔNG CỘNG từ đầu dự án, nên ngưỡng này không chạm ai.
    #
    # 0 = tắt.
    daily_workflow_quota: int = 50
    daily_quota_window_hours: int = 24


@lru_cache
def get_settings() -> Settings:
    return Settings()
