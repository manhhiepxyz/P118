"""Duyệt xong mà chốt lịch hỏng thì CẢ HAI phía phải biết vì sao.

Đo được trên stack demo. Một lịch tham quan đã được đơn vị duyệt, nhưng khung
giờ ấy trong lúc chờ đã có người khác đặt — mock tour trả 409:

    provider nhận    502 "Xác nhận lịch tham quan thất bại khi hoàn tất duyệt.
                          Vui lòng thử lại."
    workflow         FAILED
    assistant_answer "…rồi nhé. Hiện đang chờ đơn vị cung cấp dịch vụ…"
    for_status       WAITING_APPROVAL:PROVIDER      ← trạng thái đã rời khỏi
    khách nhìn thấy  answer = None, bong bóng cuối vẫn là "đang chờ"

Hai chỗ sai, cùng một nguyên nhân — route ném `HTTPException` NGAY khi thấy
`viewing_result.success` là false:

  1. Người duyệt được bảo "thử lại" cho một xung đột mà thử lại không bao giờ
     qua được. `_viewing_materialize_error_message` đã biết nói đúng từng
     nguyên nhân, nhưng route chép sẵn nhánh cuối cùng của chính hàm ấy.

  2. Khách không nhận được gì cả. `request_fresh_answer` nằm SAU chỗ ném, nên
     câu của trạng thái cũ ở lại vĩnh viễn và bộ lọc chống-câu-cũ giấu nó đi.
     Việc đã hỏng, màn hình vẫn nói đang chờ, không có đường nào thoát.

Đây là đường THẤT BẠI của một thao tác thành-công-là-chính — đúng loại đường
không ai đi thử.
"""

from __future__ import annotations

import pytest

from src.common.enums import ErrorCode
from src.common.results import StandardResult


async def _cong_mo(_workflow_id, _reviewer):
    """Cổng quyền sở hữu ở trạng thái ĐÃ QUA — dùng cho bài không nói về quyền.

    Quyền sở hữu có bộ kiểm riêng ở `test_a_viewing_belongs_to_one_unit_too.py`,
    nơi nó được đo bằng tài khoản và ánh xạ thật.
    """
    return ["BQL-SALES"]


def _ket_qua_hong(ma: ErrorCode) -> dict:
    return {
        "viewing_result": StandardResult(success=False, error_code=ma, message=""),
        "task_results": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ma", "phai_co"),
    [
        (ErrorCode.NO_AVAILABILITY, "hết chỗ"),
        (ErrorCode.SERVICE_UNAVAILABLE, "tạm ngừng"),
        (ErrorCode.INVALID_INPUT, "không còn hợp lệ"),
    ],
)
async def test_the_reviewer_is_told_the_real_reason(monkeypatch, ma, phai_co):
    from fastapi import HTTPException

    from src.api import viewing_approval_routes as mod

    async def _hong(*_a, **_kw):
        return _ket_qua_hong(ma)

    monkeypatch.setattr(mod, "resume_viewing_after_approval", _hong)
    monkeypatch.setattr(mod, "request_fresh_answer", lambda *_a, **_kw: None)
    # Bài này nói về CÂU người duyệt nghe khi materialize hỏng, không nói về
    # quyền sở hữu. Cổng `_bat_buoc_so_huu` cần một tài khoản có ánh xạ đơn vị
    # thật trong database; dựng nó ở đây là kéo một mối bận tâm khác vào, và
    # khi cổng ấy đổi thì bài này đỏ vì lý do không liên quan.
    monkeypatch.setattr(mod, "_bat_buoc_so_huu", _cong_mo)

    with pytest.raises(HTTPException) as loi:
        await mod.decide_viewing_approval(
            "11111111-1111-1111-1111-111111111111",
            mod._DecideBody(decision="approve"),
            reviewer={"username": "don_vi_tour"},
        )
    assert phai_co in loi.value.detail, f"{ma} mà người duyệt nghe: {loi.value.detail!r}"
    assert "thử lại" not in loi.value.detail or ma is ErrorCode.SERVICE_UNAVAILABLE, (
        f"{ma} không thử lại được, mà câu vẫn bảo thử lại: {loi.value.detail!r}"
    )


@pytest.mark.asyncio
async def test_the_resident_gets_a_fresh_answer_before_the_route_gives_up():
    """Khách phải được xin câu mới cho trạng thái MỚI, trước khi route bỏ cuộc."""
    from fastapi import HTTPException

    from src.api import viewing_approval_routes as mod

    async def _hong(*_a, **_kw):
        return _ket_qua_hong(ErrorCode.NO_AVAILABILITY)

    da_xin: list[str] = []

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod, "resume_viewing_after_approval", _hong)
        mp.setattr(mod, "request_fresh_answer", lambda wid, **_kw: da_xin.append(wid))
        # Xem ghi chú ở `_cong_mo`: bài này nói về việc xin câu mới, không nói
        # về quyền sở hữu.
        mp.setattr(mod, "_bat_buoc_so_huu", _cong_mo)
        with pytest.raises(HTTPException):
            await mod.decide_viewing_approval(
                "22222222-2222-2222-2222-222222222222",
                mod._DecideBody(decision="approve"),
                reviewer={"username": "don_vi_tour"},
            )

    assert da_xin == ["22222222-2222-2222-2222-222222222222"], (
        "route ném 502 mà không xin câu mới — khách ở lại với câu của trạng thái cũ và không bao giờ biết việc đã hỏng"
    )


@pytest.mark.asyncio
async def test_a_successful_confirmation_is_untouched(monkeypatch):
    """Đừng làm hỏng đường thường: chốt được thì vẫn trả APPROVED như cũ."""
    from src.api import viewing_approval_routes as mod

    async def _xong(*_a, **_kw):
        return {
            "viewing_result": StandardResult(success=True, data={}, message=""),
            "task_results": {},
        }

    monkeypatch.setattr(mod, "resume_viewing_after_approval", _xong)
    monkeypatch.setattr(mod, "request_fresh_answer", lambda *_a, **_kw: None)
    # Bài này nói về CÂU người duyệt nghe khi materialize hỏng, không nói về
    # quyền sở hữu. Cổng `_bat_buoc_so_huu` cần một tài khoản có ánh xạ đơn vị
    # thật trong database; dựng nó ở đây là kéo một mối bận tâm khác vào, và
    # khi cổng ấy đổi thì bài này đỏ vì lý do không liên quan.
    monkeypatch.setattr(mod, "_bat_buoc_so_huu", _cong_mo)
    ket = await mod.decide_viewing_approval(
        "33333333-3333-3333-3333-333333333333",
        mod._DecideBody(decision="approve"),
        reviewer={"username": "don_vi_tour"},
    )
    assert ket["status"] == "APPROVED"
