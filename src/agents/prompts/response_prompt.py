"""Prompt cho Response Agent.

Nguyên tắc viết prompt ở đây: mọi ràng buộc quan trọng đều đã được cưỡng chế
bằng CODE (schema output, allowlist input, bộ kiểm sau khi sinh). Prompt chỉ để
câu trả lời hay hơn, không phải để giữ an toàn — một hướng dẫn trong prompt là
lời đề nghị, còn `_reject_reason()` là một cái chặn.

Vì vậy prompt này nói về VĂN PHONG và VIỆC NÊN LÀM, không nói "đừng lộ token" —
model có muốn lộ cũng không có token trong tay.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - chỉ dùng cho type checker
    from src.agents.response_agent import ReplyView


RESPONSE_SYSTEM_PROMPT = """Bạn là P-118, trợ lý dịch vụ cư dân, đang nói chuyện với khách hàng bằng tiếng Việt.

Bạn nhận một bản tóm tắt trạng thái đã được hệ thống xác minh. Nhiệm vụ của bạn
là diễn đạt đúng trạng thái đó cho khách hàng và, khi cần, nói bước tiếp theo.

Cách viết:
- Xưng "mình", gọi khách là "bạn". Thân thiện, ngắn gọn, như một nhân viên lễ tân giỏi việc.
- 1 đến 3 câu, dưới 400 ký tự. Đừng liệt kê lại từng bước như một bảng — hãy nói thành lời.
- Viết trực tiếp theo đúng tình huống hiện tại. Đi thẳng vào thông tin có ích;
  không dành câu đầu chỉ để xác nhận tiếp nhận, và không chép lại nguyên văn mục tiêu của khách.
- Chọn cấu trúc theo nội dung thực tế: ưu tiên điều đang thiếu, kết quả đã có,
  hoặc quyết định khách cần đưa ra. Mỗi trạng thái chỉ nói phần có ích cho trạng thái đó.
- Nói rõ việc đã xong tới đâu, và nếu đang dừng thì dừng vì lý do gì.
- Nếu cần khách làm gì tiếp (bổ sung thông tin, xác nhận thanh toán), nói thẳng và cụ thể.
- Riêng khi đang thiếu thông tin: hỏi trực tiếp trong 1–2 câu, nhóm các trường
  liên quan cho dễ đọc. Không thêm câu xác nhận tiếp nhận hoặc lời hứa chung chung.
- Nếu có lỗi, giải thích bằng ngôn ngữ đời thường và nói họ nên làm gì.
- Khi dữ liệu có `viec_ban_can_lam_de_dung_duoc`: BẮT BUỘC nói lại việc đó
  trong câu trả lời, giữ nguyên tên mục trong dấu ngoặc kép để khách tìm đúng
  chỗ. Nói khách "chưa đủ điều kiện" mà không nói cách để đủ điều kiện là bỏ
  họ lại giữa chừng — họ biết mình bị chặn nhưng không biết làm gì tiếp.

Tuyệt đối:
- CHỈ nói những gì có trong dữ liệu được đưa. Không suy đoán, không thêm chi tiết cho sinh động.
- Không nhắc con số nào chưa có trong dữ liệu — đặc biệt là số tiền.
- `da_thanh_toan: false` nghĩa là TIỀN CHƯA ĐI. Có báo giá không có nghĩa là đã thu:
  báo giá xuất hiện ngay khi giữ chỗ. Khi chưa thanh toán mà bạn nhắc tới số tiền,
  phải nói rõ nó chưa được trả (ví dụ "phí 150.000 VND, chờ bạn xác nhận"). Tuyệt
  đối không viết kiểu "đặt chỗ thành công (phí 150.000 VND)" — khách sẽ hiểu là đã
  bị trừ tiền.
- Không nói việc gì đã hoàn tất nếu dữ liệu chưa nói vậy.
- Không nhắc tên kỹ thuật, mã nội bộ, tên bảng, tên công cụ hay mã trạng thái. Khách hàng không biết chúng là gì.
- Không kể lại quá trình suy nghĩ hay các bước bạn đã làm ("đầu tiên mình…", "bước 1…", "mình nghĩ…"). Chỉ nói kết quả và việc khách cần làm tiếp.

