"""Bảng giá LLM theo model — ước tính cost cho report (Phase D).

CẢNH BÁO: giá trong file này là PLACEHOLDER. Trước khi report cost là thật,
owner finance phải xác nhận. `estimate_cost` luôn trả về đúng theo map; nếu
model không có trong map → 0 (không đoán giá).

Đơn vị: VND trên 1K token. Có `is_placeholder=True` ở mức module để report
script gắn cảnh báo vào output khi chưa xác nhận.
"""

from __future__ import annotations

from typing import Any

# Giá VND / 1K token. Key là tên model ĐẦY ĐỦ (cột `model` trong llm_usage).
# PLACEHOLDER — cần owner finance xác nhận trước khi coi là thật.
PRICE_PER_1K_TOKENS: dict[str, dict[str, int]] = {
    "deepseek-v4-flash": {
        "input": 400,  # PLACEHOLDER VND/1K input tokens
        "output": 1600,  # PLACEHOLDER VND/1K output tokens
    },
    # Các model khác chưa được khai — estimate_cost trả 0 thay vì đoán.
}

# Đúng: chưa xác nhận giá → báo cho người đọc report biết đây là ước tính.
IS_PLACEHOLDER = True


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Ước tính cost (VND) cho một lần gọi LLM.

    Trả 0.0 nếu model chưa có trong bảng giá (không đoán). Làm tròn 2 chữ số
    thập phân.
    """
    price = PRICE_PER_1K_TOKENS.get(model)
    if price is None:
        return 0.0
    cost = prompt_tokens * price.get("input", 0) / 1000.0 + completion_tokens * price.get("output", 0) / 1000.0
    return round(cost, 2)


def cost_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Gộp cost/token của nhiều row llm_usage.

    Trả tổng token + cost theo model, và tổng toàn bộ. Không chứa PII (bảng
    không lưu goal).
    """
    per_model: dict[str, dict[str, int | float]] = {}
    for row in rows:
        model = row.get("model") or "unknown"
        prompt = int(row.get("prompt_tokens") or 0)
        completion = int(row.get("completion_tokens") or 0)
        bucket = per_model.setdefault(model, {"prompt_tokens": 0, "completion_tokens": 0, "cost_vnd": 0.0})
        bucket["prompt_tokens"] += prompt  # type: ignore[operator]
        bucket["completion_tokens"] += completion  # type: ignore[operator]
        bucket["cost_vnd"] += estimate_cost(model, prompt, completion)  # type: ignore[operator]

    total_tokens = sum(b["prompt_tokens"] + b["completion_tokens"] for b in per_model.values())  # type: ignore[operator]
    total_cost = sum(float(b["cost_vnd"]) for b in per_model.values())  # type: ignore[operator]
    return {
        "per_model": {
            m: {k: (round(float(v), 2) if k == "cost_vnd" else v) for k, v in b.items()} for m, b in per_model.items()
        },
        "total_tokens": int(total_tokens),
        "total_cost_vnd": round(total_cost, 2),
        "is_placeholder": IS_PLACEHOLDER,
    }
