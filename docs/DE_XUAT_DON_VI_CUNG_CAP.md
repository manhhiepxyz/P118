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
| Trước side effect | Đề xuất `PROPOSED` → khách bấm → `CONFIRMED` → hàng đợi đơn vị mở. Ba trạng thái kết thúc: `CONFIRMED` · `EXPIRED` · `SUPERSEDED` (xem mục D) |

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

### B. Quote có danh tính — **XONG** (27/08)

Bước A khoá quyền sở hữu, nhưng đơn vị vẫn đến từ `don_vi_mac_dinh(tool)` — một
bảng cứng trong mã. Ngay khi P-118 *chọn* đơn vị theo giá, câu hỏi đổi: lấy gì
làm bằng chứng rằng đơn vị này đã báo giá này cho yêu cầu này? Không có bằng
chứng thì `service_provider_id` chỉ là một chuỗi đi kèm request — và mọi thứ
người dùng gửi được thì người dùng sửa được.

**Nợ thiết kế được ghi nhận:** `service_provider_id` vẫn là chuỗi liên kết với
danh mục trong mã, chưa có bảng provider canonical hay khoá ngoại. Chấp nhận
cho A; B không thêm một chuỗi ID rời rạc nào nữa mà biến **quote** thành nguồn
danh tính kiểm chứng được. Bảng provider canonical vẫn là việc chưa làm.

#### Bảng `service_quotes`

`quote_id` (nội bộ) · `external_quote_id` (do đơn vị đặt) · `service_provider_id`
· `service_type` · `amount` (`BIGINT`, `CHECK > 0`) · `currency` (`CHECK IN
('VND')`) · `request_fingerprint` · `valid_until` · `status` · `created_at` ·
`confirmed_at` · `workflow_id` · `task_id`.

Bốn ràng buộc ở tầng database, không ở tầng ứng dụng:

- `workflow_id` / `task_id` **NOT NULL**, kèm khoá ngoại **tổng hợp**
  `(workflow_id, task_id) → workflow_tasks`, cùng khuôn với `approval_decisions`
  và `execution_logs`. Bản đầu để hai cột nullable và cổng chỉ kiểm *nếu* caller
  chịu truyền — nghĩa là luật chỉ tồn tại với những call site nhớ tới nó, tức
  không tồn tại. Trỏ vào `workflows` thôi cũng chưa đủ: nó cho phép neo vào một
  `task_id` không có thật.
- `CHECK ((status='CONFIRMED') = (confirmed_at IS NOT NULL))`.
- `UNIQUE (workflow_id, task_id, service_provider_id, request_fingerprint)
  WHERE status='ACTIVE'` — một lượt hỏi giá chạy hai lần không được để lại hai
  dòng ACTIVE cùng đơn vị khác giá; luật chọn sẽ lấy dòng rẻ hơn, tức hệ thống
  tự thưởng cho mình mỗi lần mạng chập chờn.
- `UNIQUE (service_provider_id, external_quote_id)` — **theo đơn vị**, không
  toàn cục. Hai đơn vị khác nhau hoàn toàn có thể cùng đánh số `Q-001`; ép
  chúng khác nhau là áp luật của P-118 lên hệ thống đánh mã nội bộ của người
  khác. Nhưng trong một đơn vị, mã phải là danh tính — trùng mã nghĩa là câu
  "chúng tôi đã xác nhận Q-001" trỏ tới hai con số và không ai phân xử được.

`luu_bao_gia()` ghi bằng `INSERT ... SELECT FROM workflow_tasks`, nên **bước
được neo phải có `tool` trùng `service_type`**. Đó là ca mà khoá ngoại thôi
không chặn được: neo báo giá chuyển nhà vào một bước *tra cứu*. Bước tra cứu
không tiêu thụ gì, nên chứng từ neo ở đó không bao giờ được đối chiếu — nó nằm
im, hợp lệ về hình thức, và vô dụng. Lỗi này có mã riêng
`QUOTE_ANCHOR_INVALID`: nó là lỗi của P-118, không phải của đối tác, và gộp vào
`QUOTE_MALFORMED` sẽ gửi người đọc log đi gọi nhầm người.

Không lưu `goal`, prompt hay văn bản hội thoại. Báo giá là chứng từ thương mại.

#### Hạn hiệu lực — thực thi ở đồng hồ của database

Ba chỗ, cùng một đồng hồ:

