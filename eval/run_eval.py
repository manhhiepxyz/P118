"""Eval harness cho Planner P-118.

Owner: Mạnh Hiệp (Executor layer)
File: eval/run_eval.py

Chạy:
    python -m eval.run_eval [--golden eval/golden_plans.json] [--output eval/results/eval_report.json]

Input:
    Golden set JSON gồm các case {goal, existing_context, expected}.

Output:
    JSON report với 3 scores per case:
      - structure (50%): đúng tool sequence + dependency + input keys
      - validator (30%): plan qua được TaskPlanValidator
      - contracts (20%): input fields pass TOOL_CONTRACTS checks

Exit code:
    0 nếu avg_score >= 0.80, ngược lại 1.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.agents.planner import Planner, PlannerResult
from src.agents.validator import MissingRequiredInputError, TaskPlanValidator
from src.common.task_plan import InputRef, Task, TaskPlan
from src.common.tool_contract import TOOL_CONTRACTS
from src.config import get_settings
from src.monitoring.usage_tracker import (
    LlmUsageLogger,
    reset_usage_context,
    usage_context,
)
from src.services.llm import get_llm, structured_output_method

SCORE_WEIGHTS = {
    "structure": 0.4,
    "validator": 0.25,
    "contracts": 0.2,
    "provenance": 0.15,
}

# Các giá trị phải có nguồn tin cậy (goal/context/InputRef). KHÔNG quét enum/string
# thường như parking_zone, vehicle_type, project_id — chúng được normalize từ văn
# phong ngườ dùng và không xuất hiện nguyên văn trong goal.
AUTHORITATIVE_FIELDS = frozenset({"booking_id", "vehicle_id", "resident_id", "amount", "currency"})

# Các key trong existing_context được coi là nguồn authoritative. Values từ key
# ngoài danh sách này (vd owner_name, phone, email) không được dùng để chứng minh
# nguồn gốc cho internal ID — đó là PII leak.
AUTHORITATIVE_CONTEXT_KEYS = frozenset({"booking_id", "vehicle_id", "resident_id", "amount", "currency"})


def _git_sha() -> str:
    """HEAD SHA của repo; 'unknown' nếu không phải git repo / lỗi."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:  # noqa: BLE001 - git không chạy được thì không phải lỗi fatal
        pass
    return "unknown"


def _build_metadata(llm_total_tokens: int) -> dict[str, Any]:
    """Metadata của một run eval — baseline commit-to-commit (Phase D).

    KHÔNG chứa goal (PII). Chứa thời điểm, model, tổng token LLM của run này
    để so sánh cost giữa các commit.
    """
    settings = get_settings()
    return {
        "git_sha": _git_sha(),
        "timestamp": datetime.now(UTC).isoformat(),
        "provider": settings.llm_provider,
        "model": (settings.deepseek_model_name if settings.llm_provider == "deepseek" else settings.model_name),
        "temperature": settings.llm_temperature,
        "llm_total_tokens": llm_total_tokens,
    }


class EvalPlanner:
    """Wrapper cho Planner thật hoặc fake để eval có thể inject.

    `usage_logger` tích lũy token/cost của từng lần plan; `run_eval` gọi
    `await planner.flush_usage()` sau vòng lặp để ghi `llm_usage` với stage='eval'
    và gắn tổng token vào metadata report (baseline commit-to-commit, Phase D).
    """

    def __init__(self, planner: Planner | None = None) -> None:
        self.usage_logger = LlmUsageLogger()
        self._planner = planner or Planner(
            get_llm(callbacks=[self.usage_logger]),
            structured_output_method=structured_output_method(),
        )

    async def plan(self, goal: str, existing_context: dict[str, Any]) -> PlannerResult:
        return await self._planner.plan(goal, existing_context)

    async def flush_usage(self) -> int:
        """Ghi usage của run này xuống DB; trả tổng token (0 nếu DB lỗi)."""
        total = sum(int(row.get("total_tokens") or 0) for row in self.usage_logger.pending)
        await self.usage_logger.flush()
        return total


