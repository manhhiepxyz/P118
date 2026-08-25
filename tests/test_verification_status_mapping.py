"""Ma trận trạng thái hiển thị. Thuần, không database, không provider.

Bất biến trung tâm: **chỉ `APPROVED` + `SUCCESS` mới được nói "Đã xác minh".**

Mọi tổ hợp khác — kể cả `APPROVED` không biên lai — đều chưa mở quyền. Suy
"đơn vị đã duyệt nên chắc xong rồi" là đúng thứ đã tạo ra split-brain: quyết
định và kết quả là hai chuyện ở hai hệ thống.
"""

from __future__ import annotations

import pytest

from src.orchestration.verification_status import (
    BUSINESS_REFUSED,
    CONSISTENT,
    EFF_APPROVED_BLOCKED,
    EFF_APPROVED_NEEDS_RETRY,
    EFF_APPROVED_PROCESSING,
    EFF_NEEDS_RECONCILIATION,
    EFF_NEEDS_RECONCILIATION_NEUTRAL,
    EFF_REJECTED,
    EFF_UNKNOWN,
    EFF_VERIFIED,
    EFF_WAITING_PROVIDER,
    MAT_NOT_REQUIRED,
    MAT_NOT_STARTED,
    MAT_UNKNOWN,
    NEEDS_RECONCILIATION,
    PROVIDER_UNKNOWN,
    derive,
)

_MA_TRAN = [
    ("chờ đơn vị", "PENDING", None, None, MAT_NOT_STARTED, EFF_WAITING_PROVIDER, CONSISTENT),
    ("từ chối", "REJECTED", None, None, MAT_NOT_REQUIRED, EFF_REJECTED, CONSISTENT),
    ("từ chối, có biên lai", "REJECTED", "NOT_REQUIRED", None, MAT_NOT_REQUIRED, EFF_REJECTED, CONSISTENT),
    ("duyệt, đang làm", "APPROVED", "PENDING", None, "PENDING", EFF_APPROVED_PROCESSING, CONSISTENT),
    ("duyệt, chưa bắt đầu", "APPROVED", "NOT_STARTED", None, MAT_NOT_STARTED, EFF_APPROVED_PROCESSING, CONSISTENT),
    (
        "duyệt, hỏng hạ tầng",
        "APPROVED",
        "FAILED",
        "DATABASE_UNAVAILABLE",
        "FAILED",
        EFF_APPROVED_NEEDS_RETRY,
        CONSISTENT,
    ),
    (
        "duyệt, nghiệp vụ chặn",
        "APPROVED",
        "FAILED",
        BUSINESS_REFUSED,
        "FAILED",
        EFF_APPROVED_BLOCKED,
        CONSISTENT,
    ),
    ("duyệt, xong", "APPROVED", "SUCCESS", None, "SUCCESS", EFF_VERIFIED, CONSISTENT),
    (
        "duyệt, không biên lai",
        "APPROVED",
        None,
        None,
        MAT_UNKNOWN,
        EFF_NEEDS_RECONCILIATION,
        NEEDS_RECONCILIATION,
    ),
]


@pytest.mark.parametrize(
    "ten,provider,mat,ma_loi,mong_mat,mong_eff,mong_consistency",
    _MA_TRAN,
    ids=[c[0].replace(" ", "-") for c in _MA_TRAN],
)
def test_the_status_matrix(ten, provider, mat, ma_loi, mong_mat, mong_eff, mong_consistency):
    view = derive(provider, mat, ma_loi)
    assert view.provider_status == provider
    assert view.materialization_status == mong_mat
    assert view.effective_status == mong_eff
    assert view.consistency_status == mong_consistency


def test_only_one_combination_is_allowed_to_say_verified():
    """Quét TOÀN BỘ tổ hợp. Đúng một ô được nói "Đã xác minh"."""
    providers = ["PENDING", "APPROVED", "REJECTED", "TRANG_THAI_LA", None]
    mats = ["NOT_STARTED", "PENDING", "SUCCESS", "FAILED", "NOT_REQUIRED", "UNKNOWN", None, "RAC"]
    codes = [None, BUSINESS_REFUSED, "DATABASE_UNAVAILABLE"]

    noi_da_xac_minh = [
        (p, m, c) for p in providers for m in mats for c in codes if derive(p, m, c).display_status == "Đã xác minh"
    ]

    assert {(p, m) for p, m, _ in noi_da_xac_minh} == {("APPROVED", "SUCCESS")}, noi_da_xac_minh


