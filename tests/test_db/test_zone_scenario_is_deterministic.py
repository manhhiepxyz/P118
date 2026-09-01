"""Khu A luôn kín, khu B luôn còn chỗ — kịch bản demo phải ĐOÁN TRƯỚC ĐƯỢC.

Luồng đáng xem nhất của sản phẩm là: chọn khu A → hết chỗ → hệ thống nêu lý do
và gợi ý khu B → người dùng đổi → chạy tiếp. Muốn diễn lại được thì kết quả
không được phụ thuộc "hôm nay đã có ai đặt chưa".

Làm bằng SỨC CHỨA chứ không bằng booking giả: sức chứa 0 áp cho mọi ngày, còn
booking giả vừa sai sự thật vừa phải gieo lại cho từng ngày.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest


@pytest.mark.asyncio
async def test_zone_a_is_configured_full(db_pool) -> None:
    async with db_pool.acquire() as conn:
        capacity = await conn.fetchval("SELECT capacity FROM zone_capacity_config WHERE parking_zone='ZONE_A'")
    assert capacity == 0, "khu A phải kín theo cấu hình, không phụ thuộc booking trong ngày"


@pytest.mark.asyncio
async def test_zone_b_has_room_for_a_demo(db_pool) -> None:
    async with db_pool.acquire() as conn:
        capacity = await conn.fetchval("SELECT capacity FROM zone_capacity_config WHERE parking_zone='ZONE_B'")
    assert capacity >= 50, f"khu B chỉ còn {capacity} chỗ — một buổi demo dài có thể lấp đầy"


@pytest.mark.asyncio
async def test_zero_capacity_is_allowed_by_both_tables(db_pool) -> None:
    """HAI bảng, HAI ràng buộc — dễ nới một và quên bảng kia.

    Đo được: nới mỗi `zone_capacity_config` thì seed chạy tới câu đồng bộ là đổ
    `CheckViolationError`, và vì migration dừng ở đó nên CẢ file seed không
    hoàn tất — config đúng mà các ngày vẫn giữ số cũ.
    """
    async with db_pool.acquire() as conn:
        for table in ("zone_capacity_config", "parking_capacity"):
            definition = await conn.fetchval(
                """
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = $1::regclass AND conname LIKE '%capacity_check'
                """,
                table,
            )
            assert definition is not None, f"{table} không còn ràng buộc sức chứa"
            assert ">= 0" in definition, f"{table} vẫn cấm sức chứa 0: {definition}"


@pytest.mark.asyncio
async def test_a_fresh_future_date_inherits_the_scenario(db_pool) -> None:
    """Ngày chưa ai chạm cũng phải theo kịch bản.

    `parking_capacity` vật hoá một dòng cho mỗi (khu, ngày) ở lần dùng ĐẦU,
    chép sức chứa tại thời điểm đó — nên nguồn sự thật là bảng cấu hình.
    """
    from src.db.capacity_repository import CapacityRepository, NoAvailabilityError

    repository = CapacityRepository(db_pool)
    far_future = (date.today() + timedelta(days=900)).isoformat()

    with pytest.raises(NoAvailabilityError):
        await repository.check_and_reserve_capacity("ZONE_A", far_future, "BOOK-scenario", "VEH-scenario", 150_000)
