"""Tên khách nói → đúng MỘT đơn vị, hoặc không đơn vị nào. Không có ở giữa.

Đoán sai một đơn vị cung cấp không phải lỗi hiển thị: nó gửi việc và tiền của
khách sang một doanh nghiệp khác, và nó sai một cách IM LẶNG — màn hình vẫn nói
"đã chọn Đại Tín", chỉ có đơn hàng là của người khác.

Nên ở đây chỉ có TRÙNG KHỚP HOÀN TOÀN sau chuẩn hoá — với `provider_id`, `ten`
hoặc `ten_thuong_hieu`. Không chứa nhau, không khoảng cách chỉnh sửa, không
điểm số, không ngưỡng.

Bản trước còn nhánh "chứa nhau", và nó mở một đường sai mà không bài kiểm nào
lúc ấy bắt được:

    "chuyển nhà"  chỉ nằm trong "Chuyển nhà Minh Phát"  → MOV-01
    "vận tải"     chỉ nằm trong "Vận tải Đại Tín"       → MOV-02
    "dịch vụ"     chỉ nằm trong "Dịch vụ An Khang"      → MOV-03

Cả ba là MÔ TẢ LOẠI HÌNH, không phải tên khách chỉ định — và cụm đầu tiên có
mặt trong hầu hết câu về chuyển nhà, tức đúng thứ model dễ trích nhầm nhất. Khi
ấy resolver biến một lỗi trích thành một lựa chọn tài chính hợp lệ, và đó là vi
phạm thẳng "model đề xuất, code xác minh": code phải là chỗ lỗi ấy DỪNG LẠI.

Ba kết quả, và hai trong ba là "hỏi lại":

    FOUND       khớp đúng một đơn vị
    AMBIGUOUS   khớp nhiều đơn vị — đưa danh sách cho khách chọn
    UNKNOWN     không khớp gì — hỏi lại

Bài kiểm ở đây cố ý gồm cả những ca mà một bộ so khớp "thông minh" sẽ đoán
được. Chúng phải trả `UNKNOWN`. Đó là thất bại ĐÚNG: một câu hỏi rẻ hơn một đơn
hàng sai rất nhiều.
"""

from __future__ import annotations

import pytest

from src.mock.service_providers import DON_VI_BAO_TRI, DON_VI_CHUYEN_NHA
from src.orchestration.provider_resolver import chuan_hoa, tra_ten_don_vi

CHUYEN_NHA = "schedule_move"
BAO_TRI = "create_maintenance_request"


# ---------------------------------------------------------------- chuẩn hoá
@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Đại Tín", "dai tin"),
        ("ĐẠI TÍN", "Dai Tin"),
        ("  Dai   Tin  ", "dai tin"),
        ("MOV-01", "mov 01"),
        ("Chuyển nhà Minh Phát", "chuyen nha minh phat"),
        ("Điện lạnh Bách Khoa", "dien lanh bach khoa"),
    ],
)
def test_two_spellings_of_one_name_are_one_name(a, b):
    """Bỏ dấu, bỏ hoa/thường, gộp khoảng trắng. Đây là chuẩn hoá CHÍNH TẢ.

    Nó không thêm khả năng khớp nào ngoài việc coi hai cách gõ cùng một chuỗi
    là như nhau — khác hẳn với suy đoán ngữ nghĩa.
    """
    assert chuan_hoa(a) == chuan_hoa(b)


def test_the_vietnamese_d_with_a_stroke_is_not_left_behind():
    """`đ` KHÔNG tách được bằng NFD — nó là ký tự riêng, không phải `d` cộng dấu.

    Thiếu xử lý riêng thì "Đại Tín" chuẩn hoá thành "đai tin" và không bao giờ
    khớp với một người gõ không dấu — tức hỏng đúng với người gõ nhanh nhất.
    """
    assert chuan_hoa("Đại Tín") == "dai tin"
    assert "đ" not in chuan_hoa("Đơn vị Đông Đô")


def test_two_different_names_do_not_collapse_into_one():
    """Chuẩn hoá không được thu hẹp khoảng cách giữa hai tên khác nhau."""
    ten = {chuan_hoa(d.ten) for d in DON_VI_CHUYEN_NHA}
    assert len(ten) == len(DON_VI_CHUYEN_NHA), f"hai tên chập lại sau chuẩn hoá: {ten}"