| | |
|---|---|
| **Không ghi** | `luu_bao_gia` có `AND $valid_until > NOW()` trong `WHERE`. Ghi một báo giá đã chết rồi chờ bước quét dọn là tạo rác kèm một khoảng nó trông như còn sống — và trong khoảng ấy nó là một lựa chọn hợp lệ trên màn hình. Mã riêng: `QUOTE_ALREADY_EXPIRED` |
| **Không đề xuất** | `bao_gia_dang_song` lọc `valid_until > NOW()` ở SQL; `loc_theo_ngan_sach` lọc lại ở tầng thuần để bắt khoảng giữa "đọc xong" và "chọn xong" |
| **Không xác nhận** | `xac_nhan_bao_gia` kiểm **chín** điều kiện trong MỘT lệnh `UPDATE` |

Bước quét `het_han_bao_gia_qua_han()` — `ACTIVE + valid_until <= NOW() →
EXPIRED` — chạy **trước mỗi lượt xin báo giá**. Đây là nghĩa vụ đi kèm ràng
buộc `UNIQUE ... WHERE ACTIVE`: thời gian trôi qua **không** tự đổi `status`,
nên không có bước quét thì một báo giá 30 phút biến thành một cái khoá 30 phút
*trở lên* — cùng đơn vị, cùng yêu cầu, không bao giờ xin lại được nữa. Ràng
buộc dựng lên để chống trùng lại thành ngõ cụt.

Quét đúng bước sắp ghi, ngay trước khi ghi — không có job nền. Một job định kỳ
đúng "phần lớn thời gian", và phần còn lại là đúng lúc người dùng đang chờ.

#### `request_fingerprint`

SHA-256 của input canonical `schedule_move` — 5 field, JSON `sort_keys` +
separator cố định. `date` và chuỗi ISO cho **cùng** vân tay (hai đường vào,
một danh tính). Field thiếu khác field bằng `False`. Khoá nội bộ không ảnh
hưởng. `max_price` bị **cấm** vào vân tay bằng một `ValueError` tường minh.

#### Luồng — thứ tự là toàn bộ nội dung

```
yêu cầu canonical
  → hỏi TẤT CẢ đơn vị song song, KHÔNG gửi max_price
  → đơn vị trả báo giá
  → persist TỪNG báo giá
  → rồi mới lọc theo max_price
  → luật tất định chọn đề xuất
```

Đảo hai bước cuối lên trước là hỏng cả cơ chế. Gửi ngân sách đi thì đơn vị trả
một con số sát ngân sách, và "đơn vị rẻ nhất" đo một thứ do chính P-118 tạo ra.
Lọc trước khi persist thì báo giá bị loại không để lại dấu vết — và câu "không
ai trong 500k, rẻ nhất là 620k" không có gì để dựa vào.

**Hai hàng rào cho ngân sách.** Phía gửi: `payload_gui_provider()` là
allowlist, nên field mới mặc định là *không gửi*. Phía nhận: mock provider dùng
`extra="forbid"` và trả **422** cho `max_price`. Allowlist nằm cùng phía với
đoạn mã sẽ vi phạm nó; hàng rào ở phía không do P-118 kiểm soát thì không.

#### Chín điều kiện tiêu thụ

`kiem_bao_gia()` kiểm từ thô tới tinh: tồn tại → ACTIVE → chưa hết hạn → đúng
service → đúng provider → vân tay khớp → amount/currency khớp → **đúng
workflow** → **đúng task**. Hai vế cuối **luôn** được kiểm; chữ ký hàm không
cho phép bỏ qua chúng nữa (thiếu là `TypeError` ngay lúc gọi).

`amount` được **đối chiếu**, không **đọc ra**: caller đưa vào thứ họ định dùng.
Chỉ đọc ra thì một con số bị sửa ở task sẽ lặng lẽ bị thay thế và không ai biết
đã có một lần thử.

`xac_nhan_bao_gia()` kiểm **lại toàn bộ** trong cùng lệnh ghi. Không phải thừa:

1. Bản đầu chỉ có `WHERE quote_id = $1 AND status = 'ACTIVE'`, nên một báo giá
   **hết hạn vẫn chuyển được sang CONFIRMED**.
2. Giữa lúc cổng nói "được" và lúc `UPDATE` chạy, chứng từ có thể vừa hết hạn
   hoặc vừa bị một lượt sửa làm SUPERSEDED. Cửa sổ nhỏ, nhưng nó mở đúng vào
   lúc hệ thống bận nhất.

