# P-118 — Audit đường React → Docker backend → LLM → Provider → PostgreSQL

Ngày: 2026-08-15 · Worktree: `integration/gate2-canonical`

Bảng này lập TRƯỚC khi sửa, và mỗi dòng "khoảng trống test" đã được lấp bằng
một test hoặc harness tái hiện được, trước khi code sản phẩm bị đụng tới.

Ký hiệu tầng test: **U** unit fake · **I** integration ASGI · **P** PostgreSQL
thật · **D** Docker system · **B** browser E2E · **E** manual eval LLM thật.

---

## 1. React API proxy

| | |
|---|---|
| Config đầu vào | `VITE_API_PROXY_TARGET` (dev), đường dẫn tương đối `/api/v1` (prod) |
| Nguồn sự thật | `vite.config.ts`; ở prod là reverse proxy trước bundle tĩnh |
| Lỗi có thể xảy ra | proxy trỏ sai cổng → mọi request 404/ECONNREFUSED |
| Trạng thái DB sau lỗi | không có gì được ghi |
| Response public | `ApiError(0)` — "Không kết nối được máy chủ" |
| Test hiện có | B (browser E2E chạy qua proxy thật) |
| **Khoảng trống** | không có test nào chạy React với **Docker** backend |

## 2. Auth

| | |
|---|---|
| Config đầu vào | `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRE_MINUTES` |
| Nguồn sự thật | bảng `users` |
| Lỗi có thể xảy ra | sai mật khẩu; token hết hạn; token rác |
| Trạng thái DB sau lỗi | không đổi |
| Response public | 401 với một câu duy nhất cho cả hai |
| Test hiện có | I, P |
| **Khoảng trống** | **client dùng CHUNG một câu 401 cho login sai và phiên hết hạn** → đã sửa (mục G) |

## 3. Capabilities

| | |
|---|---|
| Config đầu vào | token |
| Nguồn sự thật | `user_resident_links.verification_status = 'VERIFIED'` |
| Lỗi có thể xảy ra | DB lỗi → fail-closed về "chưa liên kết" |
| Trạng thái DB sau lỗi | không đổi |
| Response public | danh sách kèm `available` + `blocked_reason` |
| Test hiện có | I, P, B |
| Khoảng trống | (không) |

## 4. Workflow start / poll / continue

| | |
|---|---|
| Config đầu vào | body chỉ có `goal` (+`project_name`) |
| Nguồn sự thật | `workflows`, `workflow_tasks`, `workflow_clarifications`, `payment_approvals` |
| Lỗi có thể xảy ra | background task ném exception bất kỳ |
| Trạng thái DB sau lỗi | **`PENDING` VĨNH VIỄN — lỗi chỉ ghi vào `_DEMO_JOBS`** |
| Response public | `EXECUTION_ERROR` kèm `Workflow unavailable (LLMConfigurationError)` |
| Test hiện có | I, P (đường thành công), B |
| **Khoảng trống** | không test nào cho đường exception; không test nào đọc DB sau lỗi → lấp bằng `tests/test_db/test_workflow_failure_is_terminal.py` (P) |

## 5. LLM factory

| | |
|---|---|
| Config đầu vào | `LLM_PROVIDER`, `*_API_KEY`, `*_MODEL_NAME`, `*_BASE_URL` |
| Nguồn sự thật | shell env → compose interpolation → `env_file` → mặc định của `Settings` (**bốn lớp, không nơi nào báo lớp nào thắng**) |
| Lỗi có thể xảy ra | provider X + key của provider Y; model sai; key hết hạn; rate limit |
| Trạng thái DB sau lỗi | như mục 4 — PENDING |
| Response public | như mục 4 |
| Test hiện có | U (`get_llm` raise đúng) |
| **Khoảng trống** | `get_llm()` lazy nên lỗi chỉ nổ khi Planner chạy; không có hàm kiểm chạy được lúc khởi động → lấp bằng `check_llm_configuration` + `tests/test_llm_config_invariants.py` (U) |

## 6. Planner

| | |
|---|---|
| Config đầu vào | goal + trusted context server-side |
| Nguồn sự thật | `PLANNER_ALLOWED_TOOLS`, `PLANNER_FORBIDDEN_TOOLS` |
| Lỗi có thể xảy ra | đề xuất tool bị cấm; hỏi field đã có; JSON không parse được |
| Trạng thái DB sau lỗi | shell workflow đã tồn tại; clarification không ghi |
| Response public | `PLANNING_ERROR` / `NEEDS_INFORMATION` |
| Test hiện có | U, P, E |
| Khoảng trống | (không) |

