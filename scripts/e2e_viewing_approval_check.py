"""E2E: lịch tham quan chờ duyệt qua /review + đặt xe 30s + thông tin tài xế.

Chạy một luồng thật qua HTTP trên stack đang chạy (backend 8000 + providers
8001-8009 + PostgreSQL). Không import Planner/Executor — đúng như browser:
mọi thứ qua API. Kiểm ba thứ feature yêu cầu:

  1. Khách gửi "đặt lịch tham quan + đặt xe" → workflow DỪNG ở
     `WAITING_APPROVAL`, response có `viewing_approval` (lịch + dự án, KHÔNG PII).
  2. Provider duyệt lịch qua `/viewing-approvals/{wf}/decide`, rồi duyệt riêng
     bước `book_shuttle` qua hàng đợi `/service-approvals` → workflow SUCCESS;
     task xe có details
     Tài xế / Biển số xe / Loại xe / Giờ đón.
  3. Reject path: provider từ chối kèm lý do → workflow FAILED, khách thấy lý do.

Không in response thô, không in credential. Mọi lỗi → exit 1 với message an toàn.

Cách chạy (backend + providers đã lên, PYTHONIOENCODING=utf-8):
    .venv/Scripts/python.exe scripts/e2e_viewing_approval_check.py
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Cho phép chạy trực tiếp: sys.path[0] là scripts/, thiếu repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

from src.api.auth import hash_password  # noqa: E402
from src.config import get_settings  # noqa: E402

BACKEND = os.environ.get("P118_BACKEND", "http://127.0.0.1:8000")
PROVIDER_USERNAME = f"e2e_viewing_provider_{int(time.time())}"
CUSTOMER_USERNAME = f"e2e_viewing_customer_{int(time.time())}"
PASSWORD = "E2eViewingPass123!"

TERMINAL = frozenset({"SUCCESS", "FAILED", "PLANNING_ERROR", "VALIDATION_ERROR", "EXECUTION_ERROR", "CANCELLED"})


class _APIError(RuntimeError):
    pass


class _Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, path: str, *, method: str = "GET", body: dict | None = None,
                 token: str | None = None) -> dict:
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=payload, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get("detail")
            except Exception:  # noqa: BLE001
                detail = None
            raise _APIError(
                detail if isinstance(detail, str) else f"HTTP {exc.code}"
            ) from None
        except OSError:
            raise _APIError(f"không kết nối được tới {self.base_url}") from None

    def register(self, username: str) -> None:
        self._request(
            "/api/v1/auth/register",
            method="POST",
            body={
                "username": username,
                "password": PASSWORD,
                "full_name": "Khách E2E tham quan",
                "phone": "0912-345-678",
            },
        )

    def login(self, username: str) -> dict:
        result = self._request(
            "/api/v1/auth/login", method="POST",
            body={"username": username, "password": PASSWORD},
        )
        return {"token": result["access_token"], "user": result["user"]}

    def start_goal(self, goal: str, token: str) -> dict:
        return self._request(
            "/api/v1/workflows/demo/start", method="POST",
            body={"goal": goal}, token=token,
        )

    def status(self, workflow_id: str, token: str) -> dict:
        return self._request(f"/api/v1/workflows/demo/{workflow_id}", token=token)

    def continue_workflow(self, workflow_id: str, message: str, token: str,
                          fields: dict | None = None) -> dict:
        body: dict = {}
        if message:
            body["message"] = message
        if fields:
            body["fields"] = fields
        return self._request(
            f"/api/v1/workflows/demo/{workflow_id}/continue", method="POST",
            body=body, token=token,
        )

    def list_viewing_approvals(self, token: str, status: str | None = None) -> list[dict]:
        path = "/api/v1/viewing-approvals"
        if status:
            path += f"?status={status}"
        return self._request(path, token=token)["items"]

    def decide_viewing(self, workflow_id: str, token: str, decision: str, reject_reason: str | None = None) -> dict:
        body: dict = {"decision": decision}
        if reject_reason is not None:
            body["reject_reason"] = reject_reason
        return self._request(
            f"/api/v1/viewing-approvals/{workflow_id}/decide", method="POST",
            body=body, token=token,
        )

    def list_service_approvals(self, token: str, status: str = "AWAITING") -> list[dict]:
        return self._request(
            f"/api/v1/service-approvals?status={status}", token=token,
        )["items"]

    def decide_service(self, workflow_id: str, task_id: str, token: str,
                       decision: str, reject_reason: str | None = None) -> dict:
        body: dict = {"decision": decision}
        if reject_reason is not None:
            body["reject_reason"] = reject_reason
        return self._request(
            f"/api/v1/service-approvals/{workflow_id}/{task_id}/decide",
            method="POST", body=body, token=token,
        )


def _poll(client: _Client, workflow_id: str, token: str, *,
          follow_up: dict[str, str | int] | None = None,
          sleep: float = 1.0, timeout: float = 180.0) -> dict:
    """Poll tới trạng thái dừng; tự trả lời NEEDS_INFORMATION nếu có `follow_up`.

    Planner đôi khi không tách được ngày/giờ từ câu goal — nó hỏi lại đúng như
    UI. Script trả lời bằng `fields` structured (contract form của /continue),
    không phải câu chat tự do, để luồng deterministic.
    """
    deadline = time.monotonic() + timeout
    current_id = workflow_id
    while time.monotonic() < deadline:
        body = client.status(current_id, token)
        status = body.get("status")
        if status == "NEEDS_INFORMATION":
            missing = body.get("missing_fields") or []
            if follow_up:
                fields = {k: v for k, v in follow_up.items() if k in missing}
                if fields:
                    try:
                        answered = client.continue_workflow(current_id, "", token, fields=fields)
                    except _APIError as exc:
                        raise _APIError(
                            f"trả lời bổ sung {sorted(fields)} bị từ chối: {exc}"
                        ) from None
                    # /continue đóng workflow cha, tạo workflow con chạy tiếp plan.
                    # Theo id con, không được tiếp tục poll id cha đã archive.
                    child_id = answered.get("workflow_id")
                    if child_id and child_id != current_id:
                        current_id = child_id
                    continue
            raise _APIError(
                f"workflow cần bổ sung {missing}: {body.get('question') or 'thiếu thông tin'}"
            )
        if status in TERMINAL or status == "WAITING_APPROVAL":
            return body
        time.sleep(sleep)
    raise _APIError("workflow không tới trạng thái dừng trong thời gian cho phép")


async def _ensure_provider_account() -> None:
    """Tạo tài khoản provider E2E với mật khẩu đã biết — idempotent."""
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES ($1, $2, 'provider')
                ON CONFLICT (username) DO UPDATE
                    SET password_hash = EXCLUDED.password_hash,
                        role = 'provider',
                        updated_at = NOW()
                """,
                PROVIDER_USERNAME,
                hash_password(PASSWORD),
            )
    finally:
        await pool.close()


