from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.auth_routes import router as auth_router
from src.api.routes import router
from src.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    # Dựng runtime (connectors + PostgreSQL repository) một lần; giữ app.state
    # để các route đọc qua dependency get_runtime. Nếu DB chưa lên, log và
    # tiếp tục để /health vẫn phục vụ — route workflow sẽ trả 503.
    try:
        from src.orchestration.deps import build_execution_boundary

        app.state.runtime = await build_execution_boundary()
    except Exception as exc:  # noqa: BLE001 — không để startup crash khi DB chưa sẵn sàng
        print(f"Runtime init failed: {type(exc).__name__}")
        app.state.runtime = None
    yield
    runtime = getattr(app.state, "runtime", None)
    if runtime:
        _, repository = runtime
        close = getattr(repository, "close", None)
        if close is not None:
            await close()
    print("Shutting down...")


app = FastAPI(
    title="AI20K Agent",
    description="AI Agent built with LangGraph",
    version="1.0.0",
    lifespan=lifespan,
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
app.include_router(auth_router, prefix="/api/v1")


# health
@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}
