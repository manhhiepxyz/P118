# P118-001 — Thành Bảo · Tầng Quyết định

> Đọc `docs/shared_contracts.md` và `AGENTS.md` trước khi bắt đầu.

---

## Phạm vi

Thành Bảo chịu trách nhiệm toàn bộ tầng **Quyết định**:

- Hiểu mục tiêu người dùng
- Tạo và validate `TaskPlan`
- Phát hiện thiếu thông tin → hỏi user
- Lập kế hoạch lại khi lỗi (Replanner)
- Xây dựng Policy Engine
- Xác định khi nào cần HITL

**Phạm vi code:** `src/agents/`, `src/common/task_plan.py`

**Schema Thành Bảo sở hữu:** `src/common/task_plan.py` — `TaskPlan`, `Task`, `InputRef`

**Schema Thành Bảo import (không sửa):**
- `TaskStatus` từ `src/common/enums.py`
- `StandardResult` từ `src/common/results.py`

---

## Tuần 1 (03–10/08) — Nền tảng quyết định

### Việc cần làm

| Việc | File | Hoàn thành khi |
|---|---|---|
| Định nghĩa Pydantic model `TaskPlan`, `Task`, `InputRef` | `src/common/task_plan.py` | Model import được, validate đúng theo `shared_contracts.md` |
| Import `TaskStatus` từ `src/common/enums.py` | `src/agents/validator.py` | Dùng đúng enum, không định nghĩa lại |
| Viết Validator — check allowlist | `src/agents/validator.py` | Reject tool ngoài allowlist |
| Viết Validator — check dependency | `src/agents/validator.py` | Phát hiện task phụ thuộc task không tồn tại |
| Viết Validator — check cycle | `src/agents/validator.py` | Phát hiện dependency vòng tròn |
| Viết Validator — check required fields | `src/agents/validator.py` | Phát hiện thiếu input bắt buộc |
| Tạo 3–5 goal mẫu | `src/agents/examples/goals.py` | Có goal đầy đủ và partial |
| Tạo TaskPlan mẫu cho luồng đầy đủ | `src/agents/examples/plans.py` | T1→T2→T3→T4 hợp lệ |
| Tạo TaskPlan mẫu — partial goal 1 | `src/agents/examples/plans.py` | Chỉ `book_parking` (đã có vehicle_id) |
| Tạo TaskPlan mẫu — partial goal 2 | `src/agents/examples/plans.py` | `book_parking → pay_fee` (đã có vehicle_id) |

### Partial goals cần hỗ trợ

**Partial goal 1:**
```
User goal: "Đặt chỗ cho xe của tôi."
Existing context: vehicle_id đã tồn tại
TaskPlan: chỉ gồm book_parking
```

**Partial goal 2:**
```
User goal: "Đặt chỗ và thanh toán giúp tôi."
Existing context: vehicle_id đã tồn tại
TaskPlan: book_parking → pay_fee
```

> - Planner **không được tự thêm** `pay_fee` nếu user chỉ yêu cầu đặt chỗ.
> - Planner **không được tạo lại** `register_resident` hoặc `register_vehicle` nếu ID đã tồn tại trong context.
> - **Không được tự đoán ID** — nếu thiếu thông tin phải hỏi user.

### Làm độc lập trong tuần 1

Thành Bảo không cần đợi Executor, Connector hay Mock API. Tự test bằng:

```python
# Fake StandardResult để test Validator
from src.common.results import StandardResult
from src.common.enums import ErrorCode

fake_success = StandardResult(success=True, data={"resident_id": "RES-001"},
                               error_code=None, message="OK", retryable=False)

fake_failure = StandardResult(success=False, data=None,
                               error_code=ErrorCode.NO_AVAILABILITY,
                               message="ZONE_A is full", retryable=False)
```

### Chưa cần tuần này

- Gọi LLM thật (dùng hardcoded plan để test)
- Replanner hoàn chỉnh
- Policy Engine
- LangGraph graph

