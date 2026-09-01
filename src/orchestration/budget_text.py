"""Đọc một mức ngân sách từ NGUYÊN VĂN lời người dùng. Tất định, không model.

Vì sao không để model trả thẳng con số
--------------------------------------
Một con số do model đọc ra trông y hệt một con số do người dùng nói. Nếu model
đọc "dưới 500k" thành `5000000` thì hệ thống lọc báo giá bằng một ngân sách gấp
mười lần thật, chọn một đơn vị đắt hơn mọi lựa chọn khách chấp nhận, và không
tầng nào phát hiện được — vì `5000000` là một số nguyên dương hoàn toàn hợp lệ.

Model chỉ chép lại đoạn chữ. Việc đọc ra số là việc của file này, và file này
TỪ CHỐI nhiều hơn là đoán.

Trả `None` nghĩa là "không đọc được" — người gọi hỏi lại, không tự chọn.
"""

from __future__ import annotations

import re

# Trần trên: 1 tỷ. Không có trần thì "500 tỷ" là một ngân sách hợp lệ, và nó lọc
# đúng bằng không lọc — tệ hơn, nó trông như khách đã nêu một điều kiện.
_TRAN = 1_000_000_000

# Đơn vị người Việt nói. `k` và `nghìn` cùng nghĩa; `tr`/`triệu` cùng nghĩa.
_BOI: tuple[tuple[str, int], ...] = (
    ("nghìn", 1_000),
    ("nghin", 1_000),
    ("ngàn", 1_000),
    ("ngan", 1_000),
    ("triệu", 1_000_000),
    ("trieu", 1_000_000),
    ("tr", 1_000_000),
    ("tỷ", 1_000_000_000),
    ("ty", 1_000_000_000),
    ("k", 1_000),
)

# Một cụm số, cho phép dấu chấm/phẩy ngăn nhóm ("450.000", "1,5").
#
# Bắt cả dấu âm đứng liền trước. Không bắt thì `-5000` đọc thành `5000`: dấu trừ
# bị nuốt và một giá trị vô nghĩa thành một ngân sách hợp lệ.
#
# KHÔNG có chốt `startswith("-")` riêng trong `_thanh_so`. Đã từng có, và nó là
# mã chết: `float("-5000")` trả về một số âm, và phép kiểm `gia_tri <= 0` ở
# `doc_ngan_sach` đã loại. Một hàng rào không bao giờ chạy tới còn tệ hơn không
# có hàng rào — người đọc tin là trường hợp ấy đã được xử lý ở đây.
_SO = re.compile(r"(-?\d[\d.,]*)")


def _thanh_so(cum: str) -> float | None:
    """`"450.000"` → 450000; `"1,5"` → 1.5. `None` khi không đọc nổi.

    Dấu chấm và dấu phẩy đều được người Việt dùng cho CẢ hai việc — ngăn nhóm
    nghìn và ngăn phần thập phân. Phân biệt bằng số chữ số sau dấu cuối cùng:
    đúng ba chữ số là ngăn nhóm, còn lại là thập phân. Không hoàn hảo, nhưng nó
    sai theo hướng trả `None` chứ không theo hướng ra một số khác.
    """
    sach = cum.strip().rstrip(".,")
    if not sach:
        return None
    vi_tri = max(sach.rfind("."), sach.rfind(","))
    if vi_tri == -1:
        return float(sach) if sach.isdigit() else None
    dau = sach[:vi_tri].replace(".", "").replace(",", "")
    duoi = sach[vi_tri + 1 :]
    if not dau.isdigit() or not duoi.isdigit():
        return None
    if len(duoi) == 3:
        return float(dau + duoi)
    return float(f"{dau}.{duoi}")


def doc_ngan_sach(text: str | None) -> int | None:
    """Mức tiền tối đa đọc được từ `text`, hoặc `None`.

    `None` ở MỌI nhánh không chắc chắn: không có số, nhiều số, số âm, vượt trần,
    hay không đọc nổi cụm số. Người gọi phải hỏi lại — một ngân sách đoán sai sẽ
    lặng lẽ loại đúng lựa chọn khách muốn.

    Nhiều số cũng trả `None`: "từ 400 tới 600 nghìn" là một KHOẢNG, và lấy số
    nào trong đó cũng là một quyết định không ai đưa ra.
    """
    if not text or not isinstance(text, str):
        return None

    thuong = text.lower()
    cac_so = _SO.findall(thuong)
    if len(cac_so) != 1:
        return None

    gia_tri = _thanh_so(cac_so[0])
    if gia_tri is None:
        return None

    # Đơn vị nằm NGAY SAU cụm số. Quét cả câu sẽ nhặt phải "nghìn" của một mệnh
    # đề khác ("rẻ hơn vài chục nghìn thì tôi lấy 600").
    sau = thuong[thuong.index(cac_so[0]) + len(cac_so[0]) :].lstrip(" .")
    for ten, boi in _BOI:
        if sau.startswith(ten):
            gia_tri *= boi
            break

    # MỘT phép kiểm biên, đặt sau khi đã nhân đơn vị.
    #
    # Từng có thêm một phép `gia_tri <= 0` ngay sau khi đọc số, và một phép
    # `startswith("-")` bên trong `_thanh_so`. Cả hai đều là mã chết: dòng dưới
    # đã loại số âm, số 0, và số làm tròn về 0. Đo được bằng đột biến — bỏ từng
    # cái đi không làm bài kiểm nào đỏ.
    #
    # Ba hàng rào cho một điều kiện không phải là ba lớp bảo vệ. Nó là hai chỗ
    # để người đọc tin nhầm rằng trường hợp ấy được xử lý ở đó.
    so_nguyen = int(round(gia_tri))
    if so_nguyen <= 0 or so_nguyen > _TRAN:
        return None
    return so_nguyen