def _fail(message: str) -> int:
    print(f"\nDỪNG: {message}", file=sys.stderr)
    return 1


def _check_shuttle_details(body: dict, workflow_id: str) -> str:
    """Xác nhận task xe có 4 dòng details tài xế."""
    tasks = {t.get("tool"): t for t in body.get("tasks") or []}
    shuttle = tasks.get("book_shuttle")
    if shuttle is None:
        raise _APIError(f"workflow {workflow_id} thiếu task book_shuttle")
    if shuttle.get("status") != "SUCCESS":
        raise _APIError(
            f"task xe chưa SUCCESS (status={shuttle.get('status')}): {shuttle.get('message')}"
        )
    details = {d.get("label"): d.get("value") for d in shuttle.get("details") or []}
    for label in ("Tài xế", "Biển số xe", "Loại xe", "Giờ đón"):
        if not details.get(label):
            raise _APIError(f"task xe thiếu details '{label}'")
    return (
        f"✓ Xe: tài xế {details['Tài xế']}, biển số {details['Biển số xe']}, "
        f"{details['Loại xe']}, giờ đón {details['Giờ đón']}"
    )


def main() -> int:
    # Ngày tham quan phải mới mỗi lần chạy: Tour provider (8005) giữ slot
    # booking TRONG BỘ NHỚ của nó và chặn trùng (project_id, ngày, giờ) bằng 409
    # VIEWING_ALREADY_BOOKED. Ngày đặt cứng khiến lần chạy thứ hai đặt trùng slot
    # lần đầu → duyệt thất bại không phải do code. Offset theo giờ thực giúp mỗi
    # lần chạy chọn một ngày khác nhau, tránh đụng slot cũ.
    today = _dt.date.today()
    offset = int(time.time()) % 90 + 7
    viewing_day = today + _dt.timedelta(days=offset)
    reject_day = today + _dt.timedelta(days=offset + 1)
    viewing_date = viewing_day.isoformat()
    reject_date = reject_day.isoformat()

    goal = (
        f"Đặt lịch tham quan Vinhomes Sài Gòn Park ngày {viewing_date} lúc 09:30 "
        "và đặt xe đưa đón cho 4 người"
    )

    client = _Client(BACKEND)

    try:
        asyncio.run(_ensure_provider_account())
    except Exception as exc:  # noqa: BLE001
        return _fail(f"không tạo được tài khoản provider ({type(exc).__name__}).")

    try:
        client.register(CUSTOMER_USERNAME)
    except _APIError:
        pass  # đã tồn tại từ lần chạy trước — login là đủ
    customer = client.login(CUSTOMER_USERNAME)
    provider = client.login(PROVIDER_USERNAME)
    print(f"[1] Tài khoản: khách={CUSTOMER_USERNAME}, provider={PROVIDER_USERNAME}")

    # -- 1. Khách gửi goal tham quan + đặt xe ---------------------------------
    started = client.start_goal(goal, customer["token"])
    workflow_id = started.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id:
        return _fail("backend không tạo được workflow_id.")

    body = _poll(
        client, workflow_id, customer["token"],
        follow_up={
            "project_name": "Vinhomes Sài Gòn Park",
            "viewing_date": viewing_date,
            "viewing_time": "09:30",
        },
    )
    if body.get("status") != "WAITING_APPROVAL":
        return _fail(
            f"workflow {workflow_id} không dừng ở WAITING_APPROVAL mà {body.get('status')}: "
            f"{body.get('summary') or body.get('message')}"
        )
    # /continue đã đóng cha và tạo con; viewing approval nằm trên id CON.
    workflow_id = body.get("workflow_id") or workflow_id

    approval = body.get("viewing_approval") or {}
    if not approval.get("project_name") or not approval.get("viewing_date"):
        return _fail(f"response thiếu viewing_approval: {approval}")
    if approval.get("wants_shuttle") is not True:
        return _fail("viewing_approval phải có wants_shuttle=true (goal có đặt xe).")

    waiting_task = next(
        (t for t in body.get("tasks") or [] if t.get("tool") == "schedule_property_viewing"),
        None,
    )
    if waiting_task is None or waiting_task.get("status") != "WAITING_APPROVAL":
        return _fail("task schedule_property_viewing phải hiện WAITING_APPROVAL khi chờ duyệt.")
    print(
        f"[2] Workflow {workflow_id} dừng chờ duyệt: {approval['project_name']} "
        f"ngày {approval['viewing_date']} lúc {approval['viewing_time']} — "
        f"{approval.get('passenger_count')} khách, xe: {'có' if approval.get('wants_shuttle') else 'không'}"
    )

    # Không được rò PII người yêu cầu ở view khách.
    raw = json.dumps(body, ensure_ascii=False)
    if "applicant_name" in raw or "applicant_phone" in raw:
        return _fail("view khách lộ PII người yêu cầu.")

    # -- 2. Provider thấy request AWAITING và duyệt ---------------------------
    items = client.list_viewing_approvals(provider["token"], status="AWAITING")
    pending = next((item for item in items if item.get("workflow_id") == workflow_id), None)
    if pending is None:
        return _fail("provider không thấy yêu cầu AWAITING trong /viewing-approvals.")
    if not pending.get("applicant_name") or not pending.get("applicant_phone"):
        return _fail("view provider thiếu PII người yêu cầu (applicant_name/phone).")
    print(f"[3] Provider thấy request AWAITING của {pending['applicant_name']} ({pending['applicant_phone']}).")

    decided = client.decide_viewing(workflow_id, provider["token"], decision="approve")
    if decided.get("decision") != "approve" or decided.get("status") != "APPROVED":
        return _fail(f"provider duyệt thất bại: {decided}")

    after_viewing = _poll(client, workflow_id, customer["token"], timeout=120)
    if after_viewing.get("status") != "WAITING_APPROVAL":
        return _fail(
            f"sau duyệt lịch, workflow phải chờ đơn vị xe mà {after_viewing.get('status')}."
        )
    service_queue = client.list_service_approvals(provider["token"])
    shuttle_request = next(
        (
            item for item in service_queue
            if item.get("workflow_id") == workflow_id and item.get("tool") == "book_shuttle"
        ),
        None,
    )
    if shuttle_request is None:
        return _fail("provider không thấy bước book_shuttle trong hàng đợi dịch vụ.")
    client.decide_service(
        workflow_id,
        str(shuttle_request["task_id"]),
        provider["token"],
        decision="approve",
    )

    final = _poll(client, workflow_id, customer["token"], timeout=120)
    if final.get("status") != "SUCCESS":
        return _fail(
            f"workflow sau duyệt không SUCCESS mà {final.get('status')}: "
            f"{final.get('summary') or final.get('message')}"
        )
    shuttle_line = _check_shuttle_details(final, workflow_id)
    print(f"[4] Workflow {workflow_id} SUCCESS sau khi duyệt (~30s đặt xe).")
    print(f"    {shuttle_line}")

    # -- 3. Reject path: request thứ 2 bị từ chối -----------------------------
    started2 = client.start_goal(
        f"Đặt lịch tham quan Vinhomes Global Gate Hạ Long ngày {reject_date} lúc 14:00",
        customer["token"],
    )
    workflow2 = started2.get("workflow_id")
    if not isinstance(workflow2, str) or not workflow2:
        return _fail("backend không tạo được workflow_id thứ hai.")

    body2 = _poll(
        client, workflow2, customer["token"],
        follow_up={"viewing_date": reject_date, "viewing_time": "14:00"},
    )
    if body2.get("status") != "WAITING_APPROVAL":
        return _fail(f"workflow thứ hai không chờ duyệt mà {body2.get('status')}.")
    workflow2 = body2.get("workflow_id") or workflow2

    reason = "Khung giờ 14:00 đã kín trong tuần tham quan."
    decided2 = client.decide_viewing(workflow2, provider["token"], decision="reject", reject_reason=reason)
    if decided2.get("status") != "REJECTED":
        return _fail(f"provider từ chối thất bại: {decided2}")

    final2 = _poll(client, workflow2, customer["token"])
    if final2.get("status") != "FAILED":
        return _fail(f"workflow bị từ chối phải FAILED mà {final2.get('status')}.")
    # Khách thấy lý do từ chối ở message/summary.
    visible = f"{final2.get('summary') or ''} {final2.get('message') or ''}"
    if "kín" not in visible and reason not in visible:
        return _fail("khách không thấy lý do từ chối ở trạng thái workflow.")
    print(f"[5] Workflow {workflow2} FAILED đúng khi bị từ chối kèm lý do.")

    print("\n✅ E2E PASS — tham quan chờ duyệt, duyệt rồi đặt xe 30s kèm 4 thông tin tài xế.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
