"""Danh tính lần thử: `T1` → `T1R2` → `T1R3`, và không bao giờ đụng ai.

Luật "không dài quá 20 ký tự, không đụng id đang có" là một BẤT BIẾN CỦA
SCHEMA. Trước module `task_attempt` nó có ba bản sao — `repair_attempt`,
`support_request`, và một lời gọi xuyên dấu gạch dưới từ `provider_reselection`.
Một bất biến ba bản sao là một bất biến sẽ lệch.

Hàm THUẦN: không đọc database, không giữ trạng thái. Tính duy nhất đến từ
`taken`, và `taken` chỉ đúng trong lượt nó vừa được đọc — nên bài kiểm cuối
file khoá đúng điều đó, thay vì hứa một sự an toàn hàm này không cung cấp.
"""

from __future__ import annotations

import pytest

from src.orchestration.task_attempt import (
    DAI_TOI_DA,
    cap_danh_tinh,
    cap_danh_tinh_lan_thu,
    cung_mot_chuoi,
    goc_chuoi_lan_thu,
)


def test_the_chain_grows_one_step_at_a_time():
    """`T1 → T1R2 → T1R3`. Hành vi này KHÔNG đổi so với trước refactor."""
    assert cap_danh_tinh_lan_thu("T1", {"T1"}) == "T1R2"
    assert cap_danh_tinh_lan_thu("T1", {"T1", "T1R2"}) == "T1R3"
    assert cap_danh_tinh_lan_thu("T1R2", {"T1", "T1R2"}) == "T1R3"


def test_any_member_of_the_chain_asks_for_the_same_next_name():
    """Gọi với `T1`, `T1R2` hay `T1R7` đều cho lần kế tiếp của CÙNG chuỗi.

    Nếu không, một lượt chọn lại lần thứ ba sẽ mở `T1R2R2` — một id trông như
    một chuỗi khác, và tập loại trừ đơn vị đã từ chối sẽ không phủ tới nó.
    """
    da_co = {"T1", "T1R2", "T1R3"}
    assert cap_danh_tinh_lan_thu("T1", da_co) == "T1R4"
    assert cap_danh_tinh_lan_thu("T1R3", da_co) == "T1R4"
    assert goc_chuoi_lan_thu("T1R7") == "T1"
    assert cung_mot_chuoi("T1", "T1R9")
    assert not cung_mot_chuoi("T1", "T2")


def test_it_never_hands_out_a_name_that_is_taken():
    """`taken` là tập id ĐÃ TỒN TẠI. Không id nào được cấp hai lần."""
    da_co = {"T1"}
    for _ in range(20):
        moi = cap_danh_tinh_lan_thu("T1", da_co)
        assert moi is not None
        assert moi not in da_co
        da_co.add(moi)
    assert len(da_co) == 21


def test_it_refuses_rather_than_truncating():
    """Hết chỗ thì trả `None`, KHÔNG cắt cho vừa.

    Một `task_id` bị cắt có thể đụng một id đang có, và khi đó lần thử mới ghi
    đè lên bằng chứng của một bước không liên quan. Người gọi phải fail closed
    khi nhận `None` — đi tiếp nghĩa là chạy lại trên bằng chứng cũ.
    """
    qua_dai = "T" * DAI_TOI_DA
    assert cap_danh_tinh_lan_thu(qua_dai, set()) is None
    vua_du = "T" * (DAI_TOI_DA - 2)
    assert cap_danh_tinh_lan_thu(vua_du, set()) == f"{vua_du}R2"
    assert len(cap_danh_tinh_lan_thu(vua_du, set())) <= DAI_TOI_DA


def test_a_full_chain_runs_out_instead_of_wrapping_around():
    goc = "T1"
    da_co = {goc} | {f"T1R{i}" for i in range(2, 100)}
    assert cap_danh_tinh_lan_thu(goc, da_co) is None


def test_the_cancel_chain_is_a_different_chain():
    """Chuỗi HUỶ dùng `H`, chuỗi THỬ LẠI dùng `R`.

    Hai chuỗi khác nhau trên cùng một bước. Gộp chúng vào một tiền tố sẽ làm
    một lượt huỷ và một lượt thử lại tranh nhau cùng một cái tên — và cái thua
    sẽ ghi đè bằng chứng của cái thắng.
    """
    assert cap_danh_tinh("T1", {"T1"}, dau="H") == "T1H2"
    assert cap_danh_tinh("T1", {"T1", "T1R2"}, dau="H") == "T1H2"
    assert cap_danh_tinh_lan_thu("T1", {"T1", "T1H2"}) == "T1R2"


def test_two_callers_reading_the_same_snapshot_collide_on_purpose():
    """Hàm này KHÔNG chống được đồng thời, và bài kiểm nói thẳng điều đó.

    Hai lượt song song đọc cùng một `taken` sẽ cùng nhắm `T1R2`. Đó không phải
    lỗi của hàm — nó thuần, và tính duy nhất chỉ đúng với ảnh chụp nó nhận.

    Hàng rào thật nằm ở người gọi: `provider_reselection.mo_lan_chon_lai` kiểm
    "đã mở lần thử mới chưa" trước khi ghi (có bài kiểm riêng cho bấm đúp).
    Đặt hàng rào ấy vào đây sẽ đúng cho một đường và sai cho hai đường còn lại
    — mỗi đường có một định nghĩa khác nhau về "đã mở rồi".
    """
    anh_chup = {"T1"}
    assert cap_danh_tinh_lan_thu("T1", anh_chup) == cap_danh_tinh_lan_thu("T1", anh_chup)


@pytest.mark.parametrize("xau", ["", "R", "R2"])
def test_a_degenerate_id_still_returns_something_usable_or_nothing(xau):
    """Id suy biến không được làm hàm ném — người gọi đã có nhánh `None`."""
    ket_qua = cap_danh_tinh_lan_thu(xau, set())
    assert ket_qua is None or (len(ket_qua) <= DAI_TOI_DA and ket_qua.endswith("R2"))
