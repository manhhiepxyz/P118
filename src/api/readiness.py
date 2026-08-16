"""Readiness — hệ thống có SẴN SÀNG NHẬN VIỆC không.

Tách khỏi `/health` là có chủ ý, và sự tách này đến từ một sự cố thật: Docker
Compose báo mọi service healthy, trong khi backend chạy với một `LLM_PROVIDER`
không có key tương ứng. `/health` nói đúng sự thật của nó — tiến trình còn sống
— nhưng đó không phải câu hỏi người vận hành đang hỏi. Câu họ hỏi là "gửi việc
vào có chạy không", và câu đó chỉ `/ready` trả lời được.

Bốn thứ được kiểm, đều KHÔNG gọi mạng ra ngoài:

  1. cấu hình LLM  — provider, key, model có khớp nhau không
  2. PostgreSQL    — kết nối được không
  3. migration     — các bảng bắt buộc đã có chưa
  4. connector     — tám provider có URL đầy đủ và đúng dạng không

Cố ý KHÔNG gọi thử LLM: healthcheck lặp mỗi 30 giây sẽ đốt tiền và tự tạo rate
limit cho chính mình. Kiểm khoá thật là việc của lệnh smoke chạy một lần khi
deploy (`scripts/smoke_llm.py`).

Response KHÔNG bao giờ chứa key, DSN, hay URL có credential: `/ready` thường
được mở ra ngoài cho load balancer.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from src.config import Settings, get_settings
from src.orchestration.runtime_provider import acquire_repository
from src.services.llm import LLMConfigurationError, check_llm_configuration

# Bảng mà thiếu là hệ thống không chạy được việc gì có ý nghĩa. Không liệt kê
# toàn bộ schema: danh sách càng dài càng dễ lệch với migration thật, và mục
# tiêu ở đây là bắt trường hợp "migration chưa chạy", không phải diff schema.
REQUIRED_TABLES: tuple[str, ...] = (
    "users",
    "user_resident_links",
    "residents",
    "workflows",
    "workflow_tasks",
    "workflow_clarifications",
    "payment_approvals",
    "payments",
    "parking_bookings",
    "vehicles",
    "sessions",
)

# Tám provider canonical. `book_shuttle` (8009) đã là tool thứ 10 của contract
# public và được đăng ký với Executor, nên nó nằm trong danh sách bắt buộc —
# thiếu shuttle là hệ thống không hoàn thành được chuỗi tham quan → xe.
REQUIRED_SERVICE_URLS: tuple[str, ...] = (
    "resident_service_url",
    "transport_service_url",
    "payment_service_url",
    "tour_service_url",
    "resident_services_service_url",
    "consultation_service_url",
    "property_service_url",
    "ownership_service_url",
    "shuttle_service_url",
)


class ReadinessCheck:
    """Một hạng mục kiểm, kèm lý do đọc được khi hỏng."""

    __slots__ = ("name", "ok", "detail")

    def __init__(self, name: str, ok: bool, detail: str = "") -> None:
        self.name = name
        self.ok = ok
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def _check_llm(settings: Settings) -> ReadinessCheck:
    try:
        check_llm_configuration(settings)
    except LLMConfigurationError as exc:
        # `LLMConfigurationError` chỉ mang tên biến môi trường, không mang key —
        # đó là hợp đồng của chính exception đó, có test giữ.
        return ReadinessCheck("llm_config", False, str(exc))
    return ReadinessCheck("llm_config", True, f"provider={settings.llm_provider}")


def _check_connectors(settings: Settings) -> ReadinessCheck:
    """URL provider phải đủ và đúng dạng — không kiểm bằng cách gọi thử.

    Gọi thử ở đây sẽ biến readiness thành phép đo sức khoẻ của bên thứ ba: một
    provider chậm sẽ làm backend bị đánh dấu unhealthy rồi restart, trong khi
    nó không hỏng gì cả.
    """
    missing: list[str] = []
    malformed: list[str] = []
    for field in REQUIRED_SERVICE_URLS:
        raw = (getattr(settings, field, "") or "").strip()
        variable = field.upper()
        if not raw:
            missing.append(variable)
            continue
        parts = urlsplit(raw)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            malformed.append(variable)

    if missing or malformed:
        problems = []
        if missing:
            problems.append("thiếu: " + ", ".join(sorted(missing)))
        if malformed:
            problems.append("sai định dạng: " + ", ".join(sorted(malformed)))
        return ReadinessCheck("connectors", False, "; ".join(problems))
    return ReadinessCheck("connectors", True, f"{len(REQUIRED_SERVICE_URLS)} provider đã cấu hình")


async def _check_database_and_migrations() -> tuple[ReadinessCheck, ReadinessCheck]:
    """Kết nối và migration là HAI câu hỏi khác nhau, nên là hai dòng khác nhau.

    Gộp chung thì "DB chưa lên" và "DB lên nhưng chưa migrate" ra cùng một
    thông báo, và hai tình huống đó cần hai hành động khác hẳn nhau.
    """
    try:
        repository = await acquire_repository()
        pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
        async with pool.acquire() as conn:
            # TÊN database, không phải DSN. Người vận hành cần biết backend đang
            # đọc kho nào để đối chiếu với provider; DSN thì mang cả mật khẩu.
            database_name = await conn.fetchval("SELECT current_database()")
            present = {
                row["table_name"]
                for row in await conn.fetch(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
            }
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi, DSN không được lộ
        failure = ReadinessCheck("database", False, f"không kết nối được ({type(exc).__name__})")
        return failure, ReadinessCheck("migrations", False, "chưa kiểm được vì database không sẵn sàng")

    connected = ReadinessCheck("database", True, f"kết nối được · database={database_name}")
    absent = sorted(set(REQUIRED_TABLES) - present)
    if absent:
        return connected, ReadinessCheck("migrations", False, "thiếu bảng: " + ", ".join(absent))
    return connected, ReadinessCheck("migrations", True, f"{len(REQUIRED_TABLES)} bảng bắt buộc đã có")


async def evaluate_readiness(settings: Settings | None = None) -> tuple[bool, list[dict[str, Any]]]:
    """Chạy toàn bộ hạng mục. Trả (sẵn sàng, danh sách kết quả).

    Chạy HẾT rồi mới kết luận, không dừng ở lỗi đầu tiên: người vận hành cần
    thấy tất cả những gì đang sai trong một lần nhìn, thay vì sửa một cái rồi
    chạy lại để phát hiện cái kế tiếp.
    """
    settings = settings or get_settings()
    database, migrations = await _check_database_and_migrations()
    checks = [_check_llm(settings), database, migrations, _check_connectors(settings)]
    return all(check.ok for check in checks), [check.as_dict() for check in checks]
