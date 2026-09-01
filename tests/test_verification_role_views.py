"""Ba vai, ba view. Thuần — không database, không provider.

Ba vai cần ba thứ khác nhau, và khác nhau theo hướng đối nghịch:

    customer   biết hồ sơ của mình tới đâu; KHÔNG cần thấy lại ảnh giấy tờ
    provider   PHẢI thấy claim + ảnh — đó là toàn bộ lý do bước duyệt tồn tại
    admin      biết ai đang xin gì và kẹt ở đâu; không cần giấy tờ của ai

Một schema dùng chung ép mọi quyết định privacy thành "có hay không có trường",
trong khi câu hỏi đúng là "vai nào được thấy trường nào".
"""

from __future__ import annotations

import pytest

from src.db.verification_receipt_repository import ReceiptSnapshot
from src.orchestration.verification_views import enrich

_NHAY_CAM = {
    "apartment_code": "A1201",
    "residential_area": "Vinhomes Ocean Park",
    "full_name": "Lâm Thành Bảo",
    "id_number": "001234567890",
}


def _record(**kw):
    goc = {
        "record_id": "rec-1",
        "record_type": "apartment",
        "status": "PENDING",
        "applicant_user_id": "user-1",
        "claimed_data": dict(_NHAY_CAM),
        "proof_image_urls": ["/uploads/rec-1/giay-to.png"],
        "ownership_match": True,
        "resident_id": "RES-BI-MAT",
        "decided_by": None,
        "decided_at": None,
        "reject_reason": None,
        "created_at": "2026-08-20T10:00:00+00:00",
        # Trường provider có thể thêm ở bản sau — allowlist phải chặn nó.
        "bi_mat_tuong_lai": "KHONG_DUOC_RO",
    }
    goc.update(kw)
    return goc


def _snap(status=None, code=None):
    if status is None:
        return {}
    return {"rec-1": ReceiptSnapshot(True, status, code)}


def _one(kind, record=None, snapshots=None, accounts=None):
    return enrich([record or _record()], snapshots or {}, kind=kind, accounts=accounts)[0]


# --- privacy: khẳng định CÓ và KHÔNG CÓ, cho từng vai -----------------------


def test_the_customer_never_gets_their_own_paperwork_back():
    item = _one("customer")
    raw = str(item)

    for cam in (
        "claimed_data",
        "proof_image_urls",
        "applicant_user_id",
        "resident_id",
        "ownership_match",
        "safe_error_code",
        "can_decide",
        "bi_mat_tuong_lai",
    ):
        assert cam not in item, f"customer thấy {cam}"
    assert "001234567890" not in raw and "RES-BI-MAT" not in raw and "giay-to.png" not in raw
    # Nhưng vẫn đủ để biết hồ sơ tới đâu.
    assert {
        "provider_status",
        "materialization_status",
        "effective_status",
        "display_status",
        "consistency_status",
    } <= set(item)


def test_the_provider_keeps_the_documents_they_need_to_judge():
    """Bỏ claim và ảnh đi là lấy mất công cụ làm việc của người duyệt."""
    item = _one("provider")

    assert item["claimed_data"] == _NHAY_CAM
    assert item["proof_image_urls"] == ["/uploads/rec-1/giay-to.png"]
    assert item["ownership_match"] is True
    assert item["applicant_user_id"] == "user-1"
    for cam in ("safe_error_code", "resident_id", "bi_mat_tuong_lai"):
        assert cam not in item, f"provider thấy {cam}"


def test_the_admin_watches_without_reading_anyone_documents():
    item = _one("admin", accounts={"user-1": {"user_id": "user-1", "username": "khach", "display_name": "Khách A"}})
    raw = str(item)

    for cam in (
        "claimed_data",
        "proof_image_urls",
        "resident_id",
        "safe_error_code",
        "ownership_match",
        "can_decide",
        "bi_mat_tuong_lai",
    ):
        assert cam not in item, f"admin thấy {cam}"
    assert "001234567890" not in raw and "RES-BI-MAT" not in raw and "giay-to.png" not in raw
    assert "/review" not in raw
    # `account.user_id` là định danh TÀI KHOẢN, không phải `resident_id`.
    assert item["account"]["username"] == "khach"
    assert item["request_name"] == "Xác minh căn hộ"