Cổng vẫn giữ vai trò của nó: nó nói **vì sao** không được, và nói *trước* khi
ai đó nhìn thấy một lựa chọn không có thật. Nhưng nó không còn là thứ duy nhất
đứng giữa một chứng từ hỏng và một cam kết.

Đổi ngày/xe/thang máy/bốc xếp → báo giá đời cũ thành `SUPERSEDED` (không phải
`EXPIRED` — hết hạn là thời gian trôi, bị thay thế là khách đổi ý). Đã
`CONFIRMED` thì **không** bị viết đè: đó là cam kết đã xảy ra.

#### Nghiệm thu

**105 test mới**, tất cả xanh; toàn bộ suite **3923 xanh** (3 lỗi baseline theo
lịch). **Hai mươi chín mutation, hai mươi chín cái cắn** — 12 ở vòng đầu, 17 sau
khi vá bốn lỗ hổng:

| mutation | test bắt |
|---|---|
| payload gửi provider thành blocklist | rò `max_price` — 2 test |
| provider bỏ `extra="forbid"` | `test_any_unexpected_field_is_refused` |
| vân tay bỏ `move_vehicle` | 6 test |
| xác nhận không nguyên tử | `test_two_simultaneous_confirms...` |
| cổng bỏ kiểm hết hạn | 3 test |
| cổng bỏ kiểm workflow | `test_a_quote_from_another_workflow...` |
| cổng bỏ kiểm amount | `test_editing_the_amount_in_the_task...` |
| lọc ngân sách trước khi persist | 2 test |
| không dọn báo giá đời cũ | `test_changing_the_request_starts_a_clean_round` |
| connector không ép kiểu `amount` | `test_a_numeric_string_amount...` |
| bỏ kiểm đơn vị mạo danh | `test_a_provider_cannot_quote_on_behalf_of_another` |
| bỏ `UNIQUE ... WHERE ACTIVE` | 2 test |
| **neo lại thành tuỳ chọn ở domain** | `test_the_ownership_check_is_not_optional` |
| **cổng bỏ kiểm neo (bản cũ)** | `test_a_quote_from_another_workflow...` |
| **`luu_bao_gia` không kiểm `tool` của bước** | `test_a_quote_cannot_be_anchored_to_a_lookup_step` |
| **`luu_bao_gia` nhận báo giá đã hết hạn** | `test_a_provider_quote_that_is_already_expired...` |
| **đường đọc không lọc hạn (SQL)** | `test_an_expired_quote_never_reaches_the_recommendation` |
| **`loc_theo_ngan_sach` không lọc hạn** | `test_an_expired_quote_is_filtered_even_when_it_fits...` |
| **bỏ bước quét hết hạn** | `test_a_quote_that_expires_can_be_asked_for_again` |
| **quét hết hạn đụng cả dòng còn hạn** | 2 test |
| **bỏ `UNIQUE (provider, external_quote_id)`** | 2 test |
| **gộp lỗi neo vào `QUOTE_MALFORMED`** | `test_anchoring_to_the_wrong_step_is_named_as_our_bug...` |
| **8 vế của lệnh xác nhận, từng vế một** | 8 test, mỗi vế một test riêng |

Vế cuối đáng nói: kiểm chúng một lượt cùng nhau thì một điều kiện bị xoá vẫn
xanh nhờ các điều kiện còn lại, và mutation "bỏ một vế" sống sót. Mỗi ca phá
đúng **một** vế, nên mỗi vế có một bài kiểm nói tên nó.

**Canary trên `p118_db`** — connector thật → mock provider thật qua HTTP →
PostgreSQL thật (dữ liệu gieo đã dọn):

```
1. MOV-03 420k · MOV-01 430k · MOV-02 470k    → ngân sách 440k → đề xuất MOV-03
2. neo vào bước tra cứu T0      → ghi 0, QUOTE_ANCHOR_INVALID
   neo vào bước T99 không có    → ghi 0, QUOTE_ANCHOR_INVALID
   gọi cổng thiếu neo           → TypeError (không gọi được)
3. sai đơn vị→WRONG_PROVIDER · sai giá→AMOUNT_MISMATCH
   đổi yêu cầu→STALE_REQUEST · workflow khác→WRONG_WORKFLOW · bước khác→WRONG_TASK
4. sai giá, VÒNG QUA cổng, gọi thẳng lệnh xác nhận → CHẶN, vẫn ACTIVE
   hai lượt xác nhận đồng thời                    → 1/2 thắng
5. xác nhận báo giá quá hạn     → CHẶN
   hỏi lại sau khi hết hạn      → ghi 3, không lượt nào bị chặn
   database: ACTIVE=3, EXPIRED=3, đề xuất dùng chứng từ MỚI
6. cặp (đơn vị, mã báo giá) bị trùng: 0
```

