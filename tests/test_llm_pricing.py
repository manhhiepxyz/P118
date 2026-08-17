"""tests/test_llm_pricing.py
P-118 — Bảng giá LLM + cost_summary (Phase D).

Khoá hành vi:
  - model có trong map → cost tính theo VND/1K token.
  - model không có → 0 (KHÔNG đoán giá).
  - cost_summary gộp đúng per-model + tổng, không chứa PII.
"""

from __future__ import annotations

from src.monitoring.llm_pricing import IS_PLACEHOLDER, cost_summary, estimate_cost


def test_estimate_cost_for_known_model() -> None:
    # deepseek-v4-flash: input 400, output 1600 VND/1K. 1000 in + 500 out:
    # 400*1 + 1600*0.5 = 400 + 800 = 1200.
    assert estimate_cost("deepseek-v4-flash", 1000, 500) == 1200.0


def test_estimate_cost_zero_for_unknown_model() -> None:
    assert estimate_cost("gpt-4o-mini", 100_000, 50_000) == 0.0
    assert estimate_cost("unknown", 1, 1) == 0.0


def test_estimate_cost_zero_tokens() -> None:
    assert estimate_cost("deepseek-v4-flash", 0, 0) == 0.0


def test_placeholder_flag_is_set_for_transparency() -> None:
    # Giá chưa được finance xác nhận — report phải gắn cờ PLACEHOLDER.
    assert IS_PLACEHOLDER is True


def test_cost_summary_aggregates_per_model_and_total() -> None:
    rows = [
        {"model": "deepseek-v4-flash", "prompt_tokens": 1000, "completion_tokens": 500},
        {"model": "deepseek-v4-flash", "prompt_tokens": 500, "completion_tokens": 500},
        {"model": "gpt-4o-mini", "prompt_tokens": 1000, "completion_tokens": 1000},
    ]

    summary = cost_summary(rows)

    flash = summary["per_model"]["deepseek-v4-flash"]
    assert flash["prompt_tokens"] == 1500
    assert flash["completion_tokens"] == 1000
    # 1500 in * 0.4 + 1000 out * 1.6 = 600 + 1600 = 2200
    assert flash["cost_vnd"] == 2200.0

    # Model chưa khai giá → cost 0, nhưng token vẫn đếm.
    gpt = summary["per_model"]["gpt-4o-mini"]
    assert gpt["prompt_tokens"] == 1000
    assert gpt["cost_vnd"] == 0.0

    assert summary["total_tokens"] == 4500
    assert summary["total_cost_vnd"] == 2200.0
    assert summary["is_placeholder"] is True