def test_an_unknown_account_does_not_break_the_admin_view():
    item = _one("admin", accounts={})
    assert item["account"]["user_id"] == "user-1"
    assert item["account"]["username"] is None


# --- can_decide: ba điều kiện, không một ------------------------------------

_CAN = [
    ("chờ duyệt, sạch", "PENDING", None, None, True),
    ("chờ duyệt, đang mở biên lai", "PENDING", "PENDING", None, True),
    ("chờ duyệt nhưng biên lai đã xong", "PENDING", "SUCCESS", None, False),
    ("đã duyệt", "APPROVED", "SUCCESS", None, False),
    ("đã duyệt, đang hoàn tất", "APPROVED", "PENDING", None, False),
    ("đã duyệt, không biên lai", "APPROVED", None, None, False),
    ("đã từ chối", "REJECTED", None, None, False),
    ("từ chối nhưng biên lai xong", "REJECTED", "SUCCESS", None, False),
    ("trạng thái lạ", "TRANG_THAI_LA", None, None, False),
]


@pytest.mark.parametrize("ten,provider,mat,code,mong", _CAN, ids=[c[0].replace(" ", "-") for c in _CAN])
def test_when_the_provider_may_still_decide(ten, provider, mat, code, mong):
    """Sau khi đã quyết định, việc còn lại là HOÀN TẤT — không phải duyệt lần hai.

    Provider sẽ trả `ALREADY_DECIDED`, đúng chỗ split-brain cũ mắc kẹt.
    """
    item = _one("provider", record=_record(status=provider), snapshots=_snap(mat, code))
    assert item["can_decide"] is mong, f"{ten}: {item['can_decide']}"


# --- ba vai kể CÙNG một câu chuyện ------------------------------------------

_TRANG_THAI = [
    ("PENDING", None, None, "WAITING_PROVIDER"),
    ("PENDING", "PENDING", None, "WAITING_PROVIDER"),
    ("REJECTED", None, None, "REJECTED"),
    ("REJECTED", "SUCCESS", None, "NEEDS_RECONCILIATION"),
    ("APPROVED", "PENDING", None, "APPROVED_PROCESSING"),
    ("APPROVED", "FAILED", "DATABASE_UNAVAILABLE", "APPROVED_NEEDS_RETRY"),
    ("APPROVED", "FAILED", "BUSINESS_REFUSED", "APPROVED_BLOCKED"),
    ("APPROVED", "SUCCESS", None, "VERIFIED"),
    ("APPROVED", None, None, "APPROVED_NEEDS_RECONCILIATION"),
]


@pytest.mark.parametrize("provider,mat,code,mong", _TRANG_THAI)
def test_all_three_roles_tell_the_same_story(provider, mat, code, mong):
    """Ba màn hình nói khác nhau về cùng một hồ sơ là cách để hai người tranh
    cãi về một việc mà cả hai đều "nhìn thấy"."""
    record, snaps = _record(status=provider), _snap(mat, code)
    views = [_one(k, record=record, snapshots=snaps) for k in ("customer", "provider", "admin")]

    for v in views:
        assert v["effective_status"] == mong
    assert len({v["display_status"] for v in views}) == 1
    assert len({v["consistency_status"] for v in views}) == 1
    assert len({v["provider_status"] for v in views}) == 1


def test_the_compatibility_alias_is_the_provider_status_not_a_conclusion():
    """`status` là alias của `provider_status`. Không phải kết luận."""
    for provider, mat in (("APPROVED", "FAILED"), ("APPROVED", None), ("APPROVED", "SUCCESS")):
        for kind in ("customer", "provider"):
            item = _one(kind, record=_record(status=provider), snapshots=_snap(mat))
            assert item["status"] == item["provider_status"]
            assert item["status"] != item["effective_status"] or provider == "APPROVED" and mat == "SUCCESS"


def test_output_is_built_from_an_allowlist_not_by_deleting_fields():
    """Provider thêm trường nhạy cảm ở bản sau: blocklist im lặng để nó đi tiếp."""
    them = _record(bi_mat_tuong_lai="RO_RI", them_truong_moi={"cccd": "001234567890"})
    for kind in ("customer", "admin"):
        raw = str(_one(kind, record=them))
        assert "RO_RI" not in raw and "them_truong_moi" not in raw
