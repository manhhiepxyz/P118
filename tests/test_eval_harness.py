"""Unit tests cho eval harness — không cần LLM API key."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eval.run_eval import (
    AUTHORITATIVE_FIELDS,
    EvalPlanner,
    _score_contracts,
    _score_provenance,
    _score_structure,
    _score_validator,
    evaluate_planner,
    run_eval,
    score_case,
)
from src.agents.planner import PlannerResult
from src.common.task_plan import InputRef, Task, TaskPlan


def _plan(tools: list[tuple[str, dict[str, Any], list[str]]], goal: str = "Goal") -> TaskPlan:
    tasks: list[Task] = []
    for i, (tool, input_data, deps) in enumerate(tools, start=1):
        tasks.append(Task(task_id=f"T{i}", tool=tool, depends_on=deps, input=input_data))
    return TaskPlan(goal=goal, tasks=tasks)


@pytest.fixture
def golden_path(tmp_path: Path) -> Path:
    data = {
        "version": "1.0",
        "cases": [
            {
                "id": "perfect",
                "goal": "Tìm căn hộ cho thuê",
                "existing_context": {},
                "expected": {
                    "tools": ["search_properties"],
                    "dependencies": {},
                    "inputs": {
                        "search_properties": {
                            "transaction_type": "rent",
                            "property_type": "apartment",
                            "residential_area": "Ocean Park",
                            "max_price": 10000000,
                        }
                    },
                },
            },
            {
                "id": "wrong_tool",
                "goal": "Đặt lịch xem nhà",
                "existing_context": {},
                "expected": {
                    "tools": ["schedule_property_viewing"],
                    "dependencies": {},
                    "inputs": {
                        "schedule_property_viewing": {
                            "project_id": "PRJ-001",
                            "viewing_date": "2026-12-10",
                            "viewing_time": "10:00",
                        }
                    },
                },
            },
            {
                "id": "missing",
                "goal": "Đặt chỗ đỗ xe",
                "existing_context": {"resident_id": "RES-001"},
                "expected": {
                    "status": "NEEDS_INFORMATION",
                    "missing_fields": ["plate_number", "vehicle_type", "parking_zone"],
                },
            },
            {
                "id": "chain",
                "goal": "Đăng ký xe và đặt chỗ",
                "existing_context": {"resident_id": "RES-001"},
                "expected": {
                    "tools": ["register_vehicle", "book_parking"],
                    "dependencies": {"book_parking": ["register_vehicle"]},
                    "inputs": {
                        "register_vehicle": {
                            "resident_id": "RES-001",
                            "plate_number": "51A-12345",
                            "vehicle_type": "car",
                        },
                        "book_parking": {
                            "vehicle_id": "T1",
                            "booking_date": "2026-12-10",
                            "parking_zone": "ZONE_A",
                        },
                    },
                },
            },
        ],
    }
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _FakePlanner:
    def __init__(self, results: dict[str, PlannerResult]) -> None:
        self.results = results

    async def plan(
        self,
        goal: str,
        existing_context: dict[str, Any],
        # Ký ức hội thoại. Fake PHẢI nhận tham số này, kể cả khi không dùng:
        # graph gọi `plan(..., recalled=...)`, và một fake thiếu tham số sẽ ném
        # TypeError — vốn bị `except Exception` trong `plan_node` nuốt và biến
        # thành `planning_error`. Test khi đó đỏ ở một chỗ hoàn toàn khác, với
        # `KeyError: 'planner_status'`, không nhắc gì tới chữ ký hàm.
        recalled: list[dict[str, Any]] | None = None,
    ) -> PlannerResult:
        return self.results[goal]


def test_score_perfect_plan() -> None:
    plan = _plan(
        [
            (
                "search_properties",
                {
                    "transaction_type": "rent",
                    "property_type": "apartment",
                    "residential_area": "Ocean Park",
                    "max_price": 10000000,
                },
                [],
            )
        ]
    )
    expected = {
        "tools": ["search_properties"],
        "dependencies": {},
        "inputs": {
            "search_properties": {
                "transaction_type": "rent",
                "property_type": "apartment",
                "residential_area": "Ocean Park",
                "max_price": 10000000,
            }
        },
    }
    assert _score_structure(plan, expected) == 1.0
    assert _score_validator(plan) == 1.0
    assert _score_contracts(plan) == 1.0


def test_score_wrong_tool() -> None:
    plan = _plan(
        [
            (
                "search_properties",
                {
                    "transaction_type": "rent",
                    "property_type": "apartment",
                    "residential_area": "Ocean Park",
                    "max_price": 10000000,
                },
                [],
            )
        ]
    )
    expected = {
        "tools": ["schedule_property_viewing"],
        "dependencies": {},
        "inputs": {
            "schedule_property_viewing": {
                "project_id": "PRJ-001",
                "viewing_date": "2026-12-10",
                "viewing_time": "10:00",
            }
        },
    }
    assert _score_structure(plan, expected) == 0.0


def test_score_validator_rejects_missing_required() -> None:
    plan = _plan([("book_parking", {"vehicle_id": "VEH-001", "booking_date": "2026-12-10"}, [])])
    assert _score_validator(plan) == 0.0


def test_score_contracts_partial() -> None:
    plan = _plan(
        [
            (
                "search_properties",
                {
                    "transaction_type": "rent",
                    "property_type": "apartment",
                    "residential_area": "Ocean Park",
                    "max_price": -1,
                },
                [],
            )
        ]
    )
    # max_price là một trong 4 field, nhưng contract chỉ kiểm kiểu/giá trị; -1 fail.
    # Tuy nhiên search_properties contract không check max_price > 0? Actually it does.
    assert _score_contracts(plan) == 0.75


def test_score_case_needs_information_match() -> None:
    result = score_case(
        {"status": "NEEDS_INFORMATION", "missing_fields": ["plate_number"]},
        None,
        "NEEDS_INFORMATION",
    )
    assert result["status_match"] == 1.0
    assert result["weighted"] > 0.0


def test_score_case_needs_information_mismatch() -> None:
    result = score_case(
        {"status": "NEEDS_INFORMATION", "missing_fields": ["plate_number"]},
        None,
        "READY",
    )
    assert result["status_match"] == 0.0
    assert result["weighted"] == 0.0


def test_provenance_rejects_fabricated_id() -> None:
    plan = _plan(
        [
            (
                "pay_fee",
                {"booking_id": "BOOK-FAKE", "amount": 150000, "currency": "VND"},
                [],
            )
        ]
    )
    assert _score_provenance(plan, "Thanh toán phí đặt chỗ đỗ xe", {}) == 0.0


def test_provenance_accepts_inputref() -> None:
    plan = _plan(
        [
            (
                "register_vehicle",
                {"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"},
                [],
            ),
            (
                "book_parking",
                {
                    "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                    "booking_date": "2026-12-10",
                    "parking_zone": "ZONE_A",
                },
                ["T1"],
            ),
        ]
    )
    assert _score_provenance(plan, "Đăng ký xe và đặt chỗ", {"resident_id": "RES-001"}) == 1.0


def test_provenance_accepts_values_from_context() -> None:
    plan = _plan(
        [
            (
                "pay_fee",
                {"booking_id": "BOOK-001", "amount": 150000, "currency": "VND"},
                [],
            )
        ]
    )
    context = {"booking_id": "BOOK-001", "amount": 150000, "currency": "VND"}
    assert _score_provenance(plan, "Thanh toán phí", context) == 1.0


def test_pii_not_leaked_to_plan_as_internal_id() -> None:
    """Context chứa PII; plan không được bịa internal ID từ PII dù goal trùng tên."""
    plan = _plan(
        [
            (
                "register_resident",
                {
                    "full_name": "Nguyễn Văn A",
                    "apartment_code": "A1201",
                    "residential_area": "Ocean Park",
                    "resident_id": "Nguyễn Văn A",
                },
                [],
            )
        ]
    )
    # Goal chỉ nói chung, không chứa "Nguyễn Văn A"; PII nằm trong context.
    context = {"owner_name": "Nguyễn Văn A"}
    assert _score_provenance(plan, "Đăng ký cư dân", context) == 0.0


def test_provenance_ignores_non_authoritative_fields() -> None:
    """vehicle_type, parking_zone, project_id là enum-normalized — không bị quét."""
    plan = _plan(
        [
            (
                "schedule_property_viewing",
                {"project_id": "PRJ-007", "viewing_date": "2026-12-10", "viewing_time": "10:00"},
                [],
            )
        ]
    )
    assert _score_provenance(plan, "Đặt lịch tham quan Vinhomes Ocean Park", {}) == 1.0


def test_authoritative_fields_do_not_include_common_strings() -> None:
    assert "parking_zone" not in AUTHORITATIVE_FIELDS
    assert "vehicle_type" not in AUTHORITATIVE_FIELDS
    assert "project_id" not in AUTHORITATIVE_FIELDS
    assert "full_name" not in AUTHORITATIVE_FIELDS


@pytest.mark.asyncio
async def test_report_goal_is_masked(tmp_path: Path) -> None:
    pii_goal = "PII leak test Nguyễn Văn A 0948500414"
    results = {
        pii_goal: PlannerResult(
            status="READY",
            plan=_plan(
                [
                    (
                        "search_properties",
                        {
                            "transaction_type": "rent",
                            "property_type": "apartment",
                            "residential_area": "Ocean Park",
                            "max_price": 10000000,
                        },
                        [],
                    )
                ],
                goal=pii_goal,
            ),
        ),
    }

    async def _factory() -> EvalPlanner:
        return EvalPlanner(_FakePlanner(results))  # type: ignore[arg-type]

    golden = {
        "cases": [
            {
                "id": "mask_test",
                "goal": pii_goal,
                "existing_context": {},
                "expected": {"tools": ["search_properties"]},
            }
        ]
    }
    golden_path = tmp_path / "golden.json"
    golden_path.write_text(json.dumps(golden), encoding="utf-8")
    output_path = tmp_path / "report.json"

    report = await run_eval(golden_path, output_path, planner_factory=_factory)

    assert report["results"][0]["goal"] is None
    assert "Nguyễn Văn A" not in output_path.read_text(encoding="utf-8")
    assert "0948500414" not in output_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_evaluate_planner_with_fake(golden_path: Path) -> None:
    results = {
        "Tìm căn hộ cho thuê": PlannerResult(
            status="READY",
            plan=_plan(
                [
                    (
                        "search_properties",
                        {
                            "transaction_type": "rent",
                            "property_type": "apartment",
                            "residential_area": "Ocean Park",
                            "max_price": 10000000,
                        },
                        [],
                    )
                ],
                goal="Tìm căn hộ cho thuê",
            ),
        ),
        "Đặt lịch xem nhà": PlannerResult(
            status="READY",
            plan=_plan(
                [
                    (
                        "schedule_property_viewing",
                        {"project_id": "PRJ-001", "viewing_date": "2026-12-10", "viewing_time": "10:00"},
                        [],
                    )
                ],
                goal="Đặt lịch xem nhà",
            ),
        ),
        "Đặt chỗ đỗ xe": PlannerResult(
            status="NEEDS_INFORMATION",
            missing_fields=("plate_number", "vehicle_type", "parking_zone"),
        ),
        "Đăng ký xe và đặt chỗ": PlannerResult(
            status="READY",
            plan=_plan(
                [
                    (
                        "register_vehicle",
                        {"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"},
                        [],
                    ),
                    (
                        "book_parking",
                        {
                            "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                            "booking_date": "2026-12-10",
                            "parking_zone": "ZONE_A",
                        },
                        ["T1"],
                    ),
                ],
                goal="Đăng ký xe và đặt chỗ",
            ),
        ),
    }
    planner = EvalPlanner(_FakePlanner(results))  # type: ignore[arg-type]
    report = await evaluate_planner(planner, golden_path)

    assert report["summary"]["total_cases"] == 4
    assert report["summary"]["status_accuracy"] == 1.0
    assert report["summary"]["average_score"] >= 0.75


def _fake_results_for(golden_path: Path) -> dict[str, PlannerResult]:
    """Dựng PlannerResult cho 4 case trong `golden_path` fixture."""
    return {
        "Tìm căn hộ cho thuê": PlannerResult(
            status="READY",
            plan=_plan(
                [
                    (
                        "search_properties",
                        {
                            "transaction_type": "rent",
                            "property_type": "apartment",
                            "residential_area": "Ocean Park",
                            "max_price": 10000000,
                        },
                        [],
                    )
                ],
                goal="Tìm căn hộ cho thuê",
            ),
        ),
        "Đặt lịch xem nhà": PlannerResult(
            status="READY",
            plan=_plan(
                [
                    (
                        "schedule_property_viewing",
                        {"project_id": "PRJ-001", "viewing_date": "2026-12-10", "viewing_time": "10:00"},
                        [],
                    )
                ],
                goal="Đặt lịch xem nhà",
            ),
        ),
        "Đặt chỗ đỗ xe": PlannerResult(
            status="NEEDS_INFORMATION",
            missing_fields=("plate_number", "vehicle_type", "parking_zone"),
        ),
        "Đăng ký xe và đặt chỗ": PlannerResult(
            status="READY",
            plan=_plan(
                [
                    (
                        "register_vehicle",
                        {"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"},
                        [],
                    ),
                    (
                        "book_parking",
                        {
                            "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                            "booking_date": "2026-12-10",
                            "parking_zone": "ZONE_A",
                        },
                        ["T1"],
                    ),
                ],
                goal="Đăng ký xe và đặt chỗ",
            ),
        ),
    }


async def _run_metadata_report(golden_path: Path, tmp_path: Path) -> tuple[dict, Path]:
    """Chạy run_eval với fake planner; trả (report, output_path)."""
    results = _fake_results_for(golden_path)
    output_path = tmp_path / "report.json"

    async def _factory() -> EvalPlanner:
        return EvalPlanner(_FakePlanner(results))  # type: ignore[arg-type]

    report = await run_eval(golden_path, output_path, planner_factory=_factory)
    return report, output_path


@pytest.mark.asyncio
async def test_report_metadata_block_exists(golden_path: Path, tmp_path: Path) -> None:
    """Baseline commit-to-commit (Phase D): report phải có metadata block."""
    report, _ = await _run_metadata_report(golden_path, tmp_path)

    metadata = report["metadata"]
    assert "git_sha" in metadata
    assert "timestamp" in metadata
    assert "provider" in metadata
    assert "model" in metadata
    assert "temperature" in metadata
    assert "llm_total_tokens" in metadata


@pytest.mark.asyncio
async def test_report_writes_runs_copy(golden_path: Path, tmp_path: Path) -> None:
    """Report viết BOTH: eval_report.json + runs/<sha>-<ts>.json (giữ baseline)."""
    report, output_path = await _run_metadata_report(golden_path, tmp_path)

    assert output_path.exists()
    runs_dir = output_path.parent / "runs"
    run_files = list(runs_dir.glob("*.json"))
    assert len(run_files) == 1

    runs_report = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert runs_report["metadata"]["git_sha"] == report["metadata"]["git_sha"]
    assert runs_report["summary"] == report["summary"]
    # Goal bị mask (PII) trong cả hai file.
    assert all(r["goal"] is None for r in runs_report["results"])


@pytest.mark.asyncio
async def test_report_metadata_git_mocked(golden_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Không phải git repo → git_sha='unknown'; metadata KHÔNG rò goal (PII)."""
    monkeypatch.setattr("eval.run_eval._git_sha", lambda: "unknown")

    report, output_path = await _run_metadata_report(golden_path, tmp_path)

    assert report["metadata"]["git_sha"] == "unknown"
    serialized = json.dumps(report, ensure_ascii=False)
    assert "Đặt chỗ đỗ xe" not in serialized  # goal không được lọt vào report
    assert "Nguyễn" not in serialized


