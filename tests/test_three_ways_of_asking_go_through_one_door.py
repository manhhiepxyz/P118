"""Ba đường vào, một hàm chọn — và nó KHÔNG BAO GIỜ tự đổi ý của khách.

Ba cách khách nói:

    nói rõ tên       "cho tôi bên Đại Tín"
    nói ngân sách    "trong khoảng 450 nghìn"
    không nói gì     "đặt giúp tôi chuyển nhà ngày 30/9"

Viết ba nhánh là cách nhanh nhất để chúng lệch nhau, và chỗ lệch sẽ nằm đúng ở
luật phá thế hoà hoặc luật ngân sách — những chỗ không ai nhìn thấy cho tới khi
hoá đơn sai.

Luật quan trọng nhất ở đây là luật KHÔNG LÀM: khách chỉ đích danh một đơn vị mà
đơn vị ấy vượt ngân sách thì nói ra XUNG ĐỘT, không lặng lẽ chọn bên rẻ hơn.
Đơn vị ấy không báo giá thì nói ra, không thay bằng bên khác. Hai điều kiện của
khách mâu thuẫn nhau là chuyện của khách; tự gỡ hộ nghĩa là quyết định thay họ
về tiền, và họ chỉ biết khi đọc hoá đơn.

Bước C CHỈ ĐỌC. Không xác nhận báo giá, không ghim hàng đợi duyệt, không gọi ra
ngoài. Bài kiểm ở đây vì thế chạy trên chứng từ thuần — không cần database, vì
không có gì để ghi.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.orchestration.provider_selection import chon_don_vi
from src.orchestration.quote import BaoGia

CHUYEN_NHA = "schedule_move"
VAN_TAY = "vantay" * 8


def _q(
    don_vi: str,
    gia: int,
    *,
    con_lai_phut: int = 30,
    status: str = "ACTIVE",
    service_type: str = CHUYEN_NHA,
) -> BaoGia:
    return BaoGia(
        quote_id=f"q-{don_vi}",
        external_quote_id=f"Q-{don_vi}",
        service_provider_id=don_vi,
        service_type=service_type,
        amount=gia,
        currency="VND",
        request_fingerprint=VAN_TAY,
        valid_until=datetime.now(UTC) + timedelta(minutes=con_lai_phut),
        status=status,
        workflow_id="w",
        task_id="T1",
    )


# MOV-01 4.6 · MOV-02 4.8 · MOV-03 4.3 — dùng để phá thế hoà.
BA_BAO_GIA = [_q("MOV-01", 430_000), _q("MOV-02", 470_000), _q("MOV-03", 420_000)]


# ------------------------------------------------- đường 1: không nói gì
def test_saying_nothing_gets_the_cheapest():
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA)
    assert ket_qua.ket_qua == "SELECTED"
    assert ket_qua.bao_gia.service_provider_id == "MOV-03"
    assert ket_qua.provider_id == "MOV-03"


def test_a_tie_is_broken_by_rating_then_by_id():
    """Bằng giá → đánh giá cao hơn. Bằng cả hai → mã nhỏ hơn.

    Vế cuối trông thừa nhưng không thừa: thiếu nó thì kết quả phụ thuộc thứ tự
    đọc lên từ database, và mọi bài kiểm đi qua nó đều nhấp nháy.
    """
    bang_gia = [_q("MOV-01", 500_000), _q("MOV-02", 500_000), _q("MOV-03", 500_000)]
    for _ in range(5):
        assert chon_don_vi(bang_gia, service_type=CHUYEN_NHA).bao_gia.service_provider_id == "MOV-02"

    # Hai đơn vị bảo trì bằng giá bằng đánh giá không có trong danh mục, nên
    # dựng thẳng: mã lạ có đánh giá mặc định 0.0, hai cái bằng nhau → mã nhỏ hơn.
    bang_het = [_q("ZZZ-02", 500_000), _q("AAA-01", 500_000)]
    for _ in range(5):
        assert chon_don_vi(bang_het, service_type=CHUYEN_NHA).bao_gia.service_provider_id == "AAA-01"


def test_nothing_to_choose_from_is_said_out_loud():
    ket_qua = chon_don_vi([], service_type=CHUYEN_NHA)
    assert ket_qua.ket_qua == "NO_AVAILABLE_QUOTE"
    assert ket_qua.bao_gia is None


# ------------------------------------------------- đường 2: có ngân sách
def test_a_budget_narrows_but_never_invents():
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, max_price=425_000)
    assert (ket_qua.ket_qua, ket_qua.bao_gia.service_provider_id) == ("SELECTED", "MOV-03")


def test_a_budget_below_the_floor_says_the_real_floor():
    """Không nới ngân sách hộ, không chọn "gần nhất" — và nói ra giá thật rẻ nhất.

    Thiếu con số ấy thì lời từ chối là một khẳng định không kiểm chứng được, và
    khách không biết mình đang thiếu bao nhiêu.
    """
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, max_price=100_000)
    assert ket_qua.ket_qua == "OVER_BUDGET"
    assert ket_qua.bao_gia is None
    assert ket_qua.gia_re_nhat == 420_000


def test_a_budget_exactly_on_the_cheapest_price_still_fits():
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, max_price=420_000)
    assert (ket_qua.ket_qua, ket_qua.bao_gia.service_provider_id) == ("SELECTED", "MOV-03")


# ------------------------------------------------- đường 3: chỉ đích danh
def test_naming_a_unit_gets_that_unit_even_when_it_is_not_the_cheapest():
    """Khách chọn rõ thì hệ thống không được "giúp" bằng cách chọn bên rẻ hơn."""
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Đại Tín")
    assert (ket_qua.ket_qua, ket_qua.bao_gia.service_provider_id) == ("SELECTED", "MOV-02")
    assert ket_qua.bao_gia.amount == 470_000


def test_a_name_nobody_recognises_is_a_question():
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Chuyển nhà Thành Công")
    assert ket_qua.ket_qua == "UNKNOWN_PROVIDER"
    assert ket_qua.bao_gia is None
    # Vẫn nói được giá rẻ nhất đang có: câu hỏi lại kèm thông tin hữu ích.
    assert ket_qua.gia_re_nhat == 420_000


def test_an_ambiguous_name_returns_the_candidates_not_a_pick(monkeypatch):
    """Hai đối tác cùng thương hiệu → đưa danh sách, không chọn hộ.

    Danh mục hiện tại không có thương hiệu trùng, nên nhánh này phải dựng riêng.
    Nó vẫn phải sống: hai đối tác cùng mang thương hiệu "An Khang" là chuyện
    bình thường ngoài đời, và ngày ấy đến thì đây là thứ duy nhất đứng giữa
    khách và một đơn hàng gửi nhầm.
    """
    from dataclasses import replace

    import src.orchestration.provider_resolver as resolver
    from src.mock.service_providers import DON_VI_CHUYEN_NHA

    monkeypatch.setitem(
        resolver._DANH_MUC,
        CHUYEN_NHA,
        (
            replace(DON_VI_CHUYEN_NHA[0], provider_id="MOV-01", ten_thuong_hieu="An Khang"),
            DON_VI_CHUYEN_NHA[2],  # MOV-03, thương hiệu "An Khang"
        ),
    )

    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="An Khang")

    assert ket_qua.ket_qua == "AMBIGUOUS_PROVIDER"
    assert ket_qua.bao_gia is None, "mơ hồ mà vẫn chốt một báo giá"
    assert ket_qua.ung_vien == ("MOV-01", "MOV-03")


def test_a_code_prefix_is_not_an_ambiguous_name_it_is_no_name():
    """ "MOV" nằm trong cả ba mã, nhưng nó chưa bao giờ là cách khách gọi tên
    một đơn vị. Khớp chính xác gọi đó là `UNKNOWN`, và đó là câu trả lời đúng."""
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="MOV")
    assert ket_qua.ket_qua == "UNKNOWN_PROVIDER"


def test_a_named_unit_that_did_not_quote_is_reported_not_replaced(caplog):
    """Đơn vị có thật nhưng không báo giá → nói ra, KHÔNG thay bằng bên khác.

    Bận ngày ấy, không nhận loại việc ấy, hoặc vừa hết hạn — cả ba đều là câu
    trả lời. Thay bằng một đơn vị khác là quyết định thay khách về việc họ giao
    tiền cho ai.
    """
    khong_co_mov02 = [_q("MOV-01", 430_000), _q("MOV-03", 420_000)]
    ket_qua = chon_don_vi(khong_co_mov02, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Đại Tín")

    assert ket_qua.ket_qua == "NO_AVAILABLE_QUOTE"
    assert ket_qua.provider_id == "MOV-02", "không nói được là đơn vị NÀO không có giá"
    assert ket_qua.bao_gia is None
    assert ket_qua.gia_re_nhat == 420_000


# ------------------------------------------------- xung đột: tên + ngân sách
def test_a_named_unit_over_budget_is_a_conflict_not_a_swap():
    """LUẬT QUAN TRỌNG NHẤT của bước này.

    Khách nói "cho tôi Đại Tín, trong 450 nghìn" là hai điều kiện mâu thuẫn
    nhau. Tự gỡ bằng cách chọn MOV-03 (420k, vừa ngân sách) là quyết định thay
    họ — và họ chỉ biết khi đọc hoá đơn mang tên một công ty họ không chọn.

    Trả về CẢ báo giá của đơn vị được chỉ định lẫn giá rẻ nhất, để tầng trên
    nói được đủ hai vế: "Đại Tín báo 470k, vượt 450k; rẻ nhất đang có là 420k."
    """
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Đại Tín", max_price=450_000)

    assert ket_qua.ket_qua == "OVER_BUDGET"
    assert ket_qua.provider_id == "MOV-02"
    assert ket_qua.bao_gia.amount == 470_000, "không nói được đơn vị ấy báo bao nhiêu"
    assert ket_qua.gia_re_nhat == 420_000, "không nói được rẻ nhất là bao nhiêu"


def test_a_named_unit_inside_the_budget_is_simply_selected():
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Đại Tín", max_price=500_000)
    assert (ket_qua.ket_qua, ket_qua.bao_gia.service_provider_id) == ("SELECTED", "MOV-02")


def test_an_unknown_name_is_answered_before_the_budget_is_considered():
    """Một cái tên không tra ra được thì ngân sách chưa liên quan gì.

    Đảo thứ tự thì khách nhận "không ai trong ngân sách" cho một câu hỏi thật
    ra là "tôi không biết bên bạn vừa nói là bên nào" — và họ sẽ đi nâng ngân
    sách để sửa một lỗi chính tả.
    """
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Thành Công", max_price=1_000)
    assert ket_qua.ket_qua == "UNKNOWN_PROVIDER"


def test_a_named_unit_with_no_quote_is_answered_before_the_budget():
    """Không có giá thì không thể vượt ngân sách — thứ tự này cũng không đảo được."""
    khong_co_mov02 = [_q("MOV-03", 420_000)]
    ket_qua = chon_don_vi(khong_co_mov02, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Đại Tín", max_price=1_000)
    assert ket_qua.ket_qua == "NO_AVAILABLE_QUOTE"
    assert ket_qua.provider_id == "MOV-02"


# ------------------------------------------------- chỉ chọn từ chứng từ sống
def test_an_expired_quote_is_not_a_choice_even_when_it_is_the_cheapest():
    """Hết hạn thì không phải một lựa chọn, kể cả khi nó rẻ nhất.

    Đây là chỗ dễ sai nhất: báo giá rẻ nhất cũng là báo giá được xếp đầu, nên
    nếu quên lọc thì nó luôn là thứ được chọn.
    """
    het_han = [_q("MOV-03", 100_000, con_lai_phut=-1), _q("MOV-01", 430_000)]
    ket_qua = chon_don_vi(het_han, service_type=CHUYEN_NHA)

    assert (ket_qua.ket_qua, ket_qua.bao_gia.service_provider_id) == ("SELECTED", "MOV-01")
    assert ket_qua.gia_re_nhat == 430_000, "giá rẻ nhất vẫn tính cả chứng từ đã chết"


def test_a_named_unit_whose_only_quote_expired_is_reported_not_replaced():
    het_han = [_q("MOV-02", 470_000, con_lai_phut=-1), _q("MOV-03", 420_000)]
    ket_qua = chon_don_vi(het_han, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Đại Tín")
    assert (ket_qua.ket_qua, ket_qua.provider_id) == ("NO_AVAILABLE_QUOTE", "MOV-02")


@pytest.mark.parametrize("trang_thai", ["CONFIRMED", "EXPIRED", "SUPERSEDED"])
def test_only_active_quotes_are_choices(trang_thai):
    """Đã chốt, đã hết hạn, đã bị thay thế — không cái nào là một lựa chọn MỚI."""
    khong_song = [_q("MOV-03", 100_000, status=trang_thai), _q("MOV-01", 430_000)]
    ket_qua = chon_don_vi(khong_song, service_type=CHUYEN_NHA)
    assert ket_qua.bao_gia.service_provider_id == "MOV-01"


def test_everything_expired_is_not_the_same_as_over_budget():
    """Hai câu trả lời khác nhau cho hai tình huống khác nhau.

    "Báo giá đã hết hạn, để tôi hỏi lại" và "không ai trong ngân sách của bạn"
    dẫn tới hai hành động khác nhau. Gộp chúng thì khách được bảo đi nâng ngân
    sách cho một việc chỉ cần bấm hỏi lại.
    """
    ket_qua = chon_don_vi([_q("MOV-03", 100_000, con_lai_phut=-1)], service_type=CHUYEN_NHA)
    assert ket_qua.ket_qua == "NO_AVAILABLE_QUOTE"
    assert ket_qua.gia_re_nhat is None


# ------------------------------------------------- chỉ đọc
def test_selecting_never_mutates_the_quotes_it_was_given():
    """Bước C chỉ ĐỌC — kể cả với đối tượng trong bộ nhớ.

    `BaoGia` là frozen, nên phép gán sẽ ném; bài kiểm này khoá điều đó lại để
    không ai đổi nó thành mutable rồi "tiện tay" đặt `status` ngay tại chỗ chọn.
    """
    truoc = [(q.status, q.amount) for q in BA_BAO_GIA]
    chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Đại Tín", max_price=450_000)
    assert [(q.status, q.amount) for q in BA_BAO_GIA] == truoc

    with pytest.raises((AttributeError, TypeError)):
        BA_BAO_GIA[0].status = "CONFIRMED"  # type: ignore[misc]


def test_every_outcome_is_one_of_the_six_names():
    """Kết quả là một tập ĐÓNG. Thêm một nhánh trả về chuỗi lạ sẽ đỏ ở đây."""
    hop_le = {
        "SELECTED",
        "UNKNOWN_PROVIDER",
        "AMBIGUOUS_PROVIDER",
        "OVER_BUDGET",
        "NO_AVAILABLE_QUOTE",
        "INVALID_BUDGET",
    }
    cac_ca = [
        chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA),
        chon_don_vi([], service_type=CHUYEN_NHA),
        chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, max_price=1),
        chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, max_price=-1),
        chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="MOV"),
        chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Không Có Bên Này"),
        chon_don_vi([_q("MOV-01", 430_000)], service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Đại Tín"),
    ]
    assert {c.ket_qua for c in cac_ca} <= hop_le
    assert all((c.bao_gia is not None) == c.da_chon or c.ket_qua == "OVER_BUDGET" for c in cac_ca)


# ------------------------------------------- model trích nhầm, code chặn lại
@pytest.mark.parametrize("model_trich_nham", ["chuyển nhà", "vận tải", "dịch vụ", "Chuyển nhà"])
def test_a_generic_word_the_model_mistook_for_a_name_never_becomes_a_choice(model_trich_nham):
    """Model trích "chuyển nhà" vào ô tên đơn vị → KHÔNG có `SELECTED`.

    Đây là bài kiểm cho chính nguyên tắc kiến trúc. Mỗi cụm ở đây nằm trong
    đúng MỘT tên đơn vị, nên một resolver dùng phép chứa-nhau sẽ trả về `FOUND`
    — duy nhất, tự tin, và sai. Lúc ấy code không còn xác minh gì cả: nó hợp
    thức hoá lỗi của model thành một lựa chọn tài chính, và khách nhận hoá đơn
    mang tên một công ty họ chưa từng nhắc tới.

    Câu trả lời đúng là hỏi lại. Nó rẻ hơn rất nhiều.
    """
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi=model_trich_nham)
    assert ket_qua.ket_qua == "UNKNOWN_PROVIDER"
    assert ket_qua.bao_gia is None, f"{model_trich_nham!r} đã thành một lựa chọn"


# ------------------------------------------------- hàng rào: đúng dịch vụ
def test_a_quote_for_another_service_is_never_chosen():
    """Chứng từ bảo trì không được chọn cho một lượt chuyển nhà.

    Kiểm ở HÀM CHỌN, không chỉ ở wrapper đọc database: caller truyền thẳng một
    danh sách là đường vào hợp lệ, nên hàng rào nằm ở wrapper thôi là hàng rào
    chỉ có với một trong hai đường vào. Hai chứng từ cùng hình dạng, khác ngành
    — không có gì trong `BaoGia` tự nói ra điều đó.
    """
    lan_nganh = [
        _q("FIX-01", 100_000, service_type="create_maintenance_request"),
        _q("MOV-01", 430_000),
    ]

    ket_qua = chon_don_vi(lan_nganh, service_type=CHUYEN_NHA)

    assert ket_qua.bao_gia.service_provider_id == "MOV-01", "chọn phải chứng từ của ngành khác"
    assert ket_qua.gia_re_nhat == 430_000, "giá rẻ nhất tính cả chứng từ ngoài ngành"


def test_only_quotes_of_another_service_is_the_same_as_none():
    chi_bao_tri = [_q("FIX-01", 100_000, service_type="create_maintenance_request")]
    assert chon_don_vi(chi_bao_tri, service_type=CHUYEN_NHA).ket_qua == "NO_AVAILABLE_QUOTE"


def test_a_named_unit_cannot_be_served_by_another_services_quote():
    lan_nganh = [_q("MOV-02", 470_000, service_type="create_maintenance_request")]
    ket_qua = chon_don_vi(lan_nganh, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Đại Tín")
    assert (ket_qua.ket_qua, ket_qua.provider_id) == ("NO_AVAILABLE_QUOTE", "MOV-02")


# ------------------------------------------------- hàng rào: ngân sách đọc được
@pytest.mark.parametrize(
    "ngan_sach_hong",
    [
        -1,
        0,
        "450000",
        "bốn trăm năm mươi nghìn",
        450_000.5,
        True,
        False,
        [450_000],
    ],
)
def test_a_budget_that_is_not_a_positive_integer_never_reaches_the_rule(ngan_sach_hong):
    """`-1` KHÔNG được thành `OVER_BUDGET`.

    `max_price` đến từ một lượt trích của model, nên nó có thể là `"450000"`,
    `-1`, hay `True`. Cả ba đi lọt qua phép so sánh và ra `OVER_BUDGET` — một
    câu trả lời SAI về nghiệp vụ cho một lỗi kiểu dữ liệu, và khách được bảo đi
    nâng ngân sách cho một thứ không phải ngân sách.

    `True` là ca âm thầm nhất: `bool` là `int` trong Python, nên `True` = 1 và
    mọi báo giá đều "vượt ngân sách 1 đồng".
    """
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, max_price=ngan_sach_hong)
    assert ket_qua.ket_qua == "INVALID_BUDGET", f"{ngan_sach_hong!r} → {ket_qua.ket_qua}"
    assert ket_qua.bao_gia is None


def test_an_unreadable_budget_is_caught_before_the_name_is_resolved():
    """Ngân sách hỏng là lỗi LẬP TRÌNH, đứng ngoài chuỗi quyết định nghiệp vụ.

    Báo `UNKNOWN_PROVIDER` cho một lượt gọi mang `max_price=-1` sẽ gửi người
    sửa đi tìm lỗi ở chỗ không có.
    """
    ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi="Không Có Bên Này", max_price=-1)
    assert ket_qua.ket_qua == "INVALID_BUDGET"


def test_the_budget_value_is_never_written_to_a_log(caplog):
    """Log nói KIỂU, không nói GIÁ TRỊ.

    Ngân sách là thông tin tài chính riêng của khách. Log đi vào file, vào máy
    chủ log tập trung, vào ảnh chụp màn hình lúc gỡ lỗi — những chỗ không ai
    kiểm soát được ai đọc. Và nhánh này chạy đúng lúc có sự cố, tức đúng lúc
    log được đọc nhiều nhất.

    Kiểu đã đủ để sửa lỗi: nó nói `str` hay `bool` hay số âm. Giá trị không
    thêm gì cho việc sửa.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    for hong in (-1, "450000", 999_888_777, 450_000.5):
        chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, max_price=hong)

    da_ghi = caplog.text
    assert da_ghi.strip(), "không ghi log nào — bài kiểm này sẽ xanh vì lý do sai"
    for hong in ("-1", "450000", "999888777", "999_888_777", "450000.5"):
        assert hong not in da_ghi, f"giá trị ngân sách {hong!r} lọt vào log"
    # Kiểu thì phải có, nếu không log mất hết giá trị chẩn đoán.
    assert "int" in da_ghi and "str" in da_ghi and "float" in da_ghi


def test_no_budget_at_all_is_still_perfectly_valid():
    """`None` nghĩa là khách không nói ngân sách — khác hẳn ngân sách hỏng."""
    assert chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, max_price=None).ket_qua == "SELECTED"


def test_a_blank_name_is_treated_as_saying_nothing():
    """Model trả về chuỗi rỗng khi không trích được tên — đó là "không nói gì",
    không phải "một cái tên không tra ra được"."""
    for xau in ("", "   "):
        ket_qua = chon_don_vi(BA_BAO_GIA, service_type=CHUYEN_NHA, ten_don_vi_khach_noi=xau)
        assert (ket_qua.ket_qua, ket_qua.bao_gia.service_provider_id) == ("SELECTED", "MOV-03")