Điểm 4 là điểm quan trọng nhất của vòng vá: canary gọi **thẳng** lệnh xác nhận,
bỏ qua cổng, để kiểm mệnh đề `WHERE` chứ không kiểm kỷ luật của call site.

Ngoài lề: mock provider trước đây sinh mã bằng bộ đếm reset mỗi lần khởi động,
nên sau một lượt deploy nó phát lại `QMOV-001` và ràng buộc mới sẽ từ chối một
báo giá hợp lệ. Đã đổi sang mã có hậu tố ngẫu nhiên — ngoài đời không nhà cung
cấp nào đánh số lại từ đầu sau khi khởi động lại máy chủ.

#### Chưa làm trong B (đúng phạm vi)

UI, trạng thái `PROPOSED`, vòng sửa lỗi, feature flag, và **chưa nối vào
Planner**. Ranh giới với A khi C nối vào: task có quote hợp lệ → chủ sở hữu lấy
từ quote đã persist; task cũ chưa có quote → tiếp tục `don_vi_mac_dinh`; không
bao giờ lấy chủ sở hữu trực tiếp từ `provider_id` do model/biểu mẫu gửi; quote
không hợp lệ → **không ghim approval**.

### C. Một đường chọn canonical — **XONG** (27/08)

Bước B cho chứng từ một danh tính kiểm chứng được. Bước C biến nó thành một
lựa chọn — và luật quan trọng nhất ở đây là luật **không làm**.

#### Resolver — chỉ TRÙNG KHỚP, không chứa nhau

`src/orchestration/provider_resolver.py`. Nguồn canonical là
`src/mock/service_providers.py` và **chỉ nó** — không bảng tên/alias/đơn vị song
song, vì hai danh mục là hai chỗ để lệch nhau.

Chuẩn hoá **chính tả**, không suy đoán ngữ nghĩa: bỏ dấu, bỏ hoa/thường, dấu
câu thành khoảng trắng. `đ` phải xử lý riêng — nó không tách được bằng NFD, nên
thiếu dòng ấy thì "Đại Tín" thành `đai tin` và không bao giờ khớp với người gõ
không dấu, tức hỏng đúng với người gõ nhanh nhất.

**Khớp CHÍNH XÁC với ba trường**: `provider_id`, `ten`, `ten_thuong_hieu`.
Không chứa nhau, không khoảng cách chỉnh sửa, không điểm số, không ngưỡng.

Bản đầu còn nhánh "chứa nhau" và nó mở một lỗ mà không bài kiểm nào lúc ấy bắt:

```
"chuyển nhà"  chỉ nằm trong "Chuyển nhà Minh Phát"  → MOV-01
"vận tải"     chỉ nằm trong "Vận tải Đại Tín"       → MOV-02
"dịch vụ"     chỉ nằm trong "Dịch vụ An Khang"      → MOV-03
```

Cả ba là **mô tả loại hình**, không phải tên khách chỉ định — và cụm đầu có mặt
trong hầu hết câu về chuyển nhà, tức đúng thứ model dễ trích nhầm vào ô tên đơn
vị nhất. Khi ấy resolver biến một lỗi trích thành một lựa chọn tài chính hợp
lệ: vi phạm thẳng *"model đề xuất, code xác minh"* — code phải là chỗ lỗi ấy
**dừng lại**, không phải chỗ nó được hợp thức hoá.

`ten_thuong_hieu` là thứ cho phép bỏ hẳn phép chứa-nhau mà vẫn gọi được "Đại
Tín". Nó là **thuộc tính của đơn vị trong cùng nguồn canonical** — bắt buộc,
không mặc định, nên một đối tác mới quên khai sẽ vỡ lúc dựng danh mục thay vì
lặng lẽ thành đơn vị gọi tên ngắn không ai tra ra.

| | |
|---|---|
| `FOUND` | khớp đúng một đơn vị |
| `AMBIGUOUS` | khớp nhiều — trả **danh sách ứng viên**, không chọn cái "khớp tốt hơn" |
| `UNKNOWN` | không khớp gì |

Phạm vi theo **dịch vụ** là bắt buộc: một câu về chuyển nhà không được resolve
ra một đội bảo trì.