def test_an_approved_record_without_a_receipt_is_never_treated_as_done():
    """Dữ liệu cũ, hoặc một cú chết trước dòng biên lai đầu tiên.

    Đây là ô nguy hiểm nhất: nó trông giống "xong" nhất và sai nhất.
    """
    view = derive("APPROVED", None, None)
    assert view.effective_status == EFF_NEEDS_RECONCILIATION
    assert view.materialization_status == MAT_UNKNOWN
    assert "xác minh" not in view.display_status.lower().replace("đang kiểm tra", "")
    assert view.consistency_status == NEEDS_RECONCILIATION


@pytest.mark.parametrize(
    "provider,mat",
    [("PENDING", "SUCCESS"), ("REJECTED", "SUCCESS"), ("APPROVED", "NOT_REQUIRED"), ("APPROVED", "UNKNOWN")],
    ids=["chờ+xong", "từ-chối+xong", "duyệt+không-cần", "duyệt+không-rõ"],
)
def test_drift_never_becomes_verified(provider, mat):
    """Biên lai KHÔNG lật được quyết định của đơn vị.

    Biên lai là bằng chứng vận hành của main app, không phải nguồn sự thật về
    quyết định. `SUCCESS` trên một hồ sơ bị từ chối là dấu hiệu lệch dữ liệu —
    không phải lý do mở quyền.
    """
    view = derive(provider, mat, None)
    assert view.effective_status != EFF_VERIFIED
    assert view.display_status != "Đã xác minh"
    assert view.consistency_status == NEEDS_RECONCILIATION


@pytest.mark.parametrize("la", ["ĐANG_XU_LY", "approved", "  ", None, 123])
def test_an_unknown_provider_status_is_never_echoed(la):
    """Trạng thái lạ không được đi thẳng ra ngoài, và không được đoán."""
    view = derive(la, None, None)
    assert view.provider_status == PROVIDER_UNKNOWN
    assert view.effective_status == EFF_UNKNOWN
    # Chuỗi rỗng bị loại khỏi tham số: "'' không nằm trong câu" là mệnh đề
    # luôn sai, và nó nói về Python chứ không về hành vi cần kiểm.
    assert str(la) not in view.display_status


def test_recovery_required_means_finish_not_decide_again():
    """Cờ này nói "hoàn tất nốt", KHÔNG nói "duyệt lại".

    Nhầm hai thứ đó là đưa đơn vị tới nút quyết định lần hai — và provider sẽ
    trả ALREADY_DECIDED, đúng chỗ split-brain cũ mắc kẹt.
    """
    assert derive("APPROVED", "FAILED", "DATABASE_UNAVAILABLE").recovery_required is True
    assert derive("APPROVED", "PENDING", None).recovery_required is True
    assert derive("APPROVED", None, None).recovery_required is True
    assert derive("APPROVED", "SUCCESS", None).recovery_required is False
    assert derive("REJECTED", None, None).recovery_required is False
    assert derive("PENDING", None, None).recovery_required is False


def test_a_business_block_is_not_advertised_as_retryable():
    """Điều kiện nghiệp vụ chưa thoả thì thử lại bao nhiêu lần cũng hỏng y hệt."""
    chan = derive("APPROVED", "FAILED", BUSINESS_REFUSED)
    ha_tang = derive("APPROVED", "FAILED", "DATABASE_UNAVAILABLE")

    assert chan.effective_status == EFF_APPROVED_BLOCKED
    assert ha_tang.effective_status == EFF_APPROVED_NEEDS_RETRY
    assert chan.display_status != ha_tang.display_status
    assert "chưa hoàn tất" not in chan.display_status


