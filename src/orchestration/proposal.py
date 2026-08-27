"""Đề xuất đơn vị — luật thuần, kiểm được không cần database.

Bước C chọn được một đơn vị nhưng chỉ ĐỌC. Giữa lúc P-118 nói "mình đề xuất Đại
Tín, 470.000" và lúc khách bấm đồng ý có một khoảng thời gian thật: họ đọc, họ
hỏi người nhà, họ đóng tab rồi mở lại. Khoảng ấy phải sống qua restart, qua
worker thứ hai, qua một lượt deploy — nên đề xuất là một BẢN GHI.

Một nguồn sự thật, không hai
----------------------------
`DeXuat` KHÔNG mang provider, giá hay tiền tệ. Chúng nằm trên chứng từ báo giá,
và chép sang đây là tạo nguồn thứ hai — hai nguồn thì lệch, và chúng lệch đúng
vào lúc báo giá bị thay thế hoặc hết hạn, tức đúng lúc con số cũ trông vẫn hợp
lệ. Muốn biết giá thì đọc chứng từ qua `quote_id`.

`approval_actor` cũng không có mặt. Nó là thứ SUY RA lúc dựng câu trả lời —
`USER` khi đang chờ khách bấm, `PROVIDER` sau khi đã chuyển sang hàng đợi đơn
vị. Lưu nó nghĩa là có hai chỗ nói "đang chờ ai", và chỗ thứ hai sẽ đứng im
đúng lúc việc đổi tay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

TrangThaiDeXuat = Literal["PROPOSED", "CONFIRMED", "EXPIRED", "SUPERSEDED"]


class KetQuaXacNhan(StrEnum):
    """Kết quả một lượt xác nhận. Tập ĐÓNG, và mỗi mã dẫn tới một hành động khác.

    `StrEnum` chứ không phải `Literal[...]` vì tầng HTTP phải ánh xạ HẾT sang
    mã trạng thái và câu chữ. Với `Literal` thì "hết" là một lời hứa; với một
    enum liệt kê được, bài kiểm parity đối chiếu `set(_HTTP) == set(KetQuaXacNhan)`
    và thêm một kết quả mà quên ánh xạ sẽ đỏ TRƯỚC khi phát hành — thay vì nổ
    `KeyError` ở request đầu tiên chạm vào nhánh mới.
    """

    CONFIRMED = "CONFIRMED"
    # Không có đề xuất ấy, HOẶC nó không thuộc người đang hỏi. Một mã cho hai
    # tình huống là cố ý: phân biệt chúng nghĩa là xác nhận với người đang dò
    # rằng một `proposal_id` nào đó có thật.
    NOT_FOUND = "NOT_FOUND"
    # Đã xác nhận, đã hết hạn, hoặc đã bị một đề xuất mới thay thế.
    ALREADY_DECIDED = "ALREADY_DECIDED"
    # Chứng từ hết hiệu lực giữa lúc chờ. Đề xuất chuyển EXPIRED — khách phải
    # xin báo giá mới, và đó là một việc khác hẳn "bấm lại lần nữa".
    QUOTE_EXPIRED = "QUOTE_EXPIRED"
    # Chứng từ không dùng được vì lý do khác: đã chốt, đã bị thay thế, hoặc
    # neo sang bước khác. Tách khỏi `QUOTE_EXPIRED` vì hết hạn thì hỏi lại là
    # đủ, còn đây là dấu hiệu có gì đó sai trong luồng.
    QUOTE_NOT_USABLE = "QUOTE_NOT_USABLE"


@dataclass(frozen=True)
class DeXuat:
    """Một đề xuất đã persist. Đọc lại từ database ra đúng hình dạng này."""

    proposal_id: str
    workflow_id: str
    task_id: str
    quote_id: str
    status: TrangThaiDeXuat
    created_at: datetime | None = None
    confirmed_at: datetime | None = None

    @property
    def dang_cho_khach(self) -> bool:
        """Còn chờ khách bấm không.

        Tên hàm nói đúng nghĩa nghiệp vụ chứ không phải `status == "PROPOSED"`:
        chỗ gọi quan tâm "khách còn phải làm gì không", và khi thêm trạng thái
        thứ năm thì chỉ một chỗ phải sửa.
        """
        return self.status == "PROPOSED"


@dataclass(frozen=True)
class KetQuaXacNhanDeXuat:
    """Câu trả lời có KIỂU cho một lượt xác nhận.

    `de_xuat` chỉ có mặt khi thành công. Trả về nó ở nhánh thất bại nghe như
    một lời mời thử lại với cùng dữ liệu — và với `QUOTE_EXPIRED` thì thử lại
    bao nhiêu lần cũng hỏng.
    """

    ket_qua: KetQuaXacNhan
    de_xuat: DeXuat | None = None
    # Mã bước đã được ghim vào hàng đợi đơn vị, khi và chỉ khi thành công.
    task_id: str | None = None

    @property
    def thanh_cong(self) -> bool:
        return self.ket_qua is KetQuaXacNhan.CONFIRMED