**Sáu thất bại có chủ ý**, tất cả đều là thứ một bộ so khớp "thông minh" đoán
được: `chuyển nhà` · `vận tải` · `dịch vụ` (loại hình) · `Minh` (nửa thương
hiệu) · `MOV` (tiền tố mã) · `Đại Tính` (sai một chữ) · `đội Đại Tín bên quận
7` (tên lẫn chữ khác). Tất cả → `UNKNOWN`, tầng trên hỏi lại.

Hai bất biến của danh mục được khoá bằng test: không tên/mã/thương hiệu nào trỏ
vào hai đơn vị; và `ten_thuong_hieu` phải **ngắn hơn** `ten` (khai cho có thì
nó không thêm cách gọi nào).

**Không có bảng cụm từ kích hoạt** trong toàn bộ file.

#### Một hàm cho ba đường vào

`src/orchestration/provider_selection.py`. Ba cách khách nói — chỉ rõ tên, nói
ngân sách, không nói gì — khác nhau đúng **hai tham số** và đi qua **cùng một**
chuỗi quyết định. Ba nhánh riêng là cách nhanh nhất để chúng lệch nhau, và chỗ
lệch sẽ nằm ở luật phá thế hoà hoặc luật ngân sách: những chỗ không ai nhìn
thấy cho tới khi hoá đơn sai.

Thứ tự quyết định, **không đảo được**:

1. Lọc chứng từ còn sống. Hết hạn không phải một lựa chọn, kể cả khi rẻ nhất —
   và đây là chỗ dễ sai nhất, vì rẻ nhất cũng là thứ xếp đầu.
2. Có chỉ đích danh? **Tra tên trước mọi thứ khác.** Một cái tên không tra ra
   được thì ngân sách chưa liên quan gì; đảo thứ tự thì khách đi nâng ngân sách
   để sửa một lỗi chính tả.
3. Đơn vị ấy có báo giá không? Không có là **câu trả lời**, không phải lý do để
   chọn bên khác.
4. Vượt ngân sách? Nói ra **xung đột**.
5. Không ai được chỉ định: lọc ngân sách rồi xếp hạng giá → đánh giá → mã.

#### Luật không làm

Khách nói *"cho tôi Đại Tín, trong 450 nghìn"* là hai điều kiện mâu thuẫn.
Tự gỡ bằng cách chọn MOV-03 (420k, vừa ngân sách) là quyết định thay họ về
tiền — và họ chỉ biết khi đọc hoá đơn mang tên một công ty họ không chọn.

`OVER_BUDGET` vì thế trả **cả hai** con số: đơn vị được chỉ định báo bao nhiêu,
và giá thật rẻ nhất là bao nhiêu. Tầng trên nói được đủ vế; khách tự quyết.

Tương tự, đơn vị được chỉ định không báo giá → `NO_AVAILABLE_QUOTE` **kèm mã
đơn vị**, không thay bằng bên khác. Và "hết hạn hết" khác "không ai trong ngân
sách": hai tình huống dẫn tới hai hành động, gộp lại thì khách được bảo đi nâng
ngân sách cho một việc chỉ cần bấm hỏi lại.

#### Hai hàng rào ở biên

Cả hai bắt **lỗi lập trình**, không phải tình huống của khách, nên chúng đứng
ngoài chuỗi quyết định nghiệp vụ.

**Đúng dịch vụ.** `chon_don_vi()` tự lọc chứng từ có `service_type` khác — kiểm
ở hàm chọn chứ không chỉ ở wrapper đọc database, vì caller truyền thẳng một
danh sách là đường vào hợp lệ và hàng rào chỉ ở wrapper là hàng rào chỉ có với
một trong hai đường. Hai chứng từ cùng hình dạng, khác ngành; không có gì trong
`BaoGia` tự nói ra điều đó. Loại + log `error`.

**Ngân sách đọc được.** `max_price` phải là số nguyên **dương**; `bool`, số âm,
số thực và chuỗi đều → `INVALID_BUDGET`. `max_price` đến từ một lượt trích của
model, nên `"450000"`, `-1`, `True` đều có thể tới nơi — và cả ba đi lọt qua
phép so sánh rồi ra `OVER_BUDGET`, tức một câu trả lời **sai về nghiệp vụ** cho
một lỗi kiểu dữ liệu. `True` âm thầm nhất: `bool` là `int` trong Python, nên
`True` = 1 và mọi báo giá đều "vượt ngân sách 1 đồng".

#### Kết quả có kiểu