## 7. Executor / boundaries

| | |
|---|---|
| Config đầu vào | TaskPlan đã validate + `ResidentAccessBoundary` |
| Nguồn sự thật | `user_resident_links`, `vehicles`, `parking_bookings` |
| Lỗi có thể xảy ra | provider 4xx/5xx; hết chỗ; input sai |
| Trạng thái DB sau lỗi | task `FAILED` + repair hint; release side-effect |
| Response public | `EXECUTION_ERROR` hoặc `NEEDS_INFORMATION` (repair) |
| Test hiện có | U, I, P, B |
| Khoảng trống | (không) |

## 8. Bảy provider

| | |
|---|---|
| Config đầu vào | `*_SERVICE_URL` × 7 |
| Nguồn sự thật | `docker-compose.yml` (tên service nội bộ) hoặc env local |
| Lỗi có thể xảy ra | URL rỗng/thiếu scheme; **một uvicorn local giữ cùng cổng nên request vào nhầm tiến trình và nhầm database** |
| Trạng thái DB sau lỗi | ghi vào SAI database mà không ai biết |
| Response public | thành công — đó mới là vấn đề |
| Test hiện có | U (connector), D (không có) |
| **Khoảng trống** | không có kiểm nào cho URL; không có kiểm xung đột cổng → lấp bằng readiness `connectors` + `scripts/stack_up.sh` |

## 9. PostgreSQL

| | |
|---|---|
| Config đầu vào | `DATABASE_URL` |
| Nguồn sự thật | volume của compose project |
| Lỗi có thể xảy ra | DB chưa lên; **container `p118_postgres` thuộc compose project KHÁC** |
| Trạng thái DB sau lỗi | thao tác chạy trên dữ liệu của project khác |
| Response public | bình thường |
| Test hiện có | P |
| **Khoảng trống** | không ai kiểm chủ sở hữu container → lấp bằng `stack_up.sh` bước 3 |

## 10. Docker migration

| | |
|---|---|
| Config đầu vào | service `db-migrate`, `schema.sql` + `schema_migrations.sql` |
| Nguồn sự thật | `information_schema.tables` |
| Lỗi có thể xảy ra | migration lỗi; backend vẫn start |
| Trạng thái DB sau lỗi | thiếu bảng |
| Response public | 500 rải rác khi chạm bảng thiếu |
| Test hiện có | P (parity schema/migration) |
| **Khoảng trống** | không ai kiểm migration ĐÃ CHẠY lúc runtime → lấp bằng readiness `migrations` + `stack_up.sh` bước 5 |

## 11. Docker health / readiness

| | |
|---|---|
| Config đầu vào | `healthcheck` trong compose |
| Nguồn sự thật | `/health` (trước) → `/ready` (sau) |
| Lỗi có thể xảy ra | **mọi lỗi cấu hình đều lọt: `/health` chỉ nói tiến trình còn sống** |
| Trạng thái DB sau lỗi | workflow kẹt PENDING |
| Response public | UI hiện một câu chung chung |
| Test hiện có | không có |
| **Khoảng trống** | toàn bộ → lấp bằng `/ready` + `tests/test_db/test_readiness_endpoint.py` (P) + mutation Docker |

---

## Kết luận audit

Ba nguyên nhân gốc, không phải ba triệu chứng rời rạc:

1. **Cấu hình được kiểm quá muộn.** `get_llm()` lazy, và không có hàm nào kiểm
   được lúc khởi động. Mọi lỗi cấu hình vì thế biểu hiện thành "workflow hỏng"
   thay vì "hệ thống chưa sẵn sàng".
2. **Healthcheck trả lời sai câu hỏi.** `/health` nói về tiến trình; người vận
   hành hỏi về khả năng nhận việc.
3. **Trạng thái lỗi sống trong RAM.** Handler `except` của background job chỉ
   ghi `_DEMO_JOBS`, nên PostgreSQL — nguồn sự thật duy nhất sống sót qua
   restart — không bao giờ biết workflow đã chết.

Ba thứ này cộng lại tạo ra đúng bức tranh đã quan sát: Compose xanh, workflow
`PENDING` vĩnh viễn, UI hiện một câu vô nghĩa, và không tầng test nào bắt được
vì không tầng nào chạy đúng môi trường Docker.
