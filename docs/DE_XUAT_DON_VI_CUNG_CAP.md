# Đề xuất đơn vị cung cấp theo nhu cầu và ngân sách

**Nhánh:** `develop` · **Bắt đầu:** 27/08/2026 · **Phạm vi:** chỉ `schedule_move`
**Trạng thái:** nền dữ liệu mock xong · chưa nối vào luồng thật

> Thay thế `CHON_DON_VI_CUNG_CAP.md`. Bản cũ có hai câu sai và xếp sai đường
> găng — xem mục 8.

---

## 0. Đây KHÔNG phải cá nhân hoá

Tính năng chọn đơn vị theo **ngày · yêu cầu dịch vụ · ngân sách · giá · đánh
giá**. Toàn bộ là ràng buộc **của chính yêu cầu đó**, không dùng hồ sơ, sở
thích hay hành vi lịch sử của người dùng.

Đây là **provider matching/recommendation**. Gọi nó là personalization là gọi
sai, và sẽ bị hỏi vỡ.

Hệ thống **đã có** trí nhớ hội thoại (10 lượt gần nhất, kèm nhãn dịch vụ đã
chạy thật) với luật *"ngữ cảnh nhớ lại là GỢI Ý, không phải sự thật"*. Cá nhân
hoá thật, nếu làm, là một việc khác.

---

## 1. Vì sao làm

**Lập luận thương mại.** Mỗi cư dân là một khách hàng **có ý định mua và có
ngân sách**. Đó là thứ thuyết phục một đội chuyển nhà nhỏ lẻ chịu nối vào — họ
không có kênh nào tiếp cận được tệp *"đã xác minh căn hộ, có địa chỉ, cần
chuyển nhà ngày 30/08, ngân sách 500k"*.

**Lập luận kỹ thuật.** Giá trị của lớp điều phối **tăng theo số nhà cung cấp
rời rạc**, không theo số tính năng.

**Vì sao chỉ `schedule_move`.** Nó là chuỗi **bắc qua trong-ngoài toà nhà** —
thang máy của ban quản lý + xe của đội bên ngoài, hai bên không biết nhau. Giá
cũng phụ thuộc ba tham số thật (xe, thang máy, bốc xếp) nên việc so giá có
nghĩa. **Một vertical slice hoàn chỉnh mạnh hơn hai luồng cùng làm dở.**

`create_maintenance_request` **hoãn** tới sau Demo Day.

---

## 2. Thiết kế đã chốt

| Điểm | Quyết định |
|---|---|
| Ai chọn | **Mã tất định**, không phải model. Model chỉ diễn đạt lại |
| Luật chọn | Còn lịch đúng ngày → giá thấp nhất → đánh giá cao hơn → `provider_id` nhỏ hơn |
| Ngày | Ràng buộc **cứng**, không bao giờ tự đổi |
| Ba đường vào | Chọn tên / nói ngân sách / không nói gì — **cùng một quote engine** |
| `max_price` | Chỉ để **lọc phía P-118**. KHÔNG nằm trong payload gửi provider |
| Luôn nói ra | Đã chọn ai, theo luật nào, giá bao nhiêu, hạn báo giá |
| Ngân sách quá thấp | Không gửi provider. Nói giá thấp nhất, hỏi mở |
| Bị từ chối | Hỏi lại, **không tự chuyển đơn vị** |
| Trước side effect | `SEARCHED → PROPOSED → USER_CONFIRMED → AWAITING_PROVIDER` |

### Ba luật bắt buộc

1. **Model không chọn.** Chọn ai là quyết định có hệ quả tiền bạc.
2. **Không nhận thẳng `provider_id` từ model.** Kể cả khi người dùng gọi tên,
   vẫn phải: resolver tên → mã, kiểm tồn tại, kiểm lịch, lấy báo giá, tạo quote.
3. **Không tìm được thì từ chối và giải thích**, không bịa đơn vị vừa túi tiền.

### `max_price` không được rò sang provider