`SELECTED` · `UNKNOWN_PROVIDER` · `AMBIGUOUS_PROVIDER` · `OVER_BUDGET` ·
`NO_AVAILABLE_QUOTE` · `INVALID_BUDGET`. Tập **đóng** — một nhánh trả chuỗi lạ
làm test đỏ.

#### C chỉ ĐỌC

Không xác nhận báo giá, không ghim hàng đợi duyệt, không gọi ra ngoài. Test
chụp toàn bộ dấu vết có thể để lại (trạng thái chứng từ, hàng đợi duyệt, trạng
thái bước) trước và sau **sáu** lượt chọn khác nhau và so bằng.

#### Nghiệm thu

**110 test mới**, suite **4033 xanh** (3 baseline theo lịch). **Hai mươi
mutation, hai mươi cái cắn** — 13 ở vòng đầu, 7 sau khi vá lỗ chứa-nhau:

`AMBIGUOUS`→chọn đại · bỏ giới hạn dịch vụ · bỏ xử lý `đ` · vượt ngân
sách→âm thầm đổi · không báo giá→lấy bên khác · không lọc hết hạn · bỏ vế phá
thế hoà · đọc không lọc vân tay · khớp fuzzy theo tiền tố · tên lạ vẫn đi tiếp
xuống ngân sách · `OVER_BUDGET` không nói giá rẻ nhất · `NO_AVAILABLE_QUOTE`
không nói đơn vị nào · hết hạn hết→gọi là `OVER_BUDGET` · **đưa lại phép
chứa-nhau** (23 test đỏ) · **bỏ `ten_thuong_hieu` khỏi luật khớp** (17 đỏ) ·
**bỏ hàng rào dịch vụ** · **bỏ hàng rào ngân sách** · **ngân sách nhận `bool`**
· **ngân sách nhận số âm** · **`ten_thuong_hieu` khai bằng tên đầy đủ**.

**Canary trên `p118_db`**, chứng từ thật từ mock provider qua HTTP:

```
chứng từ: MOV-03=420.000 · MOV-01=430.000 · MOV-02=470.000

không nói gì                          → SELECTED            MOV-03 @ 420.000
ngân sách 425k                        → SELECTED            MOV-03 @ 420.000
ngân sách 100k                        → OVER_BUDGET         rẻ nhất 420.000
'đại tín' / 'DAI TIN' / 'Vận tải Đại Tín'
                                      → SELECTED            MOV-02 @ 470.000
'Đại Tín' + 450k                      → OVER_BUDGET         MOV-02 @ 470.000, rẻ nhất 420.000
'Đại Tín' + 500k                      → SELECTED            MOV-02 @ 470.000
'MOV' · 'chuyển nhà' · 'vận tải' · 'dịch vụ' · 'Minh'
'Đại Tính' · 'đội Đại Tín bên quận 7' · 'Thành Đạt'
                                      → UNKNOWN_PROVIDER
ngân sách -1 / '450000' / True        → INVALID_BUDGET

chỉ đọc: database Y NGUYÊN trước/sau 20 lượt chọn
```

Hai dòng đáng nhìn nhất. `'Đại Tín' + 450k`: MOV-03 rẻ hơn **và** vừa ngân sách
đang nằm ngay đó, hệ thống **không** lấy nó. `'chuyển nhà'`: một lượt trích
nhầm của model dừng lại ở resolver thay vì thành một đơn hàng cho MOV-01.

#### Nợ mang sang

Bảng provider canonical + khoá ngoại cho `service_provider_id` — hoãn sau demo
theo thống nhất. Trong C, `src/mock/service_providers.py` là nguồn duy nhất.

### D. Xác nhận của người dùng — **XONG** (27/08)

#### Bảng `service_provider_proposals`

```
proposal_id · workflow_id · task_id · quote_id · status
created_at · confirmed_at
```

**Bảy cột, hết.** Không `service_provider_id`, không `amount`, không `currency`,
không `approval_actor` — chúng nằm trên chứng từ báo giá, và đọc qua `quote_id`.

Chép sang đây là tạo nguồn sự thật thứ hai, và hai nguồn thì lệch — lệch đúng
vào lúc báo giá bị thay thế hoặc hết hạn, tức đúng lúc con số cũ trông vẫn hợp
lệ. Có một bài kiểm soi thẳng `information_schema` để một bản vá sau này không
"tiện tay" thêm cột.

