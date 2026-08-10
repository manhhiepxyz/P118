# Hướng dẫn Debug Tích hợp Liên tầng — P-118

> Owner: Mạnh Hiệp (Executor layer)
> File: docs/integration-debug-guide.md

Khi integration test hoặc smoke test fail, bước đầu tiên là **phân loại lỗi thuộc tầng nào** trước khi báo cáo hoặc tự sửa. File này ghi quy tắc phân loại và các lỗi đã gặp.

---

## 1. Bảng phân tầng lỗi

| Tầng | Dấu hiệu | Owner | Cách xác nhận |
|---|---|---|---|
| **Planner** | TaskPlan invalid, thiếu required input, tool name sai allowlist | Thành Bảo | `TaskPlanValidator.validate(plan)` raise ValueError; error_code = `INVALID_TASK_PLAN` |
| **Executor** | Task chạy sai thứ tự, InputRef không resolve, dependency check sai, không gọi repository | Mạnh Hiệp | Unit test `test_executor.py` fail; log "Dependency không thỏa mãn" |
| **Connector** | HTTP 2xx nhưng thiếu canonical field, mapping error code sai, `payment_status` ngoài allowlist | Mạnh Hiệp | `StandardResult.fail` với `error_code = UNKNOWN_EXTERNAL_ERROR` và message "Thiếu ... trong response" |
| **Mock Provider** | HTTP 4xx/5xx, envelope `success=false`, store trả ALREADY_EXISTS | Hoàng Anh | `curl http://localhost:800x/endpoint` trực tiếp; đọc log container |
| **Database** | UniqueViolation, workflow/task không persist, migration fail | Hoàng Anh | `psql` query trực tiếp; `tests/test_db/` fail |
| **Docker/Hạ tầng** | Container crash loop, port không mở, build fail, network timeout | Hoàng Anh / DevOps | `docker compose ps`, `docker compose logs` |

---

## 2. Lỗi đã gặp (Gate 2, 2026-08-10)

### 2.1 Dockerfile — Permission denied uvicorn

**Hiện tượng:**
```
p118_mock_resident  | /usr/local/bin/python3.11: can't open file '/root/.local/bin/uvicorn': [Errno 13] Permission denied
```
Cả 4 container app (backend, mock-resident, mock-transport, mock-payment) crash loop. Postgres healthy.

**Nguyên nhân:** Dockerfile multi-stage — Stage 1 `pip install --user` cài vào `/root/.local` với owner **root**, Stage 2 copy giữ nguyên owner root, rồi `USER appuser` (non-root) → appuser không đọc/execute được.

**Đã sửa:** Thêm `RUN chown -R appuser:appuser /root/.local` sau USER appuser trong Dockerfile.

### 2.2 Docker build — Network timeout PyPI

**Hiện tượng:**
```
ReadTimeoutError: HTTPSConnectionPool(host='files.pythonhosted.org', port=443): Read timed out.
ERROR: No matching distribution found for alembic>=1.14.0
```

**Nguyên nhân:** Mạng Việt Nam → PyPI không ổn định (timeout). Không phải lỗi code.

**Workaround:** Build với cache (`docker compose build` không có `--no-cache`); nếu cache mất thì retry build cho đến khi network ổn định.

**Giải pháp dài hạn (Hoàng Anh):** cấu hình PyPI mirror trong Dockerfile:
```dockerfile
RUN pip install --no-cache-dir --user -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

---

## 3. Checklist debug nhanh

Khi test fail, chạy theo thứ tự:

```bash
# 1. Kiểm tra container status
docker compose ps

# 2. Xem log container nào crash
docker compose logs --tail=30 <service-name>

# 3. Kiểm tra health endpoints
for p in 8000 8001 8002 8003; do curl -s http://localhost:$p/health; done

# 4. Chạy unit test (không cần Docker)
pytest tests/test_executor.py tests/test_connectors.py -v

# 5. Chạy integration test (cần TEST_DATABASE_URL + Docker)
TEST_DATABASE_URL=postgresql://p118:p118pass@localhost:5432/p118_db \
  pytest tests/test_integration/ -v

# 6. Chạy smoke test (cần Docker + healthy containers)
python scripts/smoke_runtime.py
```

---

## 4. Quy tắc báo lỗi

- **Lỗi Executor/Connector** → Mạnh Hiệp tự fix, PR riêng
- **Lỗi Planner** → báo Thành Bảo kèm error_code và plan JSON
- **Lỗi Mock Provider/DB** → báo Hoàng Anh kèm log container và query
- **Lỗi Docker/Hạ tầng** → báo Hoàng Anh kèm `docker compose logs` output
- **Lỗi tích hợp (không rõ tầng)** → mở issue kèm output cả 4 tầng, tag cả nhóm
