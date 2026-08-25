"""`json_mode` không gửi schema — khung JSON phải nằm trong prompt.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_intent_prompt_says_the_shape.py

LỖI THẬT trên stack demo, log lúc 10:01:36:

    pending_intent: phan loai hoi lai sau output khong dung duoc (OutputParserException)
    routes:         intent lane: không phân loại được câu (PendingIntentError)

Người dùng gõ "tôi muốn đổi qua khu B" và nhận "Mình chưa tra được thông tin
này. Bạn hỏi lại cụ thể hơn giúp mình nhé."

`PendingIntentResolver` chạy với `json_mode`. Ở chế độ ấy, API tương thích
OpenAI CHỈ nhận chỉ thị "trả JSON" — nó KHÔNG gửi schema cho model. Prompt hiện
nói "Trả lời bằng một object json đúng schema", nhưng không schema nào có mặt để
model đọc. Nó phải tự đoán tên trường, và `_IntentResponse` có
`extra="forbid"` nên đoán trượt một tên là hỏng cả lượt.

Đây là ĐÚNG lỗi đã gặp ở `src/agents/fast_lane.py` và đã sửa ở đó: bỏ khung ra
khỏi prompt thì model trả `{"service": ..., "date": ...}` và 54/54 lượt trượt
schema. Cùng nguyên nhân, cùng cách chữa.
"""

from __future__ import annotations

import pytest

from src.agents.pending_intent import PendingIntentResolver, _IntentResponse


class _LLMGia:
    def with_structured_output(self, schema, **_kw):
        return self

    async def ainvoke(self, messages):  # pragma: no cover - không dùng tới
        raise AssertionError("bài kiểm này chỉ đọc prompt")


def _prompt(method: str | None) -> str:
    resolver = PendingIntentResolver(_LLMGia(), structured_output_method=method)
    messages = resolver._messages("đổi qua khu B", ["parking_zone"], False)
    return next(noi_dung for vai, noi_dung in messages if vai == "system")


@pytest.mark.parametrize("ten", sorted(_IntentResponse.model_fields))
def test_json_mode_spells_out_every_field(ten: str) -> None:
    """Mọi tên trường phải có mặt trong prompt, ở dạng model chép lại được."""
    p = _prompt("json_mode")
    assert f'"{ten}"' in p, (
        f"`json_mode` không gửi schema, và prompt không nêu {ten!r} trong khung JSON — "
        f"model phải đoán tên, và `extra=\"forbid\"` biến một lần đoán trượt thành "
        f"hỏng cả lượt phân loại"
    )


def test_the_template_is_only_added_for_json_mode() -> None:
    """Chế độ khác gửi schema thật; nhồi thêm khung chỉ tốn chỗ trong prompt."""
    assert '"evidence"' not in _prompt(None)


def test_the_labels_are_listed_so_the_model_can_copy_them() -> None:
    """Nhãn là tập ĐÓNG — model phải đọc được đủ năm, không đoán."""
    from src.agents.pending_intent import PendingIntent

    p = _prompt("json_mode")
    for nhan in PendingIntent:
        assert nhan.value in p, f"prompt không nêu nhãn {nhan.value}"
