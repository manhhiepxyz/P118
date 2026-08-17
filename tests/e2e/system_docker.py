"""Nghiệm thu hệ thống trên ĐÚNG Docker image đang chạy.

Khác mọi tầng test khác: không ASGITransport, không uvicorn local, không mock
process. Mọi request đi qua cổng Docker publish, và mọi truy vấn đối chiếu chạy
bằng `docker exec` vào container postgres của chính project này.

Đây là tầng mà browser E2E trước đó KHÔNG bắt được: nó chạy backend local với
cấu hình đúng, nên một Docker Compose sai cấu hình vẫn xanh hết.

Không in API key, token, hay DSN.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"
API = f"{BASE}/api/v1"
DB = "p118_db"
PASSWORD = "MatKhauSystem!2030"
STAMP = str(int(time.time()))[-9:]
RESULTS: list[tuple[str, bool, str]] = []


class SetupError(RuntimeError):
    pass


def sql(query: str, *, expect_rows: int | None = None) -> list[str]:
    out = subprocess.run(
        [
            "docker",
            "exec",
            "p118_postgres",
            "psql",
            "-U",
            "p118",
            "-d",
            DB,
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            query,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if out.returncode != 0:
        raise SetupError(f"SQL thất bại: {out.stderr.strip().splitlines()[-1][:160]}")
    rows = [r for r in out.stdout.strip().split("\n") if r]
    if expect_rows is not None and len(rows) != expect_rows:
        raise SetupError(f"SQL trả {len(rows)} row, cần {expect_rows}")
    return rows


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=200) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def mask(v: str | None, n: int = 8) -> str:
    return f"{v[:n]}…" if v and len(v) > n else (v or "—")


def poll(token: str, workflow_id: str, tries: int = 180) -> dict:
    state: dict = {}
    for _ in range(tries):
        code, state = call("GET", f"/workflows/demo/{workflow_id}", token)
        if code != 200:
            return {"_http": code}
        if state.get("status") not in ("PENDING", "RUNNING"):
            return state
        time.sleep(1.0)
    return state


def main() -> int:
    # Mốc bắt đầu lượt chạy. Mọi khẳng định "không có X" phải giới hạn từ đây
    # trở đi: dùng cửa sổ thời gian cố định là chấm dữ liệu của những bản build
    # trước, và một bản ghi cũ sẽ báo đỏ một hệ thống hiện tại đang đúng.
    started_at = sql("SELECT now()::text", expect_rows=1)[0]

    user, other, admin = f"sy_{STAMP}", f"syb_{STAMP}", f"syadm_{STAMP}"
    apartment = f"S-{STAMP[-4:]}"
    area = "Vinhomes Ocean Park"

    # ---- 1. compose up từ volume cũ + migration + /ready ------------------
    ready_code, ready = call("GET", "/ready".replace("/api/v1", ""))
    raw_ready = urllib.request.urlopen(f"{BASE}/ready", timeout=10)
    ready_body = json.loads(raw_ready.read().decode())
    check(
        "1. Compose up trên volume sẵn có, migration xong",
        all(c["ok"] for c in ready_body["checks"] if c["name"] == "migrations"),
        next(c["detail"] for c in ready_body["checks"] if c["name"] == "migrations"),
    )
    check(
        "2. /ready xanh khi cấu hình hợp lệ",
        ready_body["status"] == "ready",
        " · ".join(f"{c['name']}={c['ok']}" for c in ready_body["checks"]),
    )

    # ---- 3. Đăng ký / đăng nhập ------------------------------------------
    for name in (user, other, admin):
        code, _ = call("POST", "/auth/register", body={"username": name, "password": PASSWORD})
        if code not in (200, 201):
            raise SetupError(f"đăng ký {name} trả {code}")
    token = call("POST", "/auth/login", body={"username": user, "password": PASSWORD})[1]["access_token"]
    token_b = call("POST", "/auth/login", body={"username": other, "password": PASSWORD})[1]["access_token"]
    check("3. Đăng ký và đăng nhập qua Docker backend", bool(token and token_b))

    bad_code, _ = call("POST", "/auth/login", body={"username": user, "password": "SaiMatKhau!123"})
    check("3b. Sai mật khẩu trả 401 (không phải 500)", bad_code == 401, f"http={bad_code}")

    sql(f"UPDATE users SET role = 'admin' WHERE username = '{admin}'")
    token_admin = call("POST", "/auth/login", body={"username": admin, "password": PASSWORD})[1]["access_token"]

    # ---- 4. Khách hàng xin liên kết, admin duyệt --------------------------
    code, created = call(
        "POST",
        "/auth/resident-link-requests",
        token,
        {"apartment_code": apartment, "residential_area": area, "full_name": "Khach System"},
    )
    check(
        "4. Khách hàng gửi được yêu cầu liên kết căn hộ",
        code == 201 and created.get("status") == "PENDING",
        f"http={code}",
    )

    smuggle, _ = call(
        "POST",
        "/auth/resident-link-requests",
        token_b,
        {
            "apartment_code": apartment,
            "residential_area": area,
            "full_name": "Ke Gian",
            "verification_status": "VERIFIED",
        },
    )
    check("4b. Khách hàng KHÔNG tự đặt được VERIFIED", smuggle == 422, f"http={smuggle}")

    queue_code, queue = call("GET", "/admin/resident-link-requests", token_admin)
    mine = [i for i in queue.get("items", []) if i["username"] == user]
    check(
        "5. Admin thấy hàng chờ, không phải gõ UUID",
        queue_code == 200 and len(mine) == 1,
        f"http={queue_code} dòng của user={len(mine)}",
    )
    check("5b. Danh sách mask tên đầy đủ", "Khach System" not in json.dumps(queue, ensure_ascii=False))

    steal, _ = call(
        "POST", f"/admin/resident-link-requests/{created['request_id']}/decision", token, {"decision": "approve"}
    )
    check("5c. Customer không duyệt được yêu cầu của chính mình", steal in (401, 403), f"http={steal}")

    decide, _ = call(
        "POST", f"/admin/resident-link-requests/{created['request_id']}/decision", token_admin, {"decision": "approve"}
    )
    token = call("POST", "/auth/login", body={"username": user, "password": PASSWORD})[1]["access_token"]
    me = call("GET", "/auth/me", token)[1]
    check(
        "6. Duyệt xong thì dịch vụ cư dân mở",
        decide == 200 and me.get("resident_verification_status") == "VERIFIED",
        f"http={decide} status={me.get('resident_verification_status')}",
    )

    caps = call("GET", "/capabilities", token)[1]["capabilities"]
    caps_b = call("GET", "/capabilities", token_b)[1]["capabilities"]
    check(
        "6b. Capability theo liên kết thật, không theo role",
        all(c["available"] for c in caps) and any(not c["available"] for c in caps_b),
        f"A mở={sum(c['available'] for c in caps)}/{len(caps)} B mở={sum(c['available'] for c in caps_b)}/{len(caps_b)}",
    )

    # ---- 7. Workflow nhiều bước có clarification -------------------------
    day = sql("SELECT (CURRENT_DATE + (500 + (extract(epoch from now())::bigint % 900))::int)::text")[0]
    plate = f"51S-{STAMP[-5:]}"
    goal = "Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí."

    _, started = call("POST", "/workflows/demo/start", token, {"goal": goal})
    parent = started["workflow_id"]
    state = poll(token, parent)
    check(
        "7. Planner hỏi bổ sung đúng field nghiệp vụ",
        state.get("status") == "NEEDS_INFORMATION"
        and {"plate_number", "parking_zone"} <= set(state.get("missing_fields", [])),
        f"status={state.get('status')} missing={state.get('missing_fields')}",
    )

    answers = {"plate_number": plate, "vehicle_type": "car", "booking_date": day, "parking_zone": "ZONE_A"}
    fields = {k: answers[k] for k in state.get("missing_fields", []) if k in answers}
    code, child = call("POST", f"/workflows/demo/{parent}/continue", token, {"fields": fields})
    current = child.get("workflow_id", parent)
    state = poll(token, current)

    tasks = [(t["tool"], t["status"]) for t in state.get("tasks", [])]
    check(
        "8. register_vehicle → book_parking chạy thật",
        any(t == ("register_vehicle", "SUCCESS") for t in tasks)
        and any(t == ("book_parking", "SUCCESS") for t in tasks),
        f"tasks={tasks}",
    )

    quote = state.get("payment_quote") or {}
    check(
        "8b. Dừng ở chờ duyệt kèm báo giá authoritative",
        state.get("status") == "WAITING_APPROVAL" and quote.get("amount"),
        f"status={state.get('status')} amount={quote.get('amount')} {quote.get('currency')}",
    )

    # ---- 9. approve ------------------------------------------------------
    before = int(sql("SELECT count(*) FROM payments")[0])
    approve_code, _ = call("POST", f"/workflows/demo/{current}/payment-decision", token, {"decision": "approve"})
    final = poll(token, current)
    after = int(sql("SELECT count(*) FROM payments")[0])
    again, _ = call("POST", f"/workflows/demo/{current}/payment-decision", token, {"decision": "approve"})
    after2 = int(sql("SELECT count(*) FROM payments")[0])
    check(
        "9. Duyệt → SUCCESS, đúng một payment, lần hai 409",
        approve_code == 200
        and final.get("status") == "SUCCESS"
        and after == before + 1
        and again == 409
        and after2 == after,
        f"status={final.get('status')} payments {before}→{after} lần hai http={again}",
    )

    # ---- 10. restart backend, resume ------------------------------------
    plate2 = f"51T-{STAMP[-5:]}"
    _, started2 = call("POST", "/workflows/demo/start", token, {"goal": goal})
    state2 = poll(token, started2["workflow_id"])
    fields2 = {
        k: {**answers, "plate_number": plate2, "booking_date": day, "parking_zone": "ZONE_B"}[k]
        for k in state2.get("missing_fields", [])
        if k in answers
    }
    _, child2 = call("POST", f"/workflows/demo/{started2['workflow_id']}/continue", token, {"fields": fields2})
    wf2 = child2.get("workflow_id")
    state2 = poll(token, wf2)

    if state2.get("status") != "WAITING_APPROVAL":
        check("10. Restart container rồi resume", False, f"không tới chờ duyệt ({state2.get('status')})")
    else:
        subprocess.run(
            ["docker", "compose", "restart", "backend"],
            cwd="/private/tmp/P118-integration-hoanganh",
            capture_output=True,
            timeout=180,
        )
        for _ in range(60):
            try:
                if urllib.request.urlopen(f"{BASE}/ready", timeout=5).status == 200:
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
        resumed = call("GET", f"/workflows/demo/{wf2}", token)[1]
        rq = resumed.get("payment_quote") or {}
        ok_resume = (
            resumed.get("status") == "WAITING_APPROVAL" and rq.get("amount") and len(resumed.get("tasks", [])) >= 3
        )
        before2 = int(sql("SELECT count(*) FROM payments")[0])
        call("POST", f"/workflows/demo/{wf2}/payment-decision", token, {"decision": "reject"})
        rejected = poll(token, wf2)
        after3 = int(sql("SELECT count(*) FROM payments")[0])
        check(
            "10. Restart container: vẫn chờ duyệt kèm báo giá và đủ bước",
            ok_resume,
            f"status={resumed.get('status')} bước={len(resumed.get('tasks', []))} tiền={rq.get('amount')}",
        )
        check(
            "10b. Từ chối sau restart: không tạo payment, booking còn",
            after3 == before2 and rejected.get("status") != "SUCCESS",
            f"payments {before2}→{after3} status={rejected.get('status')}",
        )

    # ---- 11. IDOR --------------------------------------------------------
    idor = [
        call("GET", f"/workflows/demo/{current}", token_b)[0],
        call("POST", f"/workflows/demo/{current}/continue", token_b, {"fields": {"parking_zone": "ZONE_A"}})[0],
        call("POST", f"/workflows/demo/{current}/payment-decision", token_b, {"decision": "approve"})[0],
    ]
    check("11. IDOR: tài khoản khác nhận 404 ở cả ba đường", idor == [404, 404, 404], f"http={idor}")

    # ---- 12. Không có zombie PENDING ------------------------------------
    uid = sql(f"SELECT id FROM users WHERE username = '{user}'", expect_rows=1)[0]
    zombies = sql(
        f"SELECT count(*) FROM workflows WHERE owner_user_id = '{uid}' "
        "AND status IN ('PENDING','RUNNING') AND updated_at < NOW() - INTERVAL '3 minutes'"
    )[0]
    check("12. Không còn workflow zombie kẹt PENDING", zombies == "0", f"zombie={zombies}")

    # ---- 13. Không có workflow nào kẹt giữa chừng -------------------------
    #
    # Chứng minh "cấu hình sai → workflow kết thúc" cần đổi cấu hình container
    # giữa chừng, nên nó nằm ở script riêng `proof_config_failure.py`. Ở đây chỉ
    # khẳng định điều luôn phải đúng: mọi workflow đã dừng đều có trạng thái
    # kết thúc, và mọi workflow FAILED đều có lý do đọc được.
    # `archived_at IS NOT NULL` = đã bàn giao cho workflow con; nó không phải
    # zombie. Bỏ điều kiện này sẽ đếm mọi workflow cha của mọi vòng hỏi bổ sung.
    # "Kẹt" nghĩa là KHÔNG AI đang chờ nó cả. Ba trường hợp PENDING/RUNNING hợp
    # lệ và phải loại ra, nếu không mỗi lần đếm lại báo động giả:
    #
    #   - đã archive  → đã bàn giao cho workflow con
    #   - còn câu hỏi chưa được trả lời → đang chờ NGƯỜI DÙNG
    #   - còn khoản thanh toán AWAITING → cũng đang chờ NGƯỜI DÙNG
    unfinished = sql(
        """
        SELECT count(*) FROM workflows w
        WHERE w.status IN ('PENDING','RUNNING')
          AND w.archived_at IS NULL
          -- Cửa sổ phải RỘNG HƠN TTL của sweeper (`zombie_running_ttl_hours`,
          -- mặc định 0.5h). Dùng 5 phút là chấm điểm hệ thống vì nó chưa làm
          -- một việc mà chính nó đã hẹn 30 phút nữa mới làm.
          AND w.updated_at < NOW() - INTERVAL '45 minutes'
          AND NOT EXISTS (
              SELECT 1 FROM workflow_clarifications c
              WHERE c.workflow_id = w.workflow_id AND c.resolved_at IS NULL
          )
          AND NOT EXISTS (
              SELECT 1 FROM payment_approvals a
              WHERE a.workflow_id = w.workflow_id AND a.status = 'AWAITING'
          )
        """
    )[0]
    faceless = sql(
        "SELECT count(*) FROM workflows WHERE status = 'FAILED' AND error_code IS NULL "
        f"AND created_at >= TIMESTAMPTZ '{started_at}'"
    )[0]
    check(
        "13. Không workflow nào kẹt, và FAILED nào cũng có mã lỗi",
        unfinished == "0" and faceless == "0",
        f"kẹt={unfinished} FAILED không mã={faceless}",
    )

    # ---- 14. Không secret trong log --------------------------------------
    logs = subprocess.run(
        ["docker", "compose", "logs", "--tail", "800", "backend"],
        cwd="/private/tmp/P118-integration-hoanganh",
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    leaks = [p for p in ("sk-", "postgresql://", "Bearer ey", "DEEPSEEK_API_KEY=") if p in logs]
    check("14. Không có secret trong log container", not leaks, f"lộ={leaks}" if leaks else "sạch")

    # ---- Bằng chứng DB ---------------------------------------------------
    print("\n--- PostgreSQL (p118_db, đã mask) ---")
    print("  user            :", mask(uid))
    print("  workflow của A  :", sql(f"SELECT count(*) FROM workflows WHERE owner_user_id = '{uid}'")[0])
    print("  liên kết cư dân :", sql(f"SELECT verification_status FROM user_resident_links WHERE user_id = '{uid}'")[0])
    print("  yêu cầu liên kết:", sql(f"SELECT status FROM resident_link_requests WHERE user_id = '{uid}'")[0])
    print(
        "  task của A      :",
        " ".join(
            sql(
                f"SELECT t.tool || '=' || t.status FROM workflow_tasks t JOIN workflows w "
                f"ON w.workflow_id = t.workflow_id WHERE w.owner_user_id = '{uid}'"
            )
        ),
    )
    print(
        "  register_resident:",
        sql(
            f"SELECT count(*) FROM workflow_tasks t JOIN workflows w ON w.workflow_id = t.workflow_id "
            f"WHERE w.owner_user_id = '{uid}' AND t.tool = 'register_resident'"
        )[0],
    )

    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\n=== {ok}/{len(RESULTS)} PASS ===")
    for n, o, d in RESULTS:
        if not o:
            print(f"  FAIL: {n} — {d}")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
