"""Đơn vị đồng ý cho huỷ → lịch phải biến mất THẬT ở phía họ.

Owner: Thành Bảo (Decision layer)
File: src/orchestration/support_request.py

Vấn đề đo được
--------------
Nút "Dừng yêu cầu này" gọi `repository.cancel_workflow`: nó đánh dấu `workflows`
và `workflow_tasks` là `CANCELLED` trong database của CHÍNH hệ thống này, và
không nói gì với đơn vị. `VIEW-014` vẫn nằm nguyên bên tour provider.

Khách bấm "đã huỷ", màn hình nói đã huỷ, và hôm sau vẫn có người chờ họ tới xem
nhà. Đó không phải lỗi hiển thị: khung giờ ấy vẫn bị giữ, và người khác không
đặt được.

Hình dạng của lời giải
----------------------
Giống hệt `zone_change.py`, vì cùng một bài toán: một thao tác trên thứ ĐÃ TỒN
TẠI, dựng từ KẾT QUẢ ĐÃ CHẠY chứ không từ câu người dùng.

    hồ sơ `YC1` được duyệt → dựng bước `T1H2` mang `viewing_id` đọc từ
    `result_data` của bước gốc → gọi provider qua gateway → ghi kết quả

Vì sao phải có một dòng `workflow_tasks` thật: `call_provider` ghi bằng chứng
gửi đi lên chính dòng ấy. Không có dòng thì không có bằng chứng, và không có
bằng chứng thì lần gọi lại sau restart không biết lần trước đã đi tới đâu.

Vì sao KHÔNG chạy thẳng bằng `Executor`: một hồ sơ được duyệt không phải một
bước trong kế hoạch của khách. Đưa nó vào kế hoạch nghĩa là mọi lượt resume sau
này đều thấy nó và cân nhắc chạy lại.

`AMEND` cố ý KHÔNG có ở đây. "Đơn vị đồng ý cho đổi" chưa nói đổi sang lúc nào;
tự chọn một mốc thay khách là bịa ra một quyết định họ chưa đưa ra.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.common.enums import TaskStatus
from src.common.results import StandardResult
from src.orchestration.provider_gateway import ProviderCall, call_provider

logger = logging.getLogger(__name__)

# `workflow_tasks.task_id` là VARCHAR(20). `T1` → `T1H2` — đọc được bằng mắt, và
# không đụng hậu tố `R` (lần thử mới) hay `Z` (đổi khu).
_CANCEL_SEPARATOR = "H"
_MAX_TASK_ID = 20

# (loại hồ sơ, tool của bước gốc) → (tool phải chạy, ô mang mã định danh).
#
# Chọn theo TOOL GỐC chứ không chỉ theo loại: "xin huỷ" của một lịch tham quan
# và "xin huỷ" của một chỗ đỗ xe là hai lời gọi tới hai đơn vị khác nhau, mang
# hai mã khác nhau. Một bảng chỉ tra theo loại sẽ gọi nhầm provider.
#
# Cặp nào KHÔNG có ở đây thì không có gì tự chạy — và đó là kết cục đúng cho
# `AMEND`: "đồng ý cho đổi" chưa nói đổi sang lúc nào, nên tự chọn một mốc thay
# khách là bịa ra một quyết định họ chưa đưa ra.
_ACTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("CANCEL", "schedule_property_viewing"): ("cancel_property_viewing", "viewing_id"),
    ("CANCEL", "book_parking"): ("cancel_parking", "booking_id"),
    # Chỗ đỗ đã ĐỔI KHU vẫn là chính chỗ ấy — `booking_id` không đổi.
    ("CANCEL", "change_parking_zone"): ("cancel_parking", "booking_id"),
    ("CANCEL", "create_maintenance_request"): ("cancel_maintenance", "maintenance_id"),
    ("CANCEL", "schedule_move"): ("cancel_move", "move_request_id"),
    ("CANCEL", "book_shuttle"): ("cancel_shuttle", "shuttle_id"),
}


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _allocate_task_id(source_task_id: str, taken: set[str]) -> str | None:
    """Trả None khi không còn tên đủ ngắn — và khi đó KHÔNG làm gì cả.

    Không cắt bớt cho vừa: một `task_id` bị cắt có thể ĐỤNG một id đang có, và
    khi đó bước huỷ ghi đè bằng chứng của một bước khác.
    """
    goc = source_task_id.split(_CANCEL_SEPARATOR)[0]
    for lan in range(2, 100):
        ung_vien = f"{goc}{_CANCEL_SEPARATOR}{lan}"
        if len(ung_vien) <= _MAX_TASK_ID and ung_vien not in taken:
            return ung_vien
    return None


async def run_approved_requests(
    repository: Any, workflow_id: str, rows: list[dict[str, Any]], connectors: list[Any]
) -> list[str]:
    """Thực hiện những hồ sơ đơn vị VỪA duyệt. Trả danh sách bước đã dựng.

    Không có hồ sơ nào được duyệt → trả rỗng, không chạm database. Đó là đường
    đi của mọi lượt resume bình thường.
    """
    can_lam = [
        row
        for row in rows
        if str(row.get("kind") or "TASK") == "REQUEST"
        and str(row.get("status")) == "APPROVED"
        and _as_dict(row.get("details")).get("loai") is not None
    ]
    if not can_lam:
        return []

    task_rows = {r["task_id"]: r for r in await repository.list_tasks(workflow_id)}
    taken = set(task_rows)
    da_lam: list[str] = []

    for row in can_lam:
        chi_tiet = _as_dict(row.get("details"))
        goc_id = str(chi_tiet.get("task_id") or "")
        goc = task_rows.get(goc_id)
        viec = _ACTIONS.get((str(chi_tiet.get("loai")), str((goc or {}).get("tool"))))
        if viec is None:
            continue
        tool, o_ma = viec
        if goc is None or str(goc.get("status")) != TaskStatus.SUCCESS.value:
            # Bước gốc chưa từng chạy xong thì không có gì ngoài kia để huỷ.
            logger.warning("ho so duyet nhung buoc goc khong con o trang thai da xong")
            continue
        ma = _as_dict(goc.get("result_data")).get(o_ma)
        if not ma:
            logger.warning("khong doc duoc ma dinh danh de thuc hien ho so")
            continue

        # Đã làm rồi thì thôi. Đơn vị có thể bấm duyệt lại, và đường resume chạy
        # ở mọi lượt quyết định — không có cửa này thì mỗi lượt là một lời gọi
        # huỷ nữa ra ngoài.
        if any(
            str(r.get("tool")) == tool and _as_dict(r.get("input_data")).get(o_ma) == ma for r in task_rows.values()
        ):
            continue

        moi = _allocate_task_id(goc_id, taken)
        if moi is None:
            logger.warning("khong cap duoc danh tinh cho mot buoc huy")
            continue
        taken.add(moi)

        await repository.create_task(
            workflow_id,
            {
                "id": moi,
                "tool": tool,
                # KHÔNG phụ thuộc bước gốc: bước gốc đã xong từ lượt trước và
                # không nằm trong lượt chạy này. Một `depends_on` trỏ vào nó sẽ
                # được đọc là "chờ một bước chưa chạy".
                "depends_on": [],
                # Mã đọc từ `result_data` — literal, không phải `InputRef`: kết
                # quả của bước gốc không còn trong RAM của lượt này.
                "input": {o_ma: ma},
                "status": TaskStatus.PENDING.value,
            },
        )

        connector = next((c for c in connectors if tool in getattr(c, "tool_names", [])), None)
        if connector is None:
            logger.warning("khong co connector nao nhan tool nay")
            continue

        # Qua GATEWAY, không gọi thẳng connector: đây là một lời gọi ra ngoài
        # như mọi lời gọi khác, và nó phải để lại bằng chứng gửi đi.
        ket_qua: StandardResult = await call_provider(
            connector,
            repository,
            ProviderCall(workflow_id=workflow_id, task_id=moi, tool=tool, input_data={o_ma: ma}),
        )
        await repository.save_task_result(workflow_id, moi, ket_qua)
        await repository.update_task_status(
            workflow_id, moi, TaskStatus.SUCCESS if ket_qua.success else TaskStatus.FAILED
        )
        da_lam.append(moi)
        logger.info("thuc hien ho so %s bang buoc %s", row.get("task_id"), moi)

    return da_lam
