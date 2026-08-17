"""Chặn secret lọt vào file được commit.

Test này ra đời sau một sự cố thật: `.env.example` chứa một `AI_LOG_API_KEY`
giá trị thật, nằm trong repo từ commit đầu tiên. Xoá file không cứu được — key
vẫn ở trong git history và phải bị thu hồi.

Nguyên tắc thiết kế:

  - Chỉ quét file GIT ĐANG TRACK. Không đọc git history: làm vậy sẽ kéo giá
    trị đã bị thu hồi ra khỏi quá khứ và in lại chúng vào log CI.
  - Không bao giờ in giá trị khớp, kể cả một phần. Báo cáo chỉ có
    file + dòng + LOẠI secret.
  - Canary trong test (chuỗi giả dùng để kiểm "secret không rò ra message")
    phải được đánh dấu tường minh bằng `# secret-fixture`. Bắt buộc dán nhãn
    thay vì loại trừ cả thư mục `tests/`: một key thật vô tình dán vào test
    vẫn phải bị bắt.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]

# Nhãn cho canary cố ý. Greppable, nên review thấy ngay mọi ngoại lệ.
ALLOW_MARKER = "# secret-fixture"

# Giá trị mang một trong các dấu hiệu này là placeholder, không phải secret.
PLACEHOLDER_MARKERS = (
    "your",
    "here",
    "...",
    "xxx",
    "change",
    "example",
    "<",
    "dummy",
    "placeholder",
    "fake",
    "not-real",
    "redacted",
    "${",
    "test-key",
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI/DeepSeek-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("OpenRouter key", re.compile(r"\bsk-or-v1-[A-Za-z0-9_\-]{16,}\b")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "High-entropy credential assignment",
        re.compile(
            r"[A-Za-z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)\s*[:=]\s*['\"]?([A-Za-z0-9_\-/+]{32,})",
            re.IGNORECASE,
        ),
    ),
)

# File nhị phân / khoá phụ thuộc: quét không có ý nghĩa và dễ báo nhầm.
SKIPPED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".lock", ".svg"}
MAX_FILE_BYTES = 2_000_000


def _tracked_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        pytest.skip("không chạy được git ls-files")
    return [REPO_ROOT / name for name in result.stdout.split()]


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _scan(path: Path) -> list[tuple[int, str]]:
    """Trả [(số dòng, loại secret)] — KHÔNG bao giờ trả giá trị."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for kind, pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            value = match.group(match.lastindex) if match.lastindex else match.group(0)
            if _looks_like_placeholder(value):
                continue
            hits.append((lineno, kind))
            break
    return hits


def test_no_secret_material_in_tracked_files() -> None:
    findings: list[str] = []
    for path in _tracked_files():
        if not path.is_file() or path.suffix in SKIPPED_SUFFIXES:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        relative = path.relative_to(REPO_ROOT)
        findings.extend(f"{relative}:{lineno} [{kind}]" for lineno, kind in _scan(path))

    assert not findings, "Secret trong file được track:\n  " + "\n  ".join(findings)


def test_env_file_is_never_tracked() -> None:
    """`.env` chứa key thật; nó phải luôn nằm ngoài repo."""
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0, ".env đang bị git track"


def test_the_scanner_actually_detects_a_planted_secret(tmp_path: Path) -> None:
    """Mutation tự kiểm: scanner phải bắt được secret thật.

    Không có test này, một regex viết sai sẽ khiến suite xanh vĩnh viễn trong
    khi không kiểm gì cả.
    """
    planted = tmp_path / "leak.env"
    planted.write_text("SOME_API_KEY=sk-" + "a1b2c3d4" * 5 + "\n", encoding="utf-8")

    hits = _scan(planted)

    assert hits, "scanner bỏ sót một secret rõ ràng"
    assert all(isinstance(kind, str) for _, kind in hits)


def test_placeholders_do_not_trigger_a_false_positive(tmp_path: Path) -> None:
    sample = tmp_path / "example.env"
    sample.write_text(
        "OPENAI_API_KEY=sk-your-key-here\n"
        "OPENROUTER_API_KEY=\n"
        "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}\n"
        "LANGCHAIN_API_KEY=your-langsmith-key-here\n",
        encoding="utf-8",
    )

    assert _scan(sample) == []


def test_an_allow_marked_line_is_skipped(tmp_path: Path) -> None:
    """Canary phải được dán nhãn; nhãn là thứ review nhìn thấy được."""
    sample = tmp_path / "fixture.py"
    sample.write_text(
        f'secret = "sk-{"z9y8x7w6" * 5}"  {ALLOW_MARKER}\n',
        encoding="utf-8",
    )

    assert _scan(sample) == []

    unmarked = tmp_path / "unmarked.py"
    unmarked.write_text(f'secret = "sk-{"z9y8x7w6" * 5}"\n', encoding="utf-8")
    assert _scan(unmarked), "thiếu nhãn thì phải bị bắt"
