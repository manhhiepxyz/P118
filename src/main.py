import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from src.api.admin_routes import router as admin_router
from src.api.auth_routes import router as auth_router
from src.api.middleware import RateLimitMiddleware
from src.api.routes import router
from src.config import get_settings
from src.orchestration.sweeper import sweep_zombie_workflows


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
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
                    print("zombie sweep error", exc_info=True)
                await asyncio.sleep(settings.zombie_sweep_interval_seconds)

        sweep_task = asyncio.create_task(_sweep_forever())
        print("Zombie sweep loop started")
    yield
    if sweep_task is not None:
        sweep_task.cancel()
        try:
            await sweep_task
        except asyncio.CancelledError:
            pass
    print("Shutting down...")


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

_DEMO_HTML = Path(__file__).resolve().parents[1] / "static" / "demo.html"


@app.get("/demo", include_in_schema=False)
async def demo_ui() -> FileResponse:
    """Trang HTML một file cho Gate 2 demo."""
    return FileResponse(
        _DEMO_HTML,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# health
@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}
