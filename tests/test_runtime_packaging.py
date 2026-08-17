"""Regression checks cho Docker image và deterministic smoke CLI."""

from pathlib import Path

from scripts import smoke_runtime

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_non_root_accessible_virtualenv() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python -m venv /opt/venv" in dockerfile
    assert "ENV PATH=/opt/venv/bin:$PATH" in dockerfile
    assert "COPY --from=builder /opt/venv /opt/venv" in dockerfile
    assert "/root/.local" not in dockerfile
    assert "USER appuser" in dockerfile


def _compose_service_block(name: str) -> str:
    """Phần thân của một service trong `docker-compose.yml`.

    Cắt theo thụt đầu dòng: service kế tiếp là dòng bắt đầu bằng đúng hai dấu
    cách rồi tới ký tự khác khoảng trắng. Tách bằng `split("\\n  ")` sẽ dừng ở
    khoá con ĐẦU TIÊN của chính service đó, và khối thu được rỗng gần như hoàn
    toàn — một test như vậy đỏ vì lý do sai, hoặc tệ hơn, xanh vì không kiểm gì.
    """
    lines = (ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {name}:")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("  ") and not line.startswith("   ") and line.strip():
            break
        body.append(line)
    return "\n".join(body)


def test_compose_points_every_required_provider_at_the_container_network() -> None:
    """Mỗi provider bắt buộc phải có URL trỏ vào mạng compose, không phải localhost.

    Đã xảy ra: `OWNERSHIP_SERVICE_URL` bị bỏ sót trong khối `environment` của
    backend, nên trong container nó giữ mặc định `http://localhost:8004` — tức
    trỏ vào chính backend. Toàn bộ luồng xác minh căn hộ trả 503, và đó là bước
    ĐẦU TIÊN người dùng mới phải làm.

    `/ready` không bắt được: nó kiểm URL có đúng dạng, không kiểm gọi được.
    Kiểm ở đây, trên file cấu hình, là chỗ rẻ nhất để chặn lần bỏ sót kế tiếp —
    thêm một provider mà quên map là test đỏ ngay, không cần dựng stack.
    """
    from src.api.readiness import REQUIRED_SERVICE_URLS

    backend = _compose_service_block("backend")

    missing = [
        field.upper()
        for field in REQUIRED_SERVICE_URLS
        if f"{field.upper()}:" not in backend
    ]
    assert not missing, f"backend thiếu URL provider trong compose: {', '.join(missing)}"

    # `localhost` trong container là chính backend. Một URL như vậy lọt qua mọi
    # kiểm tra hình thức rồi hỏng lúc gọi thật.
    for line in backend.splitlines():
        if "_SERVICE_URL:" in line:
            assert "localhost" not in line and "127.0.0.1" not in line, line.strip()


def test_every_service_backend_depends_on_starts_by_default() -> None:
    """Service mà luồng người dùng cần KHÔNG được nấp sau `profiles:`.

    `mock-ownership` từng nằm sau `profiles: [ownership]`, nên
    `docker compose up -d` bỏ qua nó. Người dựng stack đúng theo hướng dẫn vẫn
    nhận 503 ở màn hình đầu tiên, và không có gì chỉ ra là thiếu một service.
    """
    # Bỏ dòng chú thích trước khi so khớp. Bản đầu của test này đỏ vì chính
    # comment giải thích "KHÔNG đặt sau `profiles:` nữa" — nghĩa là nó đang
    # khớp văn bản, không khớp cấu hình.
    block = "\n".join(
        line for line in _compose_service_block("mock-ownership").splitlines()
        if not line.strip().startswith("#")
    )

    assert "profiles:" not in block, (
        "mock-ownership phục vụ luồng xác minh căn hộ — nó phải lên cùng stack, "
        "không phải một tuỳ chọn"
    )


def test_smoke_cli_does_not_offer_misleading_goal_or_reusable_seed() -> None:
    script = (ROOT / "scripts/smoke_runtime.py").read_text(encoding="utf-8")

    assert 'add_argument("--goal"' not in script
    assert 'add_argument("--seed"' not in script
    assert "Deterministic runtime smoke" in script


def test_smoke_plan_generates_new_business_identifiers(monkeypatch) -> None:
    ids = iter(
        [
            type("FakeUUID", (), {"hex": "00000001aaaaaaaa11111111bbbbbbbb"})(),
            type("FakeUUID", (), {"hex": "00000002cccccccc22222222dddddddd"})(),
        ]
    )
    monkeypatch.setattr(smoke_runtime.uuid, "uuid4", lambda: next(ids))

    first = smoke_runtime.build_plan()
    second = smoke_runtime.build_plan()

    assert first.tasks[0].input["apartment_code"] != second.tasks[0].input["apartment_code"]
    assert first.tasks[1].input["plate_number"] != second.tasks[1].input["plate_number"]
    assert first.tasks[2].input["booking_date"] != second.tasks[2].input["booking_date"]
