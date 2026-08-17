"""Aggregates từ execution_logs cho audit/monitoring.

Owner: Thành Bảo (Decision layer)
File: src/monitoring/execution_metrics.py

Nguyên tắc:
  - Chỉ đọc execution_logs, KHÔNG đụng Executor/Connector.
  - Dùng `standard_result.error_code` (canonical) thay vì `raw_error_code` —
    executor hiện chưa ghi raw_error_code (hardcode None) nên group theo raw
    sẽ vô nghĩa.
  - Không trả message/detail để tránh rò PII ra metrics.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConnectorMetrics:
    total_attempts: int = 0
    success_attempts: int = 0
    failed_attempts: int = 0
    retried_attempts: int = 0  # attempt_number > 1
    avg_duration_ms: float | None = None
    error_breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.success_attempts / self.total_attempts

    @property
    def retry_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.retried_attempts / self.total_attempts


@dataclass(frozen=True)
class ExecutionMetrics:
    total_attempts: int = 0
    overall_success_rate: float = 0.0
    overall_retry_rate: float = 0.0
    avg_duration_ms: float | None = None
    connector_metrics: dict[str, ConnectorMetrics] = field(default_factory=dict)
    error_breakdown: dict[str, int] = field(default_factory=dict)


def _safe_error_code(standard_result: dict[str, Any] | None) -> str:
    if not isinstance(standard_result, dict):
        return "UNKNOWN_EXTERNAL_ERROR"
    code = standard_result.get("error_code")
    return code if isinstance(code, str) else "UNKNOWN_EXTERNAL_ERROR"


def _is_success(standard_result: dict[str, Any] | None) -> bool:
    if not isinstance(standard_result, dict):
        return False
    return bool(standard_result.get("success"))


def compute_execution_metrics(log_rows: list[dict[str, Any]]) -> ExecutionMetrics:
    """Tính aggregate từ list execution log rows.

    Mỗi row là dict với keys: connector_name, attempt_number, duration_ms,
    standard_result (dict). Các keys khác được bỏ qua.
    """
    total = len(log_rows)
    if total == 0:
        return ExecutionMetrics()

    durations: list[int] = []
    global_success = 0
    global_retried = 0
    global_errors: Counter[str] = Counter()

    by_connector: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in log_rows:
        connector = row.get("connector_name") or "unknown"
        by_connector[connector].append(row)

    connector_metrics: dict[str, ConnectorMetrics] = {}
    for connector, rows in by_connector.items():
        conn_total = len(rows)
        conn_success = 0
        conn_failed = 0
        conn_retried = 0
        conn_durations: list[int] = []
        conn_errors: Counter[str] = Counter()
        for row in rows:
            std = row.get("standard_result")
            if _is_success(std):
                conn_success += 1
                global_success += 1
            else:
                conn_failed += 1
                code = _safe_error_code(std)
                conn_errors[code] += 1
                global_errors[code] += 1
            if row.get("attempt_number", 1) > 1:
                conn_retried += 1
                global_retried += 1
            dur = row.get("duration_ms")
            if isinstance(dur, int):
                conn_durations.append(dur)
                durations.append(dur)

        avg = sum(conn_durations) / len(conn_durations) if conn_durations else None
        connector_metrics[connector] = ConnectorMetrics(
            total_attempts=conn_total,
            success_attempts=conn_success,
            failed_attempts=conn_failed,
            retried_attempts=conn_retried,
            avg_duration_ms=avg,
            error_breakdown=dict(conn_errors),
        )

    overall_avg = sum(durations) / len(durations) if durations else None
    return ExecutionMetrics(
        total_attempts=total,
        overall_success_rate=global_success / total if total else 0.0,
        overall_retry_rate=global_retried / total if total else 0.0,
        avg_duration_ms=overall_avg,
        connector_metrics=connector_metrics,
        error_breakdown=dict(global_errors),
    )
