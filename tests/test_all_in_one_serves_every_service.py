"""Bản gộp phải phục vụ ĐÚNG những gì chín service rời phục vụ.

Vì sao cần bản gộp
------------------
`docker-compose` chạy 9 mock provider thành 9 service. Trên Render gói miễn phí,
750 giờ instance chia cho MỌI service — 9 mock cộng backend không cùng sống nổi
một tháng. Không gộp thì không deploy được, và mọi tối ưu độ trễ bên trong tiến
trình đều chưa có ý nghĩa.

Vì sao KHÔNG dùng `src/mock/main.py`
------------------------------------
Nó đã có sẵn và trông như câu trả lời. Nhưng nó là một implementation KHÁC, viết
trước và đã lệch: thiếu 9 endpoint connector đang gọi, còn giữ `/api/tours/bookings`
mà không connector nào dùng. Gộp bằng nó là bảo trì hai bản mock cho một hợp
đồng — và bản thứ hai sẽ lệch tiếp.

Bài kiểm này ép bản gộp phải là ĐÚNG hợp nhất của các app đang chạy, nên thêm
một endpoint ở bất kỳ service nào cũng tự có mặt; quên thì đỏ.
"""

from __future__ import annotations

import pytest

from src.services.mock.all_in_one import _SERVICES
from src.services.mock.all_in_one import app as gop

_BO_QUA = {"/health", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


def _paths(an_app) -> set[str]:
    return {p for p in an_app.openapi()["paths"] if p not in _BO_QUA}


def test_every_service_route_is_present():
    thieu: dict[str, set[str]] = {}
    for ten, con in _SERVICES:
        con_thieu = _paths(con) - _paths(gop)
        if con_thieu:
            thieu[ten] = con_thieu

    assert thieu == {}, f"bản gộp thiếu route của: {thieu}"


def test_it_invents_nothing():
    """Bản gộp không được có route nào không thuộc service nào.

    Một route thừa nghĩa là bản gộp đang tự viết hợp đồng — đúng cách
    `src/mock/main.py` đã lệch khỏi thực tế.
    """
    hop_nhat: set[str] = set()
    for _ten, con in _SERVICES:
        hop_nhat |= _paths(con)

    assert _paths(gop) - hop_nhat == set()


@pytest.mark.parametrize(
    "duong_dan",
    [
        "/api/residents",
        "/api/vehicles",
        "/api/parking/bookings",
        "/api/parking/bookings/{booking_id}/zone",
        "/api/parking/bookings/{booking_id}/cancel",
        "/api/payments",
        "/api/property/viewings",
        "/api/property/viewings/{viewing_id}/cancel",
        "/api/properties/search",
        "/api/projects/interests",
        "/api/shuttles/bookings",
        "/api/shuttles/bookings/{shuttle_id}/cancel",
        "/api/resident-services/maintenance",
        "/api/resident-services/maintenance/{maintenance_id}/cancel",
        "/api/resident-services/moves",
        "/api/resident-services/moves/{move_request_id}/cancel",
    ],
)
def test_every_endpoint_a_connector_calls_is_there(duong_dan: str):
    """Danh sách này là những đường connector THẬT SỰ gọi.

    Bản gộp thiếu một cái là một tool chết trên môi trường đã deploy — và nó chỉ
    lộ ra khi có người dùng thật bấm vào đúng dịch vụ ấy.
    """
    assert duong_dan in _paths(gop), f"bản gộp không phục vụ {duong_dan}"


def test_the_stale_monolith_is_not_what_we_ship():
    """`src/mock/main.py` KHÔNG được dùng làm bản gộp.

    Giữ phép kiểm này để lần sau ai đó thấy hai file gộp thì biết cái nào là
    thật — và biết vì sao cái kia không dùng được.
    """
    from src.mock.main import app as cu

    thieu = _paths(gop) - _paths(cu)

    assert thieu, "hai bản đã trùng nhau — kiểm lại xem còn cần cả hai không"


@pytest.mark.asyncio
async def test_a_real_call_goes_through_the_composed_app():
    """Không chỉ có route — nó phải CHẠY, qua đúng connector thật."""
    from httpx import ASGITransport, AsyncClient

    from src.connectors.property import PropertyConnector

    khach = AsyncClient(transport=ASGITransport(app=gop))
    connector = PropertyConnector(base_url="http://mock", client=khach)
    ket_qua = await connector.execute(
        "search_properties",
        {
            "transaction_type": "buy",
            "property_type": "apartment",
            "residential_area": "Vinhomes Ocean Park",
            "max_price": 5_000_000_000,
        },
    )
    await khach.aclose()

    assert ket_qua.success is True, ket_qua.message
    assert "properties" in (ket_qua.data or {})
