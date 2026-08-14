"""Mock API — FastAPI app riêng cho dịch vụ giả lập.

Chạy độc lập với app chính:
    uvicorn src.mock.main:app --port 8001

Swagger/OpenAPI tự động tại /docs — đóng vai trò "OpenAPI specs" cho 4 service.
CORS bật sẵn để sau này UI gọi được.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock.errors import install_error_handler
from src.mock.routers import (
    apartment_owners,
    consultations,
    parking,
    payments,
    residents,
    shuttles,
    tours,
    vehicles,
)

app = FastAPI(
    title="P-118 Mock Services",
    description=(
        "Dịch vụ giả lập theo shared_contracts.md — Resident, Vehicle, Parking, Payment, Tour, Shuttle, Consultation."
    ),
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(app)

app.include_router(residents.router)
app.include_router(vehicles.router)
app.include_router(parking.router)
app.include_router(payments.router)
app.include_router(apartment_owners.router)
app.include_router(tours.router)
app.include_router(shuttles.router)
app.include_router(consultations.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "mock"}
