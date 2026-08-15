"""`/ready` phải đỏ đúng lúc hệ thống không nhận việc được.

Sự cố nó sinh ra để chặn: Docker Compose báo mọi service healthy, backend chạy
với một `LLM_PROVIDER` không có key tương ứng, và mọi workflow chết ngay ở bước
lập kế hoạch. Healthcheck lúc đó gọi `/health` — endpoint chỉ nói tiến trình
còn sống, và nó nói đúng.
"""

from __future__ import annotations

import pytest

from src.api.readiness import evaluate_readiness
from src.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "llm_provider": "deepseek",
        "deepseek_api_key": "khoa-gia-cho-test",
        "deepseek_model_name": "deepseek-v4-flash",
        "openrouter_api_key": "",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_ready_is_green_when_everything_is_configured(client, db_pool):
    response = await client.get("/ready")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert {c["name"] for c in body["checks"]} == {"llm_config", "database", "migrations", "connectors"}


@pytest.mark.asyncio
async def test_health_still_answers_a_different_question(client):
    """`/health` KHÔNG được đỏ theo cấu hình — nó nói về tiến trình."""
    assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_a_provider_key_mismatch_makes_ready_red(db_pool):
    """Đúng cấu hình đã gây sự cố: provider openrouter, chỉ có key DeepSeek."""
    ok, checks = await evaluate_readiness(
        _settings(llm_provider="openrouter", deepseek_api_key="khoa-gia", openrouter_api_key="")
    )

    assert ok is False
    llm = next(c for c in checks if c["name"] == "llm_config")
    assert llm["ok"] is False
    assert "OPENROUTER_API_KEY" in llm["detail"]


@pytest.mark.asyncio
async def test_a_missing_provider_url_makes_ready_red(db_pool):
    ok, checks = await evaluate_readiness(_settings(transport_service_url=""))

    assert ok is False
    connectors = next(c for c in checks if c["name"] == "connectors")
    assert connectors["ok"] is False
    assert "TRANSPORT_SERVICE_URL" in connectors["detail"]


@pytest.mark.asyncio
async def test_a_malformed_provider_url_makes_ready_red(db_pool):
    """`localhost:8002` thiếu scheme — httpx sẽ hỏng lúc gọi, không phải lúc này."""
    ok, checks = await evaluate_readiness(_settings(payment_service_url="localhost:8003"))

    assert ok is False
    connectors = next(c for c in checks if c["name"] == "connectors")
    assert "PAYMENT_SERVICE_URL" in connectors["detail"]


@pytest.mark.asyncio
async def test_every_check_runs_even_after_one_fails(db_pool):
    """Dừng ở lỗi đầu tiên bắt người vận hành sửa - chạy lại - sửa tiếp."""
    ok, checks = await evaluate_readiness(
        _settings(llm_provider="openrouter", openrouter_api_key="", resident_service_url="")
    )

    assert ok is False
    # Cả hai lỗi phải cùng xuất hiện trong MỘT lần chạy: người vận hành cần
    # nhìn thấy hết trong một lần, thay vì sửa một cái rồi chạy lại để lộ ra
    # cái kế tiếp.
    failed = {c["name"] for c in checks if not c["ok"]}
    assert {"llm_config", "connectors"} <= failed, failed
    assert len(checks) == 4, "có hạng mục bị bỏ qua sau lỗi đầu tiên"


@pytest.mark.asyncio
async def test_the_response_never_carries_a_key_or_a_dsn(client, db_pool):
    """`/ready` hay được mở cho load balancer, nên nó là bề mặt công khai."""
    raw = (await client.get("/ready")).text

    assert "postgresql://" not in raw
    assert "sk-" not in raw
    assert "password" not in raw.lower()
    assert "DATABASE_URL" not in raw


@pytest.mark.asyncio
async def test_readiness_does_not_call_the_model(db_pool, monkeypatch):
    """Healthcheck chạy 15 giây một lần; gọi mô hình ở đây là tự đốt tiền."""
    from src.services import llm

    called: list[str] = []
    monkeypatch.setattr(llm, "get_llm", lambda *a, **k: called.append("built"))

    await evaluate_readiness(_settings())

    assert called == [], "readiness đã dựng client LLM"
