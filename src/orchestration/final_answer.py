"""Câu trả lời cuối cùng, ghi TRƯỚC khi workflow chuyển sang trạng thái kết thúc.

Vì sao cần một chỗ riêng cho việc này:

Câu trả lời của P-118 vốn được sinh ở tác vụ nền, sau khi API đã trả về — cố ý,
để không cộng một lượt gọi mô hình vào thời gian người dùng phải chờ. Cơ chế ấy
đúng cho luồng thường: workflow đổi trạng thái, lượt poll kế tiếp thấy
`assistant_for_status` lệch với `status` và sinh lại câu mới.

Nhưng luồng duyệt của đơn vị không có "lượt poll kế tiếp" đáng tin: giao diện
dừng poll ngay khi thấy trạng thái kết thúc. Đo được trong database sau khi
provider bấm duyệt:

    status = SUCCESS
    assistant_for_status = WAITING_APPROVAL
    assistant_answer = "Đơn vị tour đang xác nhận lịch…"

Tức là workflow đã xong, xe đã đặt, mà câu cuối cùng khách đọc vẫn nói đang chờ.

Cách chữa là đảo thứ tự chứ không phải poll lâu hơn: kết quả nghiệp vụ → câu
trả lời cuối → RỒI MỚI đổi trạng thái. Khi `SUCCESS` xuất hiện thì mọi thứ giao
diện cần đã nằm sẵn trong database.

Câu ở đây dựng TẤT ĐỊNH từ `workflow_tasks` — không gọi mô hình. Hai lý do:
endpoint duyệt đã mất ~30 giây cho việc đặt xe, và một câu ghép từ kết quả thật
thì không bao giờ nói sai. Nó được ghi với `state="FALLBACK"`, đúng nhãn mà hệ
thống vẫn dùng cho câu deterministic, nên nếu sau này muốn nâng lên câu do mô
hình viết thì chỉ cần đổi hàm `compose` bên dưới.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Field kết quả đáng đưa vào câu chốt, theo thứ tự người đọc quan tâm.
# Tên field do provider đặt; nhãn tiếng Việt để khách đọc được.
_FACT_LABELS: dict[str, str] = {
    "viewing_date": "ngày",
    "viewing_time": "giờ",
    "project_name": "dự án",
    "receptionist_name": "người đón tiếp",
    "reception_area": "điểm gặp",
    "driver_name": "tài xế",
    "license_plate": "biển số",
    "vehicle_type": "loại xe",
    "pickup_time": "giờ đón",
}


def compose(task_rows: list[dict[str, Any]], final_status: str) -> str:
    """Một câu chốt dựng từ kết quả THẬT. Không suy diễn, không hứa thêm."""
    if final_status != "SUCCESS":
        return "Yêu cầu chưa hoàn tất được. Bạn xem chi tiết từng bước để biết vướng ở đâu nhé."

    facts: list[str] = []
    for row in task_rows:
        data = row.get("result_data") or {}
        if not isinstance(data, dict):
            continue
        for field, label in _FACT_LABELS.items():
            value = data.get(field)
            if value not in (None, "") and f"{label} {value}" not in facts:
                facts.append(f"{label} {value}")

    if not facts:
        return "Xong rồi, mọi việc đã hoàn tất."
    return "Xong rồi — " + ", ".join(facts) + "."


async def write_final_answer(workflow_id: str, final_status: str) -> None:
    """Ghi câu chốt cho `final_status`. Không bao giờ raise.

    Không raise vì nó nằm giữa "đã đặt xe xong" và "đổi trạng thái": để một lỗi
    ghi chữ làm hỏng cả hai việc kia là đánh đổi sai chiều. Ghi hỏng thì khách
    đọc câu cũ — khó chịu; ném lỗi thì workflow treo ở WAITING_APPROVAL trong
    khi tiền và chỗ đã đặt thật.
    """
    pool = None
    try:
        from src.orchestration.demo_service import acquire_repository

        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        rows = await repository.list_tasks(workflow_id)
        await repository.save_assistant_response(
            workflow_id,
            answer=compose(rows, final_status),
            suggestions=[],
            state="FALLBACK",
            for_status=final_status,
        )
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.info("không ghi được câu chốt cho %s (%s)", workflow_id[:8], type(exc).__name__)
    finally:
        if pool is not None:
            await pool.close()
