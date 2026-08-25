"""Ranh giới DUY NHẤT giữa hệ thống và provider.

Owner: Thành Bảo (Decision layer)
File: src/orchestration/provider_gateway.py

Bốn bước, luôn theo đúng thứ tự này, cho MỌI lời gọi ra ngoài:

    prepare_submission          xin phép, ghi `SUBMITTING`, khoá hàng
    ProviderCallContext         gắn khoá ĐÃ LƯU, không phải khoá vừa tính
    connector.execute           lời gọi thật
    record_submission_outcome   ghi kết luận, không viết đè trạng thái cuối

Vì sao phải là một chỗ
----------------------
Phase 2A bản trước dựng đủ bốn bước ở Executor, và tưởng thế là xong. Đường
production của thanh toán không đi qua Executor:

    resume_payment_after_approval → _execute_payment_only → PaymentConnector.execute

Nó gọi thẳng connector với khoá TỰ TÍNH. Nghĩa là mọi bất biến vừa dựng — không
gửi mù, không ghi đè khoá, không gửi lại khi `SUBMITTING` mà không có khoá — đều
không áp dụng cho chính đường tiêu tiền của người dùng.

Một hàng rào chỉ chặn ở một trong hai lối vào thì nó không phải hàng rào. Và
chép bốn bước ấy sang lối vào thứ hai là dựng bản thứ hai của cùng một luật —
hai bản sẽ lệch nhau, đúng như đã lệch một lần rồi.

Cổng này KHÔNG quyết định gì về nghiệp vụ: không đổi task status, không chốt
workflow, không đụng hàng đợi duyệt. Nó chỉ giữ đúng bốn bước trên. Người gọi
vẫn tự tính trạng thái downstream của mình.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.connectors.base import ProviderCallContext

logger = logging.getLogger(__name__)

# Câu nói ra khi hệ thống tự dừng trước lúc gửi. Cố định, không nội suy gì —
# lý do thật nằm ở `permit.reason`, một mã đóng, và nó chỉ đi vào log.
_BLOCKED_MESSAGE = "Yêu cầu này không ở trạng thái gửi được nên hệ thống đã dừng lại."
_EVIDENCE_FAILED_MESSAGE = "Chưa ghi được trạng thái gửi yêu cầu nên hệ thống dừng lại trước khi gửi."


@dataclass(frozen=True)
class ProviderCall:
    """Một lời gọi ra ngoài, mô tả đầy đủ trước khi nó xảy ra."""

    workflow_id: str
    task_id: str
    tool: str
    input_data: dict[str, Any]


async def call_provider(
    connector: Any, repository: Any, call: ProviderCall, *, has_retry_budget: bool = False
) -> StandardResult:
    """Gọi provider qua đủ bốn bước. `StandardResult` như connector trả về.

    Bị chặn thì trả `fail` mà KHÔNG gọi provider — và không bao giờ ném ra: mọi
    người gọi đều đang ở giữa một luồng nghiệp vụ, và một exception ở đây sẽ
    biến "chưa gửi" thành một lỗi hệ thống khó đọc.

    `has_retry_budget=True` nghĩa là người gọi CÒN một lượt nữa — nhưng còn
    ngân sách không có nghĩa là kết quả này SẼ được thử lại. Kết luận chỉ được
    HOÃN khi cả ba điều cùng đúng: còn ngân sách, lần này THẤT BẠI, và thất bại
    ấy retry được.

    `retryable` chia hai chiều có chủ ý. Ghi bằng chứng hỏng là sự cố hạ tầng —
    thử lại được. Bị từ chối vì trạng thái (`ALREADY_TERMINAL`,
    `IN_FLIGHT_WITHOUT_KEY`...) thì thử lại cũng cho cùng kết quả, và thử lại
    chính là thứ đang bị chặn.
    """
    candidate = _candidate_key(connector, call)

    try:
        permit = await repository.prepare_submission(call.workflow_id, call.task_id, candidate_key=candidate)
    except Exception:  # noqa: BLE001 - không giữ gì từ exception
        # Không ghi tên loại: lý do luôn là "ghi bằng chứng hỏng", nên tên loại
        # thêm rất ít, còn nó là một kênh nữa nối exception tầng database ra log.
        logger.warning("khong ghi duoc bang chung gui di; khong goi provider")
        return StandardResult.fail(ErrorCode.INTERNAL_SERVICE_ERROR, _EVIDENCE_FAILED_MESSAGE, retryable=True)

    if not permit.allowed:
        logger.warning("tu choi gui: %s", permit.reason)
        return StandardResult.fail(ErrorCode.INTERNAL_SERVICE_ERROR, _BLOCKED_MESSAGE, retryable=False)

    try:
        result = await connector.execute(
            call.tool,
            call.input_data,
            context=ProviderCallContext(idempotency_key=permit.effective_key),
        )
    except Exception:  # noqa: BLE001 - cô lập lỗi của một nhánh
        result = StandardResult.fail(
            ErrorCode.INTERNAL_SERVICE_ERROR, "Connector gặp lỗi không mong đợi", retryable=False
        )

    # HOÃN ghi kết luận chỉ khi lượt sau THẬT SỰ sẽ chạy.
    #
    # Ba điều kiện, đồng thời — và thiếu điều nào cũng sai theo một hướng khác:
    #
    #   còn ngân sách        thiếu → hoãn ở lượt cuối, kết luận không bao giờ ghi
    #   lần này thất bại     thiếu → một lần THÀNH CÔNG bị hoãn, và người gọi
    #                                thoát vòng lặp ngay sau đó nên nó không bao
    #                                giờ được ghi. Đo được: task ghi `SUCCESS`,
    #                                provider đã tạo bản ghi thật, mà bằng chứng
    #                                nói "đang gửi dở".
    #   thất bại retry được  thiếu → lỗi non-retryable bị hoãn, cùng hậu quả
    #
    # Bản trước chỉ hỏi điều kiện đầu (`will_retry`), tên gọi ấy nói quá điều nó
    # biết: nó chỉ biết ngân sách, không biết ý định.
    #
    # Khi hoãn, bản ghi đứng ở `SUBMITTING` giữa các lượt. Đó là sự thật: một
    # lần gửi đã bắt đầu và chưa có kết luận. Ghi `UNKNOWN` ngay ở đó — một
    # trạng thái CUỐI — sẽ chặn đúng lượt thử tiếp theo mà vòng retry vừa quyết
    # định là an toàn (đo được: connector retry-safe gọi 1 lần thay vì 3).
    if has_retry_budget and not result.success and result.is_retryable:
        return result

    try:
        await repository.record_submission_outcome(call.workflow_id, call.task_id, call.tool, result)
    except Exception:  # noqa: BLE001 - nằm ngoài critical path
        # Provider ĐÃ trả lời rồi; không được biến việc ghi hỏng thành một lỗi
        # nghiệp vụ. Bản ghi đứng ở `SUBMITTING`, và với tool có khoá thì lượt
        # sau replay an toàn.
        logger.warning("khong ghi duoc ket luan gui di")
    return result


def _candidate_key(connector: Any, call: ProviderCall) -> str | None:
    """Khoá connector ĐỀ XUẤT cho lần gọi này.

    Tính từ tham số, không từ state connector: cùng bộ tham số phải ra cùng
    khoá ở mọi process. `None` nghĩa là "tool này không có khoá" — khác hẳn
    "có khoá nhưng khác".
    """
    describe = getattr(connector, "idempotency_key_for", None)
    if not callable(describe):
        return None
    try:
        key = describe(call.workflow_id, call.task_id, call.tool, call.input_data)
    except TypeError:
        # Chữ ký lệch — một connector còn dùng bản cũ. Nuốt im lặng ở đây nghĩa
        # là tool ấy đi ra provider KHÔNG mang khoá, và không ai biết.
        logger.warning("connector %s co chu ky idempotency_key_for cu", type(connector).__name__)
        return None
    except Exception:  # noqa: BLE001 - mô tả khoá không được làm hỏng lần gọi
        return None
    return key if isinstance(key, str) and key.strip() else None
