"""Tự duyệt lịch tham quan sau N giây — TIỆN ÍCH DEMO, mặc định tắt.

Khi trình bày một mình thì không có ai ngồi ở cổng `/review` để bấm nút, và
việc dừng lại đăng nhập bằng tài khoản khác làm đứt mạch câu chuyện. Bật
`P118_AUTO_APPROVE_VIEWING_SECONDS` lên thì sau chừng ấy giây, hệ thống tự
duyệt giúp.

Ba điều được giữ nguyên một cách có chủ ý:

  * Đi qua ĐÚNG `resume_viewing_after_approval` mà provider dùng. Không có
    đường tắt riêng cho demo — nếu có, thứ được diễn tập sẽ không phải thứ chạy
    thật, và mọi sửa lỗi ở đường thật sẽ lặng lẽ không áp dụng cho đường demo.
  * `decided_by = "auto-demo"` ghi vào database. Nhìn vào bảng là biết ngay
    quyết định ấy do máy đưa ra, không phải do một người nào đó.
  * Cổng `/review` vẫn hoạt động. Ai bấm trước thì tính, vì cả hai đường cùng
    khoá bằng `WHERE status='AWAITING'`.

Mặc định TẮT, và điều đó quan trọng: bật lên nghĩa là mọi lịch tham quan đều
được chấp thuận mà không ai xem — trong hệ thống thật đó là bỏ hẳn một bước
kiểm soát, chứ không phải một tuỳ chọn cho tiện.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

AUTO_REVIEWER = "auto-demo"


async def auto_approve_due_viewings(delay_seconds: int) -> list[str]:
    """Duyệt các yêu cầu đã chờ quá `delay_seconds`. Trả danh sách workflow_id.

    Best-effort: một yêu cầu hỏng không được làm dừng những cái còn lại — hàng
    chờ demo thường có vài yêu cầu cũ mà khung giờ đã bị chiếm, và để một cái
    trong số đó chặn cả vòng quét thì tính năng này vô dụng đúng lúc cần nhất.
    """
    if delay_seconds <= 0:
        return []

    from src.config import get_settings
    from src.orchestration.demo_service import acquire_repository, resume_viewing_after_approval
    from src.orchestration.viewing_approval import AWAITING

    settings = get_settings()
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        rows = await pool.fetch(
            """
            SELECT workflow_id FROM viewing_approvals
            WHERE status = $1
              AND created_at < NOW() - make_interval(secs => $2)
            ORDER BY created_at
            LIMIT 5
            """,
            AWAITING,
            float(delay_seconds),
        )
    finally:
        await pool.close()

    approved: list[str] = []
    for row in rows:
        workflow_id = str(row["workflow_id"])
        try:
            await resume_viewing_after_approval(
                workflow_id,
                tour_url=settings.tour_service_url,
                shuttle_url=settings.shuttle_service_url,
                resident_url=settings.resident_service_url,
                transport_url=settings.transport_service_url,
                payment_url=settings.payment_service_url,
                property_url=settings.property_service_url,
                resident_services_url=settings.resident_services_service_url,
                consultation_url=settings.consultation_service_url,
                decided_by=AUTO_REVIEWER,
            )
            _invalidate_cached_response(workflow_id)
            approved.append(workflow_id)
            logger.info("tự duyệt lịch tham quan %s sau %ds", workflow_id[:8], delay_seconds)
        except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
            # Không nuốt im lặng: người demo cần biết vì sao một yêu cầu không
            # tự chạy, thay vì ngồi nhìn màn hình chờ mãi.
            logger.info("không tự duyệt được %s (%s)", workflow_id[:8], type(exc).__name__)
    return approved


def _invalidate_cached_response(workflow_id: str) -> None:
    """Bỏ ảnh chụp response đang cache, y như route duyệt thủ công vẫn làm.

    Thiếu bước này thì mọi lượt poll tiếp theo vẫn được phục vụ ảnh cũ — ảnh
    được dựng lúc workflow còn chờ duyệt — dù database đã ghi SUCCESS. Giao
    diện đứng im vĩnh viễn.

    Đo được đúng như vậy: canvas hiện "Đang chờ đơn vị xác nhận" ở giây thứ 30,
    rồi tụt về "Chưa bắt đầu" và không nhúc nhích thêm trong 4 phút, trong khi
    `workflows.status` đã là SUCCESS và xe đã đặt xong.

    Route `/viewing-approvals/{id}/decide` đã làm việc này từ đầu; vòng tự duyệt
    đi thẳng vào `resume_viewing_after_approval` nên bỏ sót nó. Đây là cái giá
    của việc có hai đường vào cùng một hành động — và là lý do phần còn lại của
    file này cố ý dùng chung đúng một hàm resume.
    """
    try:
        from src.api.routes import _DEMO_JOBS

        job = _DEMO_JOBS.get(workflow_id)
        if job is not None:
            job["response"] = None
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.info("không dọn được cache response (%s)", type(exc).__name__)


def summary(approved: list[str]) -> dict[str, Any]:
    return {"auto_approved": approved, "count": len(approved)}