`approval_actor` cũng không có mặt, và đó là cố ý. Nó được **suy ra** lúc dựng
câu trả lời — `USER` khi đề xuất còn xác nhận được, `PROVIDER` sau khi hàng đợi
đơn vị đã mở. Lưu nó nghĩa là có hai chỗ nói "đang chờ ai", và chỗ thứ hai sẽ
đứng im đúng lúc việc đổi tay. Nó **không** thay thế đề xuất đã persist: nó là
một trường hiển thị, không phải trạng thái.

#### State machine

```
PROPOSED ──── khách bấm đồng ý ──────→ CONFIRMED   (không rời khỏi đây)
         ──── chứng từ hết hạn ───────→ EXPIRED
         ──── đề xuất mới thay thế ───→ SUPERSEDED
```

Không có `SEARCHED`, không có `USER_CONFIRMED`, không có `CANCELLED`, không có
`AWAITING_PROVIDER` — bản kế hoạch đầu liệt kê chúng, và bản triển khai thì
không. "Đang chờ đơn vị" không phải một trạng thái của đề xuất: nó là hệ quả
của việc `service_approvals` có một dòng `AWAITING`.

#### Ràng buộc ở database

- `workflow_id`/`task_id` **NOT NULL** + khoá ngoại tổng hợp tới `workflow_tasks`
- `quote_id` khoá ngoại tới `service_quotes`, **và** khoá ngoại tổng hợp
  `(quote_id, workflow_id, task_id)` — khoá đơn chỉ nói "chứng từ có thật",
  không nói "nó thuộc bước này"
- `UNIQUE (workflow_id, task_id) WHERE status='PROPOSED'` — đúng một đề xuất
  đang sống mỗi bước
- `CHECK (status='CONFIRMED') = (confirmed_at IS NOT NULL)`

#### Lượt xác nhận — một transaction

`xac_nhan_de_xuat(pool, proposal_id, owner_user_id)` nhận **hai** tham số.
`tool`, nhãn dịch vụ, chi tiết, người yêu cầu đều đọc trong transaction — nhận
chúng làm tham số là mở đường cho người gọi tự khai mình đang đặt dịch vụ gì.

`service_approvals.service_provider_id` lấy từ **chứng từ**. Không từ body,
không từ task, không từ model.

Từ lệnh ghi đầu tiên trở đi **không còn `return` nào cho nhánh hỏng** — mọi bất
biến vỡ đều ném để rollback. Ba lượt ghi đều kiểm command tag.

#### Đọc fail-closed

`GET` trả `effective_status` + `can_confirm`, tính từ **cả** đề xuất lẫn chứng
từ bằng đồng hồ database. Tin `proposal.status` một mình là fail-OPEN: cột vẫn
ghi `PROPOSED` cho tới khi có ai đó dọn, và đến lúc ấy khách đã bấm ba lần.
Đường đọc không ghi gì.

#### Vòng đời chứng từ ↔ đề xuất

`don_bao_gia_va_de_xuat()` là đường dọn canonical, một transaction: chứng từ
đời cũ `SUPERSEDED`, chứng từ quá hạn `EXPIRED`, và đề xuất đang chờ đi theo
**đúng loại** trạng thái của chứng từ nó trỏ vào.

### E. Feature flag — **XONG** (27/08)

`SERVICE_PROVIDER_MATCHING`, và **chỉ đúng chuỗi `"1"` là bật**. Thiếu biến,
`""`, `"0"`, `"true"`, `"yes"`, `"01"`, `" 1"` — tất cả **tắt**.

ALLOWLIST chứ không blocklist, vì đây là fail-closed cho một tính năng đụng vào
tiền: một cấu hình gõ sai phải để hệ thống chạy như **cũ**, chứ không bật một
đường mới chưa ai xem lại. `"true"` bị từ chối là cố ý — nhận nó nghĩa là phải
trả lời "thế còn `True`, `TRUE`, `on`, `enabled`?", và mỗi câu trả lời thêm một
cách để hai môi trường hiểu khác nhau về cùng một dòng trong `.env`.

Không `strip()`, không `lower()`: một khoảng trắng lọt vào `.env` là dấu hiệu
file ấy được sinh ra bởi một công cụ không ai kiểm soát, chứ không phải một ý
định.

**MỘT** chỗ đọc (`src/common/feature_flags.py`), đọc lại mỗi lần, không cache.
Mặc định `0` ở `.env.example`; test khoá cả file mẫu lẫn `docker-compose.yml`.

Khi tắt, đường cũ chạy nguyên vẹn: đơn vị mặc định theo `provider_directory`,
hàng đợi duyệt mở ngay, không chứng từ, không đề xuất, không một lời gọi báo
giá nào.

