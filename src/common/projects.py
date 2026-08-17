"""Danh mục dự án demo và phép ánh xạ tên công khai sang ID nội bộ."""

import re

PROJECTS: tuple[dict[str, str], ...] = (
    {"project_id": "PRJ-001", "project_name": "Vinhomes Sài Gòn Park"},
    {"project_id": "PRJ-002", "project_name": "Vinhomes Global Gate Hạ Long"},
    {"project_id": "PRJ-003", "project_name": "Vinhomes Hải Vân Bay"},
    {"project_id": "PRJ-004", "project_name": "Vinhomes Pearl Bay"},
    {"project_id": "PRJ-005", "project_name": "Vinhomes Green Paradise"},
    {"project_id": "PRJ-006", "project_name": "Vinhomes Golden City"},
    {"project_id": "PRJ-007", "project_name": "Vinhomes Ocean Park"},
)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


_PROJECT_BY_ID = {project["project_id"]: project for project in PROJECTS}
_PROJECT_ID_BY_NAME = {_normalize(project["project_name"]): project["project_id"] for project in PROJECTS}
# "Vinhome" thiếu "s" là lỗi gõ phổ biến nhất với bộ tên này, và người dùng
# không có cách nào biết mình sai: câu từ chối chỉ nói "chọn trong danh sách".
#
# Trước đây chỉ Ocean Park có alias này — sáu dự án còn lại thì không, nên cùng
# một kiểu gõ sai lúc chạy được lúc không. Sinh alias cho CẢ danh mục thì thêm
# dự án mới cũng tự có, không ai phải nhớ.
#
# Đây vẫn là khớp CHÍNH XÁC sau chuẩn hoá, không phải khớp gần đúng: hệ thống
# không đoán hộ người dùng họ định chọn dự án nào.
for _project in PROJECTS:
    _alias = _normalize(_project["project_name"]).replace("vinhomes ", "vinhome ", 1)
    _PROJECT_ID_BY_NAME.setdefault(_alias, _project["project_id"])


def resolve_project_id(name: str) -> str | None:
    """Đổi đúng tên/alias được hỗ trợ sang ID; không suy diễn gần đúng."""
    return _PROJECT_ID_BY_NAME.get(_normalize(name))


def find_project_id(text: str) -> str | None:
    """Tìm đúng một tên dự án được hỗ trợ trong câu tự nhiên."""
    normalized = _normalize(text)
    matches = {
        project_id
        for name, project_id in _PROJECT_ID_BY_NAME.items()
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", normalized)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def project_name(project_id: str) -> str | None:
    project = _PROJECT_BY_ID.get(project_id)
    return project["project_name"] if project is not None else None
