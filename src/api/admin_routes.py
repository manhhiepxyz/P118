"""Endpoint quản trị — gán liên kết tài khoản ↔ cư dân.

Đây là đường DUY NHẤT ghi vào `user_resident_links`. Không có endpoint tương
ứng cho customer, và đó là chủ ý: nếu người dùng tự khẳng định được mình sở
hữu một căn hộ thì toàn bộ mô hình quyền cư dân chỉ còn là một biểu mẫu.

Trong hệ thống thật, chỗ này là nơi kết quả xác minh của provider/ban quản lý
được ghi lại. Backend chỉ ĐỌC trạng thái đó — không thực hiện eKYC, không đọc
CCCD, không so khuôn mặt.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from src.api.deps import require_roles
from src.api.verification_routes import _ownership_connector
from src.config import get_settings
from src.connectors.ownership import OwnershipConnector, OwnershipProviderError
from src.db.user_repository import UserRepository
from src.db.verification_receipt_repository import (
    VerificationReceipts,
    VerificationRecoveryUnavailableError,
)
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.service_approval import SERVICE_LABELS
from src.orchestration.verification_views import enrich


class AdminUserRoleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["customer", "admin", "provider"]


class AdminUserStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_archived: bool


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Message dùng chung cho mọi trường hợp "không tìm thấy". Phân biệt "user không
# tồn tại" với "resident không tồn tại" biến endpoint này thành công cụ dò danh
# bạ: gửi ID bất kỳ, đọc thông báo, biết ID nào có thật.
_NOT_FOUND = "Không tìm thấy dữ liệu phù hợp."


def _safe_uuid(value: str):
    """ID sai định dạng phải thành 404, không phải 500.

    Một `ValueError` chưa bắt trả 500 kèm traceback, và traceback là nơi giá
    trị vừa gửi bị ghi lại nguyên văn.
    """
    import uuid

    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from None


# ĐÃ XOÁ: ba route liên kết cư dân của admin.
#
#     POST /admin/resident-links/{user_id}
#     GET  /admin/resident-link-requests
#     POST /admin/resident-link-requests/{request_id}/decision
#
# `user_resident_links.verification_status = 'VERIFIED'` là công tắc mở TOÀN BỘ
# dịch vụ cư dân. Ba route trên bật được công tắc ấy mà không cần hồ sơ nào,
# không ảnh chứng minh, không ai bên ngoài xác nhận.
#
# Đo được trước khi xoá, một request duy nhất từ tài khoản admin:
#
#     link trước           0 dòng
#     POST …/resident-links/{user_id}   http=200
#     link sau             resident_id=RES-PB, verification_status=VERIFIED
#     verification_records 0 dòng      ← không hồ sơ nào được provider duyệt
#     /auth/me             resident_verification_status=VERIFIED
#
# Tức là quyền cư dân mở hoàn toàn, dựa trên lời khẳng định của chính hệ thống.
#
# Một công tắc có hai đường bật thì đường yếu hơn là đường thật — kẻ tấn công
# và người đang vội đều chọn đường dễ. Đường canonical DUY NHẤT giờ là:
#
#     customer  POST /verification-records            (kèm ảnh chứng minh)
#     provider  POST /verification-records/{id}/decide
#               → _materialize_approved → materialize_resident_link
#
# Bảng `link_requests` và `user_resident_links` GIỮ NGUYÊN: dữ liệu cũ vẫn phải
# đọc được, và `materialize_resident_link` vẫn ghi vào `user_resident_links`.
# Chỉ đường HTTP bị đóng.


@router.get(
    "/metrics",
    summary="Số liệu vận hành toàn hệ thống (chỉ admin)",
)
async def system_metrics(
    _admin: dict = Depends(require_roles("admin")),
) -> dict:
    """Đếm workflow trên TOÀN hệ thống, không lọc theo chủ sở hữu.

    Vì sao cần endpoint riêng thay vì nới `GET /workflows/demo`: endpoint đó
    lọc theo `owner_user_id` và đó là hành vi ĐÚNG cho khách hàng. Nới phạm vi
    của nó theo role là cách rò rỉ dữ liệu giữa người dùng — chỉ cần một nhánh
    `if role == "admin"` đặt sai chỗ là mọi khách hàng đọc được workflow của
    nhau.

    Sự cố đã xảy ra vì thiếu endpoint này: `AdminDashboardPage` gọi
    `listWorkflows()` — vốn lọc theo chủ sở hữu — rồi hiển thị kết quả dưới
    tiêu đề "Giám sát toàn bộ workflow". Tài khoản admin không sở hữu workflow
    nào nên màn hình luôn hiện 0, trong khi database có 92 workflow. Admin nhìn
    thấy đúng view của một khách hàng trên dữ liệu rỗng của chính mình.

    CHỈ trả về SỐ ĐẾM. Không trả `goal`, không trả tên chủ sở hữu: giám sát vận
    hành cần biết có bao nhiêu việc đang hỏng, không cần biết ai yêu cầu gì.
    Một dashboard tiện tay hiển thị nội dung yêu cầu của cư dân là một cách rò
    rỉ dữ liệu được cấp phép sẵn.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        row = await pool.fetchrow(
            """
            SELECT
                (SELECT SUM(total_tokens) FROM llm_usage)                       AS total_llm_tokens,
                (SELECT COUNT(*) FROM llm_usage)                                AS total_llm_calls,
                -- Chi phí và độ trễ đọc từ cột trên chính `workflows` (nhánh
                -- observability thêm vào). Gộp vào ĐÂY thay vì dựng endpoint
                -- `/metrics` thứ hai: hai handler cùng đường dẫn thì FastAPI
                -- chỉ dùng cái đăng ký trước, và cái còn lại thành code chết
                -- mà không ai biết — kể cả khi nó là bản đã được sửa.
                COALESCE(SUM(total_cost), 0.0)                                  AS total_cost,
                COALESCE(AVG(latency_ms), 0.0)                                  AS avg_latency_ms,
                count(*)                                                        AS total,
                count(*) FILTER (WHERE status IN ('PENDING', 'RUNNING'))        AS running,
                count(*) FILTER (WHERE status = 'WAITING_APPROVAL')             AS waiting,
                -- Đúng 6 giá trị của `WorkflowStatus`, không hơn.
                -- Bản đầu còn lọc 'EXECUTION_ERROR'/'PLANNING_ERROR' —
                -- những cái đó là `ErrorCode`, không phải trạng thái workflow,
                -- nên chúng không bao giờ khớp và ô "Thất bại" thiếu số một
                -- cách âm thầm. Một bộ lọc sai tên trạng thái không báo lỗi;
                -- nó chỉ đếm ra 0.
                count(*) FILTER (WHERE status = 'FAILED')                       AS failed,
                count(*) FILTER (WHERE status = 'SUCCESS')                      AS success,
                count(*) FILTER (WHERE status = 'CANCELLED')                    AS cancelled,
                -- CHỜ NGƯỜI DÙNG và MỒ CÔI là hai chuyện khác hẳn nhau.
                --
                -- Bản đầu gộp chúng vào một ô "Kẹt quá 5 phút" đếm mọi
                -- PENDING/RUNNING không nhúc nhích. Đo ra: cả 17 workflow nó
                -- đếm đều đang chờ người dùng bổ sung thông tin — tức hệ thống
                -- đang hoạt động ĐÚNG. Ô đó báo động giả 100%, và một chỉ số
                -- vận hành báo động giả thì tệ hơn không có: người trực học
                -- cách phớt lờ nó, rồi phớt lờ luôn lần nó nói thật.
                --
                -- `awaiting_user` dùng chính điều kiện mà sweeper dùng để THA:
                -- có clarification chưa giải quyết. Hai chỗ phải cùng một định
                -- nghĩa, nếu không màn hình sẽ mâu thuẫn với hành vi hệ thống.
                count(*) FILTER (
                    WHERE status IN ('PENDING', 'RUNNING')
                      AND EXISTS (
                          SELECT 1 FROM workflow_clarifications c
                          WHERE c.workflow_id = workflows.workflow_id
                            AND c.resolved_at IS NULL
                      )
                )                                                               AS awaiting_user,
                -- MỒ CÔI: quá hạn TTL của sweeper mà sweeper vẫn chưa dọn.
                -- Khác 0 nghĩa là vòng quét đang không chạy — đó mới là thứ
                -- người vận hành cần biết, và nó nói về HỆ THỐNG chứ không
                -- phải về người dùng.
                count(*) FILTER (
                    WHERE status IN ('PENDING', 'RUNNING')
                      AND NOT EXISTS (
                          SELECT 1 FROM workflow_clarifications c
                          WHERE c.workflow_id = workflows.workflow_id
                            AND c.resolved_at IS NULL
                      )
                      AND updated_at < NOW() - make_interval(secs => $1)
                )                                                               AS orphaned
            FROM workflows
            WHERE archived_at IS NULL
            """,
            float(get_settings().zombie_running_ttl_hours) * 3600.0,
        )
    finally:
        await pool.close()

    return {
        "total": row["total"],
        "running": row["running"],
        "waiting_approval": row["waiting"],
        "failed": row["failed"],
        "success": row["success"],
        "cancelled": row["cancelled"],
        "awaiting_user": row["awaiting_user"],
        "orphaned": row["orphaned"],
        "llm_tokens": row["total_llm_tokens"] or 0,
        "llm_calls": row["total_llm_calls"] or 0,
        "total_cost": float(row["total_cost"] or 0.0),
        "avg_latency_ms": float(row["avg_latency_ms"] or 0.0),
    }


