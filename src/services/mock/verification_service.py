"""src/services/mock/verification_service.py

P-118 — Repository cho bảng `verification_records` (phía Mock Ownership Provider).

Provider sở hữu vòng đời xác thực có bằng chứng (ảnh giấy tờ). Main app proxy qua
HTTP; repo này là nơi duy nhất ghi/đổi trạng thái record.

Ràng buộc nghiệp vụ:
- Một record PENDING duy nhất mỗi (loại + khoá khai báo) — partial unique index
  `uq_verif_pending_{apartment,vehicle}` là trọng tài, không pre-check rồi INSERT.
- Từ chối PHẢI có lý do (`REJECT_REASON_REQUIRED`).
- Duyệt là claim: `UPDATE ... WHERE status='PENDING' RETURNING` trong một
  transaction — hai request đồng thời không thể cùng duyệt một record.

Bảo mật PII: `claimed_data` của apartment chứa `full_name` do người yêu cầu tự
khai (cần cho người duyệt so sánh). KHÔNG trả `owner_name` (tên chủ sở hữu trong
registry) — chỉ trả `ownership_match: bool`. Không log `full_name`/`owner_name`.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from src.db.parking_payment_repository import BookingError

logger = logging.getLogger(__name__)

# Field bắt buộc trong `claimed_data` theo loại hồ sơ. Provider tự kiểm — không
# phải request nào cũng đi qua schema Pydantic của FastAPI.
_REQUIRED_CLAIM_KEYS: dict[str, tuple[str, ...]] = {
    "apartment": ("apartment_code", "residential_area", "full_name"),
    "vehicle": ("plate_number", "vehicle_type"),
}

_ALLOWED_VEHICLE_TYPES = {"car", "motorcycle"}


@dataclass(frozen=True)
class VerificationRecord:
    record_id: str
    record_type: str
    status: str
    applicant_user_id: str | None
    claimed_data: dict[str, Any]
    proof_image_urls: list[str]
    reject_reason: str | None
    decided_by: str | None
    created_at: str
    decided_at: str | None

    def as_output(self, *, ownership_match: bool | None = None) -> dict[str, Any]:
        """View trả về người duyệt — KHÔNG chứa `owner_name`."""
        data: dict[str, Any] = {
            "record_id": self.record_id,
            "record_type": self.record_type,
            "status": self.status,
            "applicant_user_id": self.applicant_user_id,
            "claimed_data": self.claimed_data,
            "proof_image_urls": self.proof_image_urls,
            "reject_reason": self.reject_reason,
            "decided_by": self.decided_by,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }
        if ownership_match is not None:
            data["ownership_match"] = ownership_match
        return data


def _json_parse(value: str | Any) -> Any:
    """asyncpg trả JSONB dạng str — chuyển về dict/list như lời hứa của cột."""
    return json.loads(value) if isinstance(value, str) else value


def _row_to_record(row: asyncpg.Record) -> VerificationRecord:
    created_at = row["created_at"]
    decided_at = row["decided_at"]
    return VerificationRecord(
        record_id=str(row["record_id"]),
        record_type=row["record_type"],
        status=row["status"],
        applicant_user_id=row["applicant_user_id"],
        claimed_data=_json_parse(row["claimed_data"]),
        proof_image_urls=_json_parse(row["proof_image_urls"]),
        reject_reason=row["reject_reason"],
        decided_by=row["decided_by"],
        created_at=created_at.isoformat() if isinstance(created_at, datetime) else str(created_at),
        decided_at=decided_at.isoformat() if isinstance(decided_at, datetime) else None,
    )


def _validate_claimed_data(record_type: str, claimed_data: dict[str, Any]) -> None:
    """Kiểm field bắt buộc; message không echo giá trị người dùng (PII-safe)."""
    for key in _REQUIRED_CLAIM_KEYS[record_type]:
        value = claimed_data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise BookingError("INVALID_INPUT", f"claimed_data.{key} is required")
    if record_type == "vehicle" and claimed_data["vehicle_type"] not in _ALLOWED_VEHICLE_TYPES:
        raise BookingError("INVALID_INPUT", "vehicle_type must be car or motorcycle")


def _violated(exc: asyncpg.UniqueViolationError, constraint: str) -> bool:
    return getattr(exc, "constraint_name", None) == constraint


def _uuid_any(value: Any):
    """Biến str thành UUID cho cột UUID; sai định dạng → zero-UUID (không khớp ai).

    Filter theo `applicant_user_id` không được 500 khi người dùng gửi một chuỗi
    không phải UUID: "không khớp" là câu trả lời đúng và an toàn.
    """
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return uuid.UUID(int=0)


async def create_record(
    pool: asyncpg.Pool,
    *,
    record_type: str,
    applicant_user_id: str | None,
    claimed_data: dict[str, Any],
    proof_image_urls: list[str],
    record_id: str | None = None,
) -> VerificationRecord:
    """Tạo hồ sơ xác thực PENDING. Constraint chống trùng PENDING là trọng tài.

    `record_id` do main app sinh để ảnh có URL ổn định
    `/uploads/{record_id}/...` ngay từ lúc tạo. Provider không tự ý bỏ qua nó —
    nhưng chỉ dùng khi hợp lệ UUID; sai định dạng thì bỏ qua (provider tự sinh)
    chứ không 500, vì đây là trường mở rộng không phải phần lõi của hợp đồng.
    """
    if record_type not in _REQUIRED_CLAIM_KEYS:
        raise BookingError("INVALID_INPUT", "record_type must be apartment or vehicle")
    _validate_claimed_data(record_type, claimed_data)

    inserted_id = record_id
    if inserted_id is not None:
        try:
            uuid.UUID(str(inserted_id))
        except (ValueError, TypeError, AttributeError):
            inserted_id = None

    async with pool.acquire() as conn, conn.transaction():
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO verification_records
                    (record_id, record_type, status, applicant_user_id, claimed_data, proof_image_urls)
                VALUES (COALESCE($5::uuid, gen_random_uuid()), $1, 'PENDING', $2, $3::jsonb, $4::jsonb)
                RETURNING *
                """,
                record_type,
                applicant_user_id,
                json.dumps(claimed_data, ensure_ascii=False),
                json.dumps(proof_image_urls, ensure_ascii=False),
                inserted_id,
            )
        except asyncpg.UniqueViolationError as exc:
            if _violated(exc, "uq_verif_pending_vehicle") or _violated(exc, "uq_verif_pending_apartment"):
                raise BookingError(
                    "VERIFICATION_ALREADY_PENDING",
                    "A verification for this claim is already pending",
                ) from exc
            raise BookingError("INVALID_INPUT", "Verification record could not be created") from exc
        except asyncpg.ForeignKeyViolationError as exc:
            # applicant_user_id trỏ tới users(id) — main app luôn gửi id thật từ
            # JWT, nhưng provider tự an toàn: 404 rõ ràng thay vì 500 trần.
            raise BookingError("APPLICANT_NOT_FOUND", "Applicant user not found") from exc

    record = _row_to_record(row)
    logger.info("verification record created type=%s status=PENDING", record_type)
    return record


