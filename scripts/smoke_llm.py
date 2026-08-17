"""Kiểm khoá LLM THẬT — một lần, không nằm trong healthcheck.

`/ready` cố tình chỉ kiểm HÌNH DẠNG cấu hình: provider, key có mặt, model khớp.
Nó không gọi nhà cung cấp, vì healthcheck chạy mỗi 15 giây sẽ đốt tiền và tự
tạo rate limit cho chính mình.

Nhưng "key có mặt" khác "key còn dùng được". Script này lấp đúng khoảng đó, và
chỉ chạy khi người ta chủ động gọi — lúc deploy, hoặc trước một buổi demo:

    docker compose exec backend python scripts/smoke_llm.py

Không in key, không in prompt, không in nội dung trả về của mô hình.
"""

from __future__ import annotations

import asyncio
import sys

from src.common.failures import classify_failure
from src.config import get_settings
from src.services.llm import LLMConfigurationError, check_llm_configuration, get_llm


async def main() -> int:
    settings = get_settings()

    try:
        check_llm_configuration(settings)
    except LLMConfigurationError as exc:
        print(f"CẤU HÌNH SAI: {exc}")
        print("Sửa biến môi trường rồi chạy lại. Chưa cần gọi tới nhà cung cấp.")
        return 2

    print(f"Cấu hình hợp lệ (provider={settings.llm_provider}). Đang gọi thử một lượt ngắn…")

    try:
        client = get_llm(settings)
        # Prompt ngắn nhất có thể: mục tiêu là kiểm khoá, không phải kiểm chất
        # lượng mô hình. Nội dung trả về KHÔNG được in ra.
        reply = await client.ainvoke("ping")
    except Exception as exc:  # noqa: BLE001 - phân loại rồi mới nói
        failure = classify_failure(exc)
        print(f"THẤT BẠI: {failure.code}")
        print(f"  thử lại có ích: {'có' if failure.retryable else 'không'}")
        print(f"  loại lỗi: {type(exc).__name__}")
        return 1

    print(f"OK — nhà cung cấp trả lời ({len(getattr(reply, 'content', '') or '')} ký tự).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
