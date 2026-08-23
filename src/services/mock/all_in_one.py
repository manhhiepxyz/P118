"""MỌI mock provider trong MỘT tiến trình — để deploy được trên gói miễn phí.

Owner: Mạnh Hiệp (Executor layer)
File: src/services/mock/all_in_one.py

Vì sao tồn tại
--------------
`docker-compose` chạy 9 mock provider thành 9 service. Trên máy dev thì hợp lý:
mỗi dịch vụ một cổng, một chủ sở hữu, hỏng cái nào biết ngay cái đó. Trên Render
gói miễn phí thì không: 750 giờ instance chia cho MỌI service, tức 9 mock cộng
backend không cùng sống nổi một tháng.

`src/mock/main.py` đã có sẵn một bản gộp — nhưng nó là một implementation KHÁC,
viết trước và đã lệch. Đo được: nó thiếu 9 endpoint mà connector đang gọi
(`/api/property/viewings`, `/api/properties/search`, `/api/resident-services/*`,
và toàn bộ nhánh huỷ/đổi khu), còn giữ `/api/tours/bookings` mà không connector
nào dùng nữa. Gộp bằng cách ấy là bảo trì hai bản mock cho một hợp đồng.

File này KHÔNG viết lại gì. Nó gắn (`mount`) chính những app đang chạy vào một
app cha, nên hành vi giống hệt bản nhiều-service — cùng route, cùng store, cùng
lỗi. Thêm một endpoint ở service nào thì bản gộp có ngay, không phải chép tay.

Chạy:
    uvicorn src.services.mock.all_in_one:app --host 0.0.0.0 --port 8000

Các app con dùng ĐƯỜNG DẪN TUYỆT ĐỐI (`/api/parking/...`), không phải prefix
riêng, nên chúng không thể mount chồng lên nhau ở các tiền tố khác nhau. Cách
ghép đúng là gộp ROUTE của chúng vào một app — mỗi route giữ nguyên đường dẫn nó
vốn có.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock.errors import install_error_handler
from src.services.mock.apartment_ownership import apartment_ownership_app
from src.services.mock.consultation import consultation_app
from src.services.mock.db_pool import database_lifespan
from src.services.mock.payment import payment_app
from src.services.mock.property import property_app
from src.services.mock.resident import resident_app
from src.services.mock.resident_services import resident_services_app
from src.services.mock.shuttle import shuttle_app
from src.services.mock.tour import tour_app
from src.services.mock.transport import transport_app

logger = logging.getLogger("p118.mock.all_in_one")

# Thứ tự có ý nghĩa: route trùng đường dẫn thì app ĐỨNG TRƯỚC thắng.
#
# Hôm nay không có cặp nào trùng — `test_all_in_one_serves_every_service.py` ép
# điều đó, và nó sẽ đỏ ngay lần đầu ai đó thêm một route đụng hàng. Ghi thứ tự
# ra đây để lúc ấy người sửa biết mình đang chọn cái gì.
_SERVICES = (
    ("resident", resident_app),
    ("transport", transport_app),
    ("payment", payment_app),
    ("property", property_app),
    ("tour", tour_app),
    ("shuttle", shuttle_app),
    ("resident-services", resident_services_app),
    ("consultation", consultation_app),
    ("apartment-ownership", apartment_ownership_app),
)

# Gắn route thôi thì CHƯA đủ: mỗi app con dựng pool database trong lifespan của
# CHÍNH nó, và lifespan của app con KHÔNG chạy khi ta chỉ chép route sang.
#
# Đo được ở lần chạy đầu — `register_vehicle` trả HTTP 500:
#
#     RuntimeError: Database pool chưa sẵn sàng.
#
# App cha phải chạy đúng lifespan ấy. Một lần, dùng chung: `pool_holder` là biến
# toàn cục của tiến trình, nên chín app con đọc cùng một pool — và đó cũng chính
# là điều làm bản gộp rẻ hơn chín service rời.
app = FastAPI(
    title="P-118 Mock Providers (gộp)",
    description="Mọi mock provider trong một tiến trình. Hành vi giống hệt bản nhiều-service.",
    version="1.0.0",
    lifespan=database_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_error_handler(app)


def _gan_route() -> None:
    """Chép route của từng app con sang app cha, giữ nguyên đường dẫn.

    `/health` của các app con bị BỎ: chín cái cùng đường dẫn, và app cha có bản
    của riêng nó ở dưới. Route tài liệu (`/docs`, `/openapi.json`) cũng bỏ vì
    app cha tự sinh.
    """
    bo_qua = {"/health", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
    da_co: dict[str, str] = {}
    for ten, con in _SERVICES:
        for route in con.routes:
            duong_dan = getattr(route, "path", None)
            if duong_dan is None or duong_dan in bo_qua:
                continue
            for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
                khoa = f"{method} {duong_dan}"
                if khoa in da_co:
                    # Không im lặng bỏ qua: hai service cùng nhận một đường dẫn
                    # nghĩa là hợp đồng đã lệch, và bản gộp sẽ chỉ phục vụ một
                    # trong hai — âm thầm.
                    logger.warning("route trùng %s: giữ của %s, bỏ của %s", khoa, da_co[khoa], ten)
                    continue
                da_co[khoa] = ten
            app.router.routes.append(route)


_gan_route()


@app.get("/health", tags=["meta"])
def health() -> dict[str, object]:
    return {"status": "ok", "service": "mock-all-in-one", "services": [ten for ten, _ in _SERVICES]}