Provider chỉ nhận **yêu cầu** và báo giá. Lộ trần ngân sách là mời họ định giá
sát trần. `max_price` là tham số của **bộ lọc**, không phải của **yêu cầu báo giá**.

---

## 3. Đã xong — nền dữ liệu mock

**`src/mock/service_providers.py`** · **`tests/test_a_price_is_computed_not_invented.py`** (13 test)

3 đội chuyển nhà (`MOV-01/02/03`) + 3 đội bảo trì (`FIX-01/02/03`, để dành).
Giá **theo tham số**:

```
chuyển nhà = gia_goc + phu_phi_xe[move_vehicle]
                     + phu_phi_thang_may (nếu cần)
                     + phu_phi_boc_xep   (nếu cần)
```

Hệ số **đan chéo** có chủ ý — đơn vị rẻ nhất **đổi theo yêu cầu**:

| Yêu cầu | Rẻ nhất |
|---|---|
| Không xe, không gì thêm | An Khang 200.000đ |
| Xe tải + thang máy + bốc xếp | Đại Tín 920.000đ |

**6/6 mutation bị bắt.** Hai bài kiểm đầu tiên KHÔNG cắn được, đã sửa: một cái
gọi chính hàm nó đang kiểm (`con_lich`) nên cho hàm đó luôn trả `True` vẫn
xanh; một cái dùng `>=` nên bỏ hẳn phụ phí vẫn xanh.

> ⚠️ **Giá do mock provider catalog tính theo contract tất định**, KHÔNG phải
> "giá đến từ nhà cung cấp". Chỉ được nói vế kia khi endpoint thật sự đóng vai
> provider và trả quote có `quote_id`.

---

## 4. Mốc "đủ đúng để demo"

### A. Provider ownership — **XONG** (27/08)

Trước đó `/service-approvals` lọc **chỉ theo trạng thái**: mọi tài khoản
`provider` thấy và quyết định được **toàn bộ** hàng đợi. Chưa sửa thì "hệ thống
chọn MOV-02" chỉ tồn tại trên dữ liệu, **không tồn tại trong nghiệp vụ** — và
nó đúng là IDOR: một tổ chức quyết định trên đơn hàng của tổ chức khác.

Đã làm:

| | |
|---|---|
| `service_provider_accounts` | bảng liên kết `(user_id, service_provider_id)`. Bảng riêng chứ không phải cột trên `users`: một tài khoản quản nhiều đơn vị, một đơn vị nhiều nhân viên |
| `service_approvals.service_provider_id` | chủ sở hữu của từng dòng, có index `(provider, status, created_at)` |
| Danh sách | lọc theo đơn vị của tài khoản đang đăng nhập. Danh sách RỖNG ⇒ hàng đợi rỗng, không phải thấy hết |
| `decide` | kiểm quyền sở hữu **độc lập** với đường đọc danh sách, trả **404** (không phải 403 — 403 xác nhận dòng ấy tồn tại) |
| Đường ghi | cả ba (`save_pending_service_approvals`, `save_service_request`, `viewing_approval`) gán đơn vị cụ thể qua `provider_directory.don_vi_mac_dinh()`. Tool chưa khai thì **ném**, không rơi về `LEGACY-DEFAULT` |
| Ghim lại | `ON CONFLICT` cập nhật `service_provider_id` — ghim lại là yêu cầu MỚI, và bước B sẽ đổi đơn vị theo ngày |

**Admin KHÔNG vào hàng đợi của đơn vị.** Yêu cầu ban đầu là "admin vẫn xem được
toàn bộ"; nó được rút lại có chủ ý. Quyền duyệt là quyền *nhân danh* một đơn vị
nhận việc — admin không có mặt bằng, không có đội bảo trì, không có xe. Cho họ
đọc hàng đợi là đặt sẵn dữ liệu để một nút Duyệt mọc lên, và nó phá chính công
cụ giám sát: nếu người giám sát tự tay giải quyết được hàng đợi thì con số
"đang chờ đơn vị" không đo gì nữa. Cách nói đúng:

> Admin giám sát toàn cục qua `/admin/requests`; provider xử lý hàng đợi của
> chính đơn vị qua `/service-approvals`.