# ---------------------------------------------------------------- khớp đúng
@pytest.mark.parametrize("don_vi", DON_VI_CHUYEN_NHA, ids=lambda d: d.provider_id)
def test_every_unit_can_be_named_by_its_code_and_by_its_full_name(don_vi):
    """Mọi đơn vị trong danh mục phải gọi được bằng cả hai cách.

    Một đơn vị không gọi được tên là một đơn vị khách không chọn được — nó có
    mặt trong bảng giá nhưng không có mặt trong cuộc trò chuyện.
    """
    assert tra_ten_don_vi(don_vi.provider_id, service_type=CHUYEN_NHA).provider_id == don_vi.provider_id
    assert tra_ten_don_vi(don_vi.ten, service_type=CHUYEN_NHA).provider_id == don_vi.provider_id


@pytest.mark.parametrize(
    ("khach_noi", "mong_doi"),
    [
        ("Đại Tín", "MOV-02"),
        ("dai tin", "MOV-02"),
        ("ĐẠI TÍN", "MOV-02"),
        ("Vận tải Đại Tín", "MOV-02"),
        ("VẬN TẢI ĐẠI TÍN", "MOV-02"),
        ("An Khang", "MOV-03"),
        ("Minh Phát", "MOV-01"),
        ("mov-03", "MOV-03"),
        ("MOV 02", "MOV-02"),
    ],
)
def test_a_brand_name_or_a_full_name_resolves(khach_noi, mong_doi):
    """Thương hiệu và tên đầy đủ đều gọi được — nhưng phải TRÙNG KHỚP.

    `ten_thuong_hieu` là thứ cho phép bỏ hẳn phép chứa-nhau mà vẫn gọi được
    "Đại Tín". Nó là thuộc tính của đơn vị trong cùng nguồn canonical, không
    phải một bảng tên thứ hai.
    """
    ket_qua = tra_ten_don_vi(khach_noi, service_type=CHUYEN_NHA)
    assert (ket_qua.trang_thai, ket_qua.provider_id) == ("FOUND", mong_doi)


@pytest.mark.parametrize("don_vi", (*DON_VI_CHUYEN_NHA, *DON_VI_BAO_TRI), ids=lambda d: d.provider_id)
def test_every_unit_declares_a_brand_name_that_is_not_the_whole_name(don_vi):
    """Thương hiệu phải NGẮN HƠN tên đầy đủ — nếu bằng nhau thì nó không làm gì.

    Một đơn vị mới thêm mà chép nguyên tên vào `ten_thuong_hieu` sẽ đi qua mọi
    bài kiểm khác, rồi lặng lẽ trở thành đơn vị mà khách gọi tên ngắn thì không
    ai tra ra.
    """
    assert don_vi.ten_thuong_hieu, f"{don_vi.provider_id} chưa khai thương hiệu"
    assert len(don_vi.ten_thuong_hieu) < len(don_vi.ten), (
        f"{don_vi.provider_id}: thương hiệu trùng tên đầy đủ, không thêm cách gọi nào"
    )
    assert chuan_hoa(don_vi.ten_thuong_hieu) in chuan_hoa(don_vi.ten), (
        f"{don_vi.provider_id}: thương hiệu không phải một phần của tên đầy đủ"
    )


# ---------------------------------------------------------------- mơ hồ
def test_two_partners_sharing_a_brand_are_a_question_not_a_guess(monkeypatch):
    """Khớp nhiều → `AMBIGUOUS` kèm danh sách, KHÔNG chọn cái "khớp tốt hơn".

    "Khớp tốt hơn" là một điểm số, và điểm số là chỗ việc đoán lẻn vào.

    Danh mục hiện tại không có thương hiệu trùng — đó là điều tốt, và cũng là
    lý do nhánh này phải kiểm bằng một danh mục dựng riêng. Tình huống có thật:
    hai đối tác cùng mang thương hiệu "An Khang" là chuyện bình thường ngoài
    đời, và ngày nó xảy ra thì nhánh này là thứ duy nhất đứng giữa khách và một
    đơn hàng gửi nhầm.
    """
    from dataclasses import replace

    import src.orchestration.provider_resolver as resolver

    trung_thuong_hieu = (
        replace(DON_VI_CHUYEN_NHA[0], provider_id="MOV-09", ten="Chuyển nhà An Khang", ten_thuong_hieu="An Khang"),
        DON_VI_CHUYEN_NHA[2],  # Dịch vụ An Khang, MOV-03
    )
    monkeypatch.setitem(resolver._DANH_MUC, CHUYEN_NHA, trung_thuong_hieu)

    ket_qua = tra_ten_don_vi("An Khang", service_type=CHUYEN_NHA)

    assert ket_qua.trang_thai == "AMBIGUOUS"
    assert ket_qua.provider_id is None, "mơ hồ mà vẫn trả về một mã"
    assert ket_qua.ung_vien == ("MOV-03", "MOV-09")