@router.get("/users", summary="Danh sách tất cả người dùng (ẩn danh bớt thông tin nhạy cảm)")
async def get_all_users(_admin: dict = Depends(require_roles("admin"))):
    repository = await acquire_repository()
    pool = repository._pool
    user_repo = UserRepository(pool)
    users = await user_repo.list_all_users()
    return {"items": users}


@router.patch("/users/{user_id}/role", summary="Cập nhật quyền người dùng")
async def update_user_role(
    user_id: str, request: AdminUserRoleUpdateRequest, _admin: dict = Depends(require_roles("admin"))
):
    repository = await acquire_repository()
    pool = repository._pool
    user_repo = UserRepository(pool)
    user = await user_repo.update_role(user_id, request.role)
    if not user:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return user


@router.patch("/users/{user_id}/status", summary="Khóa/Mở khóa người dùng")
async def update_user_status(
    user_id: str, request: AdminUserStatusUpdateRequest, _admin: dict = Depends(require_roles("admin"))
):
    repository = await acquire_repository()
    pool = repository._pool
    user_repo = UserRepository(pool)
    user = await user_repo.update_status(user_id, request.is_archived)
    if not user:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return user


# `/admin/workflows/history` và `POST /admin/workflows/{id}/retry` ĐÃ BỊ XOÁ.
#
# `history` trả `goal` THÔ và `input_data` của bước hỏng — nội dung người dùng
# gõ, chưa qua lọc nào. Nó cũng trả lời đúng câu hỏi mà `/admin/requests` trả
# lời. Hai endpoint cùng trả lời "hệ thống đang có yêu cầu gì" nghĩa là bản an
# toàn chỉ là một LỰA CHỌN nằm cạnh một bản không an toàn, và lựa chọn thì có
# lúc chọn sai.
#
# `retry` đặt thẳng `workflows.status = PENDING`, không điều kiện. Đo được trước
# khi xoá, cùng một tài khoản admin:
#
#     SUCCESS           http=200  ->  workflows.status=PENDING
#     WAITING_APPROVAL  http=200  ->  workflows.status=PENDING
#     CANCELLED         http=200  ->  workflows.status=CANCELLED   (guard tầng dưới)
#
# Dòng thứ ba đáng lo ngang hai dòng đầu: endpoint trả 200 cho một việc nó
# KHÔNG làm được. Người bấm tin là đã chạy lại.
#
# Mở lại một workflow SUCCESS nghĩa là bước đã cam kết ra provider có thể chạy
# lần hai; các tool này không idempotent. Mở lại một workflow đang chờ duyệt
# nghĩa là hàng đợi còn dòng AWAITING trong khi trạng thái nói là chưa chạy.
# Bằng chứng đã gửi (`ACKNOWLEDGED`, `external_request_id`) vẫn nằm nguyên.
#
# Recovery an toàn cần biết bước nào đã cam kết ra ngoài và bước nào chưa —
# tức checkpoint + idempotency + policy. Endpoint này không biết gì trong số
# đó, nên nó không phải một phiên bản thô sơ của tính năng ấy; nó là một đường
# vòng qua mọi hàng rào. Khi nào làm recovery thật thì làm như một capability
# riêng, không phải bằng cách hồi sinh dòng này.