Bù lại, `/admin/requests/{id}` nay trả `service_provider {id, name}` cho từng
bước, cạnh `approval_status` và `decided_by` đã có. `None` ở đó là một câu trả
lời — nó nói dòng này chưa có đơn vị, tức không ai duyệt được.

#### Dòng legacy — 290 dòng trên `p118_db`

Fail-closed nghĩa là `service_provider_id IS NULL` trả False cho **mọi** đơn vị.
Đúng cho dòng mới, nhưng dữ liệu có trước cột ấy đều NULL.

`scripts/backfill_service_provider.py` — **chạy tay, không nằm trong migration
và không gọi lúc khởi động**. "Dòng nào thuộc đơn vị nào" là câu hỏi nghiệp vụ;
một migration đoán hộ nghĩa là mọi môi trường nhận cùng một cái đoán, kể cả nơi
cái đoán ấy sai. Dry-run mặc định · đối chiếu `current_database()` với
`--database` · không in DSN · chỉ `UPDATE` cột `service_provider_id` ở dòng
`IS NULL` · một transaction · idempotent · in số trước/dự kiến/sau · dừng nếu
tài khoản đích không tồn tại hoặc không phải role `provider`.

Danh tính là `LEGACY-DEFAULT` / *P-118 Legacy Provider*, **không phải** `MOV-01`
hay một đơn vị kinh doanh nào: gán dòng lịch sử cho một đơn vị thật là viết một
sự thật không có, và sau này không cách nào tách "việc thật của Minh Phát" khỏi
"việc backfill gán bừa". Backfill **toàn bộ** kể cả lịch sử đã quyết định, để
provider cũ tra lại nhất quán.

Đã chạy trên `p118_db` 27/08: 290 → 0 dòng NULL; `provider` và `provider_demo`
gắn vào `LEGACY-DEFAULT`; chạy lần hai ghi 0 dòng.

#### Nghiệm thu

**Lớp 1 — PostgreSQL + HTTP thật.** 3829 test xanh (3 lỗi còn lại là mục theo
lịch, hardcode `2026-08-25`, ngoài phạm vi). Bảy mutation, bảy cái cắn:

| mutation | test bắt |
|---|---|
| mở lại hàng đợi cho admin | `test_an_admin_does_not_get_a_queue_of_its_own` + `[service]` |
| dòng mới ghi NULL | 11 test ở `test_a_refusal_the_customer_can_answer` |
| bỏ kiểm sở hữu ở `decide` | 4 test ownership |
| ghim lại giữ chủ cũ | `test_re_pinning_a_step_gives_it_an_owner_again` |
| tool lạ rơi về `LEGACY-DEFAULT` | `test_an_unmapped_tool_raises_instead_of_becoming_legacy` |
| bỏ đơn vị khỏi cổng tham quan | `test_the_viewing_gate_names_a_unit_too` |
| màn giám sát bỏ trường đơn vị | `test_an_admin_sees_which_unit_is_holding_each_step` |

**Lớp 2 — canary trên `p118_db` qua HTTP thật** (27/08, dữ liệu gieo đã dọn):

```
A (MOV-01) thấy đúng 1 việc của mình; B (MOV-02) đúng 1; legacy 0
legacy đọc 200 dòng lịch sử; A đọc 0
A bấm vào việc của B            → 404, dòng của B vẫn AWAITING/—
B bấm vào việc của B            → 200, REJECTED/canary_p118_b; dòng của A không đổi
admin  /service-approvals       → 403
admin  /admin/requests/{A}      → MOV-01 "Chuyển nhà Minh Phát" | AWAITING | ký bởi None
admin  /admin/requests/{B}      → MOV-02 "Vận tải Đại Tín"      | REJECTED | ký bởi canary_p118_b
khách  cả hai đường             → 403
```

### B. Quote có danh tính

```
quote_id · service_provider_id · quoted_price · currency
expires_at · request_fingerprint
```

