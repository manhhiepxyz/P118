.PHONY: run run-mock run-mock-resident run-mock-transport run-mock-payment run-mock-tour run-mock-shuttle run-mock-consultation gen-test-data test test-mock lint format typecheck check clean

run:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Mock monolith (1 app, cross-check giữa service) — cổng 8001
run-mock:
	uvicorn src.mock.main:app --reload --host 0.0.0.0 --port 8001

# Mock providers độc lập theo system design (6 app, 6 cổng).
# LƯU Ý: run-mock và run-mock-resident đều bind cổng 8001 — 2 lựa chọn thay
# thế, KHÔNG chạy đồng thời. Cổng 8002–8007 tự do.
run-mock-resident:
	uvicorn src.services.mock.resident:resident_app --reload --host 0.0.0.0 --port 8001

run-mock-transport:
	uvicorn src.services.mock.transport:transport_app --reload --host 0.0.0.0 --port 8002

run-mock-payment:
	uvicorn src.services.mock.payment:payment_app --reload --host 0.0.0.0 --port 8003

run-mock-tour:
	uvicorn src.services.mock.tour:tour_app --reload --host 0.0.0.0 --port 8005

run-mock-shuttle:
	uvicorn src.services.mock.shuttle:shuttle_app --reload --host 0.0.0.0 --port 8006

run-mock-consultation:
	uvicorn src.services.mock.consultation:consultation_app --reload --host 0.0.0.0 --port 8007

# Sinh dữ liệu test hàng loạt (mặc định 100 cư dân + chuỗi happy path + 20 workflow)
# Cách dùng: make gen-test-data
#            make gen-test-data ARGS="--residents 500 --workflows 50"
gen-test-data:
	PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe src/db/generate_bulk_data.py $(ARGS)

test:
	pytest tests/ -v

test-mock:
	pytest tests/test_mock/ -v

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

typecheck:
	mypy src/

check: lint format test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