# ---------------------------------------------------------------------------
# Giám sát yêu cầu — dành cho ADMIN, và KHÔNG dùng lại hàng đợi của provider.
#
# Ranh giới ở đây là ranh giới về HÀNH ĐỘNG, không phải về dữ liệu:
#
#   provider   thấy hồ sơ để QUYẾT ĐỊNH  →  /service-approvals, /review
#   admin      thấy trạng thái để BIẾT   →  /admin/requests
#
# Hai màn hình trả lời hai câu hỏi khác nhau, nên chúng đọc hai hình dạng dữ
# liệu khác nhau. Nếu admin đọc chính endpoint của provider thì màn giám sát đã
# có sẵn mọi thứ cần để mọc một nút Duyệt — và cái nút ấy sẽ mọc, vì nó chỉ
# cách một dòng JSX. Tách ở tầng route là cách rẻ nhất để nó không bao giờ có
# dữ liệu mà mọc.
#
# Endpoint này KHÔNG có động từ ghi. Không approve, không reject, không sửa nội
# dung yêu cầu, không chạy tiếp thay khách, không xác nhận thanh toán.
# ---------------------------------------------------------------------------

# Nhãn nghiệp vụ cho MÀN GIÁM SÁT. Sáu dịch vụ có cổng duyệt dùng lại đúng nhãn
# mà người duyệt nhìn thấy — hai màn hình gọi một việc bằng hai tên là cách
# chắc chắn để hai người nói chuyện lệch nhau. Hai tool còn lại không đi qua
# cổng ấy nên không có nhãn ở đó; khai báo ở đây, không sửa bảng gốc.
_ADMIN_SERVICE_LABELS: dict[str, str] = {
    **SERVICE_LABELS,
    "schedule_property_viewing": "Lịch tham quan dự án",
    "pay_fee": "Thanh toán phí",
}

