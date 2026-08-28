"""Phân loại MỘT câu hỏi tiếp về đề xuất đơn vị cung cấp.

Vì sao một bộ phân loại riêng
-----------------------------
`IntentResolver` phân loại câu nói trong ngữ cảnh "một yêu cầu đang dở, có các ô
sửa được". Ngữ cảnh ở đây khác hẳn: không ô nào để sửa, và mọi câu đều nói về
MỘT thứ — bảng báo giá đang nằm trong database. "Rẻ hơn không", "bên này uy tín
không", "đổi sang Minh Phát" không map được vào `MODIFY_EXISTING` hay `QUESTION`
mà không mất chính phần quan trọng.

Vì sao KHÔNG dùng regex
-----------------------
Đã có một tiền lệ trong dự án này: một bảng cụm từ kích hoạt. Nó hỏng theo một
kiểu không sửa được — mỗi cách nói mới là một dòng mới, và người viết dòng ấy
phải đoán trước cách người Việt sẽ nói. "còn chỗ nào mềm hơn không", "có bên nào
đỡ hơn không", "giá này chát quá" đều là ASK_CHEAPER và đều không nằm trong bất
kỳ danh sách nào ai viết được.

Model làm ĐÚNG một việc: đọc câu và chọn một nhãn.

Ranh giới tin cậy
-----------------
Model KHÔNG được trả `provider_id`, `quote_id`, `proposal_id`, giá, hay đánh giá.
Những giá trị ấy chỉ đến từ chứng từ đã persist và từ danh mục đơn vị. Nếu model
trả về chúng, nó đang bịa ra một sự việc — và một con số bịa trông y hệt một con
số thật.

Nó được trả về NGUYÊN VĂN phần người dùng gõ (`provider_name_text`,
`budget_text`) để tầng dưới tự phân giải bằng bộ phân giải tất định. Chép lại lời
người dùng không phải là bịa; đọc ra một mã đơn vị mới là.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class YDinhHoiThem(StrEnum):
    """Nhãn ĐÓNG. Thêm nhãn là một quyết định sản phẩm, không phải một dòng mã."""

    ASK_CHEAPER = "ASK_CHEAPER"
    COMPARE_OPTIONS = "COMPARE_OPTIONS"
    ASK_REPUTATION = "ASK_REPUTATION"
    ASK_RECOMMENDATION_REASON = "ASK_RECOMMENDATION_REASON"
    SELECT_PROVIDER = "SELECT_PROVIDER"
    SELECT_CHEAPEST = "SELECT_CHEAPEST"
    SET_MAX_BUDGET = "SET_MAX_BUDGET"
    CONFIRM_CURRENT = "CONFIRM_CURRENT"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    UNKNOWN = "UNKNOWN"


class DeXuatYDinh(BaseModel):
    """ĐỀ XUẤT của model. Không phải quyết định.

    `extra="forbid"`: model trả thêm `provider_id` hay `price` thì cả câu trả
    lời bị từ chối, và tầng gọi rơi về `UNKNOWN`. Đó là hành vi mong muốn — một
    output thừa trường nghĩa là model đang tự cấp cho mình quyền quyết định dữ
    liệu, và im lặng bỏ qua trường thừa sẽ để lần sau nó thử lại.
    """

    model_config = ConfigDict(extra="forbid")

    y_dinh: YDinhHoiThem = YDinhHoiThem.UNKNOWN
    # NGUYÊN VĂN phần người dùng gõ, không phải mã đơn vị. Bộ phân giải tất định
    # ở tầng dưới đọc chuỗi này; nếu nó không khớp duy nhất một đơn vị thì hệ
    # thống HỎI LẠI chứ không đoán.
    provider_name_text: str | None = Field(default=None, max_length=120)
    # NGUYÊN VĂN, ví dụ "600 nghìn". Bộ phân tích số tất định đọc nó.
    budget_text: str | None = Field(default=None, max_length=60)


class _LLMCoStructuredOutput(Protocol):
    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any: ...


SYSTEM_PROMPT = """\
Người dùng đang được đề xuất MỘT đơn vị chuyển nhà và đang cân nhắc có đồng ý
hay không. Bạn phân loại câu họ vừa nói.

