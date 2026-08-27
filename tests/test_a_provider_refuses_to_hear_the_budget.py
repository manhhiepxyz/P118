"""Đơn vị cung cấp TỪ CHỐI nhận ngân sách của khách — hàng rào thứ hai.

P-118 đã có allowlist ở phía gửi (`quote.payload_gui_provider`). Nhưng luật
"ngân sách không rời khỏi P-118" quan trọng đến mức một hàng rào là chưa đủ:
allowlist nằm cùng phía với đoạn mã sẽ vi phạm nó, và một field mới thêm vào
đường gửi có thể vòng qua nó mà không ai thấy.

Hàng rào ở phía NHẬN thì không. Nếu một ngày nào đó P-118 rò ngân sách, provider
trả 422 và cả lượt hỏng ầm ĩ — thay vì lặng lẽ trả về một con số sát ngân sách
và mọi người tin rằng đó là giá thị trường.

File này chạy qua HTTP thật của mock provider (ASGI in-process), không phải qua
một object giả — vì thứ đang được kiểm chính là hợp đồng ở biên.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from src.mock.service_providers import DON_VI_CHUYEN_NHA, con_lich, gia_chuyen_nha
from src.services.mock.resident_services import resident_services_app

NGAY = date.today() + timedelta(days=30)
YEU_CAU = {
    "move_date": NGAY.isoformat(),
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}


@pytest.fixture
def client():
    with TestClient(resident_services_app) as c:
        yield c


def _duong_dan(don_vi: str) -> str:
    return f"/api/resident-services/moves/quotes/{don_vi}"


def test_a_quote_request_carrying_a_budget_is_refused(client):
    """`max_price` trong body → 422. Không phải bỏ qua, không phải dùng."""
    response = client.post(_duong_dan("MOV-01"), json={**YEU_CAU, "max_price": 450_000})
    assert response.status_code == 422, response.text


def test_any_unexpected_field_is_refused(client):
    """`extra="forbid"` áp cho MỌI field lạ, không riêng `max_price`.

    Chặn đúng một tên là chặn đúng một lần: lượt rò tiếp theo sẽ mang tên khác
    (`ngan_sach`, `budget`, `gia_toi_da`). Mặc định phải là từ chối.
    """
    for ten in ("ngan_sach", "budget", "gia_toi_da", "customer_note"):
        response = client.post(_duong_dan("MOV-01"), json={**YEU_CAU, ten: 1})
        assert response.status_code == 422, f"{ten} lọt qua: {response.status_code}"


def test_a_quote_says_who_issued_it_and_until_when(client):
    """Bốn dữ kiện bắt buộc; thiếu một cái là chứng từ không dùng được."""
    don_vi = next(d for d in DON_VI_CHUYEN_NHA if con_lich(d, NGAY))
    response = client.post(_duong_dan(don_vi.provider_id), json=dict(YEU_CAU))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["service_provider_id"] == don_vi.provider_id
    assert data["external_quote_id"], "báo giá không mang mã của đơn vị"
    assert data["currency"] == "VND"
    assert data["valid_until"], "báo giá không nói mình sống tới bao giờ"


def test_the_price_matches_the_catalogue_for_the_exact_request(client):
    """Giá trả về là giá TÍNH RA cho đúng yêu cầu, không phải một hằng số."""
    don_vi = next(d for d in DON_VI_CHUYEN_NHA if con_lich(d, NGAY))
    re = client.post(_duong_dan(don_vi.provider_id), json=dict(YEU_CAU)).json()["data"]["amount"]
    dat = client.post(
        _duong_dan(don_vi.provider_id),
        json={**YEU_CAU, "move_vehicle": "truck", "needs_elevator": True, "needs_loading_support": True},
    ).json()["data"]["amount"]

    assert re == gia_chuyen_nha(don_vi, move_vehicle="van", needs_elevator=False, needs_loading_support=False)
    assert dat > re, "yêu cầu nhiều hơn mà giá không tăng"


def test_each_quote_gets_its_own_identity(client):
    """Hai lượt xin → hai mã khác nhau, kể cả cùng yêu cầu.

    Mã trùng nghĩa là hai chứng từ không phân biệt được, và lúc tranh chấp
    không ai biết cái nào đã được xác nhận.
    """
    don_vi = next(d for d in DON_VI_CHUYEN_NHA if con_lich(d, NGAY))
    ma = {
        client.post(_duong_dan(don_vi.provider_id), json=dict(YEU_CAU)).json()["data"]["external_quote_id"]
        for _ in range(3)
    }
    assert len(ma) == 3


def test_a_day_off_is_a_business_answer_not_an_outage(client):
    """Đơn vị bận → 200 kèm `NO_AVAILABILITY`, KHÔNG phải 4xx/5xx.

    4xx/5xx làm connector đọc nó thành sự cố và retry — retry một ngày nghỉ thì
    lần nào cũng nghỉ, và cả lượt hỏi giá chậm lại vì một câu trả lời đã rõ.
    """
    don_vi = next(d for d in DON_VI_CHUYEN_NHA if d.nghi_thu)
    ngay_nghi = next(
        NGAY + timedelta(days=i) for i in range(14) if (NGAY + timedelta(days=i)).weekday() in don_vi.nghi_thu
    )

    response = client.post(_duong_dan(don_vi.provider_id), json={**YEU_CAU, "move_date": ngay_nghi.isoformat()})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "NO_AVAILABILITY"
    assert body["data"] is None, "lời từ chối vẫn kèm một con số"


def test_an_unknown_provider_is_not_quoted_for(client):
    """Đơn vị không có trong danh mục → 404, không phải một giá mặc định."""
    assert client.post(_duong_dan("MOV-KHONG-CO"), json=dict(YEU_CAU)).status_code == 404


def test_a_past_date_is_refused_before_any_price_is_computed(client):
    hom_qua = (date.today() - timedelta(days=1)).isoformat()
    assert client.post(_duong_dan("MOV-01"), json={**YEU_CAU, "move_date": hom_qua}).status_code == 422
