"""Huỷ yêu cầu phải RÚT LUÔN lời nhờ đơn vị tour duyệt.

`viewing_approvals` sống độc lập với `workflows.status`. Huỷ chỉ đổi bảng
workflow, còn thẻ duyệt vẫn `AWAITING` — nên đơn vị tour tiếp tục được hỏi về
một lịch khách đã huỷ.

Đo trên dữ liệu thật:

    5 yêu cầu CANCELLED vẫn AWAITING   → vẫn đang hỏi đơn vị
    1 yêu cầu CANCELLED đã APPROVED    → và họ đã duyệt nó

Cái thứ hai không dừng ở màn hình: có người sắp xếp đi phục vụ một buổi tham
quan không còn tồn tại.

Giao diện cũng đọc bảng này, nên hậu quả thứ ba là thứ người dùng nhìn thấy:
thẻ duyệt còn treo thì yêu cầu đã huỷ vẫn hiện "đang chờ đơn vị xác nhận", và
câu tiếp theo họ gõ bị đọc thành câu trả lời cho nó. Đo được: gõ "tôi muốn đổi
dịch vụ" sau khi huỷ, KHÔNG workflow nào được tạo và màn hình vẫn vẽ lịch cũ.
"""

from __future__ import annotations

import inspect

from src.orchestration.viewing_approval import expire_pending_viewing_approval


def test_the_cancel_route_withdraws_the_request() -> None:
    from src.api import routes

    source = inspect.getsource(routes.cancel_demo_workflow)
    assert "expire_pending_viewing_approval" in source, (
        "huỷ yêu cầu mà không rút lời nhờ duyệt — đơn vị tour vẫn được hỏi, và có thể duyệt, một lịch đã huỷ"
    )


def test_withdrawal_is_not_recorded_as_a_provider_rejection() -> None:
    """`EXPIRED`, KHÔNG phải `REJECTED`.

    Từ chối là quyết định của ĐƠN VỊ. Ghi nó vào đây là gán cho họ một việc họ
    chưa từng làm, và mọi con số "tỉ lệ đơn vị từ chối" đều sai theo.
    """
    source = inspect.getsource(expire_pending_viewing_approval)
    # Cắt bỏ tài liệu hàm trước khi soi: chữ "REJECTED" nằm trong chính đoạn
    # giải thích vì sao KHÔNG dùng nó, nên soi cả hàm là tự bắt lỗi mình.
    body = source[source.index('"""', source.index('"""') + 3) + 3 :]
    assert "'EXPIRED'" in body, "rút lời nhờ duyệt lại ghi thành một quyết định"
    assert "REJECTED" not in body, "ghi nhầm thành đơn vị từ chối"
    assert "status = 'AWAITING'" in body, (
        "thiếu điều kiện chỉ-đụng-hàng-đang-chờ: đơn vị đã quyết rồi thì quyết "
        "định của họ là dữ kiện, không được viết đè"
    )
