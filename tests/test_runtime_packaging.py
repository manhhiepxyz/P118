"""Regression checks cho Docker image và deterministic smoke CLI."""

from pathlib import Path

from scripts import smoke_runtime

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_non_root_accessible_virtualenv() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "python -m venv /opt/venv" in dockerfile
    assert "ENV PATH=/opt/venv/bin:$PATH" in dockerfile
    assert "COPY --from=builder /opt/venv /opt/venv" in dockerfile
    assert "/root/.local" not in dockerfile
    assert "USER appuser" in dockerfile


def test_smoke_cli_does_not_offer_misleading_goal_or_reusable_seed() -> None:
    script = (ROOT / "scripts/smoke_runtime.py").read_text()

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
