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
