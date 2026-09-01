"""Danh tính của một LẦN THỬ: `T1` → `T1R2` → `T1R3`.

Vì sao là một module riêng
--------------------------
Ba đường ghi cần cấp danh tính cho một lần thử mới — sửa lỗi sau khi khách trả
lời, yêu cầu hỗ trợ, và chọn lại đơn vị cung cấp. Trước module này, luật ấy
sống trong `repair_attempt._allocate_task_id`, và hai đường kia hoặc chép lại nó
(`support_request` có một bản gần giống) hoặc gọi xuyên qua dấu gạch dưới.

Cả hai cách đều hỏng theo cùng một kiểu: luật "một `task_id` không được dài quá
20 ký tự và không được đụng id đang có" là một BẤT BIẾN của schema, và một bất
biến có ba bản sao là một bất biến sẽ lệch.

Vì sao KHÔNG chỉ bỏ dấu gạch dưới
---------------------------------
`repair_attempt` sở hữu trách nhiệm "câu trả lời của khách biến bước này thành
một yêu cầu khác". Việc đặt tên cho lần thử là một trách nhiệm nhỏ hơn và độc
lập — nó không biết gì về câu trả lời, về hàng đợi duyệt, hay về đơn vị cung
cấp. Để nó ở đó nghĩa là mọi đường mới phải import một module lớn để dùng một
hàm nhỏ, và nhận theo cả những gì module ấy kéo vào.

Hợp đồng
--------
`cap_danh_tinh_lan_thu(task_id, taken)`:

  * cùng CHUỖI LOGIC: `T1`, `T1R2`, `T1R3` đều gốc `T1`, nên gọi với bất kỳ id
    nào trong chuỗi cũng cho lần kế tiếp của chuỗi ấy;
  * KHÔNG đụng id đang có — `taken` là tập id đã tồn tại, và người gọi có trách
    nhiệm đọc nó từ database trong cùng lượt;
  * KHÔNG cắt bớt để cho vừa `VARCHAR(20)`. Một id bị cắt có thể đụng một id
    khác, và khi đó lần thử mới ghi đè lên bằng chứng của một bước không liên
    quan. Hết chỗ thì trả `None`, và người gọi FAIL CLOSED.

Đồng thời và restart
--------------------
Hàm này THUẦN: nó không đọc database và không giữ trạng thái. Tính duy nhất
đến từ `taken` — và `taken` chỉ đúng trong lượt nó vừa được đọc. Hai lượt song
song đọc cùng một `taken` sẽ cùng nhắm `T1R2`, nên người gọi phải có một hàng
rào riêng: khoá dòng, ràng buộc duy nhất, hoặc một phép kiểm "đã mở lần thử mới
chưa" trước khi ghi (xem `provider_reselection.mo_lan_chon_lai`).

Đặt hàng rào ấy vào đây sẽ đúng cho một đường và sai cho hai đường còn lại —
mỗi đường có một định nghĩa khác nhau về "đã mở rồi".
"""

from __future__ import annotations

# `T1R2` — chữ `R` là "lần thử". Tách bằng một ký tự chứ không bằng dấu gạch
# ngang vì `task_id` đi vào `VARCHAR(20)` và mọi ký tự đều phải trả giá.
DAU_LAN_THU = "R"

# Trần của `workflow_tasks.task_id`. Đọc từ schema, không đoán: vượt trần thì
# PostgreSQL cắt hoặc từ chối, và cả hai đều tệ hơn việc dừng lại ở đây.
DAI_TOI_DA = 20

# Trần số lần thử. Không phải giới hạn nghiệp vụ — nó là mốc để vòng lặp dừng.
# Một workflow tới lần thử thứ 99 là một workflow có chuyện khác hẳn đang sai.
SO_LAN_TOI_DA = 100


def goc_chuoi_lan_thu(task_id: str) -> str:
    """`T1R3` → `T1`. Phần gốc là danh tính LOGIC của một việc.

    Dùng để gom mọi lần thử của cùng một việc: tập loại trừ đơn vị đã từ chối,
    lịch sử, và phép kiểm "đã mở lần thử mới chưa" đều hỏi theo gốc chứ không
    theo id cuối.
    """
    return task_id.split(DAU_LAN_THU)[0]


def cung_mot_chuoi(a: str, b: str) -> bool:
    """Hai `task_id` có thuộc cùng một việc không."""
    return goc_chuoi_lan_thu(a) == goc_chuoi_lan_thu(b)


def cap_danh_tinh(task_id: str, taken: set[str], *, dau: str = DAU_LAN_THU) -> str | None:
    """Dạng tổng quát: cấp danh tính kế tiếp cho một chuỗi, theo `dau` cho trước.

    `support_request` dùng `H` (huỷ) thay vì `R` (thử lại) — hai CHUỖI khác
    nhau trên cùng một bước, và gộp chúng vào một tiền tố sẽ làm một lượt huỷ
    và một lượt thử lại tranh nhau cùng một cái tên.

    Cùng một luật độ dài và cùng một luật không-đụng-id, chỉ khác ký tự tách.
    """
    goc = task_id.split(dau)[0]
    for lan in range(2, SO_LAN_TOI_DA):
        ung_vien = f"{goc}{dau}{lan}"
        if len(ung_vien) <= DAI_TOI_DA and ung_vien not in taken:
            return ung_vien
    return None


def cap_danh_tinh_lan_thu(task_id: str, taken: set[str]) -> str | None:
    """Danh tính cho lần thử KẾ TIẾP của chuỗi chứa `task_id`.

    Trả `None` khi không còn tên nào đủ ngắn và chưa bị chiếm. Người gọi phải
    FAIL CLOSED khi nhận `None`: đi tiếp nghĩa là chạy lại trên bằng chứng cũ,
    đúng thứ lần thử mới sinh ra để tránh.
    """
    return cap_danh_tinh(task_id, taken, dau=DAU_LAN_THU)