`y_dinh` chọn ĐÚNG một trong:

  ASK_CHEAPER                hỏi có lựa chọn rẻ hơn không
  COMPARE_OPTIONS            muốn xem/so sánh lựa chọn, kể cả hỏi "còn chỗ/bên nào khác không"
  ASK_REPUTATION             hỏi về uy tín, đánh giá, chất lượng của đơn vị
  ASK_RECOMMENDATION_REASON  hỏi vì sao đơn vị này được đề xuất
  SELECT_PROVIDER            muốn đổi sang một đơn vị họ GỌI TÊN
  SELECT_CHEAPEST            muốn lấy bên rẻ nhất, không gọi tên cụ thể
  SET_MAX_BUDGET             nêu một mức ngân sách tối đa
  CONFIRM_CURRENT            đồng ý với đề xuất hiện tại
  OUT_OF_SCOPE               hỏi về một dịch vụ KHÁC (đỗ xe, tham quan, bảo trì…)
  UNKNOWN                    không đủ căn cứ để chọn nhánh nào

Chọn UNKNOWN khi bạn không chắc. Đoán sai theo hướng UNKNOWN thì hệ thống hỏi
lại một câu; đoán sai theo hướng CONFIRM_CURRENT thì hệ thống chốt một đơn vị và
một khoản tiền mà người dùng chưa đồng ý.

CONFIRM_CURRENT chỉ khi câu nói RÕ RÀNG là đồng ý với đề xuất đang có. Một tiếng
"ok" hay "ừ" đứng một mình có thể là đồng ý, cũng có thể chỉ là tiếng đệm — khi
không chắc, chọn UNKNOWN.

Khi y_dinh = SELECT_PROVIDER, chép NGUYÊN VĂN phần tên đơn vị người dùng gõ vào
`provider_name_text`. Không sửa chính tả, không đoán tên đầy đủ, không trả mã.

Khi y_dinh = SET_MAX_BUDGET, chép NGUYÊN VĂN phần số tiền vào `budget_text`
("600 nghìn", "dưới 500k"). Không quy đổi.

TUYỆT ĐỐI không trả về mã đơn vị, mã báo giá, giá tiền hay điểm đánh giá. Bạn
không có những dữ liệu đó; hệ thống đọc chúng từ chứng từ đã lưu.
"""

_JSON_MODE_INSTRUCTION = "Trả lời bằng một object json đúng schema đã cho."


class BoPhanLoaiHoiThem:
    """Đọc ý định của một câu hỏi tiếp. `None` ở mọi nhánh không dùng được.

    LLM inject qua constructor — test chạy với fake runnable, không cần network
    và không đọc API key lúc import.
    """

    def __init__(self, llm: _LLMCoStructuredOutput, *, structured_output_method: str | None = None) -> None:
        self._json_mode_hint = structured_output_method == "json_mode"
        if structured_output_method is None:
            self._structured_llm = llm.with_structured_output(DeXuatYDinh)
        else:
            self._structured_llm = llm.with_structured_output(DeXuatYDinh, method=structured_output_method)

    async def doc(self, cau: str) -> DeXuatYDinh | None:
        """Ý định của `cau`. `None` = câu rỗng, model lỗi, hoặc output sai schema.

        Người gọi coi `None` như `UNKNOWN`: hỏi lại người dùng. Không có nhánh
        nào mà `None` dẫn tới một hành động.
        """
        if not (cau or "").strip():
            return None

        system = SYSTEM_PROMPT + (f"\n\n{_JSON_MODE_INSTRUCTION}" if self._json_mode_hint else "")
        try:
            tra_ve = await self._structured_llm.ainvoke([("system", system), ("human", cau.strip())])
        except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
            # Message gốc có thể chứa prompt, đoạn output, hoặc header xác thực.
            logger.warning("bo phan loai hoi them khong goi duoc LLM (%s)", type(exc).__name__)
            return None

        if not isinstance(tra_ve, DeXuatYDinh):
            logger.warning("bo phan loai hoi them nhan output sai schema (%s)", type(tra_ve).__name__)
            return None
        return tra_ve
