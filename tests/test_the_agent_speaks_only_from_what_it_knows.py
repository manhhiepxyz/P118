"""Snapshot: code dựng sự thật, model chỉ được nói lại thứ có trong đó.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_agent_speaks_only_from_what_it_knows.py

BA LỖI CÙNG MỘT GỐC, đều đo được trên stack demo:

    "có những dự án nào"   → "Hiện tại mình có các dự án: Khu A, Khu B, Khu C."
                             ("Khu A/B/C" là KHU ĐỖ XE. Không dự án nào tên vậy.)

    "xong chưa" / "giờ tôi phải làm gì"
                           → xuống Planner 3–7 giây rồi trả về vô nghĩa

    đơn vị từ chối bảo trì → màn hình chỉ nói "Bước này đã được huỷ trước khi
                             hoàn tất", trong khi service_approvals đang giữ
                             NO_AVAILABILITY · "Không có nhân viên rảnh vào giờ này"

Không phải model kém. Ta giao cho nó việc TRẢ LỜI rồi không đưa DỮ LIỆU. Với
câu hỏi danh mục, nó lấy thứ gần nhất trong vốn từ của mình.

Snapshot không phải nguồn sự thật mới. Nó gom lại đúng những nguồn đang dùng:

    danh mục dịch vụ   ← `_CAPABILITY_CATALOGUE` + `account_state`
    danh sách dự án    ← `src/common/projects.PROJECTS`
    các bước           ← chính view người dùng đang nhìn (`_task_presentation`)
    lý do từ chối      ← `service_approvals.reject_code/reject_reason`

Dựng một bản sao song song là tạo ra cách thứ hai để nói sai — nên mọi thứ ở
đây đều đến từ caller, module này KHÔNG đọc database.

`known_values()` có mặt cho chặng sau: cổng kiểm câu trả lời. Ngày, số tiền,
mã đơn, tên dự án — bốn lớp giá trị gây hại thật khi sai — phải nằm trong tập
này thì câu mới được gửi đi.
"""

from __future__ import annotations

import pytest

from src.orchestration.snapshot import build_snapshot

CATALOGUE = [
    {"name": "Đặt lịch tham quan dự án", "description": "Chọn dự án, ngày và giờ.", "requires_resident": False},
    {"name": "Đăng ký phương tiện và chỗ đỗ xe", "description": "Khu A hoặc Khu B.", "requires_resident": True},
]


class _Chi:
    def __init__(self, label, value): self.label = label; self.value = value


class _Buoc:
    def __init__(self, tool, title, status, details=(), message=""):
        self.tool = tool; self.title = title; self.status = status
        self.details = [_Chi(l, v) for l, v in details]; self.message = message
        self.task_id = "T1"


class _View:
    def __init__(self, tasks, status="SUCCESS"):
        self.tasks = tasks; self.status = status


def test_the_project_list_comes_from_the_catalogue_not_from_memory():
    """"Khu A" là khu đỗ xe. Nó không bao giờ được xuất hiện như một dự án."""
    snap = build_snapshot(account_state="prospect", capabilities=CATALOGUE)
    assert "Vinhomes Pearl Bay" in snap.projects
    assert len(snap.projects) == 7
    for khu in ("Khu A", "Khu B", "Khu C", "ZONE_A"):
        assert khu not in snap.projects


# LUẬT AN TOÀN QUAN TRỌNG NHẤT CỦA FILE.
#
# Snapshot đi thẳng vào prompt. Nếu nó nói một dịch vụ đang MỞ cho tài khoản
# chưa xác minh căn hộ, model sẽ mời họ dùng, họ gõ theo, và bị từ chối ở tầng
# dưới — đúng lỗi đã đo được với chuỗi cứng quảng cáo hai dịch vụ không tồn tại.
def test_an_unverified_account_never_sees_a_resident_service_as_open():
    snap = build_snapshot(account_state="prospect", capabilities=CATALOGUE)
    mo = [s.name for s in snap.services if s.open]
    khoa = [s.name for s in snap.services if not s.open]
    assert "Đặt lịch tham quan dự án" in mo
    assert "Đăng ký phương tiện và chỗ đỗ xe" in khoa
    assert "Đăng ký phương tiện và chỗ đỗ xe" not in mo