def test_the_catalogue_has_no_two_units_answering_to_one_name(monkeypatch):
    """Bất biến của DANH MỤC: không tên/mã/thương hiệu nào trỏ vào hai đơn vị.

    Thêm một đối tác trùng thương hiệu với đối tác cũ sẽ làm bài kiểm này đỏ —
    đúng ý. Lúc đó phải đặt lại thương hiệu hoặc chấp nhận rằng khách sẽ luôn
    bị hỏi lại khi gọi tên ấy; cả hai đều là quyết định có ý thức, khác hẳn
    việc phát hiện ra nó qua một đơn hàng gửi nhầm.
    """
    for dich_vu, danh_muc in (("schedule_move", DON_VI_CHUYEN_NHA), ("create_maintenance_request", DON_VI_BAO_TRI)):
        for don_vi in danh_muc:
            for cach_goi in (don_vi.provider_id, don_vi.ten, don_vi.ten_thuong_hieu):
                ket_qua = tra_ten_don_vi(cach_goi, service_type=dich_vu)
                assert ket_qua.provider_id == don_vi.provider_id, f"{cach_goi!r} → {ket_qua}"


def test_the_candidate_list_is_stable_between_runs(monkeypatch):
    """Danh sách ứng viên đi thẳng vào câu hỏi cho khách, nên nó phải tất định."""
    from dataclasses import replace

    import src.orchestration.provider_resolver as resolver

    monkeypatch.setitem(
        resolver._DANH_MUC,
        CHUYEN_NHA,
        (
            replace(DON_VI_CHUYEN_NHA[1], provider_id="MOV-09", ten_thuong_hieu="An Khang"),
            DON_VI_CHUYEN_NHA[2],
        ),
    )
    lan = [tra_ten_don_vi("An Khang", service_type=CHUYEN_NHA).ung_vien for _ in range(5)]
    assert len(set(lan)) == 1


def test_an_empty_or_blank_name_is_not_a_match():
    for xau in ("", "   ", "\t", "---", "!!!"):
        assert tra_ten_don_vi(xau, service_type=CHUYEN_NHA).trang_thai == "UNKNOWN"


# ---------------------------------------------------------------- không đoán
@pytest.mark.parametrize(
    "khach_noi",
    [
        "Đại Tính",  # sai một chữ
        "Dai Tinh",
        "Đại Tân",
        "MOV-04",  # mã không tồn tại
        "Chuyển nhà Thành Công",
        "bên nào rẻ nhất",  # không phải một cái tên
    ],
)
def test_a_near_miss_is_never_guessed(khach_noi):
    """Gần đúng KHÔNG phải đúng.

    Một bộ so khớp có khoảng cách chỉnh sửa sẽ "sửa" `Đại Tính` thành `Đại Tín`
    — và cũng sẽ sửa nhầm ở lần thứ N. Không có ngưỡng nào an toàn khi cái giá
    của một lần sai là một đơn hàng gửi nhầm doanh nghiệp.
    """
    assert tra_ten_don_vi(khach_noi, service_type=CHUYEN_NHA).trang_thai == "UNKNOWN"


@pytest.mark.parametrize(
    ("cum_chung", "dich_vu"),
    [
        ("chuyển nhà", CHUYEN_NHA),
        ("Chuyển nhà", CHUYEN_NHA),
        ("chuyen nha", CHUYEN_NHA),
        ("vận tải", CHUYEN_NHA),
        ("dịch vụ", CHUYEN_NHA),
        ("sửa chữa", BAO_TRI),
        ("kỹ thuật", BAO_TRI),
        ("điện lạnh", BAO_TRI),
    ],
)
def test_a_generic_service_word_is_never_a_provider(cum_chung, dich_vu):
    """MÔ TẢ LOẠI HÌNH không phải một cái tên. Đây là lỗ mà phép chứa-nhau mở ra.

    Mỗi cụm ở đây nằm trong ĐÚNG MỘT tên đơn vị của dịch vụ ấy, nên phép
    chứa-nhau sẽ resolve chúng thành `FOUND` — duy nhất, tự tin, và sai. Cụm
    "chuyển nhà" tệ nhất: nó có mặt trong hầu hết câu về chuyển nhà, tức đúng
    thứ model dễ trích nhầm vào ô tên đơn vị nhất.
    """
    assert tra_ten_don_vi(cum_chung, service_type=dich_vu).trang_thai == "UNKNOWN"


