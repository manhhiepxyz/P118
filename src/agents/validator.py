from __future__ import annotations

import re
from collections import deque
from datetime import date, time

from src.common.task_plan import InputRef, TaskPlan


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
    }

    DATE_INPUTS: dict[str, str] = {
        "schedule_property_viewing": "viewing_date",
        "book_parking": "booking_date",
        "create_maintenance_request": "preferred_date",
        "schedule_move": "move_date",
    }

    TIME_INPUTS: dict[str, tuple[str, time, time]] = {
        "schedule_property_viewing": ("viewing_time", time(8, 0), time(17, 30)),
        "create_maintenance_request": ("preferred_time", time(8, 0), time(18, 0)),
        "schedule_move": ("move_time", time(7, 0), time(20, 0)),
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

        for task in plan.tasks:
            # 5. Tool in ALLOWED_TOOLS (belt-and-suspenders; Pydantic already checks)
            if task.tool not in cls.ALLOWED_TOOLS:
                raise ValueError(f"Task '{task.task_id}' uses unknown tool '{task.tool}'")

            # 6. Required inputs present for each tool
            required = cls.REQUIRED_INPUTS.get(task.tool, frozenset())
            present = frozenset(task.input.keys())
            missing = required - present
            if missing:
                raise ValueError(
                    f"Task '{task.task_id}' (tool='{task.tool}') is missing required input fields: {sorted(missing)}"
                )

            cls._validate_schedule_values(task.tool, task.input)
            cls._validate_enum_values(task.tool, task.input)

            for key, value in task.input.items():
                if isinstance(value, InputRef):
                    # 7. InputRef.from_task exists in plan
                    if value.from_task not in task_id_set:
                        raise ValueError(
                            f"Task '{task.task_id}' input '{key}' references unknown task '{value.from_task}'"
                        )

                    # 8. InputRef.from_task is in depends_on of that task
                    if value.from_task not in task.depends_on:
                        raise ValueError(
                            f"Task '{task.task_id}' input '{key}' references task "
                            f"'{value.from_task}' but '{value.from_task}' is not in "
                            f"depends_on of '{task.task_id}'"
                        )

        return plan

    @classmethod
    def _validate_enum_values(cls, tool: str, input_data: dict) -> None:
        """Chặn literal enum ngoài contract mà không echo giá trị do LLM sinh."""
        for (rule_tool, field), allowed in cls.ENUM_INPUTS.items():
            if tool != rule_tool:
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
