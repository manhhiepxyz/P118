"""Xác thực căn hộ / xe có bằng chứng (ảnh), provider duyệt — LUỒNG SONG SONG.

Path B song song với Agent (Path A giữ nguyên). Route này là cửa ngõ duy nhất
của UI tới vòng đời `verification_records` do Mock Ownership Provider (8004)
sở hữu:

  - `POST /api/v1/verification-records` — khách hàng gửi đơn kèm ảnh giấy tờ.
  - `GET  /api/v1/verification-records/my` — đơn của chính mình.
  - `GET  /api/v1/verification-records` — danh sách cho người duyệt (provider/admin).
  - `POST /api/v1/verification-records/{record_id}/decide` — duyệt/từ chối; khi
    duyệt, main app MATERIALIZE kết quả vào hệ thống thật.

Ranh giới tin cậy:

  - Browser KHÔNG bao giờ gửi `applicant_user_id`/`verification_status`/`resident_id`.
    `applicant_user_id` được main app đặt từ JWT; `claimed_data` của xe không chứa
    `resident_id` — resident được tra từ liên kết VERIFIED lúc duyệt.
  - Xe (record_type=vehicle) bắt buộc người nộp đơn ĐÃ liên kết căn hộ VERIFIED:
    `get_verified_identity` fail-closed. Người chưa xác minh căn hộ không được
    mở đơn đăng ký xe — chặn ở đây, không phải ở provider.
  - Duyệt xe → tạo xe qua Transport provider dùng `resident_id` TRA ra từ liên kết
    VERIFIED của NGƯỜI NỘP ĐƠN, không phải của người duyệt.
  - `owner_name` không bao giờ ra response; response chỉ có `ownership_match: bool`.

Ảnh lưu vào `./data/uploads/{record_id}/` với filename `uuid4.jpg` (sanitize,
chống path traversal); URL trả về dạng `/uploads/{record_id}/{filename}`.
`record_id` do main app sinh TRƯỚC khi tạo record — provider chấp nhận nó
(`VerificationRecordCreate.record_id`) để URL ổn định ngay từ lúc gửi đơn.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.api.deps import get_current_user, require_roles
from src.common.enums import ErrorCode
from src.connectors.ownership import OwnershipConnector, OwnershipProviderError
from src.connectors.transport import TransportConnector
from src.config import get_settings
from src.db.link_request_repository import materialize_resident_link
from src.db.resident_link_repository import get_verified_identity
from src.orchestration.runtime_provider import acquire_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/verification-records", tags=["verification-records"])

# Ảnh giấy tờ: giới hạn kích thước + loại. `MAX_IMAGE_BYTES` chặn gửi file khổng
# lồ làm nghẽn đĩa; whitelist content-type chặn gửi HTML/script đội lốt ảnh.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
UPLOAD_ROOT = Path("./data/uploads")

_MISSING = "Không tìm thấy dữ liệu phù hợp."


# ---------------------------------------------------------------------------
# Dependencies — connector có thể bị override trong test (như repository)
# ---------------------------------------------------------------------------


def _ownership_connector() -> OwnershipConnector:
    """Connector tới Mock Ownership Provider — build từ settings mỗi lần.

    Dùng `Depends()` để test override bằng fake connector, không cần mở socket.
    """
    settings = get_settings()
    return OwnershipConnector(settings.ownership_service_url)


def _transport_connector() -> TransportConnector:
    """Transport provider — dùng để materialize xe khi provider duyệt."""
    settings = get_settings()
    return TransportConnector(settings.transport_service_url)


class _DecideBody(BaseModel):
    decision: str = Field(..., pattern="^(approve|reject)$")
    reject_reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Khách hàng: gửi đơn + ảnh
# ---------------------------------------------------------------------------


@router.post("", status_code=201, summary="Gửi đơn xác thực căn hộ / xe (kèm ảnh)")
async def create_verification_record(
    record_type: str = Form(..., pattern="^(apartment|vehicle)$"),
    claimed_data: str = Form(..., description="JSON string: apartment hoặc vehicle claim"),
    files: list[UploadFile] = File(default_factory=list),
    user: dict = Depends(get_current_user),
    connector: OwnershipConnector = Depends(_ownership_connector),
) -> dict:
    """Khách hàng gửi đơn PENDING.

    - `record_type=apartment`: chỉ cần đăng nhập — người chưa xác minh căn hộ
      nào vẫn được mở đơn xin xác minh căn hộ đầu tiên.
    - `record_type=vehicle`: BẮT BUỘC đã liên kết căn hộ VERIFIED. Chặn ngay ở
      đây, fail-closed, để không có đơn xe nào của người chưa rõ là cư dân ai.
    """
    try:
        claim = json.loads(claimed_data)
        if not isinstance(claim, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail="claimed_data phải là một object JSON.") from None

    if record_type == "vehicle":
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        try:
            identity = await get_verified_identity(pool, user["id"])
        finally:
            await pool.close()
        if identity is None:
            raise HTTPException(
                status_code=403,
                detail="Cần xác minh căn hộ trước khi đăng ký xe.",
            )

    # record_id sinh ở MAIN APP để ảnh có URL ổn định /uploads/{record_id}/...
    # ngay từ lúc tạo đơn — không phụ thuộc phản hồi của provider.
    record_id = str(uuid.uuid4())

    # Lưu ảnh TRƯỚC khi gọi provider. Lưu sau khi provider chấp nhận đơn thì
    # có khoảng trống: record đã tồn tại mà ảnh chưa lên đĩa → đơn PENDING nhưng
    # người duyệt không thấy bằng chứng. Ngược lại, lưu trước mà provider từ
    # chối (trùng PENDING) thì dọn ảnh thừa — xem khối except dưới.
    urls = await _save_images(record_id, files)

    try:
        data = await connector.create_record(
            {
                "record_type": record_type,
                "record_id": record_id,
                # `user["id"]` từ asyncpg là UUID object — httpx json= không
                # serialize được; chuyển sang str trước khi gửi provider.
                "applicant_user_id": str(user["id"]),
                "claimed_data": claim,
                "proof_image_urls": urls,
            }
        )
    except OwnershipProviderError as exc:
        _remove_upload_dir(record_id)
        raise _to_http(exc) from exc
    except Exception:
        # Provider down → vẫn dọn ảnh: đơn không tồn tại thì ảnh mồ côi trên đĩa.
        _remove_upload_dir(record_id)
        raise

    logger.info("verification record created type=%s record=%s", record_type, record_id)
    return {"item": data}


# ---------------------------------------------------------------------------
# Khách hàng: theo dõi đơn của mình
# ---------------------------------------------------------------------------


@router.get("/my", summary="Danh sách đơn xác thực của chính mình")
async def my_verification_records(
    user: dict = Depends(get_current_user),
    connector: OwnershipConnector = Depends(_ownership_connector),
) -> dict:
    """Chỉ đơn của user hiện tại — provider filter theo applicant_user_id."""
    try:
        items = await connector.list_records(applicant_user_id=user["id"])
    except OwnershipProviderError as exc:
        raise _to_http(exc) from exc
    return {"items": items}


# ---------------------------------------------------------------------------
# Provider/admin: danh sách + quyết định
# ---------------------------------------------------------------------------


@router.get("", summary="Danh sách hồ sơ xác thực (cho người duyệt)")
async def list_verification_records(
    record_type: str | None = None,
    status: str | None = None,
    _reviewer: dict = Depends(require_roles("provider", "admin")),
    connector: OwnershipConnector = Depends(_ownership_connector),
) -> dict:
    if status is not None and status not in {"PENDING", "APPROVED", "REJECTED"}:
        raise HTTPException(status_code=422, detail="Trạng thái không hợp lệ.")
    try:
        items = await connector.list_records(record_type=record_type, status=status)
    except OwnershipProviderError as exc:
        raise _to_http(exc) from exc
    return {"items": items}


@router.post("/{record_id}/decide", summary="Duyệt hoặc từ chối một hồ sơ xác thực")
async def decide_verification_record(
    record_id: str,
    body: _DecideBody,
    reviewer: dict = Depends(require_roles("provider", "admin")),
    ownership: OwnershipConnector = Depends(_ownership_connector),
    transport: TransportConnector = Depends(_transport_connector),
) -> dict:
    """Duyệt = đổi trạng thái ở provider RỒI materialize vào hệ thống thật.

    Provider quyết định trước (claim UPDATE chống double-decide). Chỉ khi kết
    quả là APPROVED, main app mới materialize:

      - vehicle   → tạo xe qua Transport provider, dùng resident_id TRA từ
                    liên kết VERIFIED của NGƯỜI NỘP ĐƠN (`applicant_user_id`).
      - apartment → mở quyền cư dân qua `materialize_resident_link`.

    `decided_by` lấy từ JWT của người duyệt (main app đặt, không nhận từ body).

    KHÔNG ai được duyệt hồ sơ của chính mình — xem `_reject_self_review`.
    """
    await _reject_self_review(record_id, reviewer, ownership)

    try:
        record = await ownership.decide_record(
            record_id,
            decision=body.decision,
            reject_reason=body.reject_reason,
            decided_by=reviewer["username"],
        )
    except OwnershipProviderError as exc:
        raise _to_http(exc) from exc

    if record["status"] == "APPROVED":
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        try:
            materialized = await _materialize_approved(record, pool, transport)
        finally:
            await pool.close()
        if materialized is not None:
            record["materialized"] = materialized

    return {"item": record}


async def _materialize_approved(
    record: dict,
    pool,
    transport: TransportConnector,
) -> dict | None:
    """Materialize kết quả duyệt. Trả dict bổ sung, None nếu không cần.

    `pool` là pool app-lifetime (đã acquire ở route); `transport` inject cho
    test. Hai việc materialize dùng chung một nguồn sự thật: liên kết VERIFIED
    và `claimed_data` — KHÔNG nhận gì từ body hay người duyệt.
    """
    record_type = record["record_type"]
    claim = record.get("claimed_data") or {}
    applicant_id = record.get("applicant_user_id")

    if record_type == "apartment":
        # Mở quyền cư dân: tạo/nối resident + upsert user_resident_links VERIFIED.
        # Tên dùng `claimed_data.full_name` — người nộp đơn tự khai, provider đã
        # xác minh ownership_match. Không nhận gì từ body.
        if not applicant_id:
            raise HTTPException(status_code=422, detail="Hồ sơ căn hộ thiếu người nộp đơn.")
        await materialize_resident_link(
            pool,
            user_id=applicant_id,
            apartment_code=claim.get("apartment_code", ""),
            residential_area=claim.get("residential_area", ""),
            full_name=claim.get("full_name", ""),
        )
        return None

    if record_type == "vehicle":
        # Tra resident_id từ liên kết VERIFIED của NGƯỜI NỘP ĐƠN. Không lấy từ
        # body, không lấy từ người duyệt. Người nộp đơn không còn VERIFIED lúc
        # duyệt (liên kết bị thu hồi giữa chừng) → fail-closed.
        if not applicant_id:
            raise HTTPException(status_code=422, detail="Hồ sơ xe thiếu người nộp đơn.")
        identity = await get_verified_identity(pool, applicant_id)
        if identity is None:
            raise HTTPException(
                status_code=409,
                detail="Người nộp đơn không còn liên kết căn hộ hợp lệ, không thể duyệt xe.",
            )

        result = await transport.execute(
            "register_vehicle",
            {
                "resident_id": identity.resident_id,
                "plate_number": claim.get("plate_number", ""),
                "vehicle_type": claim.get("vehicle_type", ""),
            },
        )
        if not result:
            raise HTTPException(status_code=502, detail=_materialize_error_message(result))
        return {"vehicle_id": result.data.get("vehicle_id")} if result.data else None

    return None


async def _save_images(record_id: str, files: list[UploadFile]) -> list[str]:
    """Lưu ảnh vào `./data/uploads/{record_id}/`, trả URL công khai.

    Không bao giờ dùng filename gốc của client (nguồn path traversal); filename
    là `uuid4` + đuôi từ content-type. File không phải ảnh → 422. Đọc qua
    `await upload.read()` để không chặn event loop với file lớn.
    """
    if not files:
        return []
    record_dir = UPLOAD_ROOT / record_id
    record_dir.mkdir(parents=True, exist_ok=True)

    urls: list[str] = []
    try:
        for upload in files:
            content_type = upload.content_type or ""
            if content_type not in _ALLOWED_IMAGE_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail="Chỉ chấp nhận ảnh JPEG, PNG hoặc WEBP.",
                )
            data = await upload.read()
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail="Ảnh vượt quá 5MB.",
                )
            if not data:
                continue
            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
            filename = f"{uuid.uuid4().hex}{ext}"
            (record_dir / filename).write_bytes(data)
            urls.append(f"/uploads/{record_id}/{filename}")
    except Exception:
        _remove_upload_dir(record_id)
        raise
    return urls


def _remove_upload_dir(record_id: str) -> None:
    """Dọn ảnh thừa khi đơn không được tạo (best-effort, không raise)."""
    try:
        target = UPLOAD_ROOT / record_id
        if target.is_dir():
            for f in target.iterdir():
                f.unlink(missing_ok=True)
            target.rmdir()
    except OSError:  # pragma: no cover - không quan trọng
        logger.warning("Không dọn được upload dir %s", record_id)


async def _reject_self_review(
    record_id: str,
    reviewer: dict,
    ownership: OwnershipConnector,
) -> None:
    """Chặn người duyệt tự duyệt hồ sơ do chính mình nộp.

    Lỗ đã tái hiện được, đầy đủ từ đầu đến cuối:

        provider nộp hồ sơ căn hộ "SELF-9001"  → tạo được
        provider tự duyệt hồ sơ đó              → APPROVED, decided_by=provider
        /auth/me                                → VERIFIED, căn hộ SELF-9001

    Người duyệt tự cấp cho mình tư cách cư dân của một căn hộ KHÔNG có trong
    registry. `ownership_match` trả False nhưng nó chỉ là thông tin hiển thị,
    không chặn gì. Toàn bộ giá trị của bước xác thực nằm ở chỗ có người thứ hai
    nhìn vào hồ sơ — người duyệt trùng người nộp thì bước đó bằng không.

    Đặt ở đây, TRƯỚC `decide_record`, để không có trạng thái nào bị đổi rồi mới
    phát hiện. `decide_record` claim bằng UPDATE nên nếu để lọt thì hồ sơ đã
    APPROVED, và rollback nghĩa là phải gỡ cả liên kết cư dân đã materialize.

    Chốt này nằm ở TẦNG BACKEND một cách có chủ ý. Ngay cả khi cổng duyệt có
    hệ đăng nhập riêng và tài khoản provider không còn nộp hồ sơ được qua UI,
    ràng buộc "người nộp không phải người duyệt" vẫn phải đúng — nó là quy tắc
    nghiệp vụ, không phải chi tiết điều hướng.

    Hỏi provider "hồ sơ này có phải của bạn không" thay vì tải hồ sơ rồi so
    tay: `list_records(applicant_user_id=...)` đã lọc sẵn theo chủ đơn, nên câu
    trả lời không phụ thuộc vào việc response có trả về `applicant_user_id` hay
    không — và trường đó có thể bị lược đi vì lý do riêng tư bất cứ lúc nào.
    """
    try:
        own_records = await ownership.list_records(applicant_user_id=str(reviewer["id"]))
    except OwnershipProviderError as exc:
        # Không đọc được thì KHÔNG duyệt. Fail-closed: một provider tạm thời
        # không truy vấn được còn hơn một hồ sơ tự duyệt lọt qua.
        raise _to_http(exc) from exc

    if any(record.get("record_id") == record_id for record in own_records):
        logger.warning(
            "chặn tự duyệt: reviewer=%s record=%s",
            reviewer.get("username"),
            record_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Bạn không duyệt được hồ sơ do chính mình nộp. Hồ sơ này cần người khác xem xét.",
        )


def _to_http(exc: OwnershipProviderError) -> HTTPException:
    """Chuyển lỗi provider thành HTTPException (đúng status, không echo PII)."""
    message = {
        # KHÔNG nói "Bạn đã có một đơn" — ràng buộc phía sau lỗi này là
        # `uq_verif_pending_apartment`, unique trên
        # `(record_type, apartment_code, residential_area) WHERE status='PENDING'`
        # — tức trên CĂN HỘ, không phải trên người nộp. Một tài khoản hoàn toàn
        # mới, chưa có đơn nào, vẫn ăn lỗi này nếu người khác đang xin xác minh
        # cùng căn hộ đó (đã tái hiện được). Câu cũ khiến họ đi tìm cái đơn
        # không tồn tại rồi mắc kẹt, vì màn hình của họ trống trơn.
        #
        # Ràng buộc thì đúng: hai người cùng chờ duyệt một căn hộ mà cả hai
        # được duyệt thì liên kết cư dân sẽ mâu thuẫn. Chỉ có lời giải thích là
        # sai. Câu mới không nói ai đang giữ đơn — vế đó là dữ liệu người khác.
        "VERIFICATION_ALREADY_PENDING": (
            "Căn hộ này đang có một hồ sơ chờ duyệt, nên chưa nhận thêm hồ sơ mới. "
            "Nếu hồ sơ đó không phải của bạn, vui lòng liên hệ ban quản lý toà nhà."
        ),
        "REJECT_REASON_REQUIRED": "Từ chối cần lý do.",
        "VERIFICATION_NOT_FOUND": _MISSING,
        "VERIFICATION_ALREADY_DECIDED": "Hồ sơ này đã được xử lý.",
        "APPLICANT_NOT_FOUND": _MISSING,
        "INVALID_INPUT": "Thông tin gửi lên chưa hợp lệ.",
    }.get(exc.error_code, exc.message)
    return HTTPException(status_code=exc.status_code, detail=message)


def _materialize_error_message(result) -> str:
    """Message an toàn khi materialize xe thất bại — không echo plate/resident."""
    if result.error_code == ErrorCode.SERVICE_UNAVAILABLE:
        return "Dịch vụ đăng ký xe đang tạm ngừng, vui lòng thử lại sau."
    return "Đăng ký xe thất bại khi hoàn tất duyệt. Vui lòng thử lại."
