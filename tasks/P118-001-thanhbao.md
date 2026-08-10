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

## Tuần 2 (10–14/08) — Gate 2: LLM Planner + LangGraph

> **Mục tiêu nội bộ:** hoàn thành và freeze trước 23:59 ngày 14/08; ngày
> 15/08 chỉ dùng để sửa lỗi và hoàn thiện hồ sơ nộp Gate 2. LLM trong đường
> chạy demo phải là LLM thật; các API nghiệp vụ vẫn là Mock Provider.

### Trạng thái đầu tuần

- `TaskPlan`, `Task`, `InputRef` và `TaskPlanValidator` đã hoàn thành.
- Full flow deterministic T1→T2→T3→T4 đã chạy qua Executor, Mock Provider
  và PostgreSQL.
- `src/agents/graph.py` hiện chỉ là graph mẫu; chưa có Planner thật.
- Chưa có `src/agents/planner.py`, prompt Planner và test Planner.

### Việc cần làm

| Việc | File | Hoàn thành khi |
|---|---|---|
| Viết LLM Planner tạo structured output | `src/agents/planner.py` | Goal tiếng Việt được chuyển thành `TaskPlan`, không parse JSON tùy ý ngoài schema |
| Viết system prompt cho Planner | `src/agents/prompts/planner_prompt.py` | Prompt chỉ cho phép 4 tool, dùng `InputRef` đúng contract và không chứa URL/token/credential |
| Xử lý thiếu thông tin | `src/agents/planner.py` | Không tự đoán ID hoặc dữ liệu nghiệp vụ; trả yêu cầu bổ sung thông tin có ý nghĩa |
| Tích hợp Planner → Validator vào LangGraph | `src/agents/graph.py` | Mọi plan đều phải qua `TaskPlanValidator` trước khi tới execution boundary |
| Thêm state cần thiết cho planning | `src/agents/state.py` | Lưu goal, context, plan, validation error và workflow result; không định nghĩa lại shared schema |
| Unit test Planner bằng fake LLM | `tests/test_planner.py` | Test ổn định, không cần API key và phủ full goal, thiếu thông tin, tool lạ |
| Chạy manual eval bằng LLM thật | `eval/results/report.md` | Có ít nhất 5 prompt và output thực tế để làm evidence Gate 2 |
| Cập nhật sơ đồ luồng thực tế | Tài liệu architecture hiện hành | Thể hiện Goal → Planner → Validator → Executor → Connector → Mock Provider → PostgreSQL |
| Chuẩn bị kịch bản demo 3 phút | Tài liệu Gate 2 | Có lời dẫn, sample goal, happy path, failure case và kết quả cần quay |

### Làm độc lập

- Dùng fake LLM response và fake execution boundary trong unit test; không
  cần đợi API của Hoàng Anh hoặc thay đổi Executor của Mạnh Hiệp.
- Output duy nhất của Planner là `TaskPlan` trong
  `src/common/task_plan.py`; không tạo schema kế hoạch riêng trong
  `src/agents/`.
- Mạnh Hiệp tiếp tục test Executor bằng hardcoded plan. Hoàng Anh tiếp tục
  test API bằng fake orchestration service.

### Tiêu chí hoàn thành Week 2

- [ ] Ít nhất một goal chạy bằng LLM thật và tạo `TaskPlan` hợp lệ.
- [ ] Planner chỉ sử dụng 4 tool trong shared contract.
- [ ] Plan từ LLM luôn qua `TaskPlanValidator` trước khi thực thi.
- [ ] Thiếu thông tin thì hỏi lại, không tự bịa ID hoặc input.
- [ ] Unit test không phụ thuộc network/API key; manual eval mới dùng LLM thật.
- [ ] LangGraph gọi được execution boundary do Mạnh Hiệp cung cấp.
- [ ] Có 5 manual eval case với output thực tế.
- [ ] Có architecture diagram và kịch bản video 3 phút khớp với code thực tế.
- [ ] `ruff check`, `ruff format --check` và test liên quan đều pass.

### Không làm trong critical path Gate 2

- Policy Engine/HITL thật: giữ `AUTO_ALLOWED` tạm thời cho happy path.
- Replanner hoàn chỉnh, Retry, Compensation, MCP hoặc eKYC thật.
- Không mở rộng allowlist và không thay đổi shared contract nếu không có
  blocker được cả nhóm duyệt.

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
