"""Phân loại ý định khi người dùng trả lời cảnh báo xung đột lịch.

Model làm ĐÚNG một việc: đọc câu người dùng và chọn một nhãn trong
KEEP_BOTH / CHANGE_A / CHANGE_B / UNKNOWN.

Không regex, không cụm từ cứng. Model nhận nhãn DV thực tế của A và B
(ví dụ "Đăng ký chuyển nhà" / "Yêu cầu bảo trì") qua system prompt — không
có ví dụ cố định vì thứ tự A/B thay đổi tuỳ conflict row.

Model trả về UNKNOWN khi không chắc — caller hỏi lại thay vì tạo workflow mới.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

_SYSTEM_TEMPLATE = """\
Bạn phân loại câu trả lời của người dùng khi hệ thống cảnh báo hai lịch hẹn \
có thể bị trùng giờ.

Ngữ cảnh hiện tại:
- Lịch A: {label_a}
- Lịch B: {label_b}

Trả về đúng MỘT nhãn:
- KEEP_BOTH  : người dùng muốn giữ cả hai lịch dù trùng giờ.
- CHANGE_A   : người dùng muốn đổi Lịch A ({label_a}).
- CHANGE_B   : người dùng muốn đổi Lịch B ({label_b}).
- UNKNOWN    : không hiểu rõ hoặc câu không liên quan đến hai lựa chọn trên.

Chỉ trả về JSON với trường "y_dinh". Không thêm lý giải.
"""

_JSON_MODE_INSTRUCTION = 'Trả về JSON hợp lệ có đúng một trường: {{"y_dinh": "<nhãn>"}}.'

# Nhãn mặc định khi không truyền vào — không được dùng trong production,
# chỉ để unit test không cần context thật.
_DEFAULT_LABEL_A = "Dịch vụ A"
_DEFAULT_LABEL_B = "Dịch vụ B"


class YDinhXungDot(StrEnum):
    KEEP_BOTH = "KEEP_BOTH"
    CHANGE_A = "CHANGE_A"
    CHANGE_B = "CHANGE_B"
    UNKNOWN = "UNKNOWN"


class KetQuaXungDot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    y_dinh: YDinhXungDot = YDinhXungDot.UNKNOWN


class _LLMCoStructuredOutput(Protocol):
    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any: ...


class BoPhanLoaiXungDot:
    """Đọc ý định của người dùng về xung đột lịch. `None` ở mọi nhánh lỗi."""

    def __init__(self, llm: _LLMCoStructuredOutput, *, structured_output_method: str | None = None) -> None:
        self._json_mode_hint = structured_output_method == "json_mode"
        if structured_output_method is None:
            self._structured_llm = llm.with_structured_output(KetQuaXungDot)
        else:
            self._structured_llm = llm.with_structured_output(KetQuaXungDot, method=structured_output_method)

    async def doc(
        self,
        cau: str,
        *,
        label_a: str = _DEFAULT_LABEL_A,
        label_b: str = _DEFAULT_LABEL_B,
    ) -> KetQuaXungDot | None:
        """Phân loại `cau` với ngữ cảnh dịch vụ A/B thực tế.

        label_a/label_b: tên dịch vụ tiếng Việt theo conflict row hiện tại,
        ví dụ "Đăng ký chuyển nhà" và "Yêu cầu bảo trì". Model sẽ nói đúng
        tên dịch vụ thay vì các ví dụ cứng.

        None = câu rỗng, model lỗi, hoặc output sai schema.
        """
        if not (cau or "").strip():
            return None

        system = _SYSTEM_TEMPLATE.format(label_a=label_a, label_b=label_b)
        if self._json_mode_hint:
            system += f"\n\n{_JSON_MODE_INSTRUCTION.format()}"
        try:
            tra_ve = await self._structured_llm.ainvoke([("system", system), ("human", cau.strip())])
        except Exception as exc:  # noqa: BLE001
            logger.warning("bo phan loai xung dot khong goi duoc LLM (%s)", type(exc).__name__)
            return None

        if not isinstance(tra_ve, KetQuaXungDot):
            logger.warning("bo phan loai xung dot nhan output sai schema (%s)", type(tra_ve).__name__)
            return None
        return tra_ve
