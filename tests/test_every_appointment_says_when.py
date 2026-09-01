"""Mọi dịch vụ CÓ HẸN đều phải nói mốc thời gian bằng cùng một tên.

Lỗi đo được
-----------
Khách xem một lịch tham quan đã xong thì thấy thẻ đầy đủ — ngày lớn, giờ, khu
vực đón tiếp, người phụ trách, nút thao tác. Cũng khách ấy xem một chỗ đỗ xe đã
giữ thì chỉ thấy một danh sách dữ kiện trơn.

Không phải vì chỗ đỗ xe thiếu dữ liệu. `ResultSummary` chọn bố cục theo dữ kiện
`Thời gian`, và cùng một khái niệm đang mang BỐN tên:

    schedule_property_viewing   Thời gian
    schedule_move               Thời gian
    book_parking                Ngày đặt          ← không khớp
    create_maintenance_request  Lịch hẹn          ← không khớp
    book_shuttle                Ngày đón + Giờ đón ← không khớp, và tách đôi

Ba dịch vụ dưới có mốc thời gian thật mà giao diện không nhận ra.

Vì sao kiểm bằng tên nhãn chứ không bằng tên tool
-------------------------------------------------
Cổng `if (!when)` trong `ResultSummary` tồn tại vì một lỗi đã đo được: trước
khi có nó, MỌI kết quả đã xong đều dựng thẻ mang từ vựng của lịch tham quan.
Một lần ĐĂNG KÝ TƯ VẤN (`INT-003`) — thứ không có giờ, không điểm gặp, không gì
để đổi — hiện ra "Lịch tham quan · Đổi lịch · Huỷ lịch · Trước buổi tham quan".

Nên "dịch vụ nào có thẻ hẹn" phải trả lời bằng DỮ KIỆN CÓ THẬT, không bằng một
danh sách tên tool viết tay ở tầng giao diện.
"""

from __future__ import annotations

import pytest

from src.common.results import StandardResult

# Dịch vụ có hẹn -> `result_data` tối thiểu để dựng được mốc thời gian.
_CO_HEN: dict[str, dict] = {
    "schedule_property_viewing": {"viewing_id": "VIEW-1", "viewing_date": "2029-05-04", "viewing_time": "10:30"},
    "schedule_move": {"move_id": "MOVE-1", "move_date": "2029-05-04", "move_time": "08:00"},
    "book_parking": {"booking_id": "BOOK-1", "booking_date": "2029-05-04", "parking_zone": "ZONE_A"},
    "create_maintenance_request": {
        "maintenance_id": "MNT-1",
        "appointment_date": "2029-05-04",
        "appointment_time": "14:00",
    },
    "book_shuttle": {"shuttle_id": "SHUTTLE-1", "tour_date": "2029-05-04", "pickup_time": "07:30"},
}

# Dịch vụ KHÔNG có hẹn. Chúng phải KHÔNG sinh ra `Thời gian`, nếu không giao
# diện dựng cho chúng một thẻ hẹn và mời khách đổi một buổi hẹn không tồn tại.
_KHONG_HEN: dict[str, dict] = {
    "register_property_interest": {"interest_id": "INT-003", "project_name": "Vinhomes Green Paradise"},
    "register_vehicle": {"vehicle_id": "VEH-1", "plate_number": "51H-12345"},
    "register_resident": {"resident_id": "RES-1"},
    "pay_fee": {"payment_id": "PAY-1", "payment_status": "PAID"},
}

# Ô người dùng tự chọn nằm ở `inputs`, không ở `result_data` — provider không
# trả chúng về. `register_property_interest` là ca đáng kiểm nhất: nó CÓ một
# giờ (`preferred_contact_time`), và giờ ấy tuyệt đối không được đọc thành mốc
# hẹn. "Gọi cho tôi lúc 10:00" không phải một buổi gặp; không có gì để đổi,
# không có gì để huỷ, không có chỗ nào để đến.
_INPUT_CUA_KHACH: dict[str, dict] = {
    "register_property_interest": {"preferred_contact_time": "10:00", "interest_type": "buy"},
}


def _nhan(tool: str, data: dict) -> list[str]:
    from src.api.routes import _task_presentation

    class _Task:
        def __init__(self) -> None:
            self.tool = tool
            self.task_id = "T1"
            self.input = _INPUT_CUA_KHACH.get(tool, {})

    _title, _message, details = _task_presentation(_Task(), StandardResult(success=True, data=data), {})
    return [item.label for item in details]


@pytest.mark.parametrize("tool", sorted(_CO_HEN))
def test_a_service_with_an_appointment_says_when(tool: str):
    assert "Thời gian" in _nhan(tool, _CO_HEN[tool]), f"{tool} có hẹn nhưng không nói mốc thời gian"


@pytest.mark.parametrize("tool", sorted(_KHONG_HEN))
def test_a_service_without_an_appointment_stays_quiet(tool: str):
    """`INT-003` là ca đã đo được — xem docstring đầu file."""
    assert "Thời gian" not in _nhan(tool, _KHONG_HEN[tool]), f"{tool} không có hẹn mà vẫn dựng mốc thời gian"