def test_the_mapper_is_pure():
    """Không I/O, không trạng thái: cùng input luôn cho cùng output."""
    a = derive("APPROVED", "FAILED", BUSINESS_REFUSED)
    b = derive("APPROVED", "FAILED", BUSINESS_REFUSED)
    assert a == b


# --- drift KHÔNG được trình bày như một trạng thái bình thường ---------------


def test_drift_on_a_non_approved_record_has_its_own_status():
    """`REJECTED + SUCCESS` không phải "Đã từ chối" — nó là dữ liệu lệch.

    Báo cáo trước tự mâu thuẫn: bảng ánh xạ ghi `REJECTED`, còn phần drift nói
    cần đối soát. Cả hai không thể cùng đúng, và bản đọc được là bản tệ hơn:
    "Đã từ chối" đọc như một kết cục bình thường, nên không ai đi soát tại sao
    main app lại đã ghi SUCCESS cho một hồ sơ bị từ chối.

    `APPROVED_NEEDS_RECONCILIATION` cũng sai ở đây — tên nó nói hồ sơ đã được
    duyệt, mà nó thì chưa.
    """
    tu_choi = derive("REJECTED", "SUCCESS", None)
    assert tu_choi.effective_status == EFF_NEEDS_RECONCILIATION_NEUTRAL
    assert tu_choi.display_status != "Đã từ chối"
    assert tu_choi.consistency_status == NEEDS_RECONCILIATION

    cho_duyet = derive("PENDING", "SUCCESS", None)
    assert cho_duyet.effective_status == EFF_NEEDS_RECONCILIATION_NEUTRAL
    assert cho_duyet.display_status != "Chưa xác định được trạng thái"
    assert cho_duyet.consistency_status == NEEDS_RECONCILIATION

    # Và không cái nào mang tên "APPROVED_..." — hồ sơ chưa được duyệt.
    assert "APPROVED" not in tu_choi.effective_status
    assert "APPROVED" not in cho_duyet.effective_status


def test_a_truly_unknown_provider_status_is_not_the_same_as_drift():
    """Hai chuyện khác nhau: "không hiểu trạng thái" và "hai nguồn nói ngược nhau"."""
    la = derive("TRANG_THAI_LA", None, None)
    lech = derive("REJECTED", "SUCCESS", None)
    assert la.effective_status != lech.effective_status


def test_the_drift_wording_asks_for_a_check_not_a_conclusion():
    for view in (derive("REJECTED", "SUCCESS", None), derive("PENDING", "SUCCESS", None)):
        assert "xác minh" not in view.display_status.lower() or "kiểm tra" in view.display_status.lower()
        assert view.display_status.strip() != ""


# --- "không có biên lai" phải sống sót qua mapper ---------------------------


def test_a_missing_receipt_means_two_different_things():
    """Cùng một dữ kiện nội bộ, hai kết luận khác nhau tuỳ đơn vị đã quyết chưa.

    `NOT_STARTED` KHÔNG phải trạng thái persisted — schema chỉ cho
    `NOT_REQUIRED | PENDING | SUCCESS | FAILED`, và biên lai mới mặc định
    `PENDING`. Nó là giá trị CÔNG KHAI do mapper suy ra, và chỉ đúng khi đơn vị
    cũng chưa quyết định.

    Coi mọi "không có biên lai" là `NOT_STARTED` bình thường là xoá mất hồ sơ
    APPROVED cần đối soát — ô nguy hiểm nhất, vì nó trông giống "đang xử lý".
    """
    cho_duyet = derive("PENDING", None, None)
    assert cho_duyet.materialization_status == MAT_NOT_STARTED
    assert cho_duyet.effective_status == EFF_WAITING_PROVIDER
    assert cho_duyet.consistency_status == CONSISTENT

    da_duyet = derive("APPROVED", None, None)
    assert da_duyet.materialization_status == MAT_UNKNOWN
    assert da_duyet.effective_status == EFF_NEEDS_RECONCILIATION
    assert da_duyet.consistency_status == NEEDS_RECONCILIATION


