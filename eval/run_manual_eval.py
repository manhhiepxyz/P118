"""Năm eval case chạy THẬT trên DeepSeek qua STACK DOCKER, ghi bằng chứng đã mask.

Chạy: `python eval/run_manual_eval.py > eval/results/raw.json`

KHÁC `eval/run_eval.py`: file kia chấm Planner trên golden set (offline, không
cần stack). File này chạy năm kịch bản người dùng thật đầu-cuối qua stack Docker.
Mặc định trỏ `http://127.0.0.1:8080` và database `p118_db` của stack hiện tại.

Không fixture, không output tự viết. Mọi con số đến từ HTTP response của backend
đang chạy và từ `p118_e2e_db`.

Không in API key, token, hay prompt nội bộ.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Chạy được bằng `python eval/run_manual_eval.py` từ gốc repo mà không cần đặt
# PYTHONPATH — người chạy eval không nên phải biết chuyện đóng gói.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.agents.validator import TaskPlanValidator  # noqa: E402

MAX_HORIZON_DAYS = TaskPlanValidator.MAX_HORIZON_DAYS

BASE = os.environ.get("P118_API", "http://127.0.0.1:8080/api/v1")
DB = os.environ.get("P118_DB", "p118_db")
PASSWORD = "MatKhauE2E!2030"
STAMP = str(int(time.time()))[-9:]

CASES: list[dict] = []

GOAL_PAY = "Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí."


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
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
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


def mask(value: str | None, keep: int = 8) -> str:
    if not value:
        return "—"
    return f"{value[:keep]}…" if len(value) > keep else value


def mask_plate(plate: str) -> str:
    return f"{plate[:4]}***"


def poll(token: str, workflow_id: str, tries: int = 150) -> dict:
    state: dict = {}
    for _ in range(tries):
        code, state = call("GET", f"/workflows/demo/{workflow_id}", token)
        if code != 200:
            return {"_http": code}
        if state.get("status") not in ("PENDING", "RUNNING"):
            return state
        time.sleep(1.0)
    return state


def llm_calls(workflow_ids: list[str]) -> str:
    """Số lần gọi mô hình, đọc từ bảng `llm_usage` — không phải ước lượng."""
    ids = ", ".join(f"'{w}'::uuid" for w in workflow_ids if w)
    if not ids:
        return "—"
    rows = sql(f"SELECT stage || '=' || count(*) FROM llm_usage WHERE workflow_id IN ({ids}) GROUP BY stage")
    total = sql(f"SELECT count(*) FROM llm_usage WHERE workflow_id IN ({ids})")[0]
    return f"{total} ({', '.join(rows) if rows else 'không có bản ghi'})"


def tool_titles(state: dict) -> list[str]:
    return [t.get("title") or t.get("tool", "?") for t in state.get("tasks", [])]


def task_pairs(state: dict) -> list[str]:
    return [f"{t.get('title') or t.get('tool')}={t.get('status')}" for t in state.get("tasks", [])]


def main() -> int:
    user = f"ev_{STAMP}"
    admin = f"evadm_{STAMP}"
    resident = f"RES-EV-{STAMP[-5:]}"
    apartment = f"E-{STAMP[-4:]}"

    for name in (user, admin):
        code, _ = call("POST", "/auth/register", body={"username": name, "password": PASSWORD})
        if code not in (200, 201):
            raise SetupError(f"đăng ký {name} trả {code}")

    token_admin = call("POST", "/auth/login", body={"username": admin, "password": PASSWORD})[1]["access_token"]
    sql(f"UPDATE users SET role = 'admin' WHERE username = '{admin}'")
    token_admin = call("POST", "/auth/login", body={"username": admin, "password": PASSWORD})[1]["access_token"]

    uid = sql(f"SELECT id FROM users WHERE username = '{user}'", expect_rows=1)[0]
    sql(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
        f"VALUES ('{resident}', 'Khach Eval', '{apartment}', 'Vinhomes Ocean Park') ON CONFLICT DO NOTHING"
    )
    code, _ = call(
        "POST",
        f"/admin/resident-links/{uid}",
        token_admin,
        {"resident_id": resident, "verification_status": "VERIFIED"},
    )
    if code not in (200, 201):
        raise SetupError(f"gán liên kết cư dân trả {code}")

    token = call("POST", "/auth/login", body={"username": user, "password": PASSWORD})[1]["access_token"]
    me = call("GET", "/auth/me", token)[1]
    if me.get("resident_verification_status") != "VERIFIED":
        raise SetupError("tài khoản chưa VERIFIED — setup hỏng, dừng")

    # Ngày duy nhất cho mỗi lần chạy: khu đỗ xe có sức chứa, dùng lại ngày cũ
    # sẽ nhận NO_AVAILABILITY và làm hỏng happy path.
    # Mốc tính TỪ HÔM NAY. Lần trước mốc tính từ 1970 nên rơi vào 2025 — validator
    # từ chối đúng, còn eval thì báo FAIL cho một hệ thống đang hoạt động bình thường.
    # Ngày rải rộng để hai lượt chạy không đụng nhau, NHƯNG phải nằm trong trần
    # ngày đặt trước của hệ thống (`TaskPlanValidator.MAX_HORIZON_DAYS`).
    #
    # Bản trước rải tới +3399 ngày (~9,3 năm). Lúc chưa có trần thì nó chạy;
    # từ khi hệ thống từ chối ngày vô lý, chính harness sinh ra dữ liệu mà sản
    # phẩm phải từ chối — và eval đỏ vì lỗi của harness chứ không phải của sản
    # phẩm. Đọc trần từ nguồn thay vì chép số, để hai bên không lệch lại.
    span = MAX_HORIZON_DAYS - 60  # chừa chỗ cho day(+30)
    base_day = 30 + (int(STAMP) % span)
    day = lambda off: sql(f"SELECT (CURRENT_DATE + {base_day + off})::text")[0]  # noqa: E731

    def run_parking(plate: str, date: str, zone: str) -> tuple[str, str, dict, int]:
        """Chạy tới điểm dừng đầu tiên. Trả (parent, child, state, số vòng hỏi)."""
        _, started = call("POST", "/workflows/demo/start", token, {"goal": GOAL_PAY})
        parent = started["workflow_id"]
        state = poll(token, parent)
        rounds = 0
        current = parent
        answers = {"plate_number": plate, "vehicle_type": "car", "booking_date": date, "parking_zone": zone}
        while state.get("status") == "NEEDS_INFORMATION" and rounds < 3:
            fields = {k: answers[k] for k in state.get("missing_fields", []) if k in answers}
            if not fields:
                break
            code, child = call("POST", f"/workflows/demo/{current}/continue", token, {"fields": fields})
            if code not in (200, 202):
                state = {"status": f"HTTP_{code}", "_detail": child.get("detail")}
                break
            current = child["workflow_id"]
            rounds += 1
            state = poll(token, current)
        return parent, current, state, rounds

    # ------------------------------------------------------------------
    # Case 1 — parking happy path, có clarification, dừng ở WAITING_APPROVAL
    # ------------------------------------------------------------------
    plate1, date1 = f"51E-{STAMP[-5:]}", day(0)
    p1, c1, s1, r1 = run_parking(plate1, date1, "ZONE_A")
    quote1 = s1.get("payment_quote") or {}
    CASES.append(
        {
            "title": "1. Đăng ký xe + đặt chỗ đỗ xe, có vòng hỏi bổ sung",
            "goal": GOAL_PAY,
            "account": "customer đã được ban quản lý xác minh cư dân (VERIFIED)",
            "planner": "NEEDS_INFORMATION → READY sau khi người dùng bổ sung",
            "missing": "biển số xe, loại xe, ngày đặt chỗ, khu vực đỗ xe",
            "tools": tool_titles(s1),
            "result": f"{s1.get('status')} · báo giá {quote1.get('amount')} {quote1.get('currency')}",
            "tasks": task_pairs(s1),
            "db": [
                f"workflow cha {mask(p1)} → con {mask(c1)}",
                f"vòng hỏi bổ sung = {r1}",
                "approval AWAITING = "
                + sql(
                    f"SELECT count(*) FROM payment_approvals WHERE workflow_id = '{c1}'::uuid AND status = 'AWAITING'"
                )[0],
                f"biển số canary {mask_plate(plate1)} · ngày {date1} · Khu A",
            ],
            "verdict": "PASS" if s1.get("status") == "WAITING_APPROVAL" and quote1.get("amount") else "FAIL",
            "why": "Dừng đúng ở điểm chờ người dùng quyết định, kèm báo giá đọc từ chỗ đỗ xe đã giữ.",
            "llm": llm_calls([p1, c1]),
        }
    )

    # ------------------------------------------------------------------
    # Case 2 — duyệt thanh toán
    # ------------------------------------------------------------------
    pay_before = int(sql("SELECT count(*) FROM payments")[0])
    code2, _ = call("POST", f"/workflows/demo/{c1}/payment-decision", token, {"decision": "approve"})
    s2 = poll(token, c1)
    pay_after = int(sql("SELECT count(*) FROM payments")[0])
    code2b, _ = call("POST", f"/workflows/demo/{c1}/payment-decision", token, {"decision": "approve"})
    pay_after2 = int(sql("SELECT count(*) FROM payments")[0])
    CASES.append(
        {
            "title": "2. Duyệt khoản thanh toán",
            "goal": "(tiếp tục workflow của case 1) — người dùng bấm Xác nhận thanh toán",
            "account": "chính chủ workflow, đã VERIFIED",
            "planner": "không gọi lại Planner — quyết định là hành động của người dùng",
            "missing": "—",
            "tools": tool_titles(s2),
            "result": f"HTTP {code2} → {s2.get('status')}",
            "tasks": task_pairs(s2),
            "db": [
                f"payments toàn hệ thống {pay_before} → {pay_after}",
                f"duyệt lần hai: HTTP {code2b}, payments {pay_after} → {pay_after2}",
                "approval APPROVED = "
                + sql(
                    f"SELECT count(*) FROM payment_approvals WHERE workflow_id = '{c1}'::uuid AND status = 'APPROVED'"
                )[0],
            ],
            "verdict": "PASS"
            if (
                s2.get("status") == "SUCCESS"
                and pay_after == pay_before + 1
                and code2b == 409
                and pay_after2 == pay_after
            )
            else "FAIL",
            "why": "Đúng một khoản thu được tạo; lần duyệt thứ hai bị chặn 409 chứ không thu thêm.",
            "llm": llm_calls([c1]),
        }
    )

    # ------------------------------------------------------------------
    # Case 3 — từ chối thanh toán
    # ------------------------------------------------------------------
    plate3, date3 = f"51F-{STAMP[-5:]}", day(1)
    p3, c3, s3, _ = run_parking(plate3, date3, "ZONE_B")
    booking3 = sql(f"SELECT booking_id FROM payment_approvals WHERE workflow_id = '{c3}'::uuid")
    pay_before3 = int(sql("SELECT count(*) FROM payments")[0])
    code3, _ = call("POST", f"/workflows/demo/{c3}/payment-decision", token, {"decision": "reject"})
    s3f = poll(token, c3)
    pay_after3 = int(sql("SELECT count(*) FROM payments")[0])
    db_status3 = sql(f"SELECT status FROM workflows WHERE workflow_id = '{c3}'::uuid")[0]
    booking_alive = (
        sql(f"SELECT count(*) FROM parking_bookings WHERE booking_id = '{booking3[0]}'")[0] if booking3 else "0"
    )
    CASES.append(
        {
            "title": "3. Từ chối khoản thanh toán",
            "goal": GOAL_PAY,
            "account": "customer đã VERIFIED",
            "planner": "NEEDS_INFORMATION → READY",
            "missing": "biển số xe, loại xe, ngày đặt chỗ, khu vực đỗ xe",
            "tools": tool_titles(s3f),
            "result": f"HTTP {code3} → hiển thị {s3f.get('status')} (DB: {db_status3})",
            "tasks": task_pairs(s3f),
            "db": [
                f"workflow con {mask(c3)} · DB status = {db_status3}",
                f"payments {pay_before3} → {pay_after3} (không đổi)",
                f"chỗ đỗ xe đã giữ vẫn còn = {booking_alive}",
                f"biển số canary {mask_plate(plate3)} · ngày {date3} · Khu B",
            ],
            "verdict": "PASS"
            if (pay_after3 == pay_before3 and db_status3 == "CANCELLED" and booking_alive == "1")
            else "FAIL",
            "why": "Từ chối huỷ workflow và không thu tiền, nhưng KHÔNG xoá chỗ đã giữ — huỷ chỗ là quyết định khác.",
            "llm": llm_calls([p3, c3]),
        }
    )

    # ------------------------------------------------------------------
    # Case 4 — đặt lịch tham quan dự án
    # ------------------------------------------------------------------
    tour_date = day(30)
    _, started4 = call(
        "POST", "/workflows/demo/start", token, {"goal": "Tôi muốn đặt lịch tham quan căn hộ tại Vinhomes Ocean Park."}
    )
    p4 = started4["workflow_id"]
    s4 = poll(token, p4)
    c4, r4 = p4, 0
    answers4 = {"project_name": "Vinhomes Ocean Park", "viewing_date": tour_date, "viewing_time": "10:30"}
    while s4.get("status") == "NEEDS_INFORMATION" and r4 < 3:
        fields = {k: answers4[k] for k in s4.get("missing_fields", []) if k in answers4}
        if not fields:
            break
        code, child = call("POST", f"/workflows/demo/{c4}/continue", token, {"fields": fields})
        if code not in (200, 202):
            s4 = {"status": f"HTTP_{code}", "_detail": child.get("detail")}
            break
        c4 = child["workflow_id"]
        r4 += 1
        s4 = poll(token, c4)
    # Lịch tham quan giờ DỪNG ở `WAITING_APPROVAL` chờ ĐƠN VỊ duyệt — đó là
    # kiến trúc, không phải lỗi. Case này vì vậy phải đi hết đường: xác nhận nó
    # dừng đúng chỗ với `approval_actor = PROVIDER`, rồi ĐÓNG VAI đơn vị bấm
    # duyệt, rồi mới đòi SUCCESS.
    #
    # Bản trước chấm `PASS if status == "SUCCESS"` ngay sau khi khách gửi yêu
    # cầu. Kỳ vọng đó có từ thời chưa có cổng duyệt; giữ nguyên thì eval báo đỏ
    # cho một hành vi đúng, và tệ hơn — nó sẽ báo xanh nếu ai đó lỡ bỏ mất cổng
    # duyệt.
    gated4 = s4.get("status") == "WAITING_APPROVAL" and s4.get("approval_actor") == "PROVIDER"
    approved4 = False
    if gated4:
        reviewer = f"eval_provider_{int(time.time())}"
        call("POST", "/auth/register", None, {"username": reviewer, "email": f"{reviewer}@example.test", "password": PASSWORD})
        sql(f"UPDATE users SET role = 'provider' WHERE username = '{reviewer}'")
        _, tok = call("POST", "/auth/login", None, {"username": reviewer, "password": PASSWORD})
        rtoken = tok.get("access_token")
        code_d, _ = call("POST", f"/viewing-approvals/{c4}/decide", rtoken, {"decision": "approve"})
        approved4 = code_d == 200
        if approved4:
            s4 = poll(token, c4)

    # Provider tour giữ lịch trong BỘ NHỚ của tiến trình mock, không ghi
    # `tour_bookings`. Bằng chứng bền vững nằm ở phía P-118: `workflow_tasks`
    # lưu kết quả provider trả về, và nó chứa đủ ngày/giờ/dự án để đối chiếu.
    viewings = sql(
        "SELECT (result_data->>'viewing_date') || ' ' || (result_data->>'viewing_time') "
        "|| ' · ' || (result_data->>'project_name') || ' (' || (result_data->>'project_id') || ')' "
        f"FROM workflow_tasks WHERE workflow_id = '{c4}'::uuid AND status = 'SUCCESS'"
    ) or ["(không có bản ghi task)"]
    CASES.append(
        {
            "title": "4. Đặt lịch tham quan dự án với ngày/giờ hợp lệ",
            "goal": "Tôi muốn đặt lịch tham quan căn hộ tại Vinhomes Ocean Park.",
            "account": "customer đã VERIFIED (dịch vụ này vốn mở cho cả khách chưa liên kết)",
            "planner": "NEEDS_INFORMATION → READY" if r4 else "READY ngay",
            "missing": "dự án, ngày xem nhà, giờ xem nhà" if r4 else "—",
            "tools": tool_titles(s4),
            "result": str(s4.get("status")),
            "tasks": task_pairs(s4),
            "db": [
                f"workflow {mask(c4)} · vòng hỏi = {r4}",
                f"ngày hẹn {tour_date} lúc 10:30 (giữ nguyên phút, không quy về buổi)",
                f"dừng ở cổng duyệt đơn vị = {gated4} · đơn vị đã duyệt = {approved4}",
                f"kết quả provider đã ghi vào workflow_tasks: {' | '.join(viewings)}",
                "số điện thoại đầu mối do provider giữ, không đưa vào báo cáo",
            ],
            "verdict": "PASS" if (gated4 and approved4 and s4.get("status") == "SUCCESS") else "FAIL",
            "why": (
                "Dừng đúng ở cổng duyệt của ĐƠN VỊ (approval_actor=PROVIDER, khách không có nút "
                "quyết định), chỉ thành công sau khi đơn vị duyệt. Giờ hẹn đi qua tới provider ở "
                "dạng HH:MM, không bị quy về MORNING/AFTERNOON."
            ),
            "llm": llm_calls([p4, c4]),
        }
    )

    # ------------------------------------------------------------------
    # Case 5 — lỗi có chủ ý: ngày đặt chỗ trong quá khứ
    # ------------------------------------------------------------------
    past = sql("SELECT (CURRENT_DATE - 30)::text")[0]
    plate5 = f"51G-{STAMP[-5:]}"
    p5, c5, s5, r5 = run_parking(plate5, past, "ZONE_A")
    detail5 = s5.get("_detail") or s5.get("summary") or s5.get("question") or ""
    bookings5 = sql(f"SELECT count(*) FROM parking_bookings WHERE booking_date = DATE '{past}'")[0]
    CASES.append(
        {
            "title": "5. Lỗi có chủ ý — đặt chỗ cho một ngày đã qua",
            "goal": GOAL_PAY + f" (người dùng chọn ngày {past}, đã qua)",
            "account": "customer đã VERIFIED",
            "planner": "NEEDS_INFORMATION cho vòng hỏi đầu; input sai bị chặn trước khi gọi provider",
            "missing": "biển số xe, loại xe, ngày đặt chỗ, khu vực đỗ xe",
            "tools": tool_titles(s5),
            "result": f"{s5.get('status')} — {str(detail5)[:150] or '(không có mô tả)'}",
            "tasks": task_pairs(s5),
            "db": [
                f"vòng hỏi = {r5}",
                f"chỗ đỗ xe được tạo cho ngày quá khứ = {bookings5} (phải là 0)",
                f"biển số canary {mask_plate(plate5)}",
            ],
            "verdict": "PASS" if (s5.get("status") != "SUCCESS" and bookings5 == "0") else "FAIL",
            "why": "Không tạo chỗ đỗ xe cho ngày đã qua, và nói cho người dùng biết cần sửa gì.",
            "llm": llm_calls([p5, c5]),
        }
    )

    # ------------------------------------------------------------------
    print(
        json.dumps(
            {
                "cases": CASES,
                "resident_masked": mask(resident, 9),
                "apartment_masked": apartment,
                "user_masked": mask(uid),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    failed = [c["title"] for c in CASES if c["verdict"] != "PASS"]
    print(f"\n=== {len(CASES) - len(failed)}/{len(CASES)} PASS ===", file=sys.stderr)
    for t in failed:
        print(f"  FAIL: {t}", file=sys.stderr)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
