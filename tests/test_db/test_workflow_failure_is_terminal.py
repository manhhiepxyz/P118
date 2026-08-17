"""Workflow chết giữa chừng phải KẾT THÚC, và phải kết thúc trong PostgreSQL.

Tái hiện sự cố thật trên Docker Compose: backend chạy với `LLM_PROVIDER` không
khớp key đang có, Planner ném `LLMConfigurationError`, và:

  - `workflows.status` nằm nguyên ở `PENDING` — lỗi chỉ được ghi vào
    `_DEMO_JOBS`, tức là RAM của một tiến trình.
  - Sau restart, cache mất, GET đọc `PENDING` và map thành `RUNNING`. Giao diện
    poll mãi một workflow đã chết từ lâu.
  - Response công khai mang `type(exc).__name__` — tên class nội bộ, không nói
    được cho người dùng nên thử lại hay liên hệ hỗ trợ.
  - Mọi nguyên nhân đều thành `EXECUTION_ERROR`, nên không phân biệt được
    "sai cấu hình" (gọi lại vô ích) với "provider bận" (thử lại được).
"""

from __future__ import annotations

import asyncio

import pytest

from src.api.routes import _run_demo_job as _original_run_demo_job
from tests.test_db.conftest import _register_and_login

GOAL = "Tôi muốn đăng ký xe và đặt chỗ đỗ xe."


async def _start_and_let_it_fail(client, db_pool, monkeypatch, username: str, error: Exception):
    """Chạy một workflow chắc chắn ném lỗi, rồi trả (workflow_id, headers).

    `/start` chạy với background task bị chặn, sau đó gọi thẳng `_run_demo_job`
    — cùng hàm production, nhưng chạy tuần tự nên không phải chờ hú hoạ.
    """
    from src.api import routes

    scheduled: list[tuple] = []

    async def _defer(*args, **kwargs):
        scheduled.append((args, kwargs))

    monkeypatch.setattr(routes, "_run_demo_job", _defer)

    token = await _register_and_login(client, username)
    headers = {"Authorization": f"Bearer {token}"}
    started = await client.post("/api/v1/workflows/demo/start", headers=headers, json={"goal": GOAL})
    assert started.status_code == 202, started.text
    workflow_id = started.json()["workflow_id"]

    # `/start` bọc job trong `asyncio.create_task`, nên coroutine `_defer` chỉ
    # thực sự chạy khi event loop nhường lượt.
    await asyncio.sleep(0)
    assert scheduled, "background job không được lên lịch"

    async def _boom(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(routes, "run_demo_workflow", _boom)
    args, kwargs = scheduled[0]
    # `routes._run_demo_job` đang bị thay bằng `_defer`, nên gọi tham chiếu gốc
    # đã bắt trước lúc patch.
    await _original_run_demo_job(*args, **kwargs)
    return workflow_id, headers


@pytest.mark.asyncio
async def test_a_configuration_failure_lands_in_postgresql_not_only_in_memory(client, db_pool, monkeypatch):
    """Defect chính: workflow lỗi vẫn PENDING trong database."""
    from src.services.llm import LLMConfigurationError

    workflow_id, _ = await _start_and_let_it_fail(
        client, db_pool, monkeypatch, "nn_fail_cfg", LLMConfigurationError("Thiếu biến môi trường X.")
    )

    status = await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    assert status is not None, "shell workflow không tồn tại"
    assert status != "PENDING", "workflow lỗi bị bỏ lại ở PENDING — zombie"
    assert status in {"FAILED", "CANCELLED"}, f"trạng thái kết thúc không hợp lệ: {status}"


@pytest.mark.asyncio
async def test_the_error_survives_a_restart(client, db_pool, monkeypatch):
    """`_DEMO_JOBS` mất thì người dùng vẫn phải thấy lỗi, không phải 'đang chạy'."""
    from src.api import routes
    from src.services.llm import LLMConfigurationError

    workflow_id, headers = await _start_and_let_it_fail(
        client, db_pool, monkeypatch, "nn_fail_restart", LLMConfigurationError("Thiếu biến môi trường X.")
    )

    # Đúng cái xảy ra khi container khởi động lại: cache trong tiến trình biến mất.
    routes._DEMO_JOBS.pop(workflow_id, None)

    body = (await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers=headers)).json()

    assert body["status"] not in {"PENDING", "RUNNING"}, "sau restart vẫn báo đang chạy"
    assert body.get("error_code") == "LLM_CONFIGURATION_ERROR"
    assert body.get("retryable") is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_factory", "expected_code", "retryable"),
    [
        ("llm_config", "LLM_CONFIGURATION_ERROR", False),
        ("llm_auth", "LLM_AUTHENTICATION_ERROR", False),
        ("llm_rate", "LLM_RATE_LIMITED", True),
        ("provider_down", "PROVIDER_UNAVAILABLE", True),
        ("db_down", "DATABASE_UNAVAILABLE", True),
        ("unknown", "EXECUTION_ERROR", True),
    ],
)
async def test_each_failure_kind_gets_its_own_stable_code(
    client, db_pool, monkeypatch, error_factory, expected_code, retryable
):
    """Một mã lỗi cho mọi nguyên nhân thì người dùng không biết nên làm gì.

    "Sai cấu hình" gọi lại bao nhiêu lần cũng hỏng; "provider bận" thì thử lại
    là đúng. Hai việc khác nhau nên phải là hai mã khác nhau.
    """
    import asyncpg
    import httpx

    from src.services.llm import LLMAuthenticationError, LLMConfigurationError, LLMRateLimitedError

    errors = {
        "llm_config": LLMConfigurationError("Thiếu biến môi trường X."),
        "llm_auth": LLMAuthenticationError("Nhà cung cấp từ chối khoá."),
        "llm_rate": LLMRateLimitedError("Vượt hạn mức."),
        "provider_down": httpx.ConnectError("connection refused"),
        "db_down": asyncpg.PostgresConnectionError("server closed the connection"),
        "unknown": RuntimeError("một lỗi chưa phân loại"),
    }

    workflow_id, headers = await _start_and_let_it_fail(
        client, db_pool, monkeypatch, f"nn_fail_{error_factory}", errors[error_factory]
    )
    body = (await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers=headers)).json()

    assert body.get("error_code") == expected_code
    assert body.get("retryable") is retryable


