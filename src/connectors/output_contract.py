"""Kiểm output của provider trước khi cho vào state của Agent.

Connector là ranh giới tin cậy cuối cùng giữa provider và Agent. Trước đây nó
chỉ kiểm `field in data`, nên ba lỗi im lặng lọt qua:

  - `contact_phone: None` — có key, giá trị vô dụng. Agent hiển thị "None" cho
    người dùng và không ai biết provider đã hỏng.
  - `viewing_time: 930` (int thay vì "09:30") — xuống tới tầng render mới vỡ,
    lúc đó đã không còn biết lỗi từ provider nào.
  - Field lạ đi kèm — làm rò từ vựng nội bộ (`tour_id`, `consultation_type`)
    vào state, đúng thứ contract public loại bỏ.

Nguyên tắc về message: KHÔNG bao giờ đưa giá trị vào lỗi, chỉ đưa TÊN field.
Payload của các tool này chứa tên và số điện thoại; echo lại là đẩy PII vào log
và vào DB workflow.
"""

from __future__ import annotations

from typing import Any


class OutputContractError(ValueError):
    """Response provider không đúng contract. Message chỉ chứa tên field."""


def enforce_exact_contract(
    data: Any,
    required_fields: tuple[str, ...],
    *,
    non_empty_strings: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Trả bản sao chỉ gồm `required_fields`, hoặc raise `OutputContractError`.

    Args:
        data: payload đã bóc khỏi envelope của provider.
        required_fields: bộ field canonical — phải có đủ, không thiếu.
        non_empty_strings: các field bắt buộc là `str` và không rỗng sau strip.
            Dùng cho những field mà `None` là bug chứ không phải "chưa có",
            ví dụ `contact_name`/`contact_phone` của lịch xem nhà.

    Raises:
        OutputContractError: thiếu field, sai kiểu, hoặc string rỗng.
    """
    if not isinstance(data, dict):
        raise OutputContractError("Provider trả payload không phải object")

    missing = [f for f in required_fields if f not in data]
    if missing:
        raise OutputContractError(f"Thiếu field bắt buộc: {', '.join(sorted(missing))}")

    bad_type = [f for f in non_empty_strings if not isinstance(data[f], str)]
    if bad_type:
        raise OutputContractError(f"Field sai kiểu, cần chuỗi: {', '.join(sorted(bad_type))}")

    blank = [f for f in non_empty_strings if not data[f].strip()]
    if blank:
        raise OutputContractError(f"Field bắt buộc nhưng rỗng: {', '.join(sorted(blank))}")

    # Whitelist chặt: field ngoài contract bị loại bỏ chứ không đi tiếp. Đây là
    # chỗ duy nhất chặn từ vựng nội bộ của provider rò vào state Agent.
    return {field: data[field] for field in required_fields}
