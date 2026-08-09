# Architecture — UML Diagrams (PlantUML)

Bộ sơ đồ UML cho P-118 theo chuẩn C4 và UML truyền thống, bao gồm 7 file.

## C4 Model Diagrams

| File | Level | Mô tả |
|---|---|---|
| [`c1_system_context.puml`](./c1_system_context.puml) | **C1 - System Context** | Góc nhìn hệ thống từ bên ngoài: actors (Cư dân), P-118, các hệ thống external (LLM Provider, Cloud Hosting, VinHomes [out-of-scope]). |
| [`c2_container.puml`](./c2_container.puml) | **C2 - Container** | Các container trong P-118: React UI, FastAPI REST/WS, LangGraph Agent Core (LLM layer + Deterministic layer), Tool Registry + Adapters, PostgreSQL, 3 Mock Services. |
| [`c3_component.puml`](./c3_component.puml) | **C3 - Component** | Phóng to Agent Core thành các component: Goal Parser, Task Planner, TaskPlan Validator, Scheduler, Policy Engine, HITL Manager, Executor, Recovery Handler, Compensator, Tool Registry + 3 Adapters. |

## UML Behavioral Diagrams

| File | Loại | Mô tả |
|---|---|---|
| [`sequence_happy_path.puml`](./sequence_happy_path.puml) | **Sequence Diagram** | Luồng happy path end-to-end: User → React → API → Agent Core → Tool Registry → Adapter → Mock Service → DB. Hiển thị đầy đủ 4 bước: register_resident → register_vehicle → book_parking → pay_fee. |
| [`sequence_recovery_replan.puml`](./sequence_recovery_replan.puml) | **Sequence Diagram** | Luồng hero recovery: `book_parking(Zone A)` → NO_AVAILABILITY → Recovery Handler → Replanner → `book_parking(Zone B)` → SUCCESS → pay_fee với HITL approval. |
| [`state_machines.puml`](./state_machines.puml) | **State Machine Diagram** | 2 state machine: Flow State Machine (PLANNING → RUNNING → COMPLETED/FAILED/ROLLED_BACK...) và Step State Machine (PENDING → RUNNING → COMPLETED/FAILED/COMPENSATED). |

## UML Structural Diagrams

| File | Loại | Mô tả |
|---|---|---|
| [`class_diagram.puml`](./class_diagram.puml) | **Class Diagram** | Core data structures: TaskPlan/TaskStep, StandardResult, ToolEntry/TOOL_REGISTRY, ProposedAction/PolicyDecision, RecoveryContext/RecoveryStrategy, DB models (FlowExecution, StepResult, CompensationLog), ServiceAdapter interface + 3 concrete adapters. |

---

## Cách render

### Option 1 — PlantUML plugin (VS Code / IntelliJ)
Mở file `.puml` → nhấn preview (cần cài extension PlantUML + Java).

**VS Code extensions:**
- `jebbs.plantuml` (PlantUML)
- Cài Java JDK nếu chưa có

**Keyboard shortcut:** `Alt+D` để preview.

### Option 2 — PlantUML JAR
```bash
# Tải plantuml.jar từ https://plantuml.com/download
java -jar plantuml.jar docs/architecture/c1_system_context.puml
java -jar plantuml.jar docs/architecture/c2_container.puml
java -jar plantuml.jar docs/architecture/c3_component.puml
java -jar plantuml.jar docs/architecture/sequence_happy_path.puml
java -jar plantuml.jar docs/architecture/sequence_recovery_replan.puml
java -jar plantuml.jar docs/architecture/state_machines.puml
java -jar plantuml.jar docs/architecture/class_diagram.puml
```

Sinh file `.png` cùng thư mục với file `.puml`.

### Option 3 — Render tất cả cùng lúc
```bash
java -jar plantuml.jar docs/architecture/*.puml
```

### Option 4 — Online renderer
Copy nội dung vào [plantuml.com/plantuml](http://www.plantuml.com/plantuml/uml/) để xem nhanh.

---

## Ghi chú thiết kế

### C4-PlantUML v2.0
Các diagram C4 sử dụng `!include` từ CDN của C4-PlantUML. Cần internet khi render để fetch macro.

### UML Diagrams
Các diagram sequence/state/class sử dụng `!theme plain` để render sạch, dễ đọc. Không phụ thuộc thư viện external.

### Điểm nhấn kiến trúc

**C1 - System Context:**
- Ranh giới rõ ràng: P-118 chỉ dùng mock services, KHÔNG kết nối VinHomes production API.
- LLM Provider là external system (OpenAI/Anthropic).

**C2 - Container:**
- Tách biệt Presentation Layer (optional ở Gate 2) và API Layer.
- Agent Core chia thành 2 sub-layer: LLM (non-deterministic) và Deterministic Orchestration.
- **Security boundary:** LLM không bypass được deterministic layer.

**C3 - Component:**
- 12 component trong Agent Core, chia thành 3 nhóm: LLM Layer (Parser, Planner, Replanner), Deterministic Layer (Validator, Scheduler, Policy, HITL, Executor, Recovery, Compensator), Tool Layer (Registry + 3 Adapters).
- 2 security gates: TaskPlan Validator và Policy Engine.

**Sequence Diagrams:**
- Happy path: 4 bước tuần tự, data propagation tự động.
- Recovery: NO_AVAILABILITY → REPLAN → Zone B, không restart workflow.

**State Machines:**
- Flow: 10 states, MVP chỉ dùng ATOMIC mode (không có PARTIALLY_COMPLETED).
- Step: 7 states, chỉ COMPLETED step mới có thể compensate.

**Class Diagram:**
- StandardResult là service contract chung cho mọi service.
- Tool Registry lưu metadata (schema, action_type, compensation_fn), không có runtime class riêng.
- ServiceAdapter interface + 3 concrete adapters (Resident, Transport, Payment).
- Adapter layer là layer duy nhất thay đổi khi chuyển mock → real API.

---

## Mối liên hệ với tài liệu khác

- [Architecture Diagram (Mermaid)](../architecture_diagram.md) — phiên bản Mermaid chi tiết hơn, có thể dùng song song.
- [PRD](../gate1/PRD.md) — yêu cầu chức năng, user stories, success metrics.
- [Brief](../gate1/brief.md) — tổng quan dự án 1 trang.
- [Wireframe](../gate1/wireframe.md) — UI mockup và user flow.

---

_Gate 1 — 02/08/2026 — P-118 Build Phase_
