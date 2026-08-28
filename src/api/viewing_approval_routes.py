"""Yêu cầu tham quan chờ duyệt — ĐƠN VỊ CUNG CẤP quyết định qua cổng /review.

Path song song với `verification_routes.py` (xác thực căn hộ/xe): cùng người
duyệt (chỉ provider), cùng cổng /review, nhưng nguồn dữ liệu KHÁC.

  - verification: record nằm ở Mock Ownership Provider (8004), main app chỉ
    materialize khi duyệt.
  - viewing: yêu cầu nằm thẳng trong PostgreSQL (`viewing_approvals`) — Tour
    provider (8005) CHƯA biết lịch cho tới khi được duyệt, vì workflow DỪNG ở
    bước `schedule_property_viewing` để hỏi người. Duyệt = gọi Tour materialize
    lịch (lấy `viewing_id` + 4 thông tin người đón tiếp) rồi chạy nốt các bước
    phụ thuộc (`book_shuttle`, ~30s).

Điểm khác so với verification (tải):

  - Duyệt KHÔNG chỉ đổi status: nó chạy cả phần DAG còn lại qua Executor, vì
    `schedule_property_viewing` là bước TRƯỚC của `book_shuttle` chứ không phải
    bước cuối. Vì vậy route này gọi `resume_viewing_after_approval` — đồng bộ,
    mất ~30s (đặt xe). UI đang hiện "Đang xử lý…".
  - Từ chối đánh FAILED cả chuỗi (viewing + downstream), không giữ gì cả —
    không có "chỗ đỗ" để giữ như payment.

Ranh giới tin cậy:

  - Browser KHÔNG gửi `status`/`decided_by`/`reject_reason` cho quyết định duyệt;
    chỉ gửi `{decision, reject_reason?}`. `decided_by` lấy từ JWT của người duyệt.
  - Chỉ ĐƠN VỊ CUNG CẤP vào được (`require_roles("provider")`). Admin giám sát
    bằng số liệu ở `/admin`, không ký thay đơn vị. Khách đọc trạng thái workflow
    của mình qua GET /workflows/demo/{id} — field `viewing_approval` KHÔNG chứa
    PII của người yêu cầu.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.api.deps import require_roles
from src.api.routes import _DEMO_JOBS, request_fresh_answer

# Dùng CHUNG bảng mã và bộ làm sạch với hàng đợi dịch vụ. Hai bản sao của cùng
# một danh sách sẽ lệch nhau, và lệch ở đây nghĩa là một mã hợp lệ bên này bị
# từ chối bên kia.
from src.api.service_approval_routes import REJECT_CODES, _sach
from src.config import get_settings
from src.orchestration.demo_service import (
    _viewing_materialize_error_message,
    reject_viewing,
    resume_viewing_after_approval,
)
from src.orchestration.runtime_provider import acquire_repository

# Nguồn quyền sở hữu CANONICAL — cùng hai hàm hàng đợi dịch vụ đang dùng.
# Một bảng ánh xạ thứ hai, hay một helper "gần giống", là một nguồn sự thật thứ
# hai; và hai nguồn sự thật về quyền thì sớm muộn nói khác nhau.
from src.orchestration.service_approval import don_vi_cua_tai_khoan, so_huu_boi
from src.orchestration.viewing_approval import (
    expire_stale_viewing_approvals,
    get_pending_viewing_approval,
    list_viewing_approvals,
)
from src.services.email_service import send_workflow_batch_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/viewing-approvals", tags=["viewing-approvals"])

_STATUSES = {"AWAITING", "APPROVED", "REJECTED", "EXPIRED"}


class _DecideBody(BaseModel):
    """Cùng hợp đồng với hàng đợi dịch vụ (`service_approval_routes`).

    Lịch tham quan đi đường riêng, nên nó KHÔNG tự thừa hưởng gì từ bên kia —
    và khoảng cách ấy đo được: đỗ xe hết chỗ thì khách được mời chọn khu khác,
    còn tham quan hết giờ thì yêu cầu dừng hẳn trong im lặng. Cùng một hậu quả
    nghiệp vụ phải có cùng một hợp đồng.
    """

    model_config = ConfigDict(extra="forbid")

    decision: str = Field(..., pattern="^(approve|reject)$")
    reject_code: Literal[REJECT_CODES] | None = Field(default=None)  # type: ignore[valid-type]
    reject_reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def _mot_quyet_dinh_khong_mang_ca_hai_nghia(self) -> _DecideBody:
        if self.decision == "approve":
            if self.reject_code is not None or self.reject_reason is not None:
                raise ValueError("Quyết định duyệt không mang lý do từ chối.")
            return self
        if self.reject_code is None:
            raise ValueError("Từ chối cần một nguyên nhân trong danh sách.")
        reason = _sach(self.reject_reason or "")
        if not reason:
            raise ValueError("Từ chối cần lý do cho người dùng đọc.")
        object.__setattr__(self, "reject_reason", reason)
        return self


# `_mot_quyet_dinh_khong_mang_ca_hai_nghia` trả về chính `_DecideBody`, và tên
# ấy chưa tồn tại lúc thân class được dựng. Không rebuild thì FastAPI dựng
# TypeAdapter cho một schema chưa đầy đủ và MỌI request tới route này nổ 500 —
# kể cả request hợp lệ. File này không bật `from __future__ import annotations`
# nên phải gọi tay; đừng thay bằng cách bỏ annotation, nó là thứ giữ hợp đồng.
_DecideBody.model_rebuild()


def _pending_to_dict(pending) -> dict:
    """Bản chép công khai cho người duyệt (gồm PII người yêu cầu — reviewer)."""
    return {
        "workflow_id": pending.workflow_id,
        "task_id": pending.task_id,
        "status": pending.status,
        "project_id": pending.project_id,
        "project_name": pending.project_name,
        "viewing_date": pending.viewing_date,
        "viewing_time": pending.viewing_time,
        "passenger_count": pending.passenger_count,
        "wants_shuttle": pending.wants_shuttle,
        "applicant_name": pending.applicant_name,
        "applicant_phone": pending.applicant_phone,
        "reject_reason": pending.reject_reason,
        "decided_by": pending.decided_by,
    }


@router.get("", summary="Danh sách yêu cầu lịch tham quan (cho người duyệt)")
async def list_viewing_approval_records(
    status: str | None = None,
    reviewer: dict = Depends(require_roles("provider")),
) -> dict:
    """Danh sách yêu cầu tham quan cho cổng /review — mới nhất trước.

    `status` lọc theo vòng đời quyết định (AWAITING/APPROVED/REJECTED); bỏ qua
    khi None (mặc định hiện cả ba cho tab "Lịch sử").
    """
    if status is not None and status not in _STATUSES:
        raise HTTPException(status_code=422, detail="Trạng thái không hợp lệ.")

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        # Dọn hàng chờ TRƯỚC khi trả danh sách.
        #
        # Người duyệt không có cách nào biết một yêu cầu đã hết hiệu lực chỉ
        # bằng cách nhìn: nó trông y hệt yêu cầu hợp lệ. Bấm Duyệt xong mới vỡ
        # ở Tour provider, và lỗi trả về là 502 — không nói được gì cho người
        # đang đứng trước màn hình. Lọc ở đây thì thứ không duyệt được không
        # bao giờ xuất hiện như thể duyệt được.
        await expire_stale_viewing_approvals(pool)
        # Đơn vị đến từ TÀI KHOẢN, không bao giờ từ query string — nhận nó từ
        # request là để người gọi tự khai mình nhân danh ai.
        #
        # LUÔN là một danh sách, kể cả rỗng. `None` (không lọc) chỉ dành cho
        # công cụ nội bộ; không vai nào ở tầng HTTP được miễn lọc, kể cả admin —
        # admin không có mặt bằng và không tiếp khách, nên không có gì để duyệt.
        don_vi = await don_vi_cua_tai_khoan(pool, str(reviewer["id"]))
        items = await list_viewing_approvals(pool, status, don_vi=don_vi)
    finally:
        await pool.close()
    # Response KHÔNG có `total`. Cố ý: một con số đếm bằng câu truy vấn thứ hai
    # là chỗ để mệnh đề lọc bị quên ở đúng một trong hai câu — đã xảy ra một lần
    # ở hàng đợi dịch vụ, nơi `total` đếm cả bảng trong khi danh sách đã lọc.
    # Thêm nó thì phải thêm cùng `WHERE`, không phải `count(*)` trần.
    return {"items": [_pending_to_dict(item) for item in items]}


async def _bat_buoc_so_huu(workflow_id: str, reviewer: dict) -> list[str]:
    """Chặn 404 nếu người duyệt không nhân danh đơn vị giữ lịch này.

    Trả về danh sách đơn vị của tài khoản để người gọi truyền tiếp xuống câu
    ghi — cùng một danh sách, đọc một lần.

    Vì sao cần cổng này dù `record_viewing_decision` đã mang mệnh đề quyền sở
    hữu trong câu UPDATE: hai lời từ chối đều KHÔNG ghi gì, nhưng chúng nói hai
    câu khác nhau. Không có cổng, một đơn vị lạ nhận `409 ALREADY_DECIDED` cho
    một dòng có thật và `404` cho một `workflow_id` bịa ra — và sự khác nhau ấy
    chính là câu trả lời cho "dòng này có tồn tại không".

    Câu UPDATE vẫn giữ mệnh đề của nó. Hai hàng rào cho hai việc: hàm này quyết
    ĐỊNH DẠNG CÂU TRẢ LỜI, câu UPDATE bảo đảm KHÔNG GHI. Bỏ cái nào cũng để lại
    một nửa lỗ — và mỗi nửa đã được đo bằng một đột biến riêng.

    Là hàm riêng ở tầng module để bài kiểm nào không nói về quyền sở hữu có thể
    thay đúng một thứ, thay vì dựng cả một tài khoản có ánh xạ chỉ để đi qua nó.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        don_vi = await don_vi_cua_tai_khoan(pool, str(reviewer["id"]))
        pending = await get_pending_viewing_approval(pool, workflow_id)
        # `so_huu_boi` là hàm đã dùng cho hàng đợi dịch vụ — cùng bảng, cùng
        # luật fail-closed cho `service_provider_id IS NULL`.
        duoc_phep = pending is not None and await so_huu_boi(pool, workflow_id, pending.task_id, don_vi)
    finally:
        await pool.close()
    if not duoc_phep:
        # MỘT câu duy nhất cho cả "không có" lẫn "không phải của bạn". Hai câu
        # khác nhau là một kênh dò: gọi thử từng id và đọc mã trạng thái.
        logger.info("chan quyet dinh tham quan ngoai quyen so huu user=%s", reviewer.get("username"))
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu tham quan này.")
    return don_vi


