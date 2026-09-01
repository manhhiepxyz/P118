"""Bản chụp sự thật cho tầng nói. Code dựng, model chỉ được nói lại.

Owner: Thành Bảo (Decision layer)
File: src/orchestration/snapshot.py

VÌ SAO MODULE NÀY TỒN TẠI

Ba lỗi cùng một gốc, đo được trên stack demo:

    "có những dự án nào"
        → "Hiện tại mình có các dự án: Khu A, Khu B, Khu C."
          "Khu A/B/C" là KHU ĐỖ XE. Không dự án nào tên như vậy.

    "xong chưa" / "vậy giờ tôi phải làm gì" / "đã đổi ngày cho tôi chưa"
        → xuống Planner 3–7 giây rồi trả về một câu vô nghĩa

    đơn vị từ chối `create_maintenance_request`
        → màn hình nói "Bước này đã được huỷ trước khi hoàn tất", trong khi
          `service_approvals` đang giữ NO_AVAILABILITY và câu "Không có nhân
          viên rảnh vào giờ này"

Truy lượt gọi của ca thứ nhất (workflow a39d6ebc):

    fast_plan 1,31s → nhường (0 dịch vụ, đây là câu hỏi — đúng)
    plan      2,45s → Planner trả QUESTION — đúng
    respond   1,27s → Response Agent VIẾT câu trả lời

Ba tầng đầu làm đúng. Tầng cuối được giao việc trả lời mà không được đưa dữ
liệu, nên nó lấy thứ gần nhất trong vốn từ của mình.

Không phải model kém. Là ta bảo nó trả lời rồi không đưa dữ liệu.

KHÔNG PHẢI NGUỒN SỰ THẬT MỚI

Module này gom lại đúng những nguồn đang dùng, và KHÔNG đọc database:

    danh mục dịch vụ  ← `_CAPABILITY_CATALOGUE` + `account_state` (caller đưa)
    danh sách dự án   ← `src/common/projects.PROJECTS`
    các bước          ← chính view người dùng đang nhìn (`_task_presentation`)
    lý do từ chối     ← `service_approvals.reject_code/reject_reason`

Dựng một bản sao song song là tạo ra cách thứ hai để nói sai. Đặc biệt với các
bước: lấy thẳng view nghĩa là model nói đúng thứ người dùng đang thấy trên màn
hình, không phải một cách diễn giải khác của cùng dữ liệu.

QUYỀN

`account_state` quyết định dịch vụ nào MỞ. Dịch vụ khoá vẫn được NÊU TÊN kèm
cờ khoá — giấu đi thì người dùng không biết nó tồn tại để đi xác minh căn hộ,
đúng lý do `_capability_reply` cố ý liệt kê cả phần khoá. Cái không được phép
là nói nó đang mở: model sẽ mời họ dùng, họ gõ theo, và bị từ chối ở tầng dưới.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.common.projects import PROJECTS


@dataclass(frozen=True)
class ServiceFact:
    name: str
    description: str
    open: bool


@dataclass(frozen=True)
class StepFact:
    title: str
    status: str
    details: Mapping[str, str]


@dataclass(frozen=True)
class RefusalFact:
    title: str
    code: str
    reason: str


@dataclass(frozen=True)
class Snapshot:
    services: tuple[ServiceFact, ...]
    projects: tuple[str, ...]
    steps: tuple[StepFact, ...]
    refusals: tuple[RefusalFact, ...]

    def as_text(self) -> str:
        """Sự thật dưới dạng văn bản cho prompt. Không diễn giải, không đoán."""
        dong: list[str] = ["## Dịch vụ"]
        for s in self.services:
            trang_thai = "dùng được" if s.open else "KHOÁ — cần xác minh căn hộ"
            dong.append(f"- {s.name} ({trang_thai}): {s.description}")
        dong.append("")
        dong.append("## Dự án")
        dong.append("- " + "; ".join(self.projects))
        if self.steps:
            dong.append("")
            dong.append("## Các bước của yêu cầu này")
            for b in self.steps:
                chi = " · ".join(f"{k}: {v}" for k, v in b.details.items())
                dong.append(f"- {b.title} [{b.status}]" + (f" — {chi}" if chi else ""))
        if self.refusals:
            dong.append("")
            dong.append("## Đơn vị đã từ chối")
            for t in self.refusals:
                dong.append(f"- {t.title}: {t.reason} ({t.code})")
        return "\n".join(dong)

    def known_values(self) -> frozenset[str]:
        """Mọi giá trị cụ thể câu trả lời được phép viện dẫn.

        Dành cho cổng kiểm ở chặng sau. Bốn lớp gây hại thật khi sai — ngày, số
        tiền, mã đơn, tên dự án — đều nằm trong đây; giá trị không có mặt thì
        câu chứa nó không được gửi đi.

        Đây KHÔNG phải phép kiểm văn xuôi: model vẫn có thể diễn giải sai. Nó
        chỉ chặn được thứ chặn được, và đó là những thứ đắt nhất khi sai.
        """
        biet: set[str] = set(self.projects)
        for b in self.steps:
            biet.add(b.title)
            biet.update(str(v) for v in b.details.values())
        for t in self.refusals:
            biet.add(t.reason)
            biet.add(t.code)
        for s in self.services:
            biet.add(s.name)
        return frozenset(biet)


def build_snapshot(
    *,
    account_state: str,
    capabilities: Iterable[Mapping[str, Any]],
    view: Any = None,
    refusals: Iterable[Mapping[str, Any]] = (),
) -> Snapshot:
    """Gom sự thật lại. Không đọc database, không gọi model, không đoán.

    `view` là bản công khai người dùng đang nhìn (`DemoWorkflowResponse` hoặc
    bất cứ thứ gì có `.tasks` với `title`/`status`/`details`). Nhận nguyên nó
    thay vì đọc lại từ database là có chủ ý: hai đường đọc là hai cách để lệch.
    """
    la_cu_dan = account_state == "resident"
    dich_vu = tuple(
        ServiceFact(
            name=str(item.get("name", "")),
            description=str(item.get("description", "")),
            open=la_cu_dan or not item.get("requires_resident", False),
        )
        for item in capabilities
    )

    buoc: list[StepFact] = []
    for task in getattr(view, "tasks", ()) or ():
        chi_tiet = {
            str(d.label): str(d.value)
            for d in (getattr(task, "details", ()) or ())
            if getattr(d, "label", None) and getattr(d, "value", None)
        }
        buoc.append(
            StepFact(
                title=str(getattr(task, "title", "")),
                status=str(getattr(task, "status", "")),
                details=chi_tiet,
            )
        )

    tu_choi = tuple(
        RefusalFact(
            title=str(r.get("title", "")),
            code=str(r.get("code", "")),
            reason=str(r.get("reason", "")),
        )
        for r in refusals
        if r.get("reason")
    )

    return Snapshot(
        services=dich_vu,
        projects=tuple(str(p["project_name"]) for p in PROJECTS),
        steps=tuple(buoc),
        refusals=tu_choi,
    )