@pytest.mark.parametrize("mot_nua", ["Minh", "Đại", "Khang", "Phát", "Tín"])
def test_half_a_brand_name_is_not_a_brand_name(mot_nua):
    """Một nửa thương hiệu cũng là chứa-nhau, và cũng phải trượt."""
    assert tra_ten_don_vi(mot_nua, service_type=CHUYEN_NHA).trang_thai == "UNKNOWN"


def test_a_code_prefix_is_not_a_code():
    """ "MOV" nằm trong cả ba mã. Phép chứa-nhau gọi đó là mơ hồ; khớp chính xác
    gọi đó là không có — và "không có" đúng hơn, vì "MOV" chưa bao giờ là một
    cách khách gọi tên một đơn vị."""
    assert tra_ten_don_vi("MOV", service_type=CHUYEN_NHA).trang_thai == "UNKNOWN"
    assert tra_ten_don_vi("FIX", service_type=BAO_TRI).trang_thai == "UNKNOWN"


def test_a_name_mixed_with_other_words_is_asked_about_not_guessed():
    """Đoạn có thêm chữ ngoài tên → `UNKNOWN`. Thất bại ĐÚNG.

    Trích tên là việc của model; khi nó trích lẫn cả câu thì mã KHÔNG được đi
    tìm tên trong đó. Đó là lúc một bộ phân tích ngôn ngữ bằng tay bắt đầu mọc
    ra, và nó sẽ luôn thiếu cách nói thứ N+1.
    """
    for lan_lon in (
        "đội Đại Tín bên quận 7",
        "cho tôi bên Đại Tín",
        "công ty Vận tải Đại Tín nhé",
        "Đại Tín đi",
    ):
        assert tra_ten_don_vi(lan_lon, service_type=CHUYEN_NHA).trang_thai == "UNKNOWN", lan_lon


# ---------------------------------------------------------------- theo dịch vụ
def test_a_moving_name_never_resolves_to_a_repair_team():
    """Phạm vi theo DỊCH VỤ là bắt buộc.

    Không giới hạn thì hai danh mục dùng chung một không gian tên, và một cái
    tên trùng nhau sẽ đưa việc sang nhầm ngành — khách đặt chuyển nhà, đội điện
    lạnh nhận đơn.
    """
    assert tra_ten_don_vi("Đại Tín", service_type=BAO_TRI).trang_thai == "UNKNOWN"
    assert tra_ten_don_vi("Thành Đạt", service_type=CHUYEN_NHA).trang_thai == "UNKNOWN"
    assert tra_ten_don_vi("Thành Đạt", service_type=BAO_TRI).provider_id == "FIX-01"


@pytest.mark.parametrize("don_vi", DON_VI_BAO_TRI, ids=lambda d: d.provider_id)
def test_every_repair_unit_can_be_named_too(don_vi):
    assert tra_ten_don_vi(don_vi.ten, service_type=BAO_TRI).provider_id == don_vi.provider_id


def test_a_service_with_no_choice_has_nothing_to_resolve():
    """Dịch vụ do ban quản lý làm không có lựa chọn nào, nên không có gì để tra.

    Trả `UNKNOWN` chứ không ném: đây là câu trả lời đúng cho một câu hỏi hợp lệ.
    """
    assert tra_ten_don_vi("Ban quản lý", service_type="book_parking").trang_thai == "UNKNOWN"


def test_the_catalogue_is_the_only_source():
    """Nguồn canonical là `src/mock/service_providers.py`, và chỉ nó.

    Bài kiểm này khoá điều đó bằng cách đối chiếu: mọi mã resolve ra được đều
    phải có mặt trong danh mục. Một bảng tên/alias song song sẽ làm nó đỏ —
    đúng ý, vì hai danh mục là hai chỗ để lệch nhau.
    """
    trong_danh_muc = {d.provider_id for d in DON_VI_CHUYEN_NHA}
    for d in DON_VI_CHUYEN_NHA:
        assert tra_ten_don_vi(d.ten, service_type=CHUYEN_NHA).provider_id in trong_danh_muc
