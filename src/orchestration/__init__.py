"""Execution boundary cho P-118 — tầng điều phối giữa Planner/LangGraph/API và Executor.

Owner: Mạnh Hiệp (Executor layer)
File: src/orchestration/__init__.py

Module này là điểm nối DUY NHẤT để LangGraph/API gọi Executor:
    [LangGraph/API] → execute_plan() → [TaskPlanValidator] → [Executor]
                        └── ExecutionResult (workflow_id, status, task_results, failure)

Tuần 2 Gate 2: không sửa src/agents/** hoặc src/api/** — module này cung cấp
interface chuẩn để hai tầng đó tích hợp.
"""
