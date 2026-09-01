"""Connector báo giá: ép kiểu ở BIÊN, và không bịa ra gì khi đầu kia trả rác.

Biên là nơi duy nhất dữ liệu ngoài trở thành dữ liệu trong. Một `amount` dạng
`"470000"` hay `470000.5` lọt qua đây thì nó đi tiếp vào chứng từ, vào hoá đơn,
và ra tới màn hình của khách — lúc ấy không ai còn biết nó đến từ đâu.

`xin_bao_gia_chuyen_nha` KHÔNG phải một tool: nó không tạo cam kết, không nằm
trong TaskPlan, không đi qua cổng duyệt. Nên nó cũng không được có tên trong
`tool_names` — cho vào đó nghĩa là Planner có thể xếp "xin báo giá" thành một
bước, và Validator phải nghĩ ra hợp đồng input/output cho một việc thuần đọc.
"""

from __future__ import annotations

import httpx
import pytest

from src.connectors.resident_services import ResidentServicesConnector

DU = {
    "external_quote_id": "QMOV-001",
    "service_provider_id": "MOV-01",
    "amount": 430_000,
    "currency": "VND",
    "valid_until": "2026-09-30T10:00:00+00:00",
}


def _connector(handler) -> ResidentServicesConnector:
    transport = httpx.MockTransport(handler)
    return ResidentServicesConnector(client=httpx.AsyncClient(transport=transport))


def _tra(body: dict, status: int = 200):
    return lambda request: httpx.Response(status, json=body)


def test_quoting_is_not_a_tool():
    assert "quote_move" not in ResidentServicesConnector().tool_names
    assert "xin_bao_gia_chuyen_nha" not in ResidentServicesConnector().tool_names


@pytest.mark.asyncio
async def test_a_good_quote_comes_back_whole():
    ket_qua = await _connector(_tra({"success": True, "data": DU})).xin_bao_gia_chuyen_nha("MOV-01", {})
    assert ket_qua.success
    assert ket_qua.data == DU


@pytest.mark.asyncio
async def test_a_numeric_string_amount_becomes_an_integer():
    """`"430000"` là một con số hợp lệ về giá trị nhưng sai về kiểu. Ép ở biên."""
    ket_qua = await _connector(_tra({"success": True, "data": {**DU, "amount": "430000"}})).xin_bao_gia_chuyen_nha(
        "MOV-01", {}
    )
    assert ket_qua.success
    assert ket_qua.data["amount"] == 430_000
    assert isinstance(ket_qua.data["amount"], int)


@pytest.mark.asyncio
@pytest.mark.parametrize("xau", [None, "bốn trăm ba mươi nghìn", {}, []])
async def test_an_amount_that_is_not_a_number_is_a_broken_contract(xau):
    """Không ép được thì đó là response sai hợp đồng, không phải số cần làm tròn."""
    ket_qua = await _connector(_tra({"success": True, "data": {**DU, "amount": xau}})).xin_bao_gia_chuyen_nha(
        "MOV-01", {}
    )
    assert not ket_qua.success
    assert ket_qua.error_code == "UNKNOWN_EXTERNAL_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize("thieu", sorted(DU))
async def test_a_quote_missing_any_required_field_is_refused(thieu):
    """Năm trường, không trường nào tuỳ chọn.

    Thiếu `external_quote_id` thì không đối chiếu được lúc tranh chấp; thiếu
    `valid_until` thì không biết nó sống tới bao giờ. Cả hai đều biến chứng từ
    thành một con số.
    """
    ket_qua = await _connector(
        _tra({"success": True, "data": {k: v for k, v in DU.items() if k != thieu}})
    ).xin_bao_gia_chuyen_nha("MOV-01", {})
    assert not ket_qua.success, f"thiếu {thieu} mà vẫn qua"


@pytest.mark.asyncio
async def test_a_business_refusal_keeps_its_canonical_code():
    """`NO_AVAILABILITY` đi nguyên vẹn lên tầng trên để nó biết đơn vị nào rớt."""
    ket_qua = await _connector(
        _tra({"success": False, "error_code": "NO_AVAILABILITY", "message": "bận"})
    ).xin_bao_gia_chuyen_nha("MOV-01", {})
    assert not ket_qua.success
    assert ket_qua.error_code == "NO_AVAILABILITY"


@pytest.mark.asyncio
async def test_a_timeout_is_retryable_but_not_a_quote():
    def no(request):
        raise httpx.TimeoutException("quá lâu")

    ket_qua = await _connector(no).xin_bao_gia_chuyen_nha("MOV-01", {})
    assert not ket_qua.success
    assert ket_qua.error_code == "SERVICE_TIMEOUT"
    assert ket_qua.retryable is True


@pytest.mark.asyncio
async def test_the_request_goes_to_the_provider_that_was_asked():
    """Mã đơn vị nằm trong ĐƯỜNG DẪN, không trong body.

    Trong body thì nó là một field nữa mà bên kia có thể bỏ qua — và lúc ấy một
    lượt hỏi MOV-03 được MOV-01 trả lời mà không ai thấy gì bất thường.
    """
    da_goi: list[str] = []

    def ghi_lai(request):
        da_goi.append(str(request.url.path))
        return httpx.Response(200, json={"success": True, "data": {**DU, "service_provider_id": "MOV-03"}})

    await _connector(ghi_lai).xin_bao_gia_chuyen_nha("MOV-03", {})
    assert da_goi == ["/api/resident-services/moves/quotes/MOV-03"]


@pytest.mark.asyncio
async def test_the_connector_sends_exactly_what_it_was_given():
    """Connector KHÔNG tự lọc lại payload.

    Luật "gửi gì" sống ở tầng nghiệp vụ (`quote.payload_gui_provider`). Lọc lần
    hai ở đây tạo ra nơi thứ hai quyết định cùng một việc, và hai nơi thì lệch.
    Bù lại, provider từ chối field lạ bằng `extra="forbid"` — hàng rào ở phía
    không do P-118 kiểm soát, xem `test_a_provider_refuses_to_hear_the_budget`.
    """
    da_gui: list[bytes] = []

    def ghi_lai(request):
        da_gui.append(request.content)
        return httpx.Response(200, json={"success": True, "data": DU})

    await _connector(ghi_lai).xin_bao_gia_chuyen_nha("MOV-01", {"move_date": "2026-09-30"})
    assert b'"move_date"' in da_gui[0]
