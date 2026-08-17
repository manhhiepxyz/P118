"""Cầu nối giữa danh mục dự án canonical và implementation tour cũ.

`src/common/projects.py` là danh mục DUY NHẤT: `search_properties` trả
`project_id` từ đó, nên `schedule_property_viewing` và
`register_property_interest` bắt buộc phải nhìn cùng danh sách. Hai danh mục
song song đồng nghĩa với việc search trả về một dự án mà đặt lịch báo 404 —
sai ở chỗ người dùng không thể tự sửa.

Implementation tour cũ đánh chỉ mục sức chứa theo `residential_area` (tên khu,
chuỗi tự do). Module này là chỗ duy nhất nối `project_id` với tên khu đó, nên
`residential_area` không bao giờ phải do LLM sinh ra.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.common.projects import PROJECTS as _CANONICAL_PROJECTS
from src.common.projects import project_name as _project_name


@dataclass(frozen=True)
class Project:
    project_id: str
    project_name: str
    # Khoá nội bộ của tour provider. KHÔNG thuộc contract public.
    residential_area: str


class UnknownProjectError(LookupError):
    """`project_id` không có trong danh mục.

    Message chỉ chứa id được yêu cầu — id là dữ liệu công khai, không phải PII.
    """


# Tên khu trùng tên dự án: implementation tour chỉ cần một khoá ổn định, và
# dùng lại tên dự án giữ cho hai bên không thể lệch nhau.
PROJECTS: tuple[Project, ...] = tuple(
    Project(p["project_id"], p["project_name"], p["project_name"]) for p in _CANONICAL_PROJECTS
)

_BY_ID: dict[str, Project] = {p.project_id: p for p in PROJECTS}


def get_project(project_id: str) -> Project:
    project = _BY_ID.get(project_id.strip().upper())
    if project is None:
        raise UnknownProjectError(f"Không có dự án {project_id!r} trong danh mục.")
    return project


def known_project_ids() -> tuple[str, ...]:
    return tuple(_BY_ID)


__all__ = ["PROJECTS", "Project", "UnknownProjectError", "get_project", "known_project_ids", "_project_name"]