Task nhận **`quote_id`**, không chỉ `provider_id`. Thiếu nó thì: giá hiển thị
có thể khác giá thực hiện, không chứng minh được báo giá thuộc đúng ngày/yêu
cầu, restart có thể tính ra đơn vị khác, và không có dấu vết cho câu *"P-118 đã
chọn vì giá này"*.

### C. Một đường chọn canonical

Cả ba trường hợp đi qua **cùng quote engine**. `"Minh Phát"` → `MOV-01` bằng
resolver, giống `project_id` — người dùng không phải gõ mã.

### D. Xác nhận của người dùng

```
SEARCHED → PROPOSED → USER_CONFIRMED → AWAITING_PROVIDER
```

Cần **bảng bền vững** (`service_provider_proposals`):

```
workflow_id · task_id · quote_id · service_provider_id
quoted_price · currency
status: PROPOSED | CONFIRMED | EXPIRED | CANCELLED
created_at · confirmed_at
```

> **`approval_actor` KHÔNG dùng được thay cho việc này.** Đã kiểm: nó không có
> cột nào trong database — chỉ là trường trong `models/schemas.py`, suy ra lúc
> chạy để UI biết ai cần hành động. Nó không lưu người dùng xác nhận đơn vị nào,
> quote nào, giá bao nhiêu, còn hạn không, lúc nào.
>
> Cũng **không** nhét vào `payment_approvals` — đây là xác nhận lựa chọn nhà
> cung cấp, chưa phải thanh toán.

UI hiển thị: tên · giá · đánh giá · **lý do đề xuất** · hạn báo giá · nút xác
nhận / đổi đơn vị. Xác nhận xong **mới** gửi sang `/review`.

### E. Feature flag

```env
SERVICE_PROVIDER_MATCHING=0      # mặc định TẮT
```

Chỉ bật trên database demo sau khi acceptance xanh. Cùng luật opt-in với
`FAST_LANE`: chỉ đúng chuỗi `"1"` mới bật.

### F. Từ chối — mức tối thiểu an toàn

Chưa cần chịu ba lần từ chối liên tiếp. Lần đầu phải: giữ approval và quote cũ
làm lịch sử · hiện **lý do thật** · **không tự chuyển đơn vị** · không để
workflow giả vờ đang chờ · cho chọn đơn vị khác để mở attempt mới.

> **Cơ chế attempt mới ĐÃ CÓ.** Đo được trên `p118_db`: `T1 → CANCELLED`,
> `T1R2 → SUCCESS`. Giữ lịch sử, task mới, không chạy lại task đã xong — ba thứ
> đó đang chạy. **Còn phải làm:** quote mới, approval mới, loại đơn vị vừa từ
> chối khỏi gợi ý.

---

## 5. Sáu acceptance case bắt buộc

Không cần 12 ca trước demo, nhưng sáu ca này **không được bỏ**:

| # | Ca | Tầng |
|---|---|---|
| 1 | Không chỉ định → đề xuất đúng đơn vị, giá, lý do | |
| 2 | Có ngân sách → không chọn đơn vị vượt ngân sách | |
| 3 | Ngân sách quá thấp → **không gửi provider**, báo giá thấp nhất | |
| 4 | Provider A **không** thấy/duyệt được việc của Provider B | **HTTP + PostgreSQL** |
| 5 | Xác nhận → provider duyệt → `schedule_move` thành công | **HTTP + PostgreSQL + browser** |
| 6 | Restart sau xác nhận → giữ nguyên quote/provider, không tạo yêu cầu trùng | **HTTP + PostgreSQL** |

Nếu demo nhánh từ chối, thêm:

| 7 | Provider từ chối → hiện lý do → chọn đơn vị mới → attempt/quote/approval mới |

**Ca 4–6 phải chạy qua HTTP và PostgreSQL thật.** Unit test không chứng minh
được định tuyến, restart hay UI — và đó đúng là loại lỗi đã vấp nhiều lần: bài
kiểm xanh trong khi đường thật hỏng.

---

## 6. Metric trước demo — chốt ba số

