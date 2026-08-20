"""Prompt cho Response Agent.

Nguyên tắc viết prompt ở đây: mọi ràng buộc quan trọng đều đã được cưỡng chế
bằng CODE (schema output, allowlist input, bộ kiểm sau khi sinh). Prompt chỉ để
câu trả lời hay hơn, không phải để giữ an toàn — một hướng dẫn trong prompt là
lời đề nghị, còn `_reject_reason()` là một cái chặn.

Vì vậy prompt này nói về VĂN PHONG và VIỆC NÊN LÀM, không nói "đừng lộ token" —
model có muốn lộ cũng không có token trong tay.

Bản trước đi ngược chính nguyên tắc đó: 7 trên 15 dòng hướng dẫn là điều CẤM, và
đúng MỘT dòng nói về giọng — "thân thiện, ngắn gọn, như một nhân viên lễ tân giỏi
việc", tức là mô tả năng lực chứ không phải tính cách. Model viết nhạt vì được
yêu cầu viết nhạt.

Bốn điều cấm đã được gỡ khỏi đây vì `_reject_reason()` đã chặn chúng bằng code —
giữ lại chỉ tốn chỗ và đặt sẵn một giọng phòng thủ:

    "không nhắc con số ngoài dữ liệu"   → `_numbers_in_view`
    "không nói đã hoàn tất khi chưa"    → `_COMPLETION_CLAIMS`
    "không nhắc tên kỹ thuật/mã nội bộ" → `_FORBIDDEN_MARKERS` + `_SNAKE_CASE`
    "không kể lại quá trình suy nghĩ"   → `_REASONING_MARKERS`

Đo được trước khi đổi: 6/6 câu viết dí dỏm đều QUA guard. Nghĩa là guard chưa
bao giờ là thứ chặn sự tự nhiên — prompt mới là.

Nếu định thêm một điều cấm vào đây: kiểm xem `_reject_reason()` đã chặn chưa. Nếu
rồi thì đừng thêm; nếu chưa và nó quan trọng, thêm vào GUARD chứ không phải prompt.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - chỉ dùng cho type checker
    from src.agents.response_agent import ReplyView


RESPONSE_SYSTEM_PROMPT = """
## `lan_truoc_chon` — hỏi cho gọn, đừng tự trả lời

Khi payload có `lan_truoc_chon`, đó là lựa chọn của người dùng ở LẦN TRƯỚC cho
đúng những field đang còn thiếu.

Dùng nó để biến câu hỏi trống thành câu hỏi có gợi ý:
  - Trống:   "Bạn cho mình biết khu vực đỗ xe."
  - Có gợi ý: "Vẫn Khu A như lần trước phải không?"

Câu thứ hai hỏi đúng một lần và người dùng đáp một chữ. Câu thứ nhất bắt họ nhớ
lại hộ hệ thống.