async def list_records(
    pool: asyncpg.Pool,
    *,
    record_type: str | None = None,
    status: str | None = None,
    applicant_user_id: str | None = None,
) -> list[VerificationRecord]:
    """Danh sách hồ sơ.

    Không filter theo `applicant_user_id` thì dùng cho người duyệt
    (provider/admin) — họ cần thấy mọi đơn đang chờ. Filter thì dùng cho
    "đơn của tôi" — người dùng chỉ thấy đơn của chính mình, không cần kéo
    toàn bộ record của hệ thống qua wire.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if record_type:
        clauses.append("record_type = $1")
        params.append(record_type)
    if status:
        clauses.append(f"status = ${len(params) + 1}")
        params.append(status)
    if applicant_user_id:
        clauses.append(f"applicant_user_id = ${len(params) + 1}")
        params.append(_uuid_any(applicant_user_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM verification_records {where} ORDER BY created_at"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_row_to_record(row) for row in rows]


async def get_record(pool: asyncpg.Pool, record_id: str) -> VerificationRecord | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM verification_records WHERE record_id = $1",
            record_id,
        )
    return None if row is None else _row_to_record(row)


async def compute_ownership_match(
    pool: asyncpg.Pool,
    record_type: str,
    claimed_data: dict[str, Any],
) -> bool | None:
    """So claimed `full_name` với `owner_name` trong registry — trả bool, KHÔNG tên.

    Trả None với record không phải apartment. `owner_name` chỉ dùng so khớp nội
    bộ, không bao giờ ra response hay log.
    """
    if record_type != "apartment":
        return None
    apartment_code = claimed_data.get("apartment_code")
    residential_area = claimed_data.get("residential_area")
    if not apartment_code or not residential_area:
        return None
    async with pool.acquire() as conn:
        owner_name = await conn.fetchval(
            "SELECT owner_name FROM apartment_owners WHERE apartment_code = $1 AND residential_area = $2",
            apartment_code,
            residential_area,
        )
    if owner_name is None:
        return False
    return owner_name == claimed_data.get("full_name")


async def decide_record(
    pool: asyncpg.Pool,
    *,
    record_id: str,
    decision: str,
    reject_reason: str | None,
    decided_by: str,
) -> VerificationRecord:
    """Duyệt / từ chối — claim bằng UPDATE trên status=PENDING.

    Từ chối thiếu lý do bị chặn ở đây (không tin tầng schema là đủ). Đã có quyết
    định (approve/reject trước đó) → `VERIFICATION_ALREADY_DECIDED`.
    """
    if decision == "reject" and not (reject_reason and reject_reason.strip()):
        raise BookingError("REJECT_REASON_REQUIRED", "Rejection requires a reason")

    reason_to_store = reject_reason if decision == "reject" else None
    # API dùng `approve`/`reject` (động từ); cột `status` lưu quá khứ in hoa.
    status_value = {"approve": "APPROVED", "reject": "REJECTED"}[decision]

    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            UPDATE verification_records
            SET status = $2,
                decided_by = $3,
                decided_at = NOW(),
                reject_reason = $4
            WHERE record_id = $1 AND status = 'PENDING'
            RETURNING *
            """,
            record_id,
            status_value,
            decided_by,
            reason_to_store,
        )
        if row is None:
            existing = await conn.fetchrow(
                "SELECT status FROM verification_records WHERE record_id = $1",
                record_id,
            )
            if existing is None:
                raise BookingError("VERIFICATION_NOT_FOUND", "Verification record not found")
            raise BookingError("VERIFICATION_ALREADY_DECIDED", "Verification record has already been decided")

    record = _row_to_record(row)
    logger.info("verification record %s -> %s", record_id, decision)
    return record