@pytest.mark.asyncio
async def test_evaluate_planner_status_accuracy_only(golden_path: Path) -> None:
    """Kể cả score thấp, status accuracy vẫn được tính đúng."""
    results = {
        # "Tìm căn hộ cho thuê" giờ nằm NGOÀI phạm vi Agent — Planner trả
        # `supported_goal` thiếu, không hỏi field của một dịch vụ đã loại.
        "Tìm căn hộ cho thuê": PlannerResult(status="NEEDS_INFORMATION", missing_fields=("supported_goal",)),
        "Đặt lịch xem nhà": PlannerResult(status="NEEDS_INFORMATION", missing_fields=("project_id",)),
        "Đặt chỗ đỗ xe": PlannerResult(
            status="NEEDS_INFORMATION", missing_fields=("plate_number", "vehicle_type", "parking_zone")
        ),
        "Đăng ký xe và đặt chỗ": PlannerResult(
            status="READY",
            plan=_plan(
                [
                    (
                        "register_vehicle",
                        {"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"},
                        [],
                    ),
                    (
                        "book_parking",
                        {
                            "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                            "booking_date": "2026-12-10",
                            "parking_zone": "ZONE_A",
                        },
                        ["T1"],
                    ),
                ],
                goal="Đăng ký xe và đặt chỗ",
            ),
        ),
    }
    planner = EvalPlanner(_FakePlanner(results))  # type: ignore[arg-type]
    report = await evaluate_planner(planner, golden_path)

    # 2/4 status khớp (Đặt chỗ đỗ xe NEEDS_INFORMATION, Đăng ký xe và đặt chỗ READY).
    assert report["summary"]["status_accuracy"] == 0.5
    assert report["summary"]["average_score"] < 0.8
    assert report["summary"]["passed"] is False
