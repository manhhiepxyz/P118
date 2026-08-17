"""Query trạng thái "việc cần chú ý" cho icon thông báo.

Hai nguồn "đang chờ chính user này" của một workflow:

  - `workflows.status = 'WAITING_APPROVAL'` — thanh toán đang chờ duyệt
    (`persist_pending_approval` ghi trực tiếp status này vào cột).
  - tồn tại một `workflow_clarifications` chưa trả lời (`resolved_at IS NULL`) —
    workflow đang chờ user bổ sung thông tin. Trạng thái này KHÔNG được lưu
    vào `workflows.status` (detail endpoint SUY RA nó từ bảng con); vì vậy query
    ở đây phải tự nối sang bảng con, giống `get_demo_workflow_status`.

Giữ nguyên tắc tách bạch với list endpoint: `?status=attention` của
`/workflows/demo` CHỈ thấy WAITING_APPROVAL (contract đã bị test ghim); bảng
thông báo này mở rộng thêm nhánh clarification mà KHÔNG đụng endpoint cũ.
"""

from __future__ import annotations

from typing import Any

# Đánh dấu loại việc cần chú ý — dùng cho `kind` trong payload thông báo.
KIND_PAYMENT_APPROVAL = "payment_approval"
KIND_CLARIFICATION = "clarification"


async def list_actionable_workflows(
    pool: Any,
    owner_user_id: str,
) -> list[dict[str, Any]]:
    """Workflow của `owner_user_id` đang chờ chính họ hành động.

    Trả list dict `{workflow_id, goal, status, updated_at, kind}` — `kind` phân
    biệt chờ duyệt thanh toán (`payment_approval`) với chờ bổ sung thông tin
    (`clarification`). Chỉ tính workflow chưa archive; không tính workflow của
    người khác (owner filter nằm ngay trong SQL).
    """
    rows = await pool.fetch(
        """
        SELECT w.workflow_id, w.goal, w.status, w.updated_at,
               EXISTS (
                   SELECT 1 FROM workflow_clarifications c
                   WHERE c.workflow_id = w.workflow_id AND c.resolved_at IS NULL
               ) AS has_open_clarification
        FROM workflows w
        WHERE w.owner_user_id = $1
          AND w.archived_at IS NULL
          AND (
              w.status = 'WAITING_APPROVAL'
              OR EXISTS (
                  SELECT 1 FROM workflow_clarifications c
                  WHERE c.workflow_id = w.workflow_id AND c.resolved_at IS NULL
              )
          )
        ORDER BY w.updated_at DESC
        """,
        owner_user_id,
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        kind = KIND_PAYMENT_APPROVAL if row["status"] == "WAITING_APPROVAL" else KIND_CLARIFICATION
        items.append(
            {
                "workflow_id": str(row["workflow_id"]),
                "goal": row["goal"],
                "status": row["status"],
                "updated_at": row["updated_at"],
                "kind": kind,
            }
        )
    return items


async def count_pending_verification_records(pool: Any) -> int:
    """Số đơn xác thực (căn hộ/xe) đang chờ người duyệt.

    Dùng cho badge của provider/admin. `verification_records` nằm trong cùng
    PostgreSQL chia sẻ — đọc thẳng qua pool của main app (như
    `verification_routes` đang materialize bằng pool), không gọi lại provider.
    """
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM verification_records WHERE status = 'PENDING'"
    )
    return int(count or 0)


async def count_pending_viewing_approvals(pool: Any) -> int:
    """Số yêu cầu lịch tham quan đang chờ provider/admin duyệt.

    Cùng bảng `viewing_approvals` mà `/viewing-approvals` đọc — đếm thẳng qua
    pool của main app, không gọi lại Tour provider (provider chưa biết lịch
    cho tới khi được duyệt; yêu cầu chỉ tồn tại ở PostgreSQL).
    """
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM viewing_approvals WHERE status = 'AWAITING'"
    )
    return int(count or 0)
