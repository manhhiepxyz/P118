# Project Brief — P-118

**Dự án:** P-118

**Đề tài:** AI Agent orchestrate đa dịch vụ hoàn thành tác vụ liên hoàn (đặt nhà → xe → dịch vụ)

**Mã đề tài:** PTNT-02 — STT 158
**Nhóm:** Lâm Thành Bảo · Phí Hoàng Anh · Nguyễn Mạnh Hiệp · Mentor: Bùi Trung Hiếu
**Gate 1 — 02/08/2026**

---

## 1. Project Overview

P-118 xây dựng một AI Agent nhận mục tiêu nhiều bước từ người dùng bằng ngôn ngữ tự nhiên, tự lập kế hoạch, gọi tuần tự các dịch vụ có phụ thuộc lẫn nhau, truyền dữ liệu giữa các bước, theo dõi trạng thái, tự xử lý failure trong phạm vi cho phép và yêu cầu người dùng quyết định khi cần.

Giá trị cốt lõi: **goal-oriented multi-service orchestration + failure-aware execution + controlled autonomy** — không phải "LLM gọi nhiều API."

---

## 2. Problem

Trong hệ sinh thái có nhiều dịch vụ, người dùng muốn hoàn thành một mục tiêu duy nhất thường phải tự điều phối toàn bộ: tìm đúng dịch vụ, thực hiện đúng thứ tự, nhập lại thông tin ở mỗi bước, theo dõi trạng thái và tự xử lý khi một bước thất bại.

**Ví dụ:** Một cư dân mới chuyển vào căn hộ cần đăng ký cư dân, đăng ký phương tiện, đặt chỗ đậu xe và thanh toán phí — các bước có phụ thuộc lẫn nhau và nếu Zone A hết chỗ, người dùng phải tự tìm Zone B.

_Kịch bản này là business scenario mô phỏng phù hợp với bài toán multi-service orchestration, sử dụng mock services._

---

## 3. Target User

Cư dân hoặc người dùng cá nhân trong hệ sinh thái dịch vụ nhà ở/cư dân cần hoàn thành một hoặc nhiều tác vụ liên quan — người muốn đưa ra goal và để hệ thống tự điều phối, không cần biết service internals.

---

## 4. Proposed Solution

Người dùng nhập goal bằng tiếng Việt tự nhiên:

> _"Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư dân, xe, chỗ đậu và thanh toán phí giúp tôi."_

Agent tự: hiểu goal → lập kế hoạch → validate plan → thực hiện các service theo dependency → truyền output giữa bước → theo dõi và persist trạng thái → xử lý failure → hỏi người dùng khi action không được phép tự quyết định → trả kết quả cuối cùng.

---

## 5. MVP Journey

```
Register Resident  →  resident_id
       ↓
Register Vehicle   →  vehicle_id
       ↓
Book Parking       →  booking info
       ↓
Pay Fee            →  payment confirmation
```

3 service domains: **Resident · Transport/Parking · Payment** _(mock services — không kết nối production API)_

---

## 6. Why P-118 Is Different

**Goal-oriented orchestration** — User đưa một mục tiêu thay vì điều hướng từng service thủ công.

**Dependency-aware execution** — Output của bước trước tự động trở thành input bước sau; người dùng không nhập lại thông tin.

**Failure-aware recovery** — Khi `Book Parking Zone A` trả về `NO_AVAILABILITY`, Agent tự replan và thử `Zone B` mà không restart workflow; các bước đã hoàn thành không bị chạy lại.

**Controlled autonomy** — Agent không được tự quyết định mọi action. Policy Engine phân loại mỗi action thành `AUTO_ALLOWED`, `REQUIRES_APPROVAL` hoặc `DENIED`. Với action được phân loại `REQUIRES_APPROVAL` (ví dụ: financial action vượt ngưỡng, destructive action), Agent dừng lại và yêu cầu user xác nhận trước khi thực hiện.

---

## 7. Gate 2 Working MVP (~17/08/2026)

_Chương trình yêu cầu: Agent gọi được ≥3 services + Live URL._

- [ ] Natural-language goal → AI-generated TaskPlan → deterministic validation
- [ ] Agent thực hiện ≥3 mock services theo dependency order
- [ ] Data propagation đúng giữa các bước
- [ ] Workflow state persist (PostgreSQL)
- [ ] Happy path hoàn thành: Resident → Vehicle → Parking → Fee
- [ ] Hero recovery: `Zone A` NO_AVAILABILITY → REPLAN → `Zone B` → SUCCESS
- [ ] FastAPI cloud deployment · Live URL · Basic integration tests

_Gate 2 chưa yêu cầu: Policy Engine đầy đủ, HITL, Saga Compensation, polished React UI._

---

## 8. Demo Day Target (03–05/09/2026)

Bổ sung sau Gate 2:

- [ ] Policy Engine (AUTO_ALLOWED / REQUIRES_APPROVAL / DENIED)
- [ ] HITL — Agent dừng chờ user approve với action REQUIRES_APPROVAL
- [ ] Retry khi service lỗi tạm thời
- [ ] Saga Compensation — hoàn tác side effects theo reverse order
- [ ] Idempotency — retry không tạo duplicate side effect
- [ ] React Workflow Timeline (WebSocket realtime)
- [ ] Test/evaluation evidence · Video demo ≤5 phút · Pitch Deck

---

## 9. Success Criteria

| Criterion                   | Target                                                                                  |
| --------------------------- | --------------------------------------------------------------------------------------- |
| **Multi-service execution** | ≥3 services hoàn thành trong happy-path test cases                                      |
| **Data propagation**        | 100% dependency output truyền đúng vào bước tiếp theo (integration tests)               |
| **Hero recovery**           | 100% NO_AVAILABILITY scenarios: REPLAN → alternative → workflow hoàn thành              |
| **Controlled autonomy**     | 0 action classified REQUIRES_APPROVAL execute trước user approval (Demo Day test suite) |

---

## 10. Scope & Assumptions

**In scope:** 1 business domain (residential/housing services) · 1 end-to-end hero journey · partial goal execution sử dụng cùng tập dịch vụ · 3 service domains · multi-service orchestration · failure recovery · controlled autonomy · mock services

**Out of scope:** Production VinHomes API · toàn bộ dịch vụ VinHomes · multi-domain (du lịch, khách sạn...) · Admin Dashboard · Healthcare scenario · multi-tenant platform · distributed orchestration

**Architecture principle:** LLM chịu trách nhiệm goal understanding, planning và replanning. Deterministic orchestration layer chịu trách nhiệm plan validation, execution, policy enforcement, state và compensation — LLM không bypass các gate này.

---

## 11. References

| Tài liệu                  | Link                           |
| ------------------------- | ------------------------------ |
| PRD                       | `docs/gate1/prd.md`            |
| Wireframe & UI Flow       | `docs/gate1/wireframe.md`      |
| Architecture Diagram | `docs/architecture_diagram.md` |

---

_Gate 1 — 02/08/2026 — P-118 Build Phase · VinUni AI20K Cohort 3_
