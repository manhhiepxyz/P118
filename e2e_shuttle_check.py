"""E2E smoke: viewing -> shuttle chain via the real API (prospect account)."""

import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


def call(path, method="GET", body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


u = "e2e_shuttle_1786868125"
s, login = call("/auth/login", "POST", {"username": u, "password": "demo1234"})
token = login["access_token"]
GOAL = (
    "Tôi muốn đặt lịch tham quan dự án Vinhomes Ocean Park ngày 2026-08-25 lúc 15:30; "
    "đồng thời đặt xe đưa đón tham quan ngày 2026-08-25 cho 4 người."
)

s, start = call("/workflows/demo/start", "POST", {"goal": GOAL}, token)
wf = start.get("workflow_id") or start.get("id")
print(f"START {s} {start.get('status')} stage={start.get('stage')} WF={wf}", flush=True)

for i in range(40):
    s, st = call("/workflows/demo/" + wf, token=token)
    status = st.get("status")
    print(f"[{i}] {status} stage={st.get('stage')} missing={st.get('missing_fields')} WF={wf}", flush=True)
    if status in ("SUCCESS", "FAILED", "CANCELLED", "VALIDATION_ERROR") or st.get("stage") in ("COMPLETED", "FAILED"):
        break
    mf = st.get("missing_fields") or []
    if mf:
        ans = {}
        for f in mf:
            if f == "project_id" or f == "project_name":
                ans[f] = "Vinhomes Ocean Park"
            elif "date" in f:
                ans[f] = "2026-08-25"
            elif "time" in f:
                ans[f] = "15:30"
            elif f == "passenger_count":
                ans[f] = 4
            elif f == "consent":
                ans[f] = True
            else:
                ans[f] = "4"
        print(f"  ANSWER {json.dumps(ans, ensure_ascii=False)}", flush=True)
        s2, cont = call("/workflows/demo/" + wf + "/continue", "POST", {"fields": ans}, token=token)
        nw = cont.get("workflow_id")
        print(f"  CONTINUE {s2} -> new WF {nw} status={cont.get('status')}", flush=True)
        if nw and nw != wf:
            wf = nw
        continue
    time.sleep(2)

print()
if status == "SUCCESS":
    print("=== SUCCESS ===")
    for t in st.get("tasks", []):
        print(f"  {t.get('task_id')} [{t.get('tool')}] {t.get('status')}")
        print(f"      title: {t.get('title')}")
        print(f"      msg: {t.get('message')}")
else:
    print(json.dumps(st, ensure_ascii=False, indent=2)[:4000])