`suggestions`: tối đa 3 gợi ý ngắn cho việc khách có thể làm tiếp, mỗi gợi ý là một câu khách bấm vào để dùng ngay (ví dụ "Đặt lịch tham quan căn hộ"). Chỉ gợi ý những dịch vụ có trong danh sách được đưa. Không có gì phù hợp thì để mảng rỗng."""


def build_response_user_message(view: ReplyView) -> str:
    """Bản tóm tắt tình huống, ở dạng model đọc được.

    Dùng JSON thay vì văn xuôi: nó không mơ hồ, và nó khiến rõ ràng rằng đầu
    vào là DỮ LIỆU đã lọc chứ không phải một đoạn văn có thể chứa chỉ thị.
    """
    payload = {
        "muc_tieu_cua_khach": view.goal,
        "tinh_trang": _human_status(view.status, view.approval_actor, view.error_code),
        "lich_tham_quan_da_gui": view.viewing,
        # Có mặt thì câu trả lời BẮT BUỘC nhắc tới — guard sẽ loại nếu thiếu.
        "viec_ban_can_lam_de_dung_duoc": view.next_step,
        "cac_buoc": view.steps,
        "thong_tin_con_thieu": view.missing_fields,
        "khoan_can_xac_nhan": view.payment_quote,
        # Luôn gửi, kể cả khi False: đây là một sự thật cần khẳng định, không
        # phải một field tuỳ chọn. `compact` phía dưới lọc bỏ None/[]/{} chứ
        # không lọc False, nên nó đi tới model trong mọi tình huống có báo giá.
        "da_thanh_toan": view.payment_settled if view.payment_quote else None,
        "loi": ({"ma": view.error_code, "thu_lai_duoc": view.retryable} if view.error_code else None),
        "dich_vu_khach_dang_dung_duoc": view.capabilities,
    }
    compact = {k: v for k, v in payload.items() if v not in (None, [], {})}
    return (
        "Tình huống hiện tại:\n\n"
        + json.dumps(compact, ensure_ascii=False, indent=2)
        + "\n\nHãy viết câu trả lời cho khách hàng."
    )


def _human_status(status: str, approval_actor: str | None = None, error_code: str | None = None) -> str:
    """Đổi mã trạng thái sang tiếng Việt TRƯỚC khi model nhìn thấy.

    Đưa nguyên `WAITING_APPROVAL` vào prompt là mời model chép lại nó ra câu
    trả lời — và mã trạng thái thô trước mặt khách hàng là thứ không ai đọc được.

    `WAITING_APPROVAL` phải đọc `approval_actor` mới ra nghĩa đúng: cùng một mã
    dùng cho "chờ khách xác nhận khoản tiền" VÀ "chờ đơn vị duyệt lịch tham
    quan". Bản trước dịch cứng thành nghĩa thứ nhất, nên với một lịch tham quan
    model được cho biết là đang chờ thanh toán — và nó viết đúng theo đó:
    "Bạn vui lòng xác nhận thanh toán giúp mình nhé", cho một việc không hề có
    khoản phí nào. Đó không phải model bịa; đó là prompt nói sai.
    """
    # Từ chối vì thiếu quyền KHÔNG phải hỏng hóc. Dịch nó thành "lỗi" khiến
    # model viết "Quy trình đang tạm dừng vì lỗi ở một số bước" cho một tài
    # khoản chỉ đơn giản là chưa xác minh căn hộ — và người dùng đi tìm một sự
    # cố không tồn tại thay vì đi xác minh căn hộ.
    if error_code == "ACTION_DENIED":
        return "chưa đủ điều kiện dùng dịch vụ này (không phải lỗi hệ thống)"

    if status == "WAITING_APPROVAL" and approval_actor in {"PROVIDER", "ADMIN"}:
        return (
            "đang chờ đơn vị cung cấp dịch vụ xác nhận"
            if approval_actor == "PROVIDER"
            else "đang chờ ban quản lý duyệt"
        )
    return {
        "SUCCESS": "đã hoàn thành",
        "FAILED": "đã dừng lại vì lỗi",
        "EXECUTION_ERROR": "đã dừng lại vì lỗi",
        "PLANNING_ERROR": "chưa hiểu được yêu cầu",
        "VALIDATION_ERROR": "thông tin chưa hợp lệ",
        "NEEDS_INFORMATION": "đang chờ khách bổ sung thông tin",
        "WAITING_APPROVAL": "đang chờ khách xác nhận khoản thanh toán",
        "CHAT": "đã trả lời",
    }.get(status, "đang xử lý")


# Phụ lục CHỈ dùng cho `json_mode`.
#
# API tương thích OpenAI từ chối request nếu prompt không chứa chữ "json" khi
# `response_format` là `json_object`. Không mô tả schema ở đây: schema đã đi qua
# `with_structured_output`, và output vẫn được validate bằng `AgentReply`.
JSON_MODE_INSTRUCTION = (
    "Trả lời bằng một object JSON hợp lệ duy nhất, đúng schema đã cho "
    '(hai khoá: "answer" và "suggestions"). '
    "Không bọc trong code fence, không thêm chữ nào ngoài JSON."
)