### Tiêu chí hoàn thành tuần 1

- [ ] `TaskPlan` Pydantic model trong `src/common/task_plan.py` validate được JSON từ `shared_contracts.md`
- [ ] Validator reject plan có tool ngoài allowlist
- [ ] Validator reject plan có dependency sai
- [ ] Validator reject plan có cycle
- [ ] Validator phát hiện thiếu required field
- [ ] Có plan mẫu cho full flow (T1→T2→T3→T4)
- [ ] Có plan mẫu partial goal 1 (chỉ `book_parking`)
- [ ] Có plan mẫu partial goal 2 (`book_parking → pay_fee`)
- [ ] Mạnh Hiệp import được `TaskPlan` từ `src/common/task_plan.py`

---

## Tuần 2 (11–17/08) — Planner + Validator + LangGraph

| Việc | File |
|---|---|
| Viết Planner node (LLM tạo TaskPlan) | `src/agents/planner.py` |
| Viết prompt cho Planner | `src/agents/prompts/planner_prompt.py` |
| Tích hợp Validator vào LangGraph flow | `src/agents/graph.py` |
| Test Planner với 5 goal mẫu | `tests/test_planner.py` |

> Tuần 2 chưa tích hợp Policy Engine thật — placeholder hoặc `AUTO_ALLOWED` mặc định để happy path chạy được.

---

## Tuần 3 (18–24/08) — Replanner + Policy

| Việc | File |
|---|---|
| Viết Replanner node | `src/agents/replanner.py` |
| Prompt Replanner nhận: original goal, current plan, completed task IDs, failed task, `error_code`, existing context | `src/agents/prompts/replanner_prompt.py` |
| Viết Policy Engine | `src/agents/policy.py` |
| Định nghĩa điều kiện `AUTO_ALLOWED` | `src/agents/policy.py` |
| Định nghĩa điều kiện `REQUIRES_APPROVAL` | `src/agents/policy.py` |
| Định nghĩa điều kiện `DENIED` | `src/agents/policy.py` |
| Tích hợp Policy Engine vào LangGraph flow (trước Executor) | `src/agents/graph.py` |
| Test Policy chạy trước Executor | `tests/test_policy.py` |
| Test `pay_fee` có thể trả `REQUIRES_APPROVAL` | `tests/test_policy.py` |

> Gate 2 ưu tiên happy path. Policy/HITL đầy đủ có thể hoàn thiện sau Gate 2 nếu tiến độ gấp.

---

## Interface với các thành viên khác

**Thành Bảo cung cấp cho Mạnh Hiệp:**
- `TaskPlan`, `Task`, `InputRef` trong `src/common/task_plan.py`
- `TaskPlanValidator` class trong `src/agents/validator.py`

**Thành Bảo nhận từ Mạnh Hiệp:**
- `StandardResult` của task vừa thực thi
- `TaskStatus` mới sau khi task chạy xong
- Danh sách task đã `SUCCESS` (Replanner dùng để không lập lại)
- Failure signal gồm `error_code`, `message`, `retryable`
- `StandardResult` từ `src/common/results.py`
- `WorkflowStatus`, `TaskStatus`, `ErrorCode` từ `src/common/enums.py`

> Không có model `ExecutionResult`. Thông tin execution được truyền qua các field đã định nghĩa trong `shared_contracts.md`.

**Thành Bảo nhận từ Hoàng Anh:**
- HITL UI trigger (Hoàng Anh implement, Thành Bảo không cần biết chi tiết)

---

## Không làm

- Không gọi Mock API trực tiếp (việc của Mạnh Hiệp qua Connector)
- Không sửa database schema (việc của Hoàng Anh)
- Không thêm tool mới vào allowlist mà không báo nhóm
- Không định nghĩa `StandardResult` hay `WorkflowStatus` trong `src/agents/` (import từ `src/common/`)