@pytest.mark.asyncio
async def test_the_public_response_never_carries_the_exception_class(client, db_pool, monkeypatch):
    """`Workflow unavailable (LLMConfigurationError)` là chi tiết nội bộ."""
    from src.services.llm import LLMConfigurationError

    workflow_id, headers = await _start_and_let_it_fail(
        client,
        db_pool,
        monkeypatch,
        "nn_fail_leak",
        LLMConfigurationError("Thiếu biến môi trường OPENROUTER_API_KEY."),
    )
    raw = (await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers=headers)).text

    for leaked in ("LLMConfigurationError", "OPENROUTER_API_KEY", "Traceback", "Exception"):
        assert leaked not in raw, f"response lộ {leaked!r}"


@pytest.mark.asyncio
async def test_the_message_tells_the_user_what_to_do(client, db_pool, monkeypatch):
    """Sai cấu hình → liên hệ hỗ trợ. Provider bận → thử lại. Hai câu khác nhau."""
    from src.services.llm import LLMConfigurationError, LLMRateLimitedError

    config_id, headers = await _start_and_let_it_fail(
        client, db_pool, monkeypatch, "nn_fail_msg_cfg", LLMConfigurationError("x")
    )
    config_body = (await client.get(f"/api/v1/workflows/demo/{config_id}", headers=headers)).json()

    rate_id, headers2 = await _start_and_let_it_fail(
        client, db_pool, monkeypatch, "nn_fail_msg_rate", LLMRateLimitedError("x")
    )
    rate_body = (await client.get(f"/api/v1/workflows/demo/{rate_id}", headers=headers2)).json()

    config_text = f"{config_body.get('summary') or ''} {config_body.get('message') or ''}"
    rate_text = f"{rate_body.get('summary') or ''} {rate_body.get('message') or ''}"

    assert "hỗ trợ" in config_text.lower(), config_text
    assert "thử lại" in rate_text.lower(), rate_text
    assert config_text.strip() != rate_text.strip(), "hai loại lỗi dùng chung một câu"


@pytest.mark.asyncio
async def test_a_failed_workflow_keeps_its_untouched_steps_out_of_success(client, db_pool, monkeypatch):
    """Bước chưa chạy không được ghi là đã chạy."""
    from src.services.llm import LLMConfigurationError

    workflow_id, _ = await _start_and_let_it_fail(
        client, db_pool, monkeypatch, "nn_fail_tasks", LLMConfigurationError("x")
    )

    rows = await db_pool.fetch("SELECT status FROM workflow_tasks WHERE workflow_id = $1::uuid", workflow_id)
    assert all(r["status"] != "SUCCESS" for r in rows), "task chưa chạy bị đánh dấu thành công"