# Tool lạ KHÔNG được rơi ra màn hình dưới tên nội bộ của nó.
#
# `.get(tool, tool)` là một fallback trông vô hại: nó "vẫn hiện được gì đó".
# Thứ nó hiện là tên hàm nội bộ — `register_property_interest`, `book_shuttle` —
# và màn giám sát là nơi ảnh chụp bị dán vào issue, chat, slide. Một tên tool
# rò ra là một mẩu bản đồ hệ thống, và nó rò đúng lúc không ai để ý: khi có
# tool MỚI mà bảng nhãn chưa kịp cập nhật.
#
# Fail-safe theo hướng ngược lại: không biết thì nói không biết.
_NHAN_CHUA_BIET = "Dịch vụ chưa xác định"


def _service_label(tool: object) -> str:
    """Nhãn tiếng Việt cho một tool. Không bao giờ trả tên tool."""
    label = _ADMIN_SERVICE_LABELS.get(str(tool)) if tool is not None else None
    if label is None and tool is not None:
        # Telemetry chỉ mang TÊN TRƯỜNG, không mang giá trị: mục đích của dòng
        # này là báo "bảng nhãn thiếu một mục", và in giá trị ra log là làm
        # đúng cái việc hàm này sinh ra để chặn.
        logger.info("admin_requests: thiếu nhãn nghiệp vụ cho một tool (xem SERVICE_LABELS)")
    return label or _NHAN_CHUA_BIET