def _tool_sequence(plan: TaskPlan) -> list[str]:
    return [task.tool for task in plan.tasks]


def _task_by_tool(plan: TaskPlan, tool: str) -> Task | None:
    for task in plan.tasks:
        if task.tool == tool:
            return task
    return None


def _resolve_input_value(value: Any, completed: dict[str, dict[str, Any]]) -> Any:
    """Resolve InputRef bằng kết quả giả lập từ golden expected."""
    if isinstance(value, InputRef):
        src = completed.get(value.from_task, {})
        return src.get(value.field)
    return value


def _score_structure(plan: TaskPlan, expected: dict[str, Any]) -> float:
    """So sánh tool sequence, dependency edges và input keys."""
    expected_tools = expected.get("tools", [])
    actual_tools = _tool_sequence(plan)

    # Tool sequence phải khớp chính xác (trừ case multi-vehicle mà golden rút gọn).
    if actual_tools != expected_tools:
        # Cho phép rút gọn: nếu golden gộp nhiều xe thành 1 register_vehicle.
        if set(actual_tools) == set(expected_tools) and len(expected_tools) == len(actual_tools):
            pass
        else:
            return 0.0

    expected_deps = expected.get("dependencies", {})
    expected_input_keys = expected.get("inputs", {})

    points = 0.0
    total = 1 + len(expected_deps) + len(expected_input_keys)

    # 1 điểm cho tool sequence.
    points += 1.0

    # Dependency edges.
    for tool, deps in expected_deps.items():
        task = _task_by_tool(plan, tool)
        if task is None:
            continue
        expected_dep_tools = set(deps)
        actual_dep_tools = {
            plan.tasks[int(dep[1:]) - 1].tool for dep in task.depends_on if dep.startswith("T") and dep[1:].isdigit()
        }
        if expected_dep_tools == actual_dep_tools:
            points += 1.0

    # Input keys.
    for tool, expected_inputs in expected_input_keys.items():
        task = _task_by_tool(plan, tool)
        if task is None:
            continue
        actual_keys = set(task.input.keys())
        expected_keys = set(expected_inputs.keys())
        if actual_keys == expected_keys:
            points += 1.0
        elif expected_keys <= actual_keys:
            points += 0.5

    return points / total if total > 0 else 1.0


def _score_validator(plan: TaskPlan) -> float:
    """1.0 nếu Validator chấp nhận, 0.0 nếu từ chối."""
    try:
        TaskPlanValidator.validate(plan)
    except (MissingRequiredInputError, ValueError):
        return 0.0
    return 1.0


def _score_contracts(plan: TaskPlan) -> float:
    """Tỷ lệ field pass contract check."""
    total = 0
    passed = 0
    for task in plan.tasks:
        contract = TOOL_CONTRACTS.get(task.tool)
        if contract is None:
            continue
        for name, spec in contract.inputs.items():
            if name not in task.input:
                continue
            total += 1
            value = task.input[name]
            # Bỏ qua InputRef ở đây; validator/input-ref sẽ kiểm riêng.
            if isinstance(value, InputRef):
                passed += 1
                continue
            if spec.check(value) is None:
                passed += 1
    return passed / total if total > 0 else 1.0


def _flatten_values(obj: Any) -> set[str]:
    """Lấy tất cả string/primitive từ dict/list để so sánh provenance."""
    values: set[str] = set()
    if isinstance(obj, dict):
        for v in obj.values():
            values.update(_flatten_values(v))
    elif isinstance(obj, list):
        for item in obj:
            values.update(_flatten_values(item))
    elif isinstance(obj, (str, int, float, bool)):
        values.add(str(obj))
    return values


