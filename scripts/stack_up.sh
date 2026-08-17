#!/bin/sh
# P-118 — đưa toàn bộ hệ thống lên bằng MỘT lệnh, và dừng lại khi có gì đó sai.
#
#   sh scripts/stack_up.sh          # build + up + chờ /ready + smoke
#   sh scripts/stack_up.sh --no-build
#
# Script này tồn tại vì "docker compose up -d" một mình không đủ: nó báo thành
# công trong những tình huống hệ thống KHÔNG chạy được, và mỗi lần như thế lại
# tốn một buổi để tìm ra. Cụ thể:
#
#   - một uvicorn local đang giữ cổng provider, nên request đi tới đúng cổng
#     nhưng vào nhầm tiến trình, và tiến trình đó nói chuyện với database khác
#   - container tên `p118_postgres` đang thuộc một compose project khác (một
#     worktree khác), nên `docker exec` chạy đúng lệnh trên sai dữ liệu
#   - backend healthy nhưng cấu hình LLM sai, nên mọi workflow chết ở bước đầu
#
# KHÔNG BAO GIỜ tự xoá volume hay container dữ liệu. Gặp xung đột thì dừng và
# nói người dùng cần làm gì; đoán hộ ở đây là đoán trên dữ liệu của người khác.

set -e

cd "$(dirname "$0")/.." || exit 1
# Compose HẠ CHỮ THƯỜNG tên project. So sánh bằng basename nguyên gốc sẽ báo
# xung đột ngay cả với chính project của mình khi thư mục có chữ hoa.
PROJECT="$(basename "$PWD" | tr '[:upper:]' '[:lower:]')"
BUILD=1
[ "$1" = "--no-build" ] && BUILD=0

APP_PORT_VALUE="${APP_PORT:-8080}"
# Cổng host mà stack sẽ chiếm. Provider chạy trên cổng canonical, và một
# uvicorn local trên cùng cổng là nguyên nhân của kiểu lỗi khó chịu nhất:
# mọi thứ trông bình thường, chỉ dữ liệu là sai.
HOST_PORTS="$APP_PORT_VALUE ${POSTGRES_PORT:-5433} 8001 8002 8003 8005 8006 8007 8008"

say()  { printf '%s\n' "$*"; }
fail() { printf '\nDỪNG: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Docker daemon
# ---------------------------------------------------------------------------
docker info >/dev/null 2>&1 || fail "Docker daemon chưa chạy. Mở Docker Desktop rồi chạy lại."
say "[1/8] Docker daemon: OK"

# ---------------------------------------------------------------------------
# 2. Cổng host có bị tiến trình NGOÀI Docker chiếm không
# ---------------------------------------------------------------------------
busy=""
for port in $HOST_PORTS; do
  for pid in $(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null); do
    # Cổng do chính Docker publish thì bỏ qua — đó là stack, không phải kẻ lạ.
    comm=$(ps -o comm= -p "$pid" 2>/dev/null | tr -d ' ')
    case "$comm" in
      *docker*|*Docker*|*vpnkit*|*com.docke*) continue ;;
    esac
    busy="$busy\n  cổng $port ← pid $pid ($comm)"
  done
done
if [ -n "$busy" ]; then
  printf 'Có tiến trình NGOÀI Docker đang giữ cổng của stack:%b\n' "$busy" >&2
  fail "Dừng các tiến trình đó rồi chạy lại. Để nguyên thì request tới 127.0.0.1 sẽ vào nhầm tiến trình, và tiến trình đó có thể đang dùng database khác."
fi
say "[2/8] Cổng host: không có tiến trình lạ"

# ---------------------------------------------------------------------------
# 3. Container tên cố định có thuộc đúng project này không
# ---------------------------------------------------------------------------
# docker-compose.yml đặt container_name cố định (p118_postgres, …) để test và
# script dùng `docker exec` theo tên. Cái giá là hai worktree không chạy song
# song được — và nếu không kiểm, worktree thứ hai sẽ lặng lẽ thao tác trên
# container của worktree thứ nhất.
for name in p118_postgres p118_backend; do
  owner=$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$name" 2>/dev/null || true)
  if [ -n "$owner" ] && [ "$owner" != "$PROJECT" ]; then
    fail "Container '$name' đang thuộc compose project '$owner', không phải '$PROJECT'.
Hai worktree không dùng chung được vì container_name là cố định.
Hãy chạy 'docker compose down' ở worktree kia trước — script này KHÔNG tự xoá container hay volume của project khác."
  fi
done
say "[3/8] Container name: không xung đột project khác"

# ---------------------------------------------------------------------------
# 4. Build / up (giữ nguyên volume dữ liệu)
# ---------------------------------------------------------------------------
if [ "$BUILD" = "1" ]; then
  say "[4/8] Build image từ source hiện tại…"
  docker compose build >/dev/null || fail "build thất bại."
else
  say "[4/8] Bỏ qua build (--no-build)"
fi

# KHÔNG có -v, KHÔNG có --remove-orphans trên volume: dữ liệu là thứ script
# này không được phép quyết định thay người dùng.
docker compose up -d --force-recreate >/dev/null || fail "docker compose up thất bại."
say "[5/8] Compose up: xong (volume dữ liệu giữ nguyên)"

# ---------------------------------------------------------------------------
# 5. Migration — service riêng, phải chạy XONG
# ---------------------------------------------------------------------------
code=$(docker inspect -f '{{ .State.ExitCode }}' "$(docker compose ps -aq db-migrate 2>/dev/null)" 2>/dev/null || echo "?")
[ "$code" = "0" ] || fail "db-migrate kết thúc với mã $code. Xem: docker compose logs db-migrate"
say "[6/8] Migration: đã chạy xong"

# ---------------------------------------------------------------------------
# 6. Chờ /ready — không phải /health
# ---------------------------------------------------------------------------
i=0
until [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$APP_PORT_VALUE/ready")" = "200" ]; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    printf '\n/ready vẫn đỏ sau 60 lần thử. Lý do backend tự báo:\n\n' >&2
    curl -s --max-time 5 "http://127.0.0.1:$APP_PORT_VALUE/ready" >&2 || true
    fail "hệ thống chưa sẵn sàng nhận việc."
  fi
  sleep 2
done
say "[7/8] /ready: xanh"

# ---------------------------------------------------------------------------
# 7. Provider và backend có cùng một kho dữ liệu không
# ---------------------------------------------------------------------------
# Kiểm bằng HÀNH VI (canary ghi qua provider, đọc lại ở database backend dùng),
# không bằng cách so chuỗi DATABASE_URL: hai DSN khác chữ vẫn có thể là một
# database, và hai DSN giống chữ vẫn có thể là hai.
if ! python3 scripts/check_data_plane.py; then
  fail "provider và backend không dùng chung kho dữ liệu."
fi
say "[8/8] Mặt phẳng dữ liệu: provider và backend cùng một kho"

say ""
say "Hệ thống đã sẵn sàng:  http://127.0.0.1:$APP_PORT_VALUE"
say "Kiểm cấu hình chi tiết: curl -s http://127.0.0.1:$APP_PORT_VALUE/ready"
say "Kiểm KHOÁ LLM thật (một lần, có tính phí):"
say "    docker compose exec backend python scripts/smoke_llm.py"