### E1. Nối vào orchestration — **XONG** (27/08)

Điểm nối là `ServiceApprovalBoundary._park` — chỗ **duy nhất** ghim hàng đợi
duyệt cho một bước cần đơn vị. Chen vào đó nghĩa là mọi đường dẫn tới hàng đợi
(chạy lần đầu, chạy tiếp sau khi sửa, resume sau khi duyệt) đều đi qua cùng một
luật; chen ở chỗ khác là để lại ít nhất một đường không đi qua.

**Không nối vào Planner.** Model không sinh `provider_id`, `quote_id`, giá hay
trạng thái đề xuất. Nó chỉ trích hai mẩu — một đoạn **tên** và một con số
**ngân sách** — và cả hai đi qua resolver/hàng rào kiểu để mã quyết định. Đường
này không có lời gọi model nào.

Thứ tự khi cờ bật, với `schedule_move`:

```
input authoritative của bước (từ kế hoạch đã persist, không từ goal/body)
  → hỏi giá tất cả đơn vị          (dọn chứng từ + đề xuất đời cũ, một transaction)
  → chọn bằng resolver canonical    (C)
  → SELECTED  → ghim ĐÚNG MỘT proposal PROPOSED, bước dừng chờ KHÁCH
                 /review CHƯA có việc
  → còn lại   → fail-closed: không proposal, không việc cho đơn vị
```

`ProviderProposalRequiredError` là mã ngắt riêng, và `approval_actor` là `USER`.
Dùng lại `SERVICE_APPROVAL_REQUIRED` sẽ dựng câu *"đơn vị đang xác nhận, bạn
chờ chút nhé"* — nói câu ấy trước khi khách bấm là **sai hai lần**: không ai
bên kia nhận được việc, và khách đang được bảo là không phải làm gì trong khi
họ là người duy nhất còn phải làm.

#### Bất biến khi lặp

| tình huống | kết quả |
|---|---|
| poll/continue nhiều lần, yêu cầu không đổi | dùng lại đề xuất, **không** hỏi giá thêm lượt nào |
| poll/continue sau khi khách bấm | **không** dựng đề xuất mới; việc đang ở hàng đợi đơn vị |
| vân tay đổi | chứng từ + đề xuất cũ `SUPERSEDED` (một transaction), dựng đề xuất mới |
| chứng từ hết hạn | chứng từ + đề xuất `EXPIRED`, dựng đề xuất mới; cái cũ không bấm được nữa |
| restart | đọc lại đúng đề xuất đã persist, `can_confirm` không đổi |

Ca thứ hai là một lỗi thật do test tìm ra: `de_xuat_dang_cho` trả `None` sau khi
đề xuất đã `CONFIRMED`, nên lượt chạy tiếp dựng một đề xuất **thứ hai** mời
khách chọn lại một việc họ vừa chốt. Phải hỏi thêm "đã ai chốt chưa", không chỉ
"còn cái nào đang chờ không".

#### Payload trả cho chat/API

`proposal_id` · `provider {id, name}` · `amount` · `currency` · `reason` ·
`valid_until` · `effective_status` · `can_confirm`.

Ghép lúc **đọc** từ đề xuất + chứng từ + danh mục đơn vị. Không trường nào được
lưu vào bảng đề xuất — một bản sao là một bản sẽ lệch. `reason` dựng từ dữ kiện
đã persist, không từ một lượt gọi model: một câu do model viết có thể nói sai
lý do cho đúng con số, và khách không có cách nào biết.

#### Nghiệm thu

20 test cờ + 13 test đường orchestration + 7 test HTTP end-to-end. **Mười
mutation, mười cái cắn**: bỏ cờ · cờ mặc định bật · cờ nhận `"true"` · ghim
hàng đợi trước khi khách bấm · poll dựng đề xuất mới · dùng lại đề xuất sau khi
vân tay đổi · dùng lại đề xuất hết hạn · sau xác nhận vẫn dựng đề xuất mới ·
vượt ngân sách vẫn ghim đề xuất · lấy đơn vị từ input thay vì chứng từ.

#### Chưa làm trong E1

UI/browser, và `create_maintenance_request` (chưa có endpoint báo giá ở mock —
thêm nó vào danh sách trước khi có endpoint sẽ làm mọi yêu cầu bảo trì rơi vào
nhánh "không đơn vị nào báo giá", tức hỏng một luồng đang chạy để mở một luồng
chưa chạy).

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