def test_a_contact_hour_is_not_an_appointment():
    """ "Gọi cho tôi lúc 10:00" có giờ, nhưng không phải một buổi hẹn."""
    nhan = _nhan("register_property_interest", _KHONG_HEN["register_property_interest"])

    assert "Giờ liên hệ" in nhan, f"giờ khách hẹn gọi biến mất khỏi Lịch sử: {nhan}"
    assert "Thời gian" not in nhan, f"giờ gọi lại bị đọc thành mốc hẹn: {nhan}"


def test_the_pickup_time_is_not_a_second_field():
    """Xe đưa đón từng tách `Ngày đón` và `Giờ đón` — một mốc, hai ô."""
    nhan = _nhan("book_shuttle", _CO_HEN["book_shuttle"])

    assert "Ngày đón" not in nhan and "Giờ đón" not in nhan, nhan


# --- phía giao diện: thẻ không được gọi tên một dịch vụ cụ thể ----------------


def _bo_ghi_chu(code: str) -> str:
    """Bỏ ghi chú khỏi mã TSX.

    Ghi chú NÓI VỀ mã cũ — chúng chép lại đúng những chuỗi mà các bài kiểm dưới
    đây đang cấm, vì đó là cách một ghi chú giải thích "trước đây sai thế nào".
    Không bỏ chúng ra thì mọi bài kiểm loại này đỏ ngay khi ai đó viết một ghi
    chú trung thực.
    """
    con_lai, i = [], 0
    while i < len(code):
        for mo, dong in (("{/*", "*/}"), ("/*", "*/")):
            if code.startswith(mo, i):
                ket = code.find(dong, i)
                i = len(code) if ket < 0 else ket + len(dong)
                break
        else:
            con_lai.append(code[i])
            i += 1
    return "".join(con_lai)


def test_the_result_card_never_names_one_service():
    """Thẻ kết quả phục vụ 5 dịch vụ; chữ cứng của một dịch vụ là lỗi `INT-003`.

    Frontend không có hạ tầng test, nên kiểm bằng cách đọc file TSX — cùng kỹ
    thuật `tests/test_every_refusal_carries_a_cause.py` đã dùng.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "workspace" / "ResultSummary.tsx"
    code = _bo_ghi_chu(src.read_text(encoding="utf-8"))

    for ten in ("tham quan", "đỗ xe", "bảo trì", "chuyển nhà", "tư vấn", "đưa đón"):
        assert ten not in code.casefold(), f'thẻ kết quả gọi tên một dịch vụ cụ thể: "{ten}"'
    assert "title={task.title}" in code, "tiêu đề thẻ không lấy từ tên dịch vụ backend đặt"


def test_every_appointment_gets_its_own_card():
    """Hai buổi hẹn trong một yêu cầu thì phải có HAI thẻ, không phải một.

    Lỗi đo được: `WorkflowPage` chọn thẻ bằng

        [...successWithDetails].reverse().find((t) => t.details?.some(...))

    `reverse().find()` trả về "bước có `Thời gian` đứng SAU CÙNG trong mảng", và
    thứ tự ấy là thứ tự Planner xếp bước — không phải mức quan trọng, không phải
    mốc gần nhất. Một yêu cầu "đặt chỗ đỗ xe + đặt lịch tham quan" có hai mốc;
    cái nào lên thẻ là ngẫu nhiên, và buổi còn lại rơi xuống mục "Các bước", gập
    sau nút "Chi tiết": không địa điểm, không người đón tiếp, không `.ics`.

    Đây không phải ca giả định — chính codebase mô hình hoá nó:
    `ScheduleConflictAction` có `task_a`/`task_b` vì hai bước trong CÙNG một
    workflow đụng giờ nhau.

    Frontend không có hạ tầng test nên kiểm bằng cách đọc file TSX — cùng kỹ
    thuật `tests/test_every_refusal_carries_a_cause.py` đã dùng.
    """
    from pathlib import Path

    goc = Path(__file__).resolve().parents[1] / "frontend" / "src"
    trang = _bo_ghi_chu((goc / "pages" / "WorkflowPage.tsx").read_text(encoding="utf-8"))

    assert "resultTasks.map(" in trang, "trang chỉ dựng MỘT thẻ kết quả"
    assert ".reverse().find(" not in trang, "vẫn chọn một buổi hẹn theo thứ tự mảng"

    # Mỗi thẻ tự đặt tên sự kiện `.ics` của nó. Truyền một tiêu đề chung từ
    # trang xuống nghĩa là hai mốc vào lịch điện thoại dưới cùng một cái tên —
    # và đó là tên của một trong hai dịch vụ.
    card = _bo_ghi_chu((goc / "components" / "workspace" / "ResultSummary.tsx").read_text(encoding="utf-8"))
    assert "journeyTitle" not in card, "thẻ vẫn nhận tiêu đề chung của cả yêu cầu"
    assert "downloadIcs(task.title" in card, "tên sự kiện .ics không lấy từ chính buổi hẹn này"

    # Tên tệp phải khác nhau giữa hai thẻ, nếu không trình duyệt lưu thành
    # `... (1).ics` và khách không biết cái nào là cái nào.
    assert "p118-${id}.ics" in card, "mọi buổi hẹn tải về cùng một tên tệp"
