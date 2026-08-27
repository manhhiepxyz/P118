"""Mock provider cho yêu cầu bảo trì và lịch chuyển nhà của cư dân."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import install_error_handler, not_found
from src.mock.ids import make_generator
from src.mock.service_providers import DON_VI_CHUYEN_NHA, con_lich, gia_chuyen_nha

resident_services_app = FastAPI(
    title="P-118 Resident Services Mock Provider",
    description="Dịch vụ giả lập bảo trì và chuyển nhà.",
    version="0.1.0",
)
resident_services_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_error_handler(resident_services_app)

_maintenance_requests: dict[str, dict] = {}
_move_requests: dict[str, dict] = {}
_lock = RLock()
_new_maintenance_id = make_generator("MAINT")
_new_move_id = make_generator("MOVE")
# Mã báo giá phải DUY NHẤT theo đơn vị, sống qua cả restart tiến trình.
#
# `make_generator` đếm từ 1 mỗi lần khởi động, nên sau một lượt deploy nó phát
# lại đúng `QMOV-001` — và P-118 (nơi có ràng buộc `UNIQUE (provider,
# external_quote_id)`) sẽ từ chối một báo giá hoàn toàn hợp lệ. Ngoài đời không
# nhà cung cấp nào đánh số lại từ đầu sau khi khởi động lại máy chủ.
_dem_bao_gia = make_generator("QMOV")


def _new_quote_id() -> str:
    return f"{_dem_bao_gia()}-{uuid4().hex[:8]}"


# Báo giá sống bao lâu. Ngắn là CỐ Ý: một báo giá còn hiệu lực nghĩa là đơn vị
# còn giữ chỗ cho ngày ấy, và không ai giữ chỗ vô hạn. Nó cũng làm nhánh hết
# hạn chạy được trong một lượt demo thay vì chỉ tồn tại trên giấy.
HIEU_LUC_BAO_GIA = timedelta(minutes=30)


@resident_services_app.post(
    "/api/resident-services/maintenance",
    status_code=201,
    summary="Tạo yêu cầu bảo trì",
)
def create_maintenance_request(payload: schemas.CreateMaintenanceRequest) -> schemas.ApiEnvelope:
    maintenance_id = _new_maintenance_id()
    result = {
        "maintenance_id": maintenance_id,
        "maintenance_status": "SCHEDULED",
        "appointment_date": payload.preferred_date.isoformat(),
        "appointment_time": payload.preferred_time,
    }
    with _lock:
        _maintenance_requests[maintenance_id] = result
    return schemas.ApiEnvelope(success=True, data=result, message="Maintenance request scheduled")


@resident_services_app.post(
    "/api/resident-services/moves",
    status_code=201,
    summary="Đặt lịch chuyển nhà",
)
def schedule_move(payload: schemas.ScheduleMoveRequest) -> schemas.ApiEnvelope:
    move_request_id = _new_move_id()
    result = {
        "move_request_id": move_request_id,
        "move_status": "SCHEDULED",
        "move_date": payload.move_date.isoformat(),
        "move_time": payload.move_time,
        "elevator_slot": payload.move_time if payload.needs_elevator else "NOT_REQUIRED",
    }
    with _lock:
        _move_requests[move_request_id] = result
    return schemas.ApiEnvelope(success=True, data=result, message="Move scheduled")


@resident_services_app.post(
    "/api/resident-services/moves/quotes/{service_provider_id}",
    summary="Xin báo giá chuyển nhà từ MỘT đơn vị",
)
def quote_move(service_provider_id: str, payload: schemas.QuoteMoveRequest) -> schemas.ApiEnvelope:
    """Giá của đơn vị này cho đúng yêu cầu này, kèm hạn hiệu lực.

    MỘT đơn vị mỗi lượt gọi, không phải một endpoint trả về cả danh sách. Ngoài
    đời mỗi đơn vị là một hệ thống riêng với một hợp đồng riêng; gộp thành một
    lời gọi là dựng sẵn một cái phễu mà sau này phải tháo ra. P-118 hỏi cả ba
    đơn vị song song và tự chịu trách nhiệm khi một đơn vị im lặng.

    Lịch trống là kiến thức CỦA ĐƠN VỊ, nên chính đơn vị nói "không nhận ngày
    ấy" — P-118 không được tự suy từ một bảng lịch bên nó. Từ chối bằng
    `NO_AVAILABILITY`, cùng mã canonical mà cổng duyệt đang dùng.
    """
    don_vi = next((d for d in DON_VI_CHUYEN_NHA if d.provider_id == service_provider_id), None)
    if don_vi is None:
        raise not_found("PROVIDER_NOT_FOUND", f"Không có đơn vị {service_provider_id}")
    if not con_lich(don_vi, payload.move_date):
        # 200 với `success=False`, không phải 4xx: đây là một câu trả lời
        # NGHIỆP VỤ hợp lệ ("chúng tôi bận ngày đó"), không phải một lời gọi
        # sai. Trả 4xx thì connector đọc nó thành sự cố và retry — retry một
        # ngày nghỉ thì lần nào cũng nghỉ.
        return schemas.ApiEnvelope(
            success=False,
            error_code="NO_AVAILABILITY",
            message=f"{don_vi.ten} không nhận việc ngày {payload.move_date.isoformat()}",
        )
    gia = gia_chuyen_nha(
        don_vi,
        move_vehicle=payload.move_vehicle,
        needs_elevator=payload.needs_elevator,
        needs_loading_support=payload.needs_loading_support,
    )
    return schemas.ApiEnvelope(
        success=True,
        data={
            "external_quote_id": _new_quote_id(),
            "service_provider_id": don_vi.provider_id,
            "provider_name": don_vi.ten,
            "amount": gia,
            "currency": "VND",
            "valid_until": (datetime.now(UTC) + HIEU_LUC_BAO_GIA).isoformat(),
        },
        message="Quote issued",
    )


def _huy(kho: dict[str, dict], ma: str, o_trang_thai: str, ten: str) -> schemas.ApiEnvelope:
    """Đánh dấu một yêu cầu là đã huỷ. Idempotent: gọi lần hai vẫn 200.

    ĐÁNH DẤU chứ không xoá, cùng lý do với chỗ đỗ xe và lịch tham quan: xoá thì
    lần gọi thứ hai không phân biệt được "đã huỷ rồi" với "chưa bao giờ tồn
    tại", và cả hai đều thành 200 — tức xác nhận một việc chưa từng xảy ra cho
    một mã bịa ra.
    """
    with _lock:
        row = kho.get(ma)
        if row is None:
            raise not_found(f"{ten}_NOT_FOUND", f"Không tìm thấy yêu cầu {ma}")
        row[o_trang_thai] = "CANCELLED"
        ket_qua = dict(row)
    return schemas.ApiEnvelope(success=True, data=ket_qua, message="Cancelled")


@resident_services_app.post(
    "/api/resident-services/maintenance/{maintenance_id}/cancel",
    summary="Huỷ yêu cầu bảo trì",
)
def cancel_maintenance(maintenance_id: str) -> schemas.ApiEnvelope:
    return _huy(_maintenance_requests, maintenance_id, "maintenance_status", "MAINTENANCE")


@resident_services_app.post(
    "/api/resident-services/moves/{move_request_id}/cancel",
    summary="Huỷ lịch chuyển nhà",
)
def cancel_move(move_request_id: str) -> schemas.ApiEnvelope:
    return _huy(_move_requests, move_request_id, "move_status", "MOVE")


@resident_services_app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "resident-services"}