# BA trường, không một.
#
# "Đang chờ ai" và "đã quyết định gì" là hai câu hỏi khác nhau, và một trường
# duy nhất chỉ trả lời được một câu. Bản đầu tiên của endpoint này dùng chung
# `approval_status`, và hệ quả đo được ngay: sau khi đơn vị DUYỆT, giá trị về
# `NONE` — y hệt một workflow chưa ai đụng tới. Lịch sử quyết định biến mất
# khỏi màn danh sách đúng lúc nó có ý nghĩa nhất.
#
#   waiting_for               đang chờ AI làm gì tiếp
#   provider_decision_status  ĐƠN VỊ đã quyết định gì
#   payment_decision_status   KHÁCH đã quyết định gì về khoản tiền
#
# `workflows.status` cũng không thay được: `WAITING_APPROVAL` mang cả "chờ đơn
# vị nhận việc" lẫn "chờ chính khách xác nhận tiền", mà hai thứ đó gọi hai
# người khác nhau.
_WAITING_PROVIDER = "PROVIDER"
_WAITING_CUSTOMER = "CUSTOMER_PAYMENT"
_NONE = "NONE"
_AWAITING = "AWAITING"
_APPROVED = "APPROVED"
_REJECTED = "REJECTED"

# Độ dài tối đa của mọi đoạn văn bản tự do đi ra khỏi endpoint này.
_MAX_FREE_TEXT = 500


def _clean_text(value: object) -> str | None:
    """Làm sạch văn bản tự do trước khi nó rời backend.

    Ba thứ bị cắt, và không thứ nào là giả định thừa:

      * chuỗi trông như token/khoá — `error_message` của provider đã từng mang
        nguyên header trong bản dựng lỗi, và log giám sát là nơi bí mật sống
        lâu nhất;
      * chuỗi kết nối database — nó chứa user/password ngay trong URL;
      * phần đuôi quá dài — màn giám sát cần một câu, không cần một stack trace.

    Đây là lọc THEO HÌNH DẠNG, không phải theo danh sách trường. Lọc theo tên
    trường thì mỗi lần thêm một trường mới là một lần có thể quên.
    """
    if value is None:
        return None
    text = str(value)
    text = _SECRETISH.sub("[đã ẩn]", text)
    text = text.strip()
    if len(text) > _MAX_FREE_TEXT:
        text = text[:_MAX_FREE_TEXT].rstrip() + "…"
    return text or None


_SECRETISH = re.compile(
    r"""(?ix)
      (?: postgres(?:ql)? :// [^\s]+ )              # DSN, kèm user:password
    | (?: \b sk- [A-Za-z0-9_\-]{16,} )              # khoá dạng OpenAI/DeepSeek
    | (?: \b Bearer \s+ [A-Za-z0-9._\-]{20,} )      # token trong header
    | (?: \b eyJ [A-Za-z0-9._\-]{20,} )             # JWT
    | (?: (?:api[_\-]?key|token|secret|password) \s* [:=] \s* \S+ )
    """
)


def _waiting_for(awaiting_provider: int, awaiting_payment: int) -> str:
    """Đang chờ AI. Đơn vị trước, vì khoản tiền chỉ có nghĩa sau khi có dịch vụ."""
    if awaiting_provider:
        return _WAITING_PROVIDER
    if awaiting_payment:
        return _WAITING_CUSTOMER
    return _NONE


