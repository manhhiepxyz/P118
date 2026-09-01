# tests/e2e — nghiệm thu trên stack thật

Ba harness ở đây chạy trên **stack Docker đang chạy**, không phải trên một backend
local dựng riêng cho test. Đó là điểm khác biệt quan trọng: mọi tầng test khác
(unit, ASGI, PostgreSQL) đều dựng môi trường của riêng chúng với cấu hình đúng,
nên không tầng nào phát hiện được một `docker-compose.yml` sai cấu hình.

| File | Kiểm gì | Chạy |
|---|---|---|
| `system_docker.py` | 21 khẳng định qua HTTP + PostgreSQL của stack | `python tests/e2e/system_docker.py` |
| `browser_acceptance.mjs` | 41 khẳng định qua DOM thật (Playwright) | `npm test` |
| `bad-llm.override.yml` | cấu hình LLM sai, dùng cho mục 5 và cho mutation | (không chạy trực tiếp) |

## Harness chọn lại đơn vị cung cấp (`p118_e2e_db`, backend local cổng 8100)

Ba file dưới đây KHÔNG chạy trên stack Docker. Chúng cần một backend local nối
vào `p118_e2e_db` với `SERVICE_PROVIDER_MATCHING=1`, vì chúng ghi dữ liệu nghiệp
vụ và `p118_db` là kho demo — mỗi lần chạy đều đếm `p118_db` trước/sau và dừng
nếu nó đổi.

| File | Kiểm gì | Gọi model? |
|---|---|---|
| `provider_reselection_journey.mjs` | giao diện + ba endpoint, dữ liệu gieo bằng SQL | không |
| `reselection_through_the_model.mjs` | cả đường từ MỘT CÂU tiếng Việt → SUCCESS, kèm bài đối chứng `INVALID_REQUEST` | **có** |
| `reselection_across_restarts.mjs` | ba khe, mỗi khe GIẾT và khởi động lại backend | không |

`reselection_across_restarts.mjs` chạy theo pha và **harness gọi nó phải sở hữu
vòng đời backend** — nó không tự dựng backend:

```
node reselection_across_restarts.mjs seed      # rồi restart backend
node reselection_across_restarts.mjs verify1   # rồi restart backend
node reselection_across_restarts.mjs verify2   # rồi restart backend
node reselection_across_restarts.mjs verify3
```

PostgreSQL và volume KHÔNG bị đụng; chỉ tiến trình backend bị giết. Đây là bài
DUY NHẤT trả lời được "một tiến trình thứ hai đọc dữ liệu này thì thấy gì" — các
bài `_DEMO_JOBS.clear()` trong `tests/test_db/` là ĐỌC NGUỘI, không phải restart.

## Chuẩn bị

```bash
sh scripts/stack_up.sh                                   # stack + /ready xanh
cd frontend && VITE_API_PROXY_TARGET=http://127.0.0.1:8080 npm run dev -- --port 5273
cd tests/e2e && npm run setup                            # một lần
```

## Chạy một phần

Bộ browser mất vài phút vì gọi LLM thật. Khi chỉ cần kiểm một mục (ví dụ lúc chạy
mutation), dừng sớm:

```bash
P118_STOP_AFTER=auth  npm test   # chỉ mục 1 — thông báo đăng nhập
P118_STOP_AFTER=quick npm test   # tới mục 2 — quick action mở form rồi mới gửi
P118_STOP_AFTER=clarify npm test # tới câu hỏi phụ trong clarification
npm test                         # đầy đủ, gồm mục 5 (lỗi cấu hình)
```

Các mục phụ thuộc nhau theo thứ tự chạy (quick action cần một tài khoản đã được
duyệt liên kết căn hộ), nên đây là "dừng sau", không phải "chỉ chạy".

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `P118_APP` | `http://127.0.0.1:5273` | React dev server |
| `P118_API` | `http://127.0.0.1:8080/api/v1` | backend Docker |
| `P118_STOP_AFTER` | (trống) | `auth`/`link`/`admin`/`quick`/`clarify`/`happy`/`idor` |

## Lưu ý

- Bộ browser **đổi cấu hình container** ở mục 5 rồi khôi phục. Nếu nó bị ngắt giữa
  chừng, chạy `docker compose up -d --force-recreate backend` để trả stack về bình thường.
- Dữ liệu ghi vào `p118_db` — cùng kho với demo. Mọi canary đều có tiền tố riêng
  theo timestamp nên không đụng dữ liệu có sẵn.
- Không harness nào in API key, token hay DSN.
