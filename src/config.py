from functools import lru_cache
from typing import Literal

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
    # Chặn BÙNG PHÁT, không chặn dung lượng — `daily_workflow_quota` mới làm
    # việc đó, và nó là thứ giữ hoá đơn LLM.
    #
    # 20/phút + burst 10 được chọn khi bucket khoá theo IP, tức là dùng chung
    # cho mọi người. Giờ nó khoá theo PHIÊN, nên cùng con số ấy trở thành trần
    # của MỘT người — và nó chạm thật: sau 10 thao tác, người dùng bị hãm còn
    # một thao tác mỗi 3 giây, giữa lúc đang gõ liên tục. Đo được 11 lần 429
    # trong 25 phút dùng bình thường, tất cả ở `/workflows/demo/start`.
    #
    # 60/phút + burst 20 vẫn xa hơn tốc độ gõ của người thật, mà không hãm họ
    # giữa chừng. Trần chi phí không đổi: vẫn 50 workflow/ngày/người.
    rate_limit_per_minute: int = Field(default=60, ge=1)
    rate_limit_burst: int = Field(default=20, ge=1)
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

    # Cổng thanh toán thật (VNPay sandbox) — tùy chọn, mặc định mock.
    #
    # `payment_provider=mock` giữ nguyên luồng đồng bộ hiện có: duyệt là xong.
    # `payment_provider=vnpay` chuyển sang redirect + IPN bất đồng bộ: duyệt chỉ
    # MỞ PHIÊN thanh toán, tiền được xác nhận bởi callback máy-nói-chuyện-với-máy
    # của VNPay (`/api/v1/webhooks/vnpay/ipn`). Contract `pay_fee` không đổi —
    # `payment_status=PENDING` lần đầu được dùng thật cho phiên đang mở.
    #
    # Bật vnpay mà thiếu TMN/HASH_SECRET/public_base_url phải fail-fast lúc dựng
    # connector, không âm thầm rơi về mock — một phiên thanh toán mở nhầm là
    # một chỗ đỗ bị treo.
    payment_provider: Literal["mock", "vnpay"] = "mock"
    vnpay_tmn_code: str = ""
    vnpay_hash_secret: str = ""
    vnpay_payment_url: str = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
    vnpay_query_url: str = "https://sandbox.vnpayment.vn/merchant_webapi/api/transaction"
    # Phiên thanh toán sống tối đa bao lâu. Hết hạn: URL chết ở phía VNPay
    # (vnp_ExpireDate) và sweeper đánh dấu payment FAILED, nhả khóa đổi khu.
    vnpay_session_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    # Địa chỉ CÔNG KHAI của backend để VNPay gọi IPN (ngrok/deploy). Localhost
    # thuần không bao giờ nhận được callback từ máy chủ VNPay.
    public_base_url: str = ""
    # Địa chỉ frontend để trả trình duyệt user về trang kết quả sau khi VNPay
    # redirect về backend. Backend chỉ làm bưu điện, không render UI.
    frontend_base_url: str = "http://localhost:5173"

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

    # `abandoned_repair_ttl_hours`: workflow FAILED còn repair hint mà người
    # dùng không quay lại.
    #
    # `release_on_failure` cố ý KHÔNG chạy cho loại này — hint nghĩa là "người
    # dùng sẽ sửa input rồi chạy tiếp", và hoàn tác sẽ phá đúng thứ họ định
    # tiếp tục. Lập luận ấy đúng khi họ quay lại. Khi họ không quay lại thì
    # không ai gỡ, và chỗ đỗ vẫn giữ, capacity không về, phí vẫn tính.
    #
    # Đo được trên dữ liệu thật: 7 chỗ đỗ thuộc workflow FAILED/CANCELLED chưa
    # được hoàn.
    #
    # Dài hơn TTL của payment approval: sửa input là việc người dùng làm trong
    # ngày, không phải trong nửa giờ.
    abandoned_repair_ttl_hours: int = 48
    # Số yêu cầu ĐÃ KẾT THÚC giữ lại trong lịch sử mỗi người. Cũ hơn thì tự ẩn
    # (xoá mềm bằng `archived_at`, xem `trim_history_for_owner`). 0 = không cắt.
    history_keep_per_user: int = 15
    # Hạn ngạch NGÀY theo NGƯỜI DÙNG — thứ duy nhất thật sự chặn dùng vô hạn.
    #
    # `rate_limit_per_minute` chặn bùng phát tức thời, KHÔNG chặn dung lượng:
    # 60/phút vẫn cho phép 86.400 request/ngày. Nó khoá theo phiên, mà phiên
    # thì tạo mới được. Cắt lượt trong một cuộc trò chuyện cũng không chặn —
    # người dùng chỉ cần mở cuộc mới.
    #
    # Đo được: mỗi workflow ~12.264 token ≈ $0,00365. 50/ngày là trần
    # $0,18/người/ngày. Người dùng thật nhiều nhất hiện tại mới 112 lượt gọi LLM
    # TỔNG CỘNG từ đầu dự án, nên ngưỡng này không chạm ai.
    #
    # 0 = tắt.
    # Nâng từ 50 cho giai đoạn DEMO.
    #
    # 50 được chọn khi hạn ngạch còn đếm mọi dòng trong bảng; nó chạm thật —
    # tài khoản `thanhbao` hết suất giữa buổi thử. Một buổi demo là hàng chục
    # lượt thử đi thử lại, và hết suất giữa chừng thì không còn gì để trình bày.
    #
    # Chi phí: ~12.264 token mỗi tác vụ, nên 200/ngày là trần ~$0,73 mỗi người
    # mỗi ngày. Hạ lại về 50 sau demo bằng biến môi trường
    # `DAILY_WORKFLOW_QUOTA`, không cần build lại.
    daily_workflow_quota: int = 200
    daily_quota_window_hours: int = 24

    # Email / SMTP
    smtp_host: str | None = Field(default=None, description="SMTP server host, e.g. smtp.gmail.com")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_username: str | None = Field(default=None, description="SMTP account username/email")
    smtp_password: str | None = Field(default=None, description="SMTP account password (or App Password)")
    # Tài khoản xác thực SMTP không nhất thiết là địa chỉ người gửi. Resend,
    # chẳng hạn, dùng username cố định `resend` nhưng yêu cầu From thuộc domain
    # đã xác minh. Tách hai khái niệm để không phát thư với From="resend".
    smtp_from_email: str | None = Field(default=None, description="Verified From address")
    smtp_from_name: str = Field(default="P-118", description="Friendly sender name")
    smtp_reply_to: str | None = Field(default=None, description="Optional Reply-To address")


@lru_cache
def get_settings() -> Settings:
    return Settings()