def _score_provenance(
    plan: TaskPlan,
    goal: str,
    existing_context: dict[str, Any],
) -> float:
    """Kiểm tra mọi giá trị authoritative trong plan có nguồn tin cậy.

    Giá trị được coi là hợp lệ nếu:
      - Là InputRef (lấy từ kết quả task trước), hoặc
      - Nằm trong goal, hoặc
      - Nằm trong existing_context dưới một key authoritative.

    Values từ context key PII (owner_name, phone, email…) không được dùng để
    chứng minh nguồn gốc internal ID.

    Trả 1.0 nếu không vi phạm, 0.0 nếu có ít nhất một giá trị bịa.
    """
    allowed_from_goal = _flatten_values(goal)
    allowed_from_context: set[str] = set()
    for key, value in existing_context.items():
        if key in AUTHORITATIVE_CONTEXT_KEYS:
            allowed_from_context.update(_flatten_values(value))
    allowed_values = allowed_from_goal | allowed_from_context

    for task in plan.tasks:
        for name, value in task.input.items():
            if name not in AUTHORITATIVE_FIELDS:
                continue
            if isinstance(value, InputRef):
                continue
            if str(value) in allowed_values:
                continue
            return 0.0
    return 1.0


def _weighted_average(scores: dict[str, float]) -> float:
    return sum(scores[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)


def _plan_to_dict(plan: TaskPlan) -> dict[str, Any]:
    return plan.model_dump(mode="json")


def _result_to_expected(result: PlannerResult) -> dict[str, Any]:
    if result.is_ready and result.plan is not None:
        plan_dict = _plan_to_dict(result.plan)
        # Mask goal trong plan output để tránh rò PII.
        plan_dict["goal"] = None
        return {
            "status": "READY",
            "plan": plan_dict,
        }
    return {
        "status": "NEEDS_INFORMATION",
        "missing_fields": list(result.missing_fields or []),
    }


def _expected_status(expected: dict[str, Any]) -> str | None:
    return expected.get("status")


def score_case(
    expected: dict[str, Any],
    actual_plan: TaskPlan | None,
    actual_status: str | None,
    goal: str = "",
    existing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score một case dựa trên expected và actual."""
    expected_status = _expected_status(expected)
    existing_context = existing_context or {}

    # Case mong đợi NEEDS_INFORMATION / policy block.
    if expected_status in {"NEEDS_INFORMATION", "RESIDENT_LINKING_OUTSIDE_AGENT", "RESIDENT_ACCESS_REQUIRED"}:
        status_match = 1.0 if actual_status == expected_status else 0.0
        if expected_status == "NEEDS_INFORMATION":
            actual_missing: tuple[str, ...] | list[str] = []
            if actual_plan is not None and hasattr(actual_plan, "missing_fields"):
                actual_missing = actual_plan.missing_fields
            missing_score = _score_missing_fields(expected.get("missing_fields", []), list(actual_missing))
            return {
                "structure": 0.0,
                "validator": 0.0,
                "contracts": 0.0,
                "provenance": 0.0,
                "status_match": status_match,
                "missing_fields_score": missing_score,
                "weighted": 0.5 * status_match + 0.5 * missing_score,
            }
        return {
            "structure": 0.0,
            "validator": 0.0,
            "contracts": 0.0,
            "provenance": 0.0,
            "status_match": status_match,
            "missing_fields_score": None,
            "weighted": status_match,
        }

    # Case mong đợi READY plan.
    if actual_plan is None:
        return {
            "structure": 0.0,
            "validator": 0.0,
            "contracts": 0.0,
            "provenance": 0.0,
            "status_match": 0.0,
            "missing_fields_score": None,
            "weighted": 0.0,
        }

    structure = _score_structure(actual_plan, expected)
    validator = _score_validator(actual_plan)
    contracts = _score_contracts(actual_plan)
    provenance = _score_provenance(actual_plan, goal, existing_context)
    weighted = _weighted_average(
        {"structure": structure, "validator": validator, "contracts": contracts, "provenance": provenance}
    )

    return {
        "structure": structure,
        "validator": validator,
        "contracts": contracts,
        "provenance": provenance,
        "status_match": 1.0,
        "missing_fields_score": None,
        "weighted": weighted,
    }


def _score_missing_fields(expected: list[str], actual: list[str]) -> float:
    """Tỷ lệ overlap giữa expected và actual missing fields."""
    if not expected:
        return 1.0
    expected_set = set(expected)
    actual_set = set(actual)
    if not actual_set:
        return 0.0
    overlap = len(expected_set & actual_set)
    # Cho phép actual có thêm field hợp lý (ví dụ vehicle_type).
    precision = overlap / len(actual_set)
    recall = overlap / len(expected_set)
    if precision == 0 or recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


async def evaluate_planner(
    planner: EvalPlanner,
    golden_path: Path,
) -> dict[str, Any]:
    """Đánh giá Planner trên golden set."""
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    cases = golden.get("cases", [])

    results: list[dict[str, Any]] = []
    total = 0.0
    status_matches = 0

    # Theo dõi usage cho cả run eval (stage='eval', run_id=git_sha) — baseline
    # commit-to-commit (Phase D). Không thuộc workflow nào → workflow_id None.
    usage_token = usage_context(stage="eval", run_id=_git_sha())

    for case in cases:
        goal = case["goal"]
        existing_context = case.get("existing_context", {})
        result = await planner.plan(goal, existing_context)
        actual_status = "READY" if result.is_ready else "NEEDS_INFORMATION"
        actual_plan = result.plan if result.is_ready else None

        scores = score_case(case["expected"], actual_plan, actual_status, goal, existing_context)
        total += scores["weighted"]
        if scores["status_match"] == 1.0:
            status_matches += 1

        # Mask goal trong output report để tránh rò PII ra file commit.
        results.append(
            {
                "id": case["id"],
                "goal": None,
                "expected_status": _expected_status(case["expected"]),
                "actual_status": actual_status,
                "scores": scores,
                "actual": _result_to_expected(result),
            }
        )

    avg_score = total / len(cases) if cases else 0.0
    status_accuracy = status_matches / len(cases) if cases else 0.0

    reset_usage_context(usage_token)
    llm_total_tokens = await planner.flush_usage()

    return {
        "summary": {
            "total_cases": len(cases),
            "average_score": round(avg_score, 4),
            "status_accuracy": round(status_accuracy, 4),
            "passed": avg_score >= 0.8,
            "llm_total_tokens": llm_total_tokens,
        },
        "results": results,
    }


async def run_eval(
    golden_path: Path,
    output_path: Path | None,
    planner_factory: Callable[[], Awaitable[EvalPlanner]] | None = None,
) -> dict[str, Any]:
    """Entry point chính; cho phép inject planner_factory trong test."""
    if planner_factory is None:
        planner = EvalPlanner()
    else:
        planner = await planner_factory()

    report = await evaluate_planner(planner, golden_path)

    # Baseline commit-to-commit (Phase D): gắn metadata (git SHA, model, tổng
    # token LLM của run) vào report — KHÔNG chứa goal (PII).
    report["metadata"] = _build_metadata(report["summary"].get("llm_total_tokens", 0))

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        # Bản chụp lịch sử theo run — giữ được baseline để so commit-to-commit.
        runs_dir = output_path.parent / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_name = f"{report['metadata']['git_sha']}-{report['metadata']['timestamp'][:19].replace(':', '')}"
        runs_path = runs_dir / f"{run_name}.json"
        runs_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate P-118 Planner against golden set")
    parser.add_argument("--golden", type=Path, default=Path("eval/golden_plans.json"))
    parser.add_argument("--output", type=Path, default=Path("eval/results/eval_report.json"))
    args = parser.parse_args(argv)

    import asyncio

    report = asyncio.run(run_eval(args.golden, args.output))

    summary = report["summary"]
    print(f"Cases: {summary['total_cases']}")
    print(f"Average score: {summary['average_score']:.2%}")
    print(f"Status accuracy: {summary['status_accuracy']:.2%}")
    print(f"Pass threshold (>=80%): {'PASS' if summary['passed'] else 'FAIL'}")

    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
