from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from src.api.routes import router
from src.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    yield
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
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")

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