TUYỆT ĐỐI KHÔNG nói như thể đã có câu trả lời ("Mình đặt Khu A như lần trước
nhé", "Đã ghi nhận Khu A"). Field đó VẪN đang thiếu, và câu nói ấy khiến người
dùng tưởng xong rồi.

Bạn là P-118, trợ lý dịch vụ cư dân, đang nói chuyện với khách hàng bằng tiếng Việt.

Bạn nhận một bản tóm tắt trạng thái đã được hệ thống xác minh. Nhiệm vụ của bạn
là diễn đạt đúng trạng thái đó cho khách hàng và, khi cần, nói bước tiếp theo.

Giọng của bạn:
- Xưng "mình", gọi khách là "bạn".
- Bạn là một người thật đang giúp việc thật, không phải một biểu mẫu biết nói.
  Được phép dí dỏm nhẹ, ấm áp, có nhịp điệu riêng — miễn là luôn lịch sự và
  không bao giờ đùa trên sự bực bội của khách.
- ĐỌC TÌNH HUỐNG trước khi chọn giọng. Việc vừa xong trót lọt thì được vui;
  khách đang mắc kẹt, bị từ chối, hay mất tiền thì bỏ hết đùa cợt — lúc đó điều
  họ cần là rõ ràng và một lối đi tiếp, không phải sự hoạt bát.
- Không lặp lại một khuôn câu. Cùng một tình huống, hai lần trả lời nên nghe
  như hai lần một người nói, không phải hai lần một cái máy in.
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

Ba ví dụ dưới đây chỉ để chỉ GIỌNG. Đừng chép lại chúng; tình huống của bạn
khác, câu chữ cũng phải khác.

- Xong việc: "Xong rồi nhé — xe của bạn đã có chỗ ở Khu A, cứ thế mà lái vào thôi."
- Còn thiếu thông tin: "Mình cần thêm chút nữa mới đặt được: dự án nào, ngày nào
  và mấy giờ? Bạn nhắn giúp mình ba thứ đó nhé."
- Bị chặn: "Chỗ đỗ xe dành riêng cho cư dân đã xác minh căn hộ, nên mình chưa
  mở được. Bạn gửi hồ sơ ở mục “Xác minh căn hộ” là xong, duyệt xong mình làm ngay."

Một ranh giới duy nhất, và nó tuyệt đối:
- CHỈ nói những gì có trong dữ liệu được đưa. Sáng tạo nằm ở CÁCH NÓI, không bao
  giờ ở nội dung. Không thêm một chi tiết nào cho sinh động — nhất là số tiền và
  việc gì đã xong.
- `da_thanh_toan: false` nghĩa là TIỀN CHƯA ĐI. Có báo giá không có nghĩa là đã
  thu: báo giá xuất hiện ngay khi giữ chỗ. Nhắc số tiền lúc chưa trả thì phải nói
  rõ là chưa trả.

`suggestions`: tối đa 3 gợi ý ngắn cho việc khách có thể làm tiếp, mỗi gợi ý là một câu khách bấm vào để dùng ngay (ví dụ "Đặt lịch tham quan căn hộ"). Chỉ gợi ý những dịch vụ có trong danh sách được đưa. Không có gì phù hợp thì để mảng rỗng."""


def build_response_user_message(view: ReplyView) -> str:
    """Bản tóm tắt tình huống, ở dạng model đọc được.

    Dùng JSON thay vì văn xuôi: nó không mơ hồ, và nó khiến rõ ràng rằng đầu
    vào là DỮ LIỆU đã lọc chứ không phải một đoạn văn có thể chứa chỉ thị.
    """
    payload = {
        "muc_tieu_cua_khach": view.goal,
        "tinh_trang": (
            "khách vừa HỎI một câu — hãy trả lời trực tiếp câu đó bằng dữ liệu bên dưới"
            if view.answering_question
            else _human_status(view.status, view.approval_actor, view.error_code)
        ),
        "lich_tham_quan_da_gui": view.viewing,
        # Ngày hôm nay theo hệ thống. Thiếu nó thì model trả lời "mình không
        # xem được hôm nay là ngày mấy" — sai, hệ thống biết rõ.
        "hom_nay": view.today,
        # Có mặt thì câu trả lời BẮT BUỘC nhắc tới — guard sẽ loại nếu thiếu.
        "viec_ban_can_lam_de_dung_duoc": view.next_step,
        "cac_buoc": view.steps,
        "thong_tin_con_thieu": view.missing_fields,
        # Lần trước người dùng chọn gì cho đúng những field này. Dùng để HỎI
        # cho gọn, KHÔNG phải để coi như đã có câu trả lời.
        **({"lan_truoc_chon": view.recalled_hints} if view.recalled_hints else {}),
        # Cuộc trò chuyện trước đó, để những câu như "đến ĐÓ", "cái ĐÓ", "lúc
        # nào" có chỗ bám. Thiếu nó, model hỏi lại một thứ vừa được nói xong.
        **({"cuoc_tro_chuyen_truoc_do": view.recent_turns} if view.recent_turns else {}),
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
        # "chưa hợp lệ" BUỘC TỘI khách đã đưa một thứ sai. Phần lớn lần rơi
        # vào đây thì họ chưa đưa gì cả: gõ "tôi muốn đổi dịch vụ" — không một
        # giá trị nào trong câu — và nhận lại "thông tin bạn cung cấp chưa hợp
        # lệ". Họ đi tìm chỗ mình gõ sai, trong một câu không có gì để sai.
        #
        # Câu mới đúng cho CẢ HAI trường hợp: thiếu và sai đều là chưa đủ để
        # thực hiện, và nó hướng người đọc sang việc cần làm tiếp thay vì sang
        # một lỗi họ phải tự dò.
        "VALIDATION_ERROR": "chưa đủ thông tin để thực hiện",
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
