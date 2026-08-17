"""Tests cho execution metrics aggregator.

Owner: Thành Bảo (Decision layer)
File: tests/test_execution_metrics.py
"""

from __future__ import annotations

import pytest

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.monitoring.execution_metrics import compute_execution_metrics


def _row(
    connector_name: str = "TransportConnector",
    attempt_number: int = 1,
    duration_ms: int = 100,
    standard_result: dict | None = None,
) -> dict:
    return {
        "connector_name": connector_name,
        "attempt_number": attempt_number,
        "duration_ms": duration_ms,
        "standard_result": standard_result or StandardResult.ok({"id": "X"}).__dict__,
    }


def test_empty_logs_return_zero_metrics() -> None:
    metrics = compute_execution_metrics([])
    assert metrics.total_attempts == 0
    assert metrics.overall_success_rate == 0.0
    assert metrics.avg_duration_ms is None
    assert metrics.connector_metrics == {}


def test_success_rate_per_connector() -> None:
    rows = [
        _row("TransportConnector", standard_result=StandardResult.ok({"id": "T1"}).__dict__),
        _row("TransportConnector", standard_result=StandardResult.ok({"id": "T2"}).__dict__),
        _row(
            "TransportConnector",
            standard_result=StandardResult.fail(ErrorCode.NO_AVAILABILITY, "full").__dict__,
        ),
        _row("PaymentConnector", standard_result=StandardResult.ok({"id": "P1"}).__dict__),
    ]
    metrics = compute_execution_metrics(rows)

    assert metrics.total_attempts == 4
    assert metrics.overall_success_rate == 0.75
    transport = metrics.connector_metrics["TransportConnector"]
    assert transport.total_attempts == 3
    assert transport.success_rate == pytest.approx(2 / 3)
    assert transport.error_breakdown == {"NO_AVAILABILITY": 1}
    payment = metrics.connector_metrics["PaymentConnector"]
    assert payment.success_rate == 1.0


def test_retry_rate_counts_attempt_number_greater_than_one() -> None:
    rows = [
        _row("TransportConnector", attempt_number=1),
        _row("TransportConnector", attempt_number=2),
        _row("TransportConnector", attempt_number=3),
    ]
    metrics = compute_execution_metrics(rows)

    assert metrics.overall_retry_rate == 2 / 3
    conn = metrics.connector_metrics["TransportConnector"]
    assert conn.retried_attempts == 2
    assert conn.retry_rate == 2 / 3


def test_avg_duration_computed_from_valid_integers() -> None:
    rows = [
        _row("A", duration_ms=100),
        _row("A", duration_ms=200),
        _row("B", duration_ms=50),
    ]
    metrics = compute_execution_metrics(rows)

    assert metrics.avg_duration_ms == 350 / 3
    assert metrics.connector_metrics["A"].avg_duration_ms == 150.0
    assert metrics.connector_metrics["B"].avg_duration_ms == 50.0


def test_missing_duration_ignored() -> None:
    rows = [
        _row("A", duration_ms=100),
        {"connector_name": "A", "attempt_number": 1, "standard_result": StandardResult.ok({}).__dict__},
    ]
    metrics = compute_execution_metrics(rows)
    assert metrics.connector_metrics["A"].avg_duration_ms == 100.0


def test_missing_standard_result_defaults_to_unknown_error() -> None:
    rows = [
        {"connector_name": "A", "attempt_number": 1},
        {"connector_name": "A", "attempt_number": 1, "standard_result": {"success": False}},
    ]
    metrics = compute_execution_metrics(rows)
    assert metrics.overall_success_rate == 0.0
    assert metrics.error_breakdown == {"UNKNOWN_EXTERNAL_ERROR": 2}


def test_global_error_breakdown_aggregates_across_connectors() -> None:
    rows = [
        _row("A", standard_result=StandardResult.fail(ErrorCode.NO_AVAILABILITY, "").__dict__),
        _row("B", standard_result=StandardResult.fail(ErrorCode.NO_AVAILABILITY, "").__dict__),
        _row("B", standard_result=StandardResult.fail(ErrorCode.BOOKING_ALREADY_EXISTS, "").__dict__),
    ]
    metrics = compute_execution_metrics(rows)
    assert metrics.error_breakdown == {
        "NO_AVAILABILITY": 2,
        "BOOKING_ALREADY_EXISTS": 1,
    }
