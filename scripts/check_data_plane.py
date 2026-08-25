"""Provider và backend có đang ghi/đọc CÙNG một kho dữ liệu không.

Vì sao không so chuỗi `DATABASE_URL`: hai DSN khác nhau vẫn có thể trỏ cùng một
database (host `postgres` với `127.0.0.1`, cổng publish với cổng nội bộ), và hai
DSN giống nhau vẫn có thể trỏ khác nhau nếu container nằm trong hai network. So
chuỗi trả lời một câu hỏi khác với câu đang hỏi.

Ở đây kiểm bằng HÀNH VI: bảo provider ghi một canary qua HTTP, rồi đọc lại canary
đó ở đúng database mà backend đang dùng. Provider ghi sang kho khác thì canary
không xuất hiện — bất kể DSN trông thế nào.

Đây là kiểm MỘT LẦN cho lúc dựng stack, không phải healthcheck lặp: nó ghi dữ
liệu, và một healthcheck ghi dữ liệu mỗi 15 giây là một cách rò rỉ rác.

Không in DSN, không in credential.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

def _app_port() -> str:
    """Cổng host của backend, phân giải ĐÚNG như docker compose phân giải.

    Compose đọc `${APP_PORT:-8080}` từ biến môi trường trước, rồi tới `.env` ở
    gốc repo. Script này trước đây chỉ hardcode 8080, nên với `APP_PORT=8000`
    trong `.env` nó gọi vào một cổng không ai lắng nghe và báo
    "provider và backend không dùng chung kho dữ liệu" — một kết luận về dữ
    liệu, cho một sự cố thuần tuý về cổng. Thông báo đó gửi người đọc đi sai
    hướng hoàn toàn.
    """
    from_env = os.environ.get("APP_PORT")
    if from_env:
        return from_env
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            # Chỉ lấy đúng một khoá — `.env` còn chứa API key thật.
            if sep and key.strip() == "APP_PORT":
                cleaned = value.strip().strip("\"'")
                if cleaned:
                    return cleaned
    return "8080"


BACKEND = os.environ.get("P118_BACKEND", f"http://127.0.0.1:{_app_port()}")
POSTGRES_CONTAINER = os.environ.get("P118_PG_CONTAINER", "p118_postgres")
PG_USER = os.environ.get("P118_PG_USER", "p118")

# Provider có ghi PostgreSQL. Tour/consultation/property giữ dữ liệu trong bộ
# nhớ tiến trình mock nên không kiểm được bằng cách này — và nói rõ ra thì tốt
# hơn là im lặng bỏ qua.
DB_BACKED_PROVIDERS = {
    "resident": os.environ.get("P118_RESIDENT_URL", "http://127.0.0.1:8001"),
}


def fail(message: str) -> None:
    print(f"\nDỪNG: {message}", file=sys.stderr)
    raise SystemExit(1)


def backend_database_name() -> str:
    """Tên database backend đang dùng, hỏi chính backend qua `/ready`."""
    try:
        with urllib.request.urlopen(f"{BACKEND}/ready", timeout=10) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode())
    except Exception as exc:  # noqa: BLE001
        fail(f"không gọi được /ready ({type(exc).__name__}). Stack đã lên chưa?")

    for check in body.get("checks", []):
        if check["name"] == "database" and "database=" in check.get("detail", ""):
            return check["detail"].split("database=")[1].strip()
    fail("/ready không nói được nó đang dùng database nào.")
    return ""  # pragma: no cover - fail() đã thoát


def psql(database: str, query: str) -> str:
    out = subprocess.run(
        ["docker", "exec", POSTGRES_CONTAINER, "psql", "-U", PG_USER, "-d", database,
         "-q", "-v", "ON_ERROR_STOP=1", "-tAc", query],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        fail(f"truy vấn database '{database}' thất bại: {out.stderr.strip().splitlines()[-1][:150]}")
    return out.stdout.strip()


def main() -> int:
    database = backend_database_name()
    print(f"[1/3] Backend đang dùng database: {database}")

    canary = f"CANARY-{int(time.time())}"
    apartment = f"DP-{canary[-6:]}"

    payload = json.dumps({
        "full_name": "Canary Data Plane",
        "apartment_code": apartment,
        "residential_area": "Vinhomes Ocean Park",
    }).encode()
    request = urllib.request.Request(
        f"{DB_BACKED_PROVIDERS['resident']}/api/residents", data=payload, method="POST"
    )
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            created = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        fail(f"provider resident từ chối canary (HTTP {exc.code}).")
    except Exception as exc:  # noqa: BLE001
        fail(f"không gọi được provider resident ({type(exc).__name__}).")

    resident_id = (created.get("data") or {}).get("resident_id")
    if not resident_id:
        fail("provider resident không trả về mã cư dân.")
    print(f"[2/3] Provider đã ghi canary: {resident_id}")

    seen = psql(database, f"SELECT count(*) FROM residents WHERE apartment_code = '{apartment}'")
    if seen != "1":
        fail(
            f"Provider resident ghi vào một kho KHÁC với backend.\n"
            f"Canary '{apartment}' không có trong database '{database}' mà backend đang đọc.\n"
            "Kiểm DATABASE_URL của service mock-resident trong docker-compose.yml — "
            "nhiều khả năng nó trỏ sang database khác, hoặc container nằm ngoài network của stack."
        )
    print(f"[3/3] Backend đọc được canary trong '{database}' — provider và backend cùng một kho")

    # Dọn canary: nó là rác kiểm thử, không phải dữ liệu nghiệp vụ.
    psql(database, f"DELETE FROM residents WHERE apartment_code = '{apartment}'")

    skipped = "tour, consultation, property (giữ dữ liệu trong bộ nhớ tiến trình mock)"
    print(f"\nKhông kiểm được bằng cách này: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