@router.post("/{workflow_id}/decide", summary="Duyệt hoặc từ chối một lịch tham quan")
async def decide_viewing_approval(
    workflow_id: str,
    body: _DecideBody,
    background_tasks: BackgroundTasks,
    reviewer: dict = Depends(require_roles("provider")),
) -> dict:
    """ĐƠN VỊ TOUR quyết định lịch tham quan. Admin không có quyền này.

    Duyệt → `resume_viewing_after_approval` (đồng bộ, ~30s): materialize lịch
    qua Tour provider, ghi kết quả, chạy nốt `book_shuttle`. Từ chối →
    `reject_viewing` đánh FAILED chuỗi + workflow kèm lý do.

    `decided_by` lấy từ JWT của người duyệt (main app đặt, không nhận từ body).
    Double-decide bị chặn bởi `WHERE status='AWAITING'` → ResumeError ALREADY_DECIDED.
    """
    settings = get_settings()

    # Quyền sở hữu TRƯỚC mọi thứ khác, kể cả trước khi đụng cache.
    don_vi = await _bat_buoc_so_huu(workflow_id, reviewer)

    # Response đang cache trong `_DEMO_JOBS` được dựng lúc workflow còn chờ
    # duyệt. Sau quyết định, nó là ảnh cũ: nếu không bỏ đi, mọi lần poll tiếp
    # theo vẫn trả "chờ đơn vị xác nhận" dù database đã ghi SUCCESS/FAILED, và
    # giao diện mắc kẹt vĩnh viễn ở màn chờ (mirror payment route).
    job = _DEMO_JOBS.get(workflow_id)
    if job is not None:
        job["response"] = None

    if body.decision == "reject":
        try:
            outcome = await reject_viewing(
                workflow_id,
                body.reject_reason,
                decided_by=reviewer["username"],
                reject_code=body.reject_code,
                don_vi=don_vi,
            )
        except Exception as exc:  # noqa: BLE001 - lỗi map ra HTTP bên dưới
            raise _to_http(exc) from exc
        # Vòng sửa lỗi tự viết câu chốt từ lý do đơn vị vừa gõ — nhờ mô hình
        # viết lại nó là thay lời chứng bằng một bản diễn giải.
        if not (outcome or {}).get("repair_pending"):
            request_fresh_answer(workflow_id, job=job)

        # Báo cho khách bằng email — VIỆC PHỤ, và nó không được làm hỏng việc chính.
        #
        # Quyết định đã được ghi và chuỗi đã chạy xong TRƯỚC dòng này. Khâu báo tin
        # hỏng — chưa cấu hình repository, đọc user lỗi, SMTP chết — thì hệ quả đúng
        # là "khách không nhận được email", KHÔNG phải "lượt duyệt trả 500".
        #
        # Bản đầu để lỗi lan ra ngoài, nên một lượt duyệt THÀNH CÔNG vẫn ném
        # `RepositoryNotConfiguredError` và người duyệt đọc một lỗi hệ thống cho một
        # việc đã xong. Chỉ giữ TÊN loại lỗi: message có thể mang email của khách.
        try:
            repository = await acquire_repository()
            pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
            try:
                from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

                if isinstance(repository, PostgreSQLWorkflowStateRepository):
                    owner_id = await repository.get_workflow_owner(workflow_id)
                    if owner_id:
                        from src.db.user_repository import UserRepository

                        user_info = await UserRepository(pool).get_user_by_id(owner_id)
                        if user_info and user_info.get("email"):
                            ai_msg = outcome.get("assistant_message") or (
                                "Yêu cầu **tham quan bất động sản** của bạn đã được xử lý. "
                                "Vui lòng truy cập hệ thống để xem chi tiết."
                            )
                            background_tasks.add_task(send_workflow_batch_email, user_info["email"], ai_msg)
            finally:
                await pool.close()
        except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
            logger.warning("khong bao duoc email ket qua tham quan (%s)", type(exc).__name__)

        return {
            "workflow_id": workflow_id,
            "decision": "reject",
            "status": "REJECTED",
            "summary": "Đã từ chối lịch tham quan. Khách sẽ thấy lý do ở trạng thái workflow.",
        }

    try:
        outcome = await resume_viewing_after_approval(
            workflow_id,
            tour_url=settings.tour_service_url,
            shuttle_url=settings.shuttle_service_url,
            resident_url=settings.resident_service_url,
            transport_url=settings.transport_service_url,
            payment_url=settings.payment_service_url,
            property_url=settings.property_service_url,
            resident_services_url=settings.resident_services_service_url,
            consultation_url=settings.consultation_service_url,
            decided_by=reviewer["username"],
            don_vi=don_vi,
        )
    except Exception as exc:  # noqa: BLE001 - lỗi map ra HTTP bên dưới
        raise _to_http(exc) from exc

    viewing_result = outcome["viewing_result"]
    if not viewing_result.success:
        # Materialize đã thất bại → workflow đã bị đánh FAILED bên trong
        # `_materialize_and_run_remaining`.
        #
        # Đo được trên stack demo: khung giờ bị người khác đặt trong lúc chờ
        # duyệt, tour provider trả 409, và cả hai phía đều không biết vì sao.
        #
        # Người duyệt nghe "Vui lòng thử lại" cho một xung đột mà thử lại không
        # bao giờ qua được. `_viewing_materialize_error_message` đã biết nói
        # đúng từng nguyên nhân; câu chép sẵn ở đây chính là nhánh CUỐI của hàm
        # ấy, tức nhánh "không rõ nguyên nhân" — dùng nó cho mọi nguyên nhân là
        # vứt đi thứ duy nhất giúp người duyệt quyết định làm gì tiếp.
        #
        # Còn khách thì không nhận được gì cả: lệnh ném này đứng TRƯỚC mọi lần
        # xin câu mới, nên câu của trạng thái cũ ở lại vĩnh viễn —
        #
        #     workflow          FAILED
        #     assistant_answer  "…Hiện đang chờ đơn vị cung cấp dịch vụ…"
        #     for_status        WAITING_APPROVAL:PROVIDER
        #     khách nhìn thấy   answer = None, bong bóng cuối vẫn là "đang chờ"
        #
        # — và bộ lọc chống-câu-cũ giấu nó đi. Việc đã hỏng, màn hình vẫn nói
        # đang chờ, không có đường nào thoát. Xin câu mới TRƯỚC khi bỏ cuộc.
        request_fresh_answer(workflow_id, job=job)
        raise HTTPException(
            status_code=502,
            detail=_viewing_materialize_error_message(viewing_result),
        )

    shuttle_results = [r for r in outcome["task_results"].values() if r.data]
    shuttle_summary = ""
    for result in shuttle_results:
        data = result.data or {}
        if "driver_name" in data:
            shuttle_summary = (
                f" Xe đã đặt: tài xế {data.get('driver_name')}, "
                f"biển số {data.get('license_plate')}, {data.get('vehicle_type')}, "
                f"giờ đón {data.get('pickup_time')}."
            )
            break

    logger.info("viewing approved workflow=%s reviewer=%s", workflow_id, reviewer["username"])
    # Tình huống vừa đổi: lịch đã được duyệt. Câu cũ nói "đơn vị tour đang xác
    # nhận" và nó hết đúng ngay tại đây.
    request_fresh_answer(workflow_id, job=job)

    # Báo cho khách bằng email — VIỆC PHỤ, và nó không được làm hỏng việc chính.
    #
    # Quyết định đã được ghi và chuỗi đã chạy xong TRƯỚC dòng này. Khâu báo tin
    # hỏng — chưa cấu hình repository, đọc user lỗi, SMTP chết — thì hệ quả đúng
    # là "khách không nhận được email", KHÔNG phải "lượt duyệt trả 500".
    #
    # Bản đầu để lỗi lan ra ngoài, nên một lượt duyệt THÀNH CÔNG vẫn ném
    # `RepositoryNotConfiguredError` và người duyệt đọc một lỗi hệ thống cho một
    # việc đã xong. Chỉ giữ TÊN loại lỗi: message có thể mang email của khách.
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        try:
            from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

            if isinstance(repository, PostgreSQLWorkflowStateRepository):
                owner_id = await repository.get_workflow_owner(workflow_id)
                if owner_id:
                    from src.db.user_repository import UserRepository

                    user_info = await UserRepository(pool).get_user_by_id(owner_id)
                    if user_info and user_info.get("email"):
                        ai_msg = outcome.get("assistant_message") or (
                            "Yêu cầu **tham quan bất động sản** của bạn đã được xử lý. "
                            "Vui lòng truy cập hệ thống để xem chi tiết."
                        )
                        background_tasks.add_task(send_workflow_batch_email, user_info["email"], ai_msg)
        finally:
            await pool.close()
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("khong bao duoc email ket qua tham quan (%s)", type(exc).__name__)

    return {
        "workflow_id": workflow_id,
        "decision": "approve",
        "status": "APPROVED",
        "summary": f"Đã duyệt lịch tham quan.{shuttle_summary}",
    }


def _to_http(exc: Exception) -> HTTPException:
    """Map lỗi resume/materialize thành HTTPException với message an toàn.

    `ResumeError` mang message viết sẵn cho người dùng cuối (không chứa SQL,
    payload hay tên bảng); lỗi không mong đợi thì nói chung chung, không echo
    exception thật (có thể chứa URL/stack nội bộ).
    """
    code = getattr(exc, "code", None)
    if code is not None:
        status = {
            "NOT_FOUND": 404,
            "ALREADY_DECIDED": 409,
            "MATERIALIZE_FAILED": 502,
        }.get(code, 409)
        return HTTPException(status_code=status, detail=str(exc))

    logger.exception("viewing approve/reject unexpected error")
    return HTTPException(status_code=502, detail="Xử lý yêu cầu tham quan thất bại. Vui lòng thử lại.")
