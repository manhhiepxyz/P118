"""Vân tay trạng thái kế hoạch — khoá lạc quan dùng chung.

Owner: Thành Bảo (Decision layer)
File: src/common/plan_fingerprint.py

Bao phủ mọi thứ mà một quyết định sửa kế hoạch phụ thuộc vào:

    task_id · tool · depends_on · status · input_data · trạng thái duyệt
    provider_submission_status · external_request_id · provider_idempotency_key

Ba phần cuối được thêm sau khi đo được hai thế giới khác hẳn nhau cho CÙNG một
vân tay:

    NOT_SUBMITTED, external_id=None    → 483bf264151b9b76
    ACKNOWLEDGED,  external_id=BOOK-1  → 483bf264151b9b76

Consequence Analysis đọc đúng bằng chứng ấy để quyết định "sửa tại chỗ" hay
"phải là một hành động nghiệp vụ mới". Vân tay không đổi nghĩa là một quyết
định tính khi task chưa gửi vẫn ghi được sau khi provider đã xác nhận.

Thiếu bất kỳ phần nào thì có một cách để thế giới đổi mà vân tay không đổi.
`depends_on` và `tool` nằm trong đây vì một lần replan có thể giữ nguyên
`task_id` mà đổi công việc; trạng thái duyệt nằm trong đây vì một quyết định
của đơn vị cung cấp làm bản vá mất hiệu lực dù không task nào đổi.

Ở `common` vì cả hai tầng đều tính nó, và chúng phải ra CÙNG một giá trị:
`src/orchestration/patch.py` tính lúc thẩm định, `src/db/workflow_repository.py`
tính lại lúc đã khoá hàng. Hai bản cài đặt là hai câu trả lời.

Đây là PHÁT HIỆN thay đổi, không phải khoá. Tự nó không chống race — giữa lúc
tính và lúc so lại không có gì cả. Nó chỉ có nghĩa khi được tính LẠI bên trong
một transaction đã `SELECT ... FOR UPDATE`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [str(item) for item in raw] if isinstance(raw, list) else []


def _text(value: Any) -> str:
    """Chuẩn hoá về chuỗi ổn định. `None` và cột vắng mặt là một."""
    return "" if value is None else str(value)


def plan_version_of(
    task_rows: Iterable[dict[str, Any]],
    approvals: Iterable[tuple[str, str, str]],
) -> str:
    """Vân tay 16 ký tự của trạng thái kế hoạch.

    `approvals`: bộ ba `(nguồn, task_id, status)` từ MỌI hàng đợi duyệt.
    """
    material = sorted(
        "|".join(
            [
                str(row.get("task_id")),
                str(row.get("tool")),
                ",".join(sorted(_as_list(row.get("depends_on")))),
                str(row.get("status")),
                json.dumps(_as_dict(row.get("input_data")), sort_keys=True, ensure_ascii=False),
                # Bằng chứng gửi provider. `_text` chuẩn hoá cả `None` lẫn cột
                # vắng mặt về CÙNG một chuỗi rỗng: hai đường đọc khác nhau —
                # một truy vấn có chọn cột bằng chứng, một truy vấn không — phải
                # cho cùng vân tay cho cùng trạng thái.
                _text(row.get("provider_submission_status")),
                _text(row.get("external_request_id")),
                _text(row.get("provider_idempotency_key")),
            ]
        )
        for row in task_rows
    )
    approval_material = sorted("|".join(item) for item in approvals)
    digest = hashlib.sha256(json.dumps([material, approval_material], ensure_ascii=False).encode()).hexdigest()
    return digest[:16]
