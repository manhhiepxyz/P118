"""Giá của một đơn vị cung cấp phải TÍNH RA được, và tính lại vẫn ra thế.

Khi Agent tự chọn đơn vị thay người dùng, ba tính chất phải đúng — thiếu một
cái là hệ thống quyết định thay họ dựa trên một con số không giải thích được:

  1. TẤT ĐỊNH — cùng tham số thì cùng giá, mọi lúc. Một bảng giá nhảy số thì
     không ai đối chiếu được hoá đơn với báo giá.
  2. THEO THAM SỐ — chuyển nhà có thang máy và bốc xếp phải đắt hơn chuyển nhà
     không cần gì. Giá phẳng thì "chọn đơn vị rẻ nhất" mất hết ý nghĩa.
  3. ĐƠN ĐIỆU — thêm một dịch vụ không bao giờ làm giá GIẢM.

Và luật chọn phải phá được thế cờ hoà: hai đơn vị bằng giá, bằng đánh giá thì
vẫn phải ra cùng một kết quả giữa hai lần chạy. Thiếu vế cuối thì mọi bài kiểm
đi qua nó đều nhấp nháy.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.mock.service_providers import (
    DON_VI_BAO_TRI,
    DON_VI_CHUYEN_NHA,
    chon_don_vi,
    gia_bao_tri,
    gia_chuyen_nha,
    tim_don_vi_bao_tri,
    tim_don_vi_chuyen_nha,
)

NGAY = date.today() + timedelta(days=7)


# ---------------------------------------------------------------- danh mục
def test_each_service_has_enough_providers_to_choose_between():
    """Ít hơn ba đơn vị thì 'chọn đơn vị hợp lý nhất' không phải một lựa chọn."""
    assert len(DON_VI_CHUYEN_NHA) >= 3
    assert len(DON_VI_BAO_TRI) >= 3


def test_provider_ids_are_unique():
    for danh_muc in (DON_VI_CHUYEN_NHA, DON_VI_BAO_TRI):
        ma = [d.provider_id for d in danh_muc]
        assert len(ma) == len(set(ma)), f"trùng provider_id: {ma}"


# ---------------------------------------------------------------- giá
def test_the_same_request_always_costs_the_same():
    for don_vi in DON_VI_CHUYEN_NHA:
        gia = [
            gia_chuyen_nha(don_vi, move_vehicle="truck", needs_elevator=True, needs_loading_support=True)
            for _ in range(5)
        ]
        assert len(set(gia)) == 1, f"{don_vi.provider_id} cho 5 giá khác nhau: {gia}"


def test_asking_for_more_never_costs_less():
    """Đơn điệu: thêm thang máy, thêm bốc xếp, đổi xe to hơn — giá không giảm."""
    for don_vi in DON_VI_CHUYEN_NHA:
        tran = gia_chuyen_nha(don_vi, move_vehicle="none", needs_elevator=False, needs_loading_support=False)
        them_thang_may = gia_chuyen_nha(don_vi, move_vehicle="none", needs_elevator=True, needs_loading_support=False)
        them_boc_xep = gia_chuyen_nha(don_vi, move_vehicle="none", needs_elevator=False, needs_loading_support=True)
        xe_van = gia_chuyen_nha(don_vi, move_vehicle="van", needs_elevator=False, needs_loading_support=False)
        xe_tai = gia_chuyen_nha(don_vi, move_vehicle="truck", needs_elevator=False, needs_loading_support=False)

        # LỚN HƠN HẲN, không phải `>=`: mọi đơn vị trong danh mục đều khai phụ
        # phí dương, nên `>=` vẫn xanh khi phụ phí bị bỏ quên — đo được, mutation
        # "bỏ cộng phụ phí thang máy" sống sót qua bản `>=`.
        assert them_thang_may > tran, f"{don_vi.provider_id}: thêm thang máy mà giá không đổi"
        assert them_boc_xep > tran, f"{don_vi.provider_id}: thêm bốc xếp mà giá không đổi"
        assert xe_van > tran, f"{don_vi.provider_id}: thêm xe van mà giá không đổi"
        assert xe_tai > xe_van, f"{don_vi.provider_id}: xe tải không đắt hơn xe van"


def test_maintenance_price_depends_on_the_kind_of_problem():
    """Giá phẳng theo hạng mục thì tham số hoá là giả."""
    for don_vi in DON_VI_BAO_TRI:
        gia = {
            loai: gia_bao_tri(don_vi, issue_type=loai)
            for loai in ("air_conditioning", "electrical", "plumbing", "other")
        }
        assert len(set(gia.values())) > 1, f"{don_vi.provider_id} tính cùng một giá cho mọi hạng mục: {gia}"
        assert all(g > 0 for g in gia.values())


# ---------------------------------------------------------------- luật chọn
def test_the_cheapest_available_provider_wins():
    ket_qua = tim_don_vi_chuyen_nha(
        ngay=NGAY, move_vehicle="van", needs_elevator=False, needs_loading_support=False
    )
    assert ket_qua, "không đơn vị nào rảnh — dữ liệu mẫu không dùng được để demo"
    chon = chon_don_vi(ket_qua)
    assert chon.gia == min(x.gia for x in ket_qua), f"chọn {chon.gia}, rẻ nhất là {min(x.gia for x in ket_qua)}"


def test_a_tie_is_broken_the_same_way_every_time():
    """Bằng giá → đánh giá cao hơn → provider_id nhỏ hơn.

    Thiếu vế cuối thì hai đơn vị ngang nhau cho kết quả nhảy giữa các lần chạy,
    và mọi bài kiểm đi qua nó đều nhấp nháy.
    """
    from src.mock.service_providers import DonViDuocChon

    hoa = [
        DonViDuocChon(provider_id="MOV-C", ten="C", gia=500_000, danh_gia=4.5),
        DonViDuocChon(provider_id="MOV-A", ten="A", gia=500_000, danh_gia=4.5),
        DonViDuocChon(provider_id="MOV-B", ten="B", gia=500_000, danh_gia=4.8),
    ]
    for _ in range(5):
        assert chon_don_vi(list(hoa)).provider_id == "MOV-B", "đánh giá cao hơn phải thắng khi bằng giá"

    bang_het = [
        DonViDuocChon(provider_id="MOV-C", ten="C", gia=500_000, danh_gia=4.5),
        DonViDuocChon(provider_id="MOV-A", ten="A", gia=500_000, danh_gia=4.5),
    ]
    for _ in range(5):
        assert chon_don_vi(list(bang_het)).provider_id == "MOV-A", "bằng cả hai thì provider_id nhỏ hơn thắng"


def test_a_budget_filters_but_never_invents_a_cheaper_provider():
    tat_ca = tim_don_vi_chuyen_nha(
        ngay=NGAY, move_vehicle="truck", needs_elevator=True, needs_loading_support=True
    )
    assert tat_ca
    re_nhat = min(x.gia for x in tat_ca)

    # Ngân sách thấp hơn giá rẻ nhất → KHÔNG có ai, và tuyệt đối không bịa ra
    # một đơn vị vừa túi tiền để làm vừa lòng người hỏi.
    khong_ai = tim_don_vi_chuyen_nha(
        ngay=NGAY,
        move_vehicle="truck",
        needs_elevator=True,
        needs_loading_support=True,
        max_price=re_nhat - 1,
    )
    assert khong_ai == [], f"ngân sách dưới giá sàn mà vẫn trả về {khong_ai}"

    vua_du = tim_don_vi_chuyen_nha(
        ngay=NGAY,
        move_vehicle="truck",
        needs_elevator=True,
        needs_loading_support=True,
        max_price=re_nhat,
    )
    assert len(vua_du) >= 1
    assert all(x.gia <= re_nhat for x in vua_du)


def test_the_date_is_a_hard_constraint():
    """Ngày là ràng buộc CỨNG — không bao giờ trả về đơn vị bận ngày đó.

    Đối chiếu thẳng với `nghi_thu` (DỮ LIỆU), KHÔNG gọi `con_lich()`. Bản đầu
    của bài kiểm này gọi chính hàm nó đang kiểm, nên khi tôi thử phá luật bằng
    cách cho `con_lich()` luôn trả True thì nó vẫn xanh — một vòng tròn hoàn
    hảo. Bài kiểm dùng lại thứ mình đang kiểm thì không kiểm gì cả.
    """
    da_gap_ngay_nghi = False
    for ngay_lech in range(0, 14):
        ngay = date.today() + timedelta(days=7 + ngay_lech)
        tra_ve = {
            x.provider_id
            for x in tim_don_vi_chuyen_nha(
                ngay=ngay, move_vehicle="van", needs_elevator=False, needs_loading_support=False
            )
        }
        for d in DON_VI_CHUYEN_NHA:
            if ngay.weekday() in d.nghi_thu:
                da_gap_ngay_nghi = True
                assert d.provider_id not in tra_ve, (
                    f"{d.provider_id} nghỉ thứ {ngay.weekday()} mà vẫn được trả về cho {ngay}"
                )
    assert da_gap_ngay_nghi, "14 ngày mà không gặp ngày nghỉ nào — dữ liệu mẫu không kiểm được gì"


@pytest.mark.parametrize("loai", ["air_conditioning", "electrical", "plumbing", "other"])
def test_maintenance_search_works_for_every_issue_type(loai: str):
    ket_qua = tim_don_vi_bao_tri(ngay=NGAY, issue_type=loai)
    assert ket_qua, f"không đơn vị nào nhận {loai} ngày {NGAY}"
    assert all(x.gia > 0 for x in ket_qua)
