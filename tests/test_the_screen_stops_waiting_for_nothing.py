"""Những lần chờ KHÔNG đổi lấy gì — và cách bỏ chúng.

Hai loại chờ trong hệ thống này, và chỉ một loại đáng giữ:

    có đổi lấy thứ gì   backoff sau lỗi (đổi lấy khả năng provider hồi phục),
                        sàn hiển thị 900 ms của màn chuyển trang (đổi lấy việc
                        không giật), khoá giữ 1 giây lúc sửa kế hoạch
    KHÔNG đổi lấy gì    `asyncio.sleep(30)` giả lập điều phối xe;
                        nhịp đọc cố định 1500 ms

Loại thứ hai là thứ người dùng nhìn thấy dưới dạng "màn hình đứng im", và nó
không mua lại điều gì.

Nhịp đọc — vì sao tăng dần chứ không rút ngắn
---------------------------------------------
Đọc cố định 1500 ms nghĩa là MỌI thay đổi đến muộn trung bình 750 ms, kể cả thay
đổi backend trả về gần như tức thì. Nhưng rút ngắn cố định xuống 250 ms thì nhồi
request suốt một lượt Planner dài hàng chục giây — trên máy chủ 0,1 CPU đó là tự
làm chậm chính mình.

Đọc dày lúc đầu rồi thưa dần giữ được cả hai: 0,40 s cho lần đọc đầu, và tới
giây thứ 10 thì tổng số lượt đọc gần bằng nhịp cố định.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_GOC = Path(__file__).resolve().parents[1]


def _poll() -> str:
    return (_GOC / "frontend" / "src" / "lib" / "useWorkflowPolling.ts").read_text(encoding="utf-8")


# --- chờ giả lập ở mock ------------------------------------------------------


def test_the_shuttle_delay_is_configurable_and_short_by_default():
    """30 giây cứng là 30 giây không ai biết hệ thống còn sống hay đã treo."""
    import importlib
    import os

    import src.services.mock.shuttle as shuttle

    os.environ.pop("SHUTTLE_DELAY", None)
    importlib.reload(shuttle)
    assert shuttle.SHUTTLE_BOOKING_DELAY_SECONDS <= 2, shuttle.SHUTTLE_BOOKING_DELAY_SECONDS

    os.environ["SHUTTLE_DELAY"] = "30"
    importlib.reload(shuttle)
    assert shuttle.SHUTTLE_BOOKING_DELAY_SECONDS == 30, "không đặt lại được hành vi chờ thật"
    os.environ.pop("SHUTTLE_DELAY", None)
    importlib.reload(shuttle)


def test_the_delay_never_holds_a_seat_while_it_waits():
    """Chờ nằm SAU kiểm tra và TRƯỚC khi ghi — không giữ chỗ trong lúc chờ."""
    src = (_GOC / "src" / "services" / "mock" / "shuttle.py").read_text(encoding="utf-8")
    than = src[src.index("def book_shuttle") :]

    cho = than.index("asyncio.sleep(SHUTTLE_BOOKING_DELAY_SECONDS)")
    ghi = than.index("store.shuttle_bookings[shuttle_id]")
    kiem = than.index("SHUTTLE_ALREADY_BOOKED")

    assert kiem < cho < ghi, "thứ tự đã đổi: đang giữ chỗ trong lúc chờ, hoặc chờ trước cả khi kiểm"


# --- nhịp đọc ----------------------------------------------------------------


def test_the_first_read_comes_fast():
    code = _poll()

    dau = int(re.search(r"const FIRST_INTERVAL_MS = (\d+)", code).group(1))

    assert dau <= 400, f"lần đọc đầu vẫn chờ {dau} ms — thay đổi tức thì vẫn tới muộn"


def test_the_rate_backs_off_to_the_old_pace():
    """Đọc dày mãi thì nhồi request suốt một lượt Planner dài."""
    code = _poll()

    assert "Math.min(intervalMs, Math.round(nhip.current *" in code, "nhịp không thưa dần"
    assert "nhip.current = FIRST_INTERVAL_MS" in code, "nhịp không được đặt lại khi bắt đầu một lượt mới"


def test_the_ramp_is_not_slower_than_the_fixed_pace():
    """Nhịp tăng dần phải đọc SỚM HƠN nhịp cũ ở mọi lượt trong 10 giây đầu.

    Nếu không thì đây chỉ là đổi một hằng số lấy một hằng số khác, và có lượt
    người dùng phải chờ LÂU HƠN trước.
    """
    code = _poll()
    dau = int(re.search(r"const FIRST_INTERVAL_MS = (\d+)", code).group(1))
    he_so = float(re.search(r"nhip\.current \* ([\d.]+)", code).group(1))
    tran = int(re.search(r"intervalMs = (\d+)", code).group(1))

    t_moi, nhip = 0.0, dau
    moc_moi = []
    for _ in range(10):
        nhip = min(tran, round(nhip * he_so))
        t_moi += nhip / 1000
        moc_moi.append(t_moi)
    moc_cu = [(tran / 1000) * (i + 1) for i in range(10)]

    cham_hon = [(i, a, b) for i, (a, b) in enumerate(zip(moc_moi, moc_cu)) if a > b + 1e-9]
    assert cham_hon == [], f"có lượt đọc CHẬM hơn nhịp cũ: {cham_hon[:3]}"


@pytest.mark.parametrize(
    ("ten", "duong_dan", "cho_phep"),
    [
        ("backoff sau lỗi", "src/executor/executor.py", "đổi lấy khả năng provider hồi phục"),
        ("khoá sửa kế hoạch", "src/db/workflow_repository.py", "đổi lấy an toàn khi hai lệnh sửa gặp nhau"),
    ],
)
def test_the_waits_we_keep_are_the_ones_that_buy_something(ten: str, duong_dan: str, cho_phep: str):
    """Bài kiểm này không ép hành vi — nó ghi lại VÌ SAO hai chỗ chờ còn ở đó.

    Có nó thì lần sau ai đó đi càn quét `asyncio.sleep` sẽ dừng lại đọc lý do,
    thay vì gỡ một backoff và biến một lỗi tạm thời thành một lỗi vĩnh viễn.
    """
    assert (_GOC / duong_dan).exists()
    assert cho_phep