def _provider_decision_status(statuses: object) -> str:
    """Đơn vị đã quyết định gì, gộp trên mọi bước của yêu cầu.

    TỪ CHỐI thắng DUYỆT khi cả hai cùng có: một yêu cầu gồm bốn dịch vụ mà một
    dịch vụ bị từ chối thì điều người giám sát cần thấy trong danh sách là lời
    từ chối ấy, không phải ba lời đồng ý kia.
    """
    raw = json.loads(statuses) if isinstance(statuses, str) else (statuses or [])
    values = {str(v) for v in raw if v}
    if not values:
        return _NONE
    if _AWAITING in values:
        return _AWAITING
    if _REJECTED in values:
        return _REJECTED
    if _APPROVED in values:
        return _APPROVED
    # EXPIRED và mọi trạng thái tương lai: không đoán, và không phơi giá trị thô.
    return _NONE


def _payment_decision_status(status: object) -> str:
    return str(status) if status in {_AWAITING, _APPROVED, _REJECTED} else _NONE


def _service_names(tools: object) -> list[str]:
    """Nhãn nghiệp vụ, giữ thứ tự và bỏ trùng. Không phơi tên tool ra màn hình."""
    raw = json.loads(tools) if isinstance(tools, str) else (tools or [])
    seen: list[str] = []
    for tool in raw:
        label = _service_label(tool)
        if label not in seen:
            seen.append(label)
    return seen


