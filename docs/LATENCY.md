# Latency: đo ở đâu, Fast Lane làm gì, vướng chỗ nào

Tài liệu này trả lời bốn câu hỏi, theo đúng thứ tự cần biết:

1. [Latency được đo ở đâu trong code](#1-latency-được-đo-ở-đâu)
2. [Fast Lane làm như thế nào, nằm ở file nào](#2-fast-lane-làm-như-thế-nào)
3. [Fast Lane vướng gì khi dùng thật](#3-fast-lane-vướng-gì-khi-dùng-thật)
4. [Compact Planner bổ trợ được chỗ nào](#4-compact-planner-bổ-trợ-được-chỗ-nào)

Mọi con số dưới đây đọc từ bảng `llm_usage` trên database thật, không phải ước
lượng. Câu truy vấn để tự kiểm nằm ở [mục 5](#5-tự-đo-lại).

---

## 0. Kết luận một dòng

**Thời gian chờ ≈ số token model SINH RA × 7,6ms.** Không phải "model nghĩ
lâu", mà là model phải *gõ ra* quá nhiều chữ. Đo trên 658 lượt gọi thật:

```
latency_ms  =  616  +  7,62 × completion_tokens        R² = 0,994
```

R² = 0,994 nghĩa là gần như toàn bộ chênh lệch thời gian giữa các lượt gọi
giải thích được **chỉ bằng** số token đầu ra. Prompt dài không đáng kể (lượt
`plan` nạp trung bình 12.184 token đầu vào mà vẫn không phải phần đắt).

Hệ quả trực tiếp: **muốn nhanh hơn thì phải giảm số token model phải sinh ra.**
Đổi model, tăng máy, hay tinh chỉnh mạng đều không chạm vào biến này.

---

## 1. Latency được đo ở đâu

### 1.1 Bộ đo: `LlmUsageLogger`

**File: `src/monitoring/usage_tracker.py`**

Đây là một `BaseCallbackHandler` của LangChain, gắn vào mọi `ChatOpenAI` được
tạo qua `get_llm(callbacks=[usage_logger, ...])`.

| Móc | Dòng | Việc |
|---|---|---|
| `on_llm_start` | ~82–85 | Ghi mốc `time.monotonic()` khi request rời máy |
| `on_llm_end` | ~87–106 | Tính `latency_ms`, đọc token từ `usage_metadata`, xếp vào `self.pending` |
| `flush()` | ~108–122 | Ghi cả lô xuống bảng `llm_usage` |

Ba điểm thiết kế đáng lưu ý:

- **Đo bao trọn lượt gọi**, gồm cả thời gian mạng và thời gian model sinh token
  — đúng thứ người dùng phải chờ, không phải thời gian tính toán thuần.
- **`flush()` là nơi DUY NHẤT chạm database**, gọi trong `finally` của caller.
  Ghi usage hỏng không được làm vỡ workflow (`except` nuốt lỗi, chỉ log).
- **No-op khi không có `usage_context`** — lượt gọi LLM ngoài phạm vi demo/eval
  không sinh dữ liệu rác.

### 1.2 Nhãn giai đoạn: `usage_context`

Mỗi lượt gọi được gắn một `stage`. Không có nhãn thì mọi lượt trộn vào nhau và
không đo được đường tắt có tác dụng hay không.

| `stage` | Đặt ở đâu | Là gì |
|---|---|---|
| `plan` | `src/orchestration/demo_service.py:558` | Planner đầy đủ |
| `fast_plan` | `src/agents/fast_lane.py:191` | Đường tắt Fast Lane |
| `respond` | `src/api/routes.py:2772` | Response Agent viết câu trả lời |

> Việc tách `fast_plan` khỏi `plan` là bắt buộc. Gộp chung thì lượt gọi rẻ kéo
> trung vị xuống và che mất chi phí thật của Planner — đúng phép đo đã dẫn tới
> việc tạo ra Fast Lane.

### 1.3 Nơi lưu: bảng `llm_usage`

**Định nghĩa: `src/db/schema_migrations.sql:231`**

```
workflow_id · run_id · stage · provider · model
prompt_tokens · completion_tokens · total_tokens · latency_ms · created_at
```

Có index theo `workflow_id` và `created_at`. Không lưu goal, không lưu nội dung
prompt, không lưu output — chỉ số đo.

### 1.4 Số đo hiện tại

| stage | lượt | p50 | p90 | token vào | token **sinh ra** |
|---|---|---|---|---|---|
| `plan` | 132 | **29,63s** | 76,01s | 12.184 | **4.633** |
| `compact_plan` | 8 | 10,78s | 20,02s | 1.516 | 1.723 |
| `fast_plan` | 57 | **1,61s** | 3,02s | 674 | **195** |
| `respond` | 461 | 1,55s | 1,96s | 2.525 | 118 |

Đọc bảng theo cột cuối: `plan` chậm gấp ~18 lần `fast_plan` vì nó sinh ra gấp
~24 lần số token. Đúng công thức ở mục 0.

**Vì sao `plan` phải sinh nhiều token đến vậy:** nó tự gõ ra cả đồ thị công
việc — thứ tự các bước, `depends_on`, và từng `InputRef` nối kết quả bước trước
sang bước sau. Đó là phần **cơ học**: `plan_assembly` dựng lại đúng 38/38 đồ
thị và 149/149 InputRef của các kế hoạch đã ghi, bằng code, không cần model.

---

## 2. Fast Lane làm như thế nào

### 2.1 Các file liên quan

| File | Vai trò |
|---|---|
| `src/agents/fast_lane.py` | Toàn bộ logic đường tắt |
| `src/agents/plan_assembly.py` | Dựng đồ thị bằng code (`assemble_plan`) |
| `src/agents/validator.py` | `TaskPlanValidator` — cổng chung, không có cửa sau |
| `src/orchestration/demo_service.py:602–607` | Dựng đối tượng, đọc cờ `FAST_LANE` |
| `src/agents/graph.py:524–537` | Gọi nó trong `plan_node`, trước Planner |

### 2.2 Luồng chạy

```
goal người dùng
   │
   ├─► [1] Một lượt LLM RẺ  (get_llm(fast=True) — TẮT reasoning)
   │        hỏi đúng hai thứ: cần DỊCH VỤ nào? GIÁ TRỊ là gì?
   │        KHÔNG hỏi thứ tự, KHÔNG hỏi InputRef, KHÔNG hỏi phí
   │        → ~195 token đầu ra → ~1,6s
   │
   ├─► [2] resolve_project_id()   tên dự án → mã nội bộ (code, không LLM)
   │       tiêm resident_id       từ tài khoản, model không được bịa
   │
   ├─► [3] assemble_plan()        code dựng đồ thị: thứ tự, depends_on,
   │                              InputRef, và tự thêm pay_fee
   │
   └─► [4] TaskPlanValidator.validate()   ← CỔNG CHUNG với Planner
            │
            ├─ đạt   → trả TaskPlan, KHÔNG gọi Planner   (~2,1s tổng)
            └─ trượt → trả None → Planner đầy đủ chạy như cũ
```

### 2.3 Bốn quyết định thiết kế quan trọng

**`fast=True` — tắt suy luận.** `get_llm(fast=True)` đặt
`reasoning_effort="none"`. Đây là lý do nó sinh ~195 token thay vì ~4.633. Chấp
nhận được vì nó **không quyết định gì** — nó chỉ trích thông tin, còn kế hoạch
do code lắp và Validator duyệt.

**`pay_fee` KHÔNG nằm trên thực đơn của model.** Nó là hệ quả bắt buộc của
`book_parking` (37/37 kế hoạch đã ghi, không ngoại lệ theo cả hai chiều). Đo
được: để nó trên thực đơn làm độ chính xác chọn dịch vụ tụt **96% → 65%** —
bảy trên tám ca lệch là cùng một lỗi, model quên nó. `plan_assembly` thêm vào
bằng code.

**Khung JSON phải nằm trong prompt.** DeepSeek dùng `structured_output_method=
"json_mode"`, và chế độ đó **không gửi Pydantic schema cho model** — nó chỉ
nói "trả JSON". Đo được: bỏ khung ra khỏi `HUONG_DAN` thì model tự bịa tên
trường (`{"service": ..., "date": ...}`) và **54/54 lượt trượt schema**.

**Không có cổng kiểm riêng.** Kế hoạch Fast Lane lắp đi qua **đúng**
`TaskPlanValidator` mà kế hoạch Planner đi qua. Bằng chứng cần thiết, đo được
nguyên văn:

```
"cho mình xin cái chỗ để xe khu B từ 5/9 nhé, xe wave biển 51H-12345"
   → booking_date = "2023-09-05"
```

Model tự đoán năm và trượt ba năm. Đủ ô, đúng định dạng, lọt mọi phép kiểm
hình thức — và `TaskPlanValidator` chặn bằng *"has booking_date in the past"*.
Nếu Fast Lane có cổng riêng lỏng hơn, đơn đặt chỗ sai năm này đã chạy thật.

### 2.4 Công tắc

```
FAST_LANE=0   → tắt hoàn toàn, hệ thống chạy y như trước khi có nó
```

Logic là **opt-out**: chỉ đúng chuỗi `"0"` mới tắt
(`demo_service.py:603`). Công tắc tồn tại vì đây là thành phần **lõi** — một
hồi quy ở đây làm hỏng *mọi* yêu cầu, không phải một luồng.

### 2.5 Hiệu quả thật

Đo trên **56 workflow** có chạy Fast Lane (cộng dồn tới 26/8). Cột thời gian
chỉ tính hai stage lập kế hoạch (`fast_plan` + `plan`), không tính `respond`:

| kết quả | số workflow | thời gian lập kế hoạch |
|---|---|---|
| Fast Lane tự về đích | **20 (36%)** | **2,1s** |
| Chạy rồi vẫn phải gọi Planner | 36 (64%) | 30,7s |
| *(đối chứng: workflow không có Fast Lane)* | 119 | 43,2s |

**36% số yêu cầu xuống còn ~2 giây.** Tính cả 64% trượt (mất không ~1,6s
rồi vẫn đi Planner), kỳ vọng là `0,36 × 2,1 + 0,64 × 30,7 ≈ **20,5s**`.

> Đừng đọc "20,5s so với 43,2s" như một phép so sánh có kiểm soát. Hai nhóm
> workflow này khác thời điểm và khác loại câu; dòng 43,2s chỉ là mốc tham
> chiếu thô, không phải nhóm đối chứng được thiết kế.

> Tỷ lệ này **trôi theo thời gian** — lần đo trước (48 workflow) cho 29%. Nó
> phụ thuộc vào loại câu người dùng gõ, nên đừng coi là hằng số; chạy lại câu
> truy vấn ở [mục 5](#5-tự-đo-lại) để lấy số hiện tại.

---

## 3. Fast Lane vướng gì khi dùng thật

### 3.1 Nó nhị phân — trượt là mất trắng

`plan()` trả `TaskPlan | None`. Không có trạng thái thứ ba.

Nghĩa là nó **chỉ về đích được khi câu ĐỦ THÔNG TIN**. Thiếu đúng một ô —
người dùng quên nói giờ — thì `assemble_plan` ra kế hoạch thiếu ô, Validator
từ chối, và nó trả `None`. Nó *biết* thiếu ô nào nhưng **không có cách nào nói
ra**, nên yêu cầu ấy đi trọn đường 33 giây rồi Planner mới hỏi lại đúng câu
mà Fast Lane đã có thể hỏi ở giây thứ hai.

Đây là trần ~36%: phần lớn số còn lại không phải "hiểu sai", mà là **thiếu
thông tin** — loại việc Fast Lane không có ngôn ngữ để diễn đạt.

### 3.2 Túi giá trị PHẲNG — hai yêu cầu cùng loại đè lên nhau

`_DuDoan` (`fast_lane.py:86`) là một model **phẳng**: đúng một `viewing_date`,
đúng một `plate_number` cho cả câu.

Nên *"tham quan dự án A ngày 5/9 và dự án B ngày 6/9"* không biểu diễn được —
hai ngày ghi đè nhau, chỉ giá trị sau sống sót. Tương tự, hai `book_parking`
cho hai xe khác nhau chỉ để lại một task, vì `assemble_plan` khoá node theo
**tên tool**.

Loại câu này luôn rơi về Planner.

### 3.3 Không kiểm bằng chứng — model bịa giá trị vẫn lọt tới Validator

Model trả thẳng giá trị đã chuẩn hoá, không kèm đoạn văn bản nó đọc ra. Không
có gì đối chiếu "giá trị này lấy từ đâu trong câu người dùng".

Ca `2023-09-05` ở mục 2.3 là ví dụ: `TaskPlanValidator` bắt được **vì tình
cờ** ngày đó nằm trong quá khứ. Một giá trị bịa nhưng hợp lệ về hình thức —
model đoán `parking_zone: ZONE_A` trong khi người dùng chưa nói khu nào — sẽ
đi thẳng tới thực thi.

> Đã có một hướng vá cho việc này (`grounding_verifier`, bắt model trích dẫn
> nguyên văn cho từng giá trị rồi code tự đọc lại và đối chiếu). Nhưng bản
> siết qua 6 vòng ấy chặt tới mức **từ chối gần như mọi thứ** — đo được **0
> lượt về đích**, lý do `VALUE_MISMATCH`. Nó đang nằm ở nhánh
> `experiment/compact-planner-2026-08-26`, **không** dùng trên `develop`.
>
> Bài học: khi đọc lại số cũ, phải kiểm **phiên bản Fast Lane nào** sinh ra
> chúng. Con số 29%/2,2s là của bản trên `develop` (không có
> `grounding_verifier`); bản có verifier cho 0%.

### 3.4 Nó không biết ranh giới phạm vi

Bộ phân loại rẻ không phân biệt được việc Agent làm được và việc không. Đo
được: *"tôi muốn ký hợp đồng thuê căn p5"* bị xếp thành
`register_property_interest`, dù Agent không ký hợp đồng.

Vô hại **vì tình cờ** thiếu ô nên rơi về Planner. Nhưng đó là lý do `None` ở
đây chỉ có nghĩa *"không xử lý được"*, **không bao giờ** có nghĩa *"từ chối"* —
việc từ chối thuộc về Planner đầy đủ.

### 3.5 (ĐÃ SỬA) Lượt "Tiếp tục" không đi được đường nhanh

Đo được trước khi sửa, tách theo loại workflow:

| loại | có chạy Fast Lane | **về đích** | thời gian lập kế hoạch |
|---|---|---|---|
| gốc (từ `/start`) | 53 | 20 (38%) | 32,6s |
| **con (từ `/continue`)** | 4 | **0 (0%)** | **44,1s** |

Không lượt hỏi lại nào từng đi được đường nhanh — và còn *chậm hơn* workflow
gốc, vì phải trả thêm lượt Fast Lane bỏ đi.

Nguyên nhân là cấu trúc, không phải xác suất: `/continue` giữ **nguyên `goal`
cũ** và đặt câu trả lời mới vào `user_answers` (đúng thiết kế — goal là điều
người dùng nói *lúc đầu*, `user_answers` là điều họ nói *sau khi* biết còn
thiếu gì). Nhưng `plan_node` gọi `fast_lane.plan(goal, existing_context)` mà
**không truyền `user_answers`**, nên Fast Lane luôn thiếu đúng cái ô người
dùng vừa điền → trượt Validator → `None` → Planner đầy đủ.

Trớ trêu: `_apply_user_answers` nằm ngay dưới trong `plan_node`, nhưng nó ở
trong nhánh `if nhanh is not None` nên không bao giờ chạy tới.

**Đã sửa** — `plan()` nhận thêm `user_answers` và merge vào `values` **sau**
giá trị đọc từ goal (nên câu trả lời mới thắng goal cũ, cùng nguyên tắc
`_apply_user_answers`). Giá trị `None` không ghi đè. Đây là thêm một *nguồn
giá trị*, không nới cổng kiểm nào — kế hoạch vẫn qua `TaskPlanValidator`.
Test: `tests/test_the_fast_lane_uses_what_you_just_answered.py`.

### 3.6 Trượt thì đắt thêm

64% số lượt trả thêm ~1,6s cho một lượt gọi bỏ đi. Về kỳ vọng vẫn lãi (~20,5s
so với mốc 43,2s), nhưng nếu lưu lượng thật nghiêng nhiều về câu thiếu/mơ hồ thì
phần lãi mỏng đi.

---

## 4. Compact Planner bổ trợ được chỗ nào

**Trạng thái: thí nghiệm, đang ở nhánh `experiment/compact-planner-2026-08-26`,
KHÔNG chạy trên `develop`.**

### 4.1 Nó vá đúng ba lỗ hổng ở mục 3

| Vướng của Fast Lane | Compact Planner làm gì |
|---|---|
| §3.1 nhị phân, thiếu ô là mất trắng | Trả **năm trạng thái**: `READY`, `NEEDS_INFORMATION`, `INVALID_INFORMATION`, `QUESTION`, `UNKNOWN` — hỏi lại đúng ô thiếu ở giây thứ 9 thay vì giây thứ 33 |
| §3.2 túi giá trị phẳng | Mỗi yêu cầu là một `RequestDraft` riêng, giữ giá trị của **chính nó**; `assemble_plan_from_requests()` khoá node theo `(request_id, tool)` nên hai `book_parking` không đè nhau |
| §3.3 không kiểm bằng chứng | Model chỉ trả **văn bản**, không trả giá trị đã chuẩn hoá. Code tự đọc lại bằng `field_parsers.parse_field()` — cùng bộ đọc mà luồng vá câu trả lời dùng |

Bằng chứng cho §3.1: trên bộ 20 case thật, **17/20 giải quyết trọn không cần
Planner** — trong đó 5 case `NEEDS_INFORMATION` và 3 case `INVALID_INFORMATION`
là loại Fast Lane **luôn** phải nhường.

### 4.2 Nhưng chưa đủ nhanh — và biết chính xác vì sao

| | token sinh ra | p50 |
|---|---|---|
| Fast Lane | 195 | 1,61s |
| **Compact Planner** | **1.723** | **10,78s** |

JSON kết quả của Compact Planner chỉ cần ~150 token. Phần chênh ~1.500 token
là **reasoning token của DeepSeek thinking mode**.

Đối chứng nằm ngay trong bảng: Fast Lane dùng `get_llm(fast=True)` (tắt
reasoning) → ~195 token; Compact Planner dùng `fast=False` → 1.723 token. Cùng
loại việc, gấp gần 9 lần.

**Vì sao lại để `fast=False`:** tài liệu đo được tắt reasoning làm **Planner
đầy đủ** mất độ chính xác (5/6 → 4/6). Nhưng đó là đo trên Planner đầy đủ —
việc của nó nặng hơn hẳn vì phải tự dựng cả DAG. Compact Planner chỉ phân loại
và trích field, còn DAG do code lắp.

**Đây là giả thuyết CHƯA ĐO.** Không được áp số đo của Planner đầy đủ sang
Compact Planner — đó đúng là loại lỗi đã mắc một lần với con số 29% vs 0% ở
§3.3.

### 4.3 Việc cần làm để kết luận

Bộ eval 20 case đã có sẵn (`eval/run_compact_planner_eval.py`, trên nhánh thí
nghiệm) kèm baseline đã ghi: **95% đúng status · 0 wrong READY · p50 5,4s**.

Chạy lại đúng 20 case đó với reasoning tắt rồi so hai cột:

- Độ chính xác giữ nguyên → giảm 10,8s xuống ~4s, xong.
- Độ chính xác tụt → biết chắc không đánh đổi được, giữ `fast=False`.

Tốn 20 lượt gọi model thật, khoảng 4 phút.

### 4.4 Nếu cả hai cùng chạy: xếp NỐI TIẾP, không loại trừ

Hai đường bắt **hai loại câu khác nhau**, nên không thay thế nhau:

```
   nấc 1  Fast Lane        ~1,6s   câu ĐỦ THÔNG TIN       (~36% lưu lượng)
   nấc 2  Compact Planner  ~10s    câu THIẾU hoặc SAI
   nấc 3  Planner đầy đủ   ~33s    phần còn lại
```

Đánh đổi phải nói rõ: câu trượt **cả hai** nấc cộng thêm ~1,6s vào một đường
vốn đã chậm.

Một ràng buộc bắt buộc nếu triển khai: **mỗi nấc phải độc lập**. Một nấc ném
exception không được kéo nấc sau chết theo — nếu không, một lỗi ở đường *rẻ
nhất* đẩy **mọi** câu về 33 giây.

---

## 5. Tự đo lại

**Chi phí theo giai đoạn** — nhìn cột `token_sinh_ra` để biết chỗ đắt:

```bash
docker exec p118_postgres psql -U p118 -d p118_db -c "
SELECT stage, count(*) luot,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms))::numeric/1000,2) p50_giay,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY latency_ms))::numeric/1000,2) p90_giay,
       round(avg(completion_tokens)) token_sinh_ra
FROM llm_usage GROUP BY stage ORDER BY 3 DESC;"
```

**Fast Lane có thật sự thay được Planner không** — câu hỏi quan trọng nhất,
vì một lượt `fast_plan` nhanh mà vẫn phải gọi `plan` sau đó là lỗ vốn:

```bash
docker exec p118_postgres psql -U p118 -d p118_db -c "
WITH w AS (
  SELECT workflow_id,
         count(*) FILTER (WHERE stage='fast_plan') f,
         count(*) FILTER (WHERE stage='plan')      p,
         -- CHỈ cộng hai stage lập kế hoạch. Bỏ FILTER đi thì 'respond'
         -- (~1,5s mỗi lượt, có thể nhiều lượt/workflow) bị cộng vào và con
         -- số không còn so sánh được với bảng ở mục 2.5.
         sum(latency_ms) FILTER (WHERE stage IN ('fast_plan','plan')) ms
  FROM llm_usage WHERE workflow_id IS NOT NULL GROUP BY 1)
SELECT CASE WHEN f>0 AND p=0 THEN 'Fast Lane ve dich'
            WHEN f>0        THEN 'Chay roi VAN goi Planner'
            ELSE 'Chi Planner' END ket_qua,
       count(*) so_workflow, round(avg(ms)::numeric/1000,1) tb_giay_lap_ke_hoach
FROM w GROUP BY 1 ORDER BY 2 DESC;"
```

**Kiểm lại công thức latency ≈ token** trên dữ liệu mới nhất:

```bash
docker exec p118_postgres psql -U p118 -d p118_db -c "
SELECT round(regr_slope(latency_ms, completion_tokens)::numeric,2) ms_moi_token,
       round(regr_intercept(latency_ms, completion_tokens)::numeric) chan_ms,
       round(regr_r2(latency_ms, completion_tokens)::numeric,3) r2,
       count(*) mau
FROM llm_usage WHERE latency_ms IS NOT NULL;"
```

R² tụt dưới ~0,95 nghĩa là có yếu tố khác đã chen vào (provider đổi, retry, tải
mạng) — lúc đó công thức ở mục 0 không còn dùng để suy luận được nữa.

---

## 6. Bẫy khi đọc lại tài liệu này

**Luôn kiểm số đo thuộc phiên bản nào.** Đã mắc một lần: con số "Fast Lane
accept 0%" (từ bộ eval golden set, chạy trên bản **có** `grounding_verifier`)
bị đem áp cho lưu lượng thật của bản **không có** verifier — bản đó accept
29%. Hai con số đều đúng, nhưng đo hai thứ khác nhau.

**Bộ eval golden set không phải lưu lượng thật.** Nó cố ý dựng case khó để dò
lỗ hổng an toàn. Tỷ lệ accept trên đó luôn thấp hơn thực tế, và không dùng để
dự đoán hiệu quả production được.

**`stage` phải tách riêng.** Gộp `fast_plan` vào `plan` là mất khả năng trả lời
câu hỏi ở mục 5 — không còn biết đường tắt có thay được Planner hay không.
