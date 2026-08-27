"""Gán chủ sở hữu cho các dòng `service_approvals` có TRƯỚC cột đơn vị.

Vì sao phải chạy tay
--------------------
Cổng duyệt kiểm quyền sở hữu và FAIL-CLOSED: `service_provider_id IS NULL` trả
False cho mọi đơn vị. Đó là hành vi đúng cho một dòng mới — nhưng dữ liệu đã có
trước khi cột ấy tồn tại đều NULL, nên sau khi migration chạy, chúng vô hình
với mọi provider và không ai quyết định được nữa.

KHÔNG đưa việc này vào migration, và KHÔNG gọi lúc khởi động. "Dòng nào thuộc
đơn vị nào" là một câu hỏi NGHIỆP VỤ, không phải một phép biến đổi schema — một
migration đoán hộ nghĩa là mọi môi trường đều nhận cùng một cái đoán, kể cả môi
trường mà cái đoán ấy sai. Chạy tay, một lần, có người đọc con số trước khi ghi.

Vì sao là `LEGACY-DEFAULT` chứ không phải một đơn vị thật
--------------------------------------------------------
Gán dòng lịch sử cho `MOV-01` là viết một sự thật không có: đơn vị ấy chưa bao
giờ nhận những việc đó. Sau này khi đối chiếu doanh thu hay đánh giá đơn vị,
không cách nào phân biệt "việc thật của Minh Phát" với "việc được backfill gán
bừa". `LEGACY-DEFAULT` nói đúng điều đang xảy ra: đây là dữ liệu có trước khái
niệm đơn vị, và nó thuộc về một danh tính tồn tại chính vì lý do ấy.

Backfill TOÀN BỘ, kể cả lịch sử đã quyết định — provider legacy phải tra lại
được lịch sử một cách nhất quán. Dòng MỚI thì bắt buộc có đơn vị cụ thể qua
`provider_directory.don_vi_mac_dinh()` và không bao giờ rơi về đây.

Cách chạy
---------
    # xem trước, KHÔNG ghi gì
    PYTHONPATH=. .venv/bin/python scripts/backfill_service_provider.py --database p118_db

    # ghi thật
    PYTHONPATH=. .venv/bin/python scripts/backfill_service_provider.py --database p118_db \
        --account provider --account provider_demo --apply

`--database` là tên database ĐÍCH và script tự đối chiếu với `current_database()`
trước khi làm gì. Không phải thủ tục thừa: DSN đến từ môi trường, và một
`.env` trỏ sai là cách người ta backfill nhầm production trong lúc định thử ở
staging. Tên database KHÔNG phải bí mật; DSN thì có, nên nó không bao giờ được
in ra.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.orchestration.provider_directory import DON_VI_LEGACY, TEN_DON_VI_LEGACY  # noqa: E402


async def _chay(db_dich: str, tai_khoan: list[str], apply: bool) -> int:
    pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            thuc_te = await conn.fetchval("SELECT current_database()")
            if thuc_te != db_dich:
                # KHÔNG in DSN. Tên database đủ để người chạy biết mình đang ở
                # đâu, và nó là thứ duy nhất họ cần để sửa.
                print(f"DỪNG: --database là {db_dich!r} nhưng đang kết nối tới {thuc_te!r}.")
                return 2

            if await conn.fetchval("SELECT to_regclass('service_approvals')") is None:
                print("DỪNG: chưa có bảng `service_approvals` — chạy migration trước.")
                return 2

            truoc_null = await conn.fetchval("SELECT count(*) FROM service_approvals WHERE service_provider_id IS NULL")
            truoc_legacy = await conn.fetchval(
                "SELECT count(*) FROM service_approvals WHERE service_provider_id = $1", DON_VI_LEGACY
            )
            tong = await conn.fetchval("SELECT count(*) FROM service_approvals")

            # Tài khoản phải TỒN TẠI. Không tự tạo, không đoán: một tài khoản
            # có quyền duyệt được sinh ra bởi một script dọn dữ liệu là đúng
            # thứ không ai rà lại. Ai được nhân danh đơn vị legacy là quyết
            # định của người chạy, và họ phải nói tên ra.
            thieu: list[str] = []
            ids: dict[str, str] = {}
            for ten in tai_khoan:
                row = await conn.fetchrow("SELECT id, role FROM users WHERE username = $1", ten)
                if row is None:
                    thieu.append(f"{ten} (không tồn tại)")
                elif row["role"] != "provider":
                    thieu.append(f"{ten} (role={row['role']}, cần provider)")
                else:
                    ids[ten] = str(row["id"])
            if thieu:
                print("DỪNG: tài khoản đích không dùng được — " + "; ".join(thieu))
                return 2

            print(f"database          : {thuc_te}")
            print(f"đơn vị legacy     : {DON_VI_LEGACY} ({TEN_DON_VI_LEGACY})")
            print(f"tổng service_approvals : {tong}")
            print(f"đang NULL (sẽ gán)     : {truoc_null}")
            print(f"đã là legacy từ trước  : {truoc_legacy}")
            print(f"tài khoản sẽ gắn       : {', '.join(tai_khoan) or '(không có)'}")

            if not apply:
                print("\nDRY-RUN — không ghi gì. Thêm --apply để ghi thật.")
                return 0

            # MỘT transaction: mapping tài khoản và quyền sở hữu dòng phải cùng
            # sống hoặc cùng chết. Gán dòng xong mà mapping hỏng nghĩa là 287
            # dòng vừa đổi chủ sang một đơn vị không ai nhân danh được — tệ hơn
            # hẳn trạng thái ban đầu, vì bây giờ nó trông như đã sửa xong.
            async with conn.transaction():
                for ten, uid in ids.items():
                    await conn.execute(
                        "INSERT INTO service_provider_accounts (user_id, service_provider_id) "
                        "VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING",
                        uid,
                        DON_VI_LEGACY,
                    )
                # CHỈ cột `service_provider_id`. Không chạm `status`,
                # `decided_by`, `decided_at`, `reject_code`, `reject_reason`,
                # `created_at` — đây là dọn quyền sở hữu, không phải viết lại
                # lịch sử ai đã quyết định gì lúc nào.
                #
                # `WHERE ... IS NULL` làm script idempotent: chạy lần hai không
                # còn dòng nào khớp, và không dòng nào đã có chủ bị đổi chủ.
                da_gan = await conn.execute(
                    "UPDATE service_approvals SET service_provider_id = $1 WHERE service_provider_id IS NULL",
                    DON_VI_LEGACY,
                )

            sau_null = await conn.fetchval("SELECT count(*) FROM service_approvals WHERE service_provider_id IS NULL")
            sau_legacy = await conn.fetchval(
                "SELECT count(*) FROM service_approvals WHERE service_provider_id = $1", DON_VI_LEGACY
            )
            print(f"\nĐÃ GHI: {da_gan}")
            print(f"còn NULL sau khi chạy  : {sau_null}")
            print(f"tổng legacy sau khi chạy: {sau_legacy}")
            if sau_null != 0:
                print("CẢNH BÁO: vẫn còn dòng NULL — có tiến trình khác đang ghi?")
                return 1
            return 0
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="tên database ĐÍCH, đối chiếu với current_database()")
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="username (role=provider) được gắn vào đơn vị legacy; lặp lại được",
    )
    parser.add_argument("--apply", action="store_true", help="ghi thật; mặc định là dry-run")
    args = parser.parse_args()
    if args.apply and not args.account:
        print("DỪNG: --apply cần ít nhất một --account, nếu không sẽ không ai duyệt được dòng legacy.")
        return 2
    return asyncio.run(_chay(args.database, args.account, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