def test_a_resident_sees_everything_open():
    snap = build_snapshot(account_state="resident", capabilities=CATALOGUE)
    assert all(s.open for s in snap.services)


# Dịch vụ bị khoá vẫn được NÊU TÊN, chỉ là nêu kèm trạng thái khoá. Giấu đi thì
# người dùng không biết nó tồn tại để đi xác minh căn hộ — cùng lý do
# `_capability_reply` cố ý liệt kê cả phần khoá.
def test_a_locked_service_is_still_named():
    snap = build_snapshot(account_state="prospect", capabilities=CATALOGUE)
    assert "Đăng ký phương tiện và chỗ đỗ xe" in [s.name for s in snap.services]


def test_the_steps_carry_what_the_screen_already_shows():
    """"xong chưa" trả lời được vì snapshot biết từng bước đang ở đâu."""
    view = _View([
        _Buoc("schedule_property_viewing", "Đặt lịch tham quan", "SUCCESS",
              [("Thời gian", "2026-08-24 09:30"), ("Dự án", "Vinhomes Pearl Bay")]),
        _Buoc("book_parking", "Đặt chỗ đỗ xe", "WAITING_APPROVAL", [("Số tiền", "150.000 VND")]),
    ])
    snap = build_snapshot(account_state="resident", capabilities=CATALOGUE, view=view)
    theo_ten = {b.title: b for b in snap.steps}
    assert theo_ten["Đặt lịch tham quan"].status == "SUCCESS"
    assert theo_ten["Đặt lịch tham quan"].details["Thời gian"] == "2026-08-24 09:30"
    assert theo_ten["Đặt chỗ đỗ xe"].status == "WAITING_APPROVAL"


# Nguyên văn ca bạn báo: provider nói rõ vì sao, và câu đó chết trong bảng duyệt.
def test_a_refusal_carries_the_reason_the_provider_gave():
    snap = build_snapshot(
        account_state="resident",
        capabilities=CATALOGUE,
        view=_View([_Buoc("create_maintenance_request", "Yêu cầu bảo trì", "CANCELLED")]),
        refusals=[{"title": "Yêu cầu bảo trì", "code": "NO_AVAILABILITY",
                   "reason": "Không có nhân viên rảnh vào giờ này"}],
    )
    (tu_choi,) = snap.refusals
    assert tu_choi.reason == "Không có nhân viên rảnh vào giờ này"
    assert tu_choi.code == "NO_AVAILABILITY"
    assert "Không có nhân viên rảnh vào giờ này" in snap.as_text()


def test_the_text_given_to_the_model_contains_every_fact():
    view = _View([_Buoc("schedule_property_viewing", "Đặt lịch tham quan", "SUCCESS",
                        [("Thời gian", "2026-08-24 09:30")])])
    text = build_snapshot(account_state="prospect", capabilities=CATALOGUE, view=view).as_text()
    assert "Vinhomes Pearl Bay" in text
    assert "Đặt lịch tham quan" in text
    assert "2026-08-24 09:30" in text
    assert "Đăng ký phương tiện và chỗ đỗ xe" in text


# Dành cho cổng kiểm ở chặng sau: bốn lớp giá trị gây hại thật khi sai.
def test_known_values_holds_every_concrete_value_the_answer_may_cite():
    view = _View([_Buoc("book_parking", "Đặt chỗ đỗ xe", "SUCCESS",
                        [("Thời gian", "2026-08-24"), ("Số tiền", "150.000 VND"),
                         ("Mã đơn", "BOOK-77")])])
    biet = build_snapshot(account_state="resident", capabilities=CATALOGUE, view=view).known_values()
    assert "2026-08-24" in biet
    assert "150.000 VND" in biet
    assert "BOOK-77" in biet
    assert "Vinhomes Pearl Bay" in biet
    assert "Khu C" not in biet


def test_an_empty_journey_still_gives_the_catalogue():
    """Chưa có yêu cầu nào thì vẫn trả lời được "bạn làm được gì"."""
    snap = build_snapshot(account_state="prospect", capabilities=CATALOGUE)
    assert snap.steps == ()
    assert snap.services
    assert snap.projects