def test_an_approved_record_without_a_receipt_is_not_the_same_as_one_in_progress():
    """M56: nếu consumer coi missing như một biên lai PENDING, ô này sập.

    `APPROVED_PROCESSING` nói "đang chạy, chờ chút". `APPROVED_NEEDS_RECONCILIATION`
    nói "không biết main app đã làm gì, phải đi soát". Nhầm hai thứ đó là hứa
    một lượt hoàn tất không ai đang thực hiện.
    """
    khong_bien_lai = derive("APPROVED", None, None)
    dang_chay = derive("APPROVED", "PENDING", None)

    assert khong_bien_lai.effective_status != dang_chay.effective_status
    assert khong_bien_lai.effective_status == EFF_NEEDS_RECONCILIATION
    assert dang_chay.effective_status == EFF_APPROVED_PROCESSING
    assert khong_bien_lai.display_status != dang_chay.display_status
    assert khong_bien_lai.consistency_status == NEEDS_RECONCILIATION
    assert dang_chay.consistency_status == CONSISTENT


# --- ma trận drift ĐẦY ĐỦ, kiểm giá trị chính xác ---------------------------

_DRIFT = [
    # provider PENDING: chỉ "chưa có" và "đang mở" là hợp lệ.
    ("chờ + không biên lai", "PENDING", None, EFF_WAITING_PROVIDER, CONSISTENT),
    ("chờ + đang mở", "PENDING", "PENDING", EFF_WAITING_PROVIDER, CONSISTENT),
    ("chờ + đã xong", "PENDING", "SUCCESS", EFF_NEEDS_RECONCILIATION_NEUTRAL, NEEDS_RECONCILIATION),
    ("chờ + đã hỏng", "PENDING", "FAILED", EFF_NEEDS_RECONCILIATION_NEUTRAL, NEEDS_RECONCILIATION),
    ("chờ + không cần", "PENDING", "NOT_REQUIRED", EFF_NEEDS_RECONCILIATION_NEUTRAL, NEEDS_RECONCILIATION),
    ("chờ + không rõ", "PENDING", "UNKNOWN", EFF_NEEDS_RECONCILIATION_NEUTRAL, NEEDS_RECONCILIATION),
    # provider REJECTED: chỉ "chưa có" và "không cần" là hợp lệ.
    ("từ chối + không biên lai", "REJECTED", None, EFF_REJECTED, CONSISTENT),
    ("từ chối + không cần", "REJECTED", "NOT_REQUIRED", EFF_REJECTED, CONSISTENT),
    ("từ chối + đang mở", "REJECTED", "PENDING", EFF_NEEDS_RECONCILIATION_NEUTRAL, NEEDS_RECONCILIATION),
    ("từ chối + đã hỏng", "REJECTED", "FAILED", EFF_NEEDS_RECONCILIATION_NEUTRAL, NEEDS_RECONCILIATION),
    ("từ chối + đã xong", "REJECTED", "SUCCESS", EFF_NEEDS_RECONCILIATION_NEUTRAL, NEEDS_RECONCILIATION),
    ("từ chối + không rõ", "REJECTED", "UNKNOWN", EFF_NEEDS_RECONCILIATION_NEUTRAL, NEEDS_RECONCILIATION),
]


@pytest.mark.parametrize(
    "ten,provider,mat,mong_eff,mong_consistency",
    _DRIFT,
    ids=[c[0].replace(" ", "-") for c in _DRIFT],
)
def test_the_full_drift_matrix(ten, provider, mat, mong_eff, mong_consistency):
    """Kiểm GIÁ TRỊ CHÍNH XÁC, không chỉ "khác VERIFIED".

    `!= VERIFIED` xanh cho cả một ô bị gán nhầm thành "Đã từ chối" — và đó
    chính là ô nguy hiểm, vì nó đọc như kết cục bình thường nên không ai soát.
    """
    view = derive(provider, mat, None)
    assert view.effective_status == mong_eff, f"{ten}: {view.effective_status}"
    assert view.consistency_status == mong_consistency, f"{ten}: {view.consistency_status}"
    assert view.display_status == _display_of(mong_eff)


def _display_of(effective: str) -> str:
    from src.orchestration.verification_status import _CAU_CHU

    return _CAU_CHU[effective]
