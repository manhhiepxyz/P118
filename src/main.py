import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.admin_routes import router as admin_router
from src.api.auth_routes import router as auth_router
from src.api.auth_routes import users_router
from src.api.middleware import RateLimitMiddleware
from src.api.notification_routes import router as notification_router
from src.api.observability import CorrelationIdMiddleware, setup_observability_logging
from src.api.readiness import evaluate_readiness
from src.api.routes import router
from src.api.verification_routes import router as verification_router
from src.api.service_approval_routes import router as service_approval_router
from src.api.viewing_approval_routes import router as viewing_approval_router
from src.config import get_settings
from src.monitoring.llm_trace import trace_enabled
from src.orchestration.auto_approve import auto_approve_due_viewings
from src.orchestration.deps import build_repository
from src.orchestration.runtime_provider import (
    SharedPool,
    clear_repository_provider,
    set_repository_provider,
)
from src.orchestration.sweeper import sweep_zombie_workflows
from src.services.llm import LLMConfigurationError, check_llm_configuration


async def _ready(repository):
    """Provider trả repository đã dựng sẵn — không mở kết nối mới."""
    return repository


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_observability_logging()
    
    settings = get_settings()
    logging.getLogger("p118.main").info(f"Starting {settings.app_name} in {settings.app_env} mode")

    # Composition root: dựng pool MỘT LẦN và đăng ký provider.
    #
    # Trước đây mỗi handler tự gọi `build_repository()` — đọc thẳng
    # DATABASE_URL và mở một pool mới cho từng request. Ngoài chi phí bắt tay
    # TCP mỗi lần chạm database, nó còn khiến test phải patch từng namespace
    # một; quên một module là route đó lặng lẽ đọc database phát triển.
    repository = await build_repository()
    repository._pool = SharedPool(repository._pool)  # noqa: SLF001 - composition root sở hữu pool
    set_repository_provider(lambda: _ready(repository))
    app.state.runtime = (None, repository)

    # Nói ra NGAY nếu cấu hình LLM sai, thay vì để lỗi nổ ra lúc người dùng bấm
    # nút. Chỉ log rồi vẫn khởi động, không raise: raise ở đây làm container
    # restart-loop, và dòng giải thích cuộn mất trong log của mười lần thử. App
    # sống nhưng `/ready` đỏ thì người vận hành vừa đọc được lý do, vừa không bị
    # Compose coi là healthy.
    try:
        check_llm_configuration(settings)
    except LLMConfigurationError as exc:
        logging.getLogger("p118.main").error(f"[CẤU HÌNH] LLM chưa dùng được: {exc} — /ready sẽ báo not_ready.")

    sweep_task = None
    if settings.zombie_sweep_enabled:
        # Loop nền dọn workflow mồ côi (payment approval hết hạn, RUNNING không
        # còn process). Lazy trigger ở list endpoints vẫn là đường chính cho
        # demo; loop này đảm bảo chạy kể cả khi không ai poll. Best-effort:
        # lỗi của một lần sweep không được làm chết loop.
        async def _sweep_forever() -> None:
            while True:
                try:
                    await sweep_zombie_workflows()
                except Exception:  # noqa: BLE001 - vòng sweep không được chết
                    logging.getLogger("p118.sweeper").exception("zombie sweep error")
                await asyncio.sleep(settings.zombie_sweep_interval_seconds)

        sweep_task = asyncio.create_task(_sweep_forever())
        logging.getLogger("p118.main").info("Zombie sweep loop started")

    # Tự duyệt lịch tham quan — CHỈ khi được bật tường minh. Xem
    # `src/orchestration/auto_approve.py` để biết vì sao mặc định là tắt.
    auto_task = None
    if settings.auto_approve_viewing_seconds > 0:
        delay = settings.auto_approve_viewing_seconds

        async def _auto_approve_forever() -> None:
            while True:
                try:
                    await auto_approve_due_viewings(delay)
                except Exception as exc:  # noqa: BLE001 - vòng lặp không được chết
                    logging.getLogger("p118.main").error(f"auto-approve error: {type(exc).__name__}")
                # Nhịp quét bằng 1/3 độ trễ, tối thiểu 5 giây: chờ đúng bằng
                # `delay` thì thời gian thực tế có thể gấp đôi khi yêu cầu đến
                # ngay sau một lượt quét, và người demo sẽ ngồi nhìn màn hình
                # lâu gấp đôi con số đã hứa.
                await asyncio.sleep(max(5, delay // 3))

        auto_task = asyncio.create_task(_auto_approve_forever())
        logging.getLogger("p118.main").info(f"Auto-approve viewing loop started ({delay}s) — CHẾ ĐỘ DEMO")
    yield

    clear_repository_provider()
    app.state.runtime = None
    # Đóng pool THẬT đúng một lần, ở đúng nơi đã tạo ra nó.
    await repository._pool._inner.close()  # noqa: SLF001 - composition root sở hữu pool

    for task in (sweep_task, auto_task):
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logging.getLogger("p118.main").info("Shutting down...")


if trace_enabled():  # pragma: no cover - phụ thuộc biến môi trường
    # uvicorn chỉ cấu hình logger của chính nó; logger ứng dụng không có handler
    # thì trace im lặng và người demo tưởng model không chạy.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("p118.llm.trace").setLevel(logging.INFO)


app = FastAPI(
    title="AI20K Agent",
    description="AI Agent built with LangGraph",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def safe_request_validation_error(_request, _exc: RequestValidationError) -> JSONResponse:
    """Không echo PII lẫn vị trí field kỹ thuật của request sai định dạng.

    Trước đây handler ghép `error["loc"]` vào detail, nên client nhận nguyên
    chuỗi `Dữ liệu không hợp lệ: body.goal`. Chuỗi đó vừa lộ schema vừa vô
    nghĩa với người dùng cuối, mà UI lại hiện thẳng nó trong khung chat. Detail
    giờ là một câu cố định; cần debug thì đọc log server, không đọc response.
    """
    return JSONResponse(
        status_code=422,
        content={"detail": "Yêu cầu chưa hợp lệ. Bạn kiểm tra lại thông tin vừa nhập giúp mình nhé."},
    )


settings = get_settings()

app.add_middleware(CorrelationIdMiddleware)

app.add_middleware(
    RateLimitMiddleware,
    requests_per_minute=settings.rate_limit_per_minute,
    burst=settings.rate_limit_burst,
    enabled=settings.rate_limit_enabled,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(router, prefix="/api/v1")
# Auth router của nhánh Hoàng Anh. Bị rơi khi đổi nền tích hợp sang gate2
# (main.py giải xung đột theo phía gate2) — mọi endpoint /auth/* trả 404.
app.include_router(auth_router, prefix="/api/v1")
# Đường DUY NHẤT ghi user_resident_links. Chặn bằng require_roles("admin").
app.include_router(admin_router, prefix="/api/v1")
# Xác thực căn hộ/xe có ảnh, provider duyệt (Path B song song với Agent).
app.include_router(verification_router, prefix="/api/v1")
# Yêu cầu lịch tham quan chờ duyệt — provider/admin quyết định trong /review.
# Hàng đợi duyệt của đơn vị cho SÁU dịch vụ ngoài lịch tham quan.
app.include_router(service_approval_router, prefix="/api/v1")
app.include_router(viewing_approval_router, prefix="/api/v1")
# Thông báo realtime: GET /api/v1/notifications/summary + /stream (SSE).
app.include_router(notification_router, prefix="/api/v1")
# Profile tự khai: PATCH /api/v1/users/me (multipart + avatar).
app.include_router(users_router, prefix="/api/v1")


# Ảnh giấy tờ xác thực. Thư mục phải tồn tại trước khi mount — StaticFiles
# không tự tạo. Đây là nơi duy nhất phục vụ file tải lên; mọi ảnh nằm dưới
# data/uploads nên URL `/uploads/...` không thể trỏ lung tung ra đĩa.
_uploads_dir = "./data/uploads"
Path(_uploads_dir).mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_uploads_dir), name="uploads")


# health
@app.get("/health")
async def health():
    """Liveness: tiến trình còn sống. KHÔNG nói gì về việc nhận việc được hay chưa.

    Đừng dùng endpoint này làm healthcheck của Docker. Nó đã từng là healthcheck,
    và hệ quả là Compose báo mọi service healthy trong khi backend chạy với một
    `LLM_PROVIDER` không có key tương ứng — mọi workflow đều chết ngay ở bước
    lập kế hoạch. Dùng `/ready`.
    """
    return {"status": "ok", "env": settings.app_env}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: cấu hình LLM, database, migration và connector đều hợp lệ.

    Trả 503 khi chưa sẵn sàng, để Docker healthcheck đánh dấu container
    unhealthy thay vì để nó nhận việc rồi hỏng lặng lẽ.
    """
    ok, checks = await evaluate_readiness()
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )
