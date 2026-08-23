"""Từ chối dịch vụ nào cũng phải mang nguyên nhân canonical — kể cả tham quan.

Lỗi đo được trên stack thật
---------------------------
Đơn vị mở hộp thoại từ chối cho một lịch tham quan, chọn nguyên nhân, gõ lý do,
bấm xác nhận — và nhận lại:

    422  "Yêu cầu chưa hợp lệ. Bạn kiểm tra lại thông tin vừa nhập giúp mình nhé."

Sáu dịch vụ còn lại từ chối được bình thường. Chỉ lịch tham quan hỏng, vì nó là
dịch vụ DUY NHẤT đi qua một route riêng (`/viewing-approvals/{id}/decide`), và
`ProviderReviewPage.decideService` CẮT `reject_code` đi ở đúng nhánh ấy — theo
một ghi chú viết từ lúc route đó chưa nhận mã. Backend đã nhận mã từ lâu, và
còn BẮT BUỘC nó; ghi chú thì không ai đọc lại.

Vì sao kiểm ở đây, bằng cách đọc file TypeScript
------------------------------------------------
Frontend không có hạ tầng test (0 file `*.test.*`), nên không có chỗ nào khác
bắt được một lệch pha giữa hai bên. Cùng kỹ thuật mà
`tests/test_frontend_error_messages.py` đã dùng để đối chiếu danh sách câu lỗi.

Bài kiểm này thô — nó đọc chữ, không chạy code. Nó không chứng minh browser gửi
đúng; nó chỉ chặn đúng cái cách đã hỏng một lần: một nhánh gửi thiếu mã.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.orchestration.service_approval import REJECT_CODES, SERVICE_GATED_TOOLS

_UI = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "ProviderReviewPage.tsx"


def _decide_service_body() -> str:
    """Thân hàm `decideService` — nơi hình dạng body được quyết định."""
    text = _UI.read_text(encoding="utf-8")
    start = text.index("async function decideService(")
    end = text.index("async function confirmReject(", start)
    return text[start:end]


def test_the_viewing_queue_sends_a_reject_code_too():
    """Đây là lỗi được báo: nhánh tham quan gửi thiếu mã."""
    than = _decide_service_body()
    nhanh = than[than.index("decideViewingApproval(") :]
    nhanh = nhanh[: nhanh.index("} else {")]

    assert "reject_code" in nhanh, f"nhánh lịch tham quan gửi từ chối mà không mang mã:\n{nhanh}"


def test_no_branch_sends_a_reason_without_a_code():
    """Bất kỳ nhánh nào gửi `reject_reason` cũng phải gửi `reject_code`.

    Viết theo HÌNH DẠNG chứ không theo tên dịch vụ: một route thứ ba thêm vào
    sau này sẽ rơi vào đúng cái bẫy vừa rồi, và tên nó thì bài kiểm này không
    đoán trước được.
    """
    than = _decide_service_body()
    thieu = [
        dong.strip()
        for dong in than.splitlines()
        if "reject_reason" in dong and "reject_code" not in dong and "//" not in dong
    ]

    assert thieu == [], f"nhánh gửi lý do mà không gửi mã: {thieu}"


def test_the_ui_offers_exactly_the_codes_the_backend_accepts():
    """Danh sách mã ở hai bên phải khớp — lệch một mã là một lời từ chối 422."""
    text = _UI.read_text(encoding="utf-8")
    khai_bao = text[text.index("REJECT_CODES = [") : text.index("] as const")]
    tren_ui = set(re.findall(r"\['([A-Z_]+)',", khai_bao))

    assert tren_ui == set(REJECT_CODES), f"UI: {sorted(tren_ui)} / backend: {sorted(REJECT_CODES)}"


@pytest.mark.parametrize("tool", sorted(SERVICE_GATED_TOOLS | {"schedule_property_viewing"}))
def test_every_gated_service_has_a_label_on_the_review_screen(tool: str):
    """Đơn vị duyệt nhìn TÊN DỊCH VỤ, không nhìn tên tool.

    Một tool thiếu nhãn thì nó vẫn hiện ra hàng đợi — dưới cái tên nội bộ.
    """
    text = _UI.read_text(encoding="utf-8")

    assert f"{tool}:" in text, f"màn hình duyệt không có nhãn cho {tool}"