- **0** yêu cầu bị định tuyến sai provider
- **0** đề xuất vượt ngân sách hoặc sai ngày
- **6/6** acceptance case xanh

Nếu kịp user test, đo thêm: số lượt hội thoại tới lúc chọn xong · thời gian
hoàn thành · tỷ lệ chấp nhận đề xuất đầu tiên.

---

## 7. Rủi ro còn mở

**"Rẻ nhất" là phán xét giá trị.** Rẻ nhất ≠ tốt nhất. Không có ngưỡng chất
lượng tối thiểu thì hệ thống tạo ra cuộc đua xuống đáy và đối tác tốt rời đi.
Chấp nhận cho demo **nếu luật được nói ra**; cần ngưỡng trước khi có đối tác thật.

**Ngân sách quá thấp không gợi ý ngày cụ thể được.** Endpoint chỉ tra **đúng
một ngày**, nên chưa có bằng chứng để nói *"ngày 02/09 có đội 380k"*. Contract
đúng ở giai đoạn này là hỏi mở:

> Không có đơn vị trong ngân sách cho ngày đã chọn. Giá thấp nhất là **X**. Bạn
> muốn tăng ngân sách, chọn đơn vị khác, hay đổi ngày?

Chỉ nêu ngày cụ thể sau khi có API lịch nhiều ngày.

---

## 8. Bản trước sai gì

| Sai | Đúng |
|---|---|
| Gọi là "cá nhân hoá" | Provider matching theo ràng buộc yêu cầu |
| Ownership xếp vào "việc liên quan" | **Blocker**, nằm trên đường găng |
| `provider_id` qua `InputRef` là đủ | Cần **quote** có danh tính và hạn |
| "Giá đến từ nhà cung cấp" | Giá do **catalog cục bộ của P-118** tính |
| Gợi ý ngày cụ thể khi thiếu ngân sách | Chưa có bằng chứng — phải hỏi mở |
| `approval_actor` thay được `PROPOSED` | Nó **không có trong DB**, chỉ là tín hiệu UI |
| Acceptance HTTP/DB để sau demo | Ca 4–6 phải nằm trong mốc demo |
| Làm cả hai dịch vụ | Chỉ `schedule_move` |

---

## 9. Việc khác, chưa làm

| Việc | Ước tính |
|---|---|
| Màn hình dòng thời gian hỏng→sửa | ~3 giờ |
| 3 test đỏ mục theo lịch (`test_a_short_date_is_anchored_to_the_old_one`) | 5 phút |
| `ARCHITECTURE.md` còn là mẫu trống | 2 phút |
| Trang đăng nhập redesign (đã mất, chưa commit) | ~15 phút |
| **Nguồn ngày duy nhất trong mã** (`ZoneInfo`) | ~30 phút |

### Múi giờ — đã vá tạm, chưa xong

Container chạy UTC còn máy ở +07, nên 00:00–07:00 giờ VN `date.today()` trả về
**ngày hôm trước**, và `validator.py` cho phép đặt lịch vào ngày đã qua. Đo được
lúc 00:40 ngày 27/08: host thấy 27, container thấy 26.

Đã vá bằng `TZ: Asia/Ho_Chi_Minh` cho 11 service trong `docker-compose.yml`.

> ⚠️ **Không theo lên Render.** `TZ` chỉ sống trong compose. Bản đúng là một
> nguồn ngày duy nhất trong mã — 19 chỗ trong `src/` đang đọc đồng hồ hệ thống.

---

## 10. Số liệu nền — `p118_db`, 21/08 → 27/08

| | |
|---|---|
| Workflow · bước dịch vụ · lượt gọi model | 202 · 308 · 721 |
| Quyết định duyệt | 384, trong đó **60 lần từ chối** |
| Bài kiểm tự động | 3.787 passed |
| `plan` p50 · token | 23,33s · 4.116 |
| `fast_plan` p50 · token | 1,56s · 187 |
| Fast Lane về đích | 37 workflow (35%) ở 1,9s |

`latency = 616ms + 7,62ms × completion_tokens`, R² = 0,994 trên 658 lượt.