def _iso(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


@router.get("/requests", summary="Yêu cầu đang có trong hệ thống (giám sát, chỉ đọc)")
async def list_admin_requests(
    page: int = 1,
    limit: int = 50,
    search_user: str | None = None,
    status: str | None = None,
    _admin: dict = Depends(require_roles("admin")),
) -> dict:
    """Ai đang yêu cầu gì, đang chờ ai, và có gì hỏng.

    Chỉ đọc. Không có đường nào từ đây tới một quyết định nghiệp vụ.
    """
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit phải trong khoảng 1–200.")
    repository = await acquire_repository()
    page_data = await repository.list_admin_requests(page=page, limit=limit, search_user=search_user, status=status)
    items = []
    for row in page_data["items"]:
        items.append(
            {
                "workflow_id": str(row["workflow_id"]),
                "account": {
                    "user_id": str(row["owner_user_id"]) if row.get("owner_user_id") else None,
                    "username": row.get("owner_username"),
                    "display_name": row.get("owner_full_name") or row.get("owner_username"),
                },
                "goal": _clean_text(row.get("goal")),
                "service_names": _service_names(row.get("tools")),
                "workflow_status": row.get("workflow_status"),
                "waiting_for": _waiting_for(
                    int(row.get("awaiting_provider") or 0), int(row.get("awaiting_payment") or 0)
                ),
                "provider_decision_status": _provider_decision_status(row.get("provider_decisions")),
                "payment_decision_status": _payment_decision_status(row.get("payment_decision")),
                "current_step": (_service_label(row.get("current_tool")) if row.get("current_tool") else None),
                "created_at": _iso(row.get("created_at")),
                "updated_at": _iso(row.get("updated_at")),
                "failure_summary": _clean_text(row.get("failure_message")),
            }
        )
    return {"total": page_data["total"], "page": page_data["page"], "limit": page_data["limit"], "items": items}


@router.get("/requests/{workflow_id}", summary="Chi tiết một yêu cầu (giám sát, chỉ đọc)")
async def get_admin_request(
    workflow_id: str,
    _admin: dict = Depends(require_roles("admin")),
) -> dict:
    """Các bước, trạng thái từng bước, ĐƠN VỊ nào đã quyết định và lúc nào.

    `decided_by` đọc từ `service_approvals` — chỗ duy nhất ghi lại ai đã ký.
    Suy nó ra từ trạng thái task là dựng một cái tên không có trong dữ liệu.
    """
    _safe_uuid(workflow_id)
    repository = await acquire_repository()
    record = await repository.get_admin_request(workflow_id)
    if record is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    head = record["workflow"]
    payment = record.get("payment")
    awaiting_provider = sum(1 for s in record["steps"] if s.get("approval_status") == _AWAITING)
    awaiting_payment = 1 if payment and payment.get("status") == _AWAITING else 0
    provider_decisions = [s.get("approval_status") for s in record["steps"] if s.get("approval_status")]
    return {
        "workflow_id": str(head["workflow_id"]),
        "account": {
            "user_id": str(head["owner_user_id"]) if head.get("owner_user_id") else None,
            "username": head.get("owner_username"),
            "display_name": head.get("owner_full_name") or head.get("owner_username"),
        },
        "goal": _clean_text(head.get("goal")),
        "workflow_status": head.get("workflow_status"),
        "waiting_for": _waiting_for(awaiting_provider, awaiting_payment),
        "provider_decision_status": _provider_decision_status(provider_decisions),
        "payment_decision_status": _payment_decision_status(payment.get("status") if payment else None),
        "created_at": _iso(head.get("created_at")),
        "updated_at": _iso(head.get("updated_at")),
        "steps": [
            {
                "task_id": step["task_id"],
                "service_name": _service_label(step["tool"]),
                "status": step.get("status"),
                "approval_status": step.get("approval_status"),
                # AI đã ký, dưới dạng người đọc được — không phải một UUID.
                #
                # `service_approvals.decided_by` lưu `username` của người duyệt
                # (xem `decide_service_approval`). Trả nguyên chuỗi đó là đúng
                # thứ admin cần để gọi lại, và KHÔNG kèm liên hệ: màn giám sát
                # không cần số điện thoại của ai để làm việc của nó.
                "decided_by": (
                    {"username": step["decided_by"], "display_name": step["decided_by"]}
                    if step.get("decided_by")
                    else None
                ),
                "decided_at": _iso(step.get("decided_at")),
                "reject_reason": _clean_text(step.get("reject_reason")),
                "provider_submission_status": step.get("provider_submission_status"),
                "failure_summary": _clean_text(step.get("error_message")),
                "updated_at": _iso(step.get("updated_at")),
            }
            for step in record["steps"]
        ],
        "payment": (
            {
                "task_id": payment["task_id"],
                "status": payment["status"],
                "amount": payment["amount"],
                "currency": payment["currency"],
                "decided_at": _iso(payment.get("decided_at")),
            }
            if payment
            else None
        ),
        "history": [{"stage": event["stage"], "at": _iso(event.get("created_at"))} for event in record["events"]],
    }


# ---------------------------------------------------------------------------
# Giám sát hồ sơ XÁC MINH — read-only, và KHÔNG dùng lại hàng đợi của provider.
#
# `verification_records` sống ở Ownership Provider, không nằm trong workflow
# nào, nên `/admin/requests` không thấy chúng. Sau khi ba route admin legacy bị
# xoá, admin mù hẳn với loại yêu cầu này — endpoint dưới đây trả lại tầm nhìn
# ấy mà không trả lại quyền quyết định.
#
# KHÔNG copy trạng thái verification vào bảng workflow. Một bản sao là một bản
# sẽ lệch, và lệch theo hướng tệ nhất: màn giám sát báo PENDING cho một hồ sơ
# provider đã từ chối.
#
#   nguồn sự thật vòng đời hồ sơ   Ownership Provider
#   nguồn sự thật về tài khoản     PostgreSQL của main app
#
# Admin browser KHÔNG gọi thẳng `/verification-records` (đó là bề mặt của
# provider, có kèm động từ quyết định). Nó gọi endpoint này, và endpoint này
# gọi provider hộ.
# ---------------------------------------------------------------------------

_TEN_HO_SO = {"apartment": "Xác minh căn hộ", "vehicle": "Xác minh phương tiện"}
_TRANG_THAI_HO_SO = frozenset({"PENDING", "APPROVED", "REJECTED"})
# Tài khoản đã xoá/khoá vẫn phải hiện hồ sơ — audit không được có lỗ. Nhưng
# không dựng lại một cái tên không còn nữa.
_TAI_KHOAN_KHONG_CON = "Tài khoản không còn hoạt động"


def _admin_verification_view(record: dict[str, Any], accounts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Tóm tắt an toàn của MỘT hồ sơ.

    Danh sách trường ở đây là allowlist, không phải blocklist: mọi thứ provider
    trả về mà không có tên trong này thì không đi tiếp. `claimed_data` và
    `proof_image_urls` vì thế không cần một dòng lọc riêng — chúng đơn giản
    không được chép sang.
    """
    applicant = str(record.get("applicant_user_id") or "")
    account = accounts.get(applicant)
    loai = str(record.get("record_type") or "")
    return {
        "record_id": record.get("record_id"),
        "record_type": loai.upper(),
        "request_name": _TEN_HO_SO.get(loai, "Yêu cầu xác minh"),
        "account": (
            {
                "user_id": applicant or None,
                "username": account["username"],
                "display_name": account.get("full_name") or account["username"],
            }
            if account
            else {"user_id": applicant or None, "username": None, "display_name": _TAI_KHOAN_KHONG_CON}
        ),
        "status": record.get("status"),
        "decided_by": record.get("decided_by"),
        "decided_at": record.get("decided_at"),
        "reject_reason": _clean_text(record.get("reject_reason")),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at") or record.get("decided_at") or record.get("created_at"),
    }


@router.get("/verifications", summary="Hồ sơ xác minh căn hộ/xe (giám sát, chỉ đọc)")
async def list_admin_verifications(
    record_type: str | None = None,
    status: str | None = None,
    search_user: str | None = None,
    _admin: dict = Depends(require_roles("admin")),
    connector: OwnershipConnector = Depends(_ownership_connector),
) -> dict:
    """Ai đang xin xác minh gì, và ĐƠN VỊ đã quyết định chưa.

    Không có động từ ghi. Không có link tới `/review`.
    """
    if record_type is not None and record_type not in _TEN_HO_SO:
        raise HTTPException(status_code=422, detail="Loại hồ sơ không hợp lệ.")
    if status is not None and status not in _TRANG_THAI_HO_SO:
        raise HTTPException(status_code=422, detail="Trạng thái không hợp lệ.")

    try:
        records = await connector.list_records(record_type=record_type, status=status)
    except OwnershipProviderError:
        # Message của provider KHÔNG đi tiếp, và URL của nó cũng vậy: cả hai
        # đều mô tả hạ tầng nội bộ cho một người không cần biết.
        raise HTTPException(status_code=503, detail="Dịch vụ xác minh đang tạm ngừng, vui lòng thử lại sau.") from None
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("admin_verifications: không đọc được provider (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Dịch vụ xác minh đang tạm ngừng, vui lòng thử lại sau.") from None

    # Tra tài khoản MỘT lượt cho cả trang. Tra từng dòng là N+1 query trên một
    # màn hình mà admin để mở suốt ngày.
    repository = await acquire_repository()
    repository_pool = repository._pool  # noqa: SLF001 - pool dùng chung của runtime
    ids = {str(r.get("applicant_user_id")) for r in records if r.get("applicant_user_id")}
    accounts: dict[str, dict[str, Any]] = {}
    if ids:
        async with repository_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, username, full_name FROM users WHERE id = ANY($1::uuid[])",
                [_safe_uuid(i) for i in ids],
            )
        accounts = {str(r["id"]): dict(r) for r in rows}

    # Cùng `enrich()` với hai endpoint kia — admin không có bảng trạng thái
    # riêng. Ba màn hình nói khác nhau về một hồ sơ là cách để hai người tranh
    # cãi về một việc mà cả hai đều "nhìn thấy".
    try:
        receipts = VerificationReceipts(repository_pool)
        snapshots = await receipts.snapshot_for([str(r.get("record_id")) for r in records])
    except VerificationRecoveryUnavailableError:
        # Fail-closed: không trả danh sách một phần, không rơi về provider
        # status — bản rút gọn ấy hiện `APPROVED` như đã hoàn tất.
        raise HTTPException(
            status_code=503, detail="Hệ thống chưa đọc được tiến trình xác minh. Vui lòng thử lại sau."
        ) from None
    items = enrich(records, snapshots, kind="admin", accounts=accounts)
    if search_user:
        can = search_user.lower()
        items = [i for i in items if can in (i["account"]["username"] or "").lower()]
    return {"items": items}
