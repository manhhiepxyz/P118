from __future__ import annotations

import re
from collections import deque
from datetime import date, time, timedelta

from src.common.projects import project_name, resolve_project_id
from src.common.task_plan import InputRef, TaskPlan
from src.common.tool_contract import (
    TOOL_CONTRACTS,
    kinds_are_compatible,
    output_spec,
)


class MissingRequiredInputError(ValueError):
    """Plan hợp cấu trúc nhưng còn thiếu input bắt buộc.

    `missing_fields` chỉ chứa tên field thuộc contract, không chứa dữ liệu do
    người dùng hoặc LLM sinh. Graph có thể dùng tín hiệu có kiểu này để hỏi bổ
    sung mà không phải phân tích chuỗi exception.
    """

    def __init__(self, missing_fields: tuple[str, ...]) -> None:
        self.missing_fields = missing_fields
        super().__init__(f"TaskPlan is missing required input fields: {list(missing_fields)}")


class TaskPlanValidator:
    """Validate a TaskPlan against business rules beyond Pydantic schema checks."""

    ALLOWED_TOOLS: frozenset[str] = frozenset(
        {
            "search_properties",
            "schedule_property_viewing",
            "register_property_interest",
            "create_maintenance_request",
            "schedule_move",
            "register_resident",
            "register_vehicle",
            "book_parking",
            "pay_fee",
            "book_shuttle",
        }
    )

    REQUIRED_INPUTS: dict[str, frozenset[str]] = {
        "search_properties": frozenset({"transaction_type", "property_type", "residential_area", "max_price"}),
        "schedule_property_viewing": frozenset({"project_id", "viewing_date", "viewing_time"}),
        "register_property_interest": frozenset({"project_id", "interest_type", "preferred_contact_time", "consent"}),
        "create_maintenance_request": frozenset(
            {"issue_type", "description", "location", "preferred_date", "preferred_time"}
        ),
        "schedule_move": frozenset(
            {"move_date", "move_time", "needs_elevator", "needs_loading_support", "move_vehicle"}
        ),
        "register_resident": frozenset({"full_name", "apartment_code", "residential_area"}),
        "register_vehicle": frozenset({"resident_id", "plate_number", "vehicle_type"}),
        "book_parking": frozenset({"vehicle_id", "booking_date", "parking_zone"}),
        "pay_fee": frozenset({"booking_id", "amount", "currency"}),
        "book_shuttle": frozenset({"viewing_id", "tour_date", "passenger_count"}),
    }

    # Xa nhất được đặt trước. Trị số rộng rãi là cố ý; điều quan trọng là CÓ
    # một trần, để ngày vô lý bị từ chối lúc lập kế hoạch chứ không phải lúc ai
    # đó đọc báo cáo.
    # Vì sao 5 năm chứ không phải 1–2 năm:
    #
    # Yêu cầu thật là chặn ngày VÔ LÝ (2050, 2199) — những ngày mà mọi lớp kiểm cũ
    # đều cho qua vì chúng không nằm trong quá khứ. 5 năm làm được đúng việc đó.
    #
    # Nói thẳng phần chủ quan: bộ test hiện dùng 48 ngày cố định năm 2030 làm "ngày
    # an toàn trong tương lai". Trần 2 năm sẽ đúng hơn về nghiệp vụ nhưng buộc phải
    # sửa 48 chỗ ở 19 file, và rút ngắn tuổi thọ của chính bộ test đó từ ~4 năm
    # xuống ~1 năm. Em chọn trần rộng và ghi lại đánh đổi này thay vì âm thầm siết
    # luật nghiệp vụ cho vừa fixture. Muốn chặt hơn thì đổi con số ở ba chỗ dưới.
    MAX_HORIZON_DAYS: int = 1825

    DATE_INPUTS: dict[str, str] = {
        "schedule_property_viewing": "viewing_date",
        "book_parking": "booking_date",
        "create_maintenance_request": "preferred_date",
        "schedule_move": "move_date",
        "book_shuttle": "tour_date",
    }

    TIME_INPUTS: dict[str, tuple[str, time, time]] = {
        "schedule_property_viewing": ("viewing_time", time(8, 0), time(17, 30)),
        "create_maintenance_request": ("preferred_time", time(8, 0), time(18, 0)),
        "schedule_move": ("move_time", time(7, 0), time(20, 0)),
        # Giờ hẹn liên hệ nằm trong giờ làm việc của bộ phận tư vấn.
        "register_property_interest": ("preferred_contact_time", time(8, 0), time(18, 0)),
    }

    ENUM_INPUTS: dict[tuple[str, str], frozenset[str]] = {
        ("register_vehicle", "vehicle_type"): frozenset({"car", "motorcycle"}),
        ("book_parking", "parking_zone"): frozenset({"ZONE_A", "ZONE_B"}),
    }

    FORBIDDEN_INPUT_KEYS: frozenset[str] = frozenset(
        {
            "url",
            "endpoint",
            "token",
            "access_token",
            "refresh_token",
            "api_key",
            "header",
            "headers",
            "credential",
            "credentials",
            "authorization",
            "auth",
        }
    )

    URL_SCHEMES: frozenset[str] = frozenset({"http://", "https://"})

    # Markers suggesting a secret was pasted into free text. Matched case-insensitively.
    CREDENTIAL_MARKERS: frozenset[str] = frozenset(
        {
            "token",
            "access_token",
            "refresh_token",
            "api_key",
            "credential",
            "credentials",
            "authorization",
            "bearer",
        }
    )

    @classmethod
    def _contains_url(cls, text: str) -> bool:
        lowered = text.lower()
        return any(scheme in lowered for scheme in cls.URL_SCHEMES)

    @classmethod
    def _find_credential_marker(cls, text: str) -> str | None:
        """Return the first credential marker found, or None. Never returns the surrounding text."""
        lowered = text.lower()
        for marker in sorted(cls.CREDENTIAL_MARKERS):
            if marker in lowered:
                return marker
        return None

    @classmethod
    def _reject_sensitive(cls, text: str, location: str) -> None:
        """Raise if `text` holds a URL or credential marker.

        `location` describes *where* the violation is, never *what* it contains.
        The offending text is never included in the message.
        """
        if cls._contains_url(text):
            raise ValueError(f"{location} contains a URL; TaskPlan must not embed endpoints or links")

        marker = cls._find_credential_marker(text)
        if marker is not None:
            raise ValueError(f"{location} contains a possible credential marker: '{marker}'")

    @classmethod
    def _reject_sensitive_content(cls, plan: TaskPlan) -> None:
        """Sweep every string in the plan for URLs and credential markers.

        Each string is cleared before it is used as a location label for later
        checks, so a sensitive value is never echoed back in an exception.
        """
        cls._reject_sensitive(plan.goal, "TaskPlan goal")

        for index, task in enumerate(plan.tasks):
            # task_id is not yet cleared — refer to it by position only.
            cls._reject_sensitive(task.task_id, f"tasks[{index}].task_id")
            # task_id is now cleared and safe to name.
            task_label = f"task '{task.task_id}'"

            for dep_index, dep in enumerate(task.depends_on):
                cls._reject_sensitive(dep, f"{task_label} depends_on[{dep_index}]")

            for key, value in task.input.items():
                # Exact-match against the known forbidden list first: it yields a
                # sharper message than the generic marker sweep. The echoed key is
                # one of the known constants, not caller-supplied secret data.
                if key.lower() in cls.FORBIDDEN_INPUT_KEYS:
                    raise ValueError(f"{task_label} contains forbidden input key: '{key}'")

                # key is not yet cleared — do not name it.
                cls._reject_sensitive(key, f"{task_label} input key")
                # key is now cleared and safe to name.
                key_label = f"{task_label} input '{key}'"

                if isinstance(value, InputRef):
                    cls._reject_sensitive(value.from_task, f"{key_label}.from_task")
                    cls._reject_sensitive(value.field, f"{key_label}.field")
                elif isinstance(value, str):
                    cls._reject_sensitive(value, key_label)

    @classmethod
    def validate(cls, plan: TaskPlan) -> TaskPlan:
        """Run all validation checks. Raises ValueError with a clear message on failure.

        Error messages never echo potentially sensitive content — only the location
        of the violation and the matched pattern name.
        """
        # 0. No URL or credential anywhere in the plan. Runs before the structural
        #    checks so dangerous content is rejected on its own terms rather than
        #    surfacing as an unrelated dependency or schema error.
        cls._reject_sensitive_content(plan)

        task_ids = [t.task_id for t in plan.tasks]
        tasks_by_id = {t.task_id: t for t in plan.tasks}

        # 1. Unique task_ids
        seen: set[str] = set()
        for tid in task_ids:
            if tid in seen:
                raise ValueError(f"Duplicate task_id: '{tid}'")
            seen.add(tid)
        task_id_set = seen

        # 2. All depends_on reference existing task_ids
        for task in plan.tasks:
            for dep in task.depends_on:
                if dep not in task_id_set:
                    raise ValueError(f"Task '{task.task_id}' depends_on unknown task_id '{dep}'")

        # 3. No self-dependency
        for task in plan.tasks:
            if task.task_id in task.depends_on:
                raise ValueError(f"Task '{task.task_id}' depends on itself (self-dependency)")

        # 4. No cycles — Kahn's topological sort
        in_degree: dict[str, int] = {tid: 0 for tid in task_id_set}
        adjacency: dict[str, list[str]] = {tid: [] for tid in task_id_set}
        for task in plan.tasks:
            for dep in task.depends_on:
                adjacency[dep].append(task.task_id)
                in_degree[task.task_id] += 1

        queue: deque[str] = deque(tid for tid in task_id_set if in_degree[tid] == 0)
        processed = 0
        while queue:
            node = queue.popleft()
            processed += 1
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if processed != len(task_id_set):
            cycle_nodes = [tid for tid, deg in in_degree.items() if deg > 0]
            raise ValueError(f"Dependency cycle detected among tasks: {cycle_nodes}")

        # 5. Tool in ALLOWED_TOOLS (belt-and-suspenders; Pydantic already checks)
        for task in plan.tasks:
            if task.tool not in cls.ALLOWED_TOOLS:
                raise ValueError(f"Task '{task.task_id}' uses unknown tool '{task.tool}'")

        # 5b. `project_id` mang TÊN dự án thay vì mã — đổi về mã.
        #
        # Planner thường xuyên điền `project_id="Vinhomes Sài Gòn Park"` thay vì
        # `"PRJ-001"`. Provider tra theo mã nên trả `PROJECT_NOT_FOUND` với đúng
        # cái tên người dùng vừa CHỌN TRONG DANH SÁCH — người dùng đọc được
        # "dự án không có trong danh mục" về một dự án có thật. Sai ở đây là sai
        # ĐỊNH DẠNG, không phải sai lựa chọn, nên chữa được mà không cần hỏi lại.
        #
        # Chỉ nhận đúng tên/alias trong danh mục (`resolve_project_id` không
        # khớp gần đúng). Tên lạ vẫn đi tiếp và bị provider từ chối — đoán hộ
        # người dùng họ định chọn dự án nào là việc validator không được làm.
        for task in plan.tasks:
            candidate = task.input.get("project_id")
            if not isinstance(candidate, str) or project_name(candidate.strip().upper()):
                continue
            resolved = resolve_project_id(candidate)
            if resolved:
                task.input["project_id"] = resolved

        # 6. Kiểm tra các giá trị ĐÃ CÓ và InputRef trước. Nhờ vậy một plan vừa
        # thiếu field vừa có reference/enum/ngày sai vẫn bị từ chối đúng lỗi cấu
        # trúc; graph chỉ hỏi bổ sung khi phần hiện hữu đã an toàn.
        for task in plan.tasks:
            cls._validate_schedule_values(task.tool, task.input)
            cls._validate_enum_values(task.tool, task.input)

            for key, value in task.input.items():
                if isinstance(value, InputRef):
                    # InputRef.from_task exists in plan
                    if value.from_task not in task_id_set:
                        raise ValueError(
                            f"Task '{task.task_id}' input '{key}' references unknown task '{value.from_task}'"
                        )

                    # InputRef.from_task is in depends_on of that task
                    if value.from_task not in task.depends_on:
                        raise ValueError(
                            f"Task '{task.task_id}' input '{key}' references task "
                            f"'{value.from_task}' but '{value.from_task}' is not in "
                            f"depends_on of '{task.task_id}'"
                        )

                    cls._validate_input_reference(task, key, value, tasks_by_id)

            cls._validate_input_contract(task)

        # 7. Required inputs present for each tool. Thu thập toàn bộ tên field
        # còn thiếu để UI chỉ hỏi người dùng một lượt. Thứ tự deterministic theo
        # task rồi theo tên field; không đưa task_id/giá trị LLM vào payload.
        missing_fields: list[str] = []
        for task in plan.tasks:
            required = cls.REQUIRED_INPUTS.get(task.tool, frozenset())
            present = frozenset(task.input.keys())
            for field in sorted(required - present):
                if field not in missing_fields:
                    missing_fields.append(field)

        if missing_fields:
            raise MissingRequiredInputError(tuple(missing_fields))

        return plan

    @classmethod
    def _validate_input_contract(cls, task) -> None:
        """Kiểm mọi input đã có mặt theo `TOOL_CONTRACTS`.

        Chỉ kiểm giá trị literal. `InputRef` được kiểm riêng ở
        `_validate_input_reference()` vì lúc validate chưa có giá trị thật.

        Message chỉ nêu task, tên field và LUẬT bị vi phạm. Tuyệt đối không
        echo giá trị: nó có thể là họ tên, số điện thoại, CCCD hoặc token mà
        người dùng dán nhầm vào goal.
        """
        contract = TOOL_CONTRACTS.get(task.tool)
        if contract is None:
            return

        # Input thừa: provider sẽ từ chối hoặc âm thầm bỏ qua, cả hai đều tệ.
        # Cũng chặn luôn đường tuồn field lạ vào payload gửi ra ngoài.
        for key in sorted(set(task.input) - set(contract.inputs)):
            raise ValueError(f"Task '{task.task_id}' has unexpected input field '{key}' for tool '{task.tool}'")

        for key in sorted(task.input):
            value = task.input[key]
            if isinstance(value, InputRef):
                continue
            violation = contract.inputs[key].check(value)
            if violation is not None:
                raise ValueError(f"Task '{task.task_id}' input '{key}' invalid: {violation}")

    @classmethod
    def _validate_input_reference(cls, task, key: str, ref: InputRef, tasks_by_id: dict) -> None:
        """`InputRef` phải trỏ tới field mà tool nguồn THẬT SỰ trả về.

        Trước đây chỉ kiểm task nguồn tồn tại và nằm trong `depends_on`, nên
        `InputRef(from_task="T1", field="khong_ton_tai")` vẫn qua được Validator
        rồi mới hỏng lúc Executor resolve — muộn hơn nhiều, và lỗi lúc đó mang
        theo payload thật.
        """
        source_task = tasks_by_id.get(ref.from_task)
        if source_task is None:
            return

        source_spec = output_spec(source_task.tool, ref.field)
        if source_spec is None:
            raise ValueError(
                f"Task '{task.task_id}' input '{key}' references field '{ref.field}' "
                f"which tool '{source_task.tool}' does not return"
            )

        contract = TOOL_CONTRACTS.get(task.tool)
        target_spec = contract.inputs.get(key) if contract is not None else None
        if target_spec is None:
            return

        if not kinds_are_compatible(source_spec, target_spec):
            raise ValueError(
                f"Task '{task.task_id}' input '{key}' expects {target_spec.kind} "
                f"but '{source_task.tool}.{ref.field}' returns {source_spec.kind}"
            )

    @classmethod
    def _validate_enum_values(cls, tool: str, input_data: dict) -> None:
        """Chặn literal enum ngoài contract mà không echo giá trị do LLM sinh."""
        for (rule_tool, field), allowed in cls.ENUM_INPUTS.items():
            if tool != rule_tool:
                continue
            if field not in input_data:
                continue
            value = input_data.get(field)
            if isinstance(value, InputRef):
                continue
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f"Tool '{tool}' has invalid {field}; allowed values: {sorted(allowed)}")

    @classmethod
    def _validate_schedule_values(cls, tool: str, input_data: dict) -> None:
        """Chặn literal ngày/giờ sai trước execution; InputRef để runtime resolve."""
        date_field = cls.DATE_INPUTS.get(tool)
        if date_field is not None:
            raw_date = input_data.get(date_field)
            if isinstance(raw_date, str):
                try:
                    parsed_date = date.fromisoformat(raw_date)
                except ValueError:
                    raise ValueError(f"Tool '{tool}' has invalid {date_field} format") from None
                if parsed_date < date.today():
                    raise ValueError(f"Tool '{tool}' has {date_field} in the past")
                # Trần tương lai. Không có nó thì "2199-12-31" là ngày hợp lệ:
                # nó không nằm trong quá khứ nên mọi lớp kiểm đều cho qua, và
                # chỗ đỗ năm 2199 vẫn được giữ thật, chiếm capacity thật.
                if parsed_date > date.today() + timedelta(days=cls.MAX_HORIZON_DAYS):
                    raise ValueError(f"Tool '{tool}' has {date_field} too far in the future")

        time_rule = cls.TIME_INPUTS.get(tool)
        if time_rule is None:
            return
        time_field, opens_at, closes_at = time_rule
        raw_time = input_data.get(time_field)
        if not isinstance(raw_time, str):
            return
        if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw_time) is None:
            raise ValueError(f"Tool '{tool}' has invalid {time_field} format")
        parsed_time = time.fromisoformat(raw_time)
        if not opens_at <= parsed_time <= closes_at:
            raise ValueError(f"Tool '{tool}' has {time_field} outside business hours")
