"""Fixtures dùng chung cho test mock API."""

import pytest

from src.mock.store import store
from src.services.mock.payment import store as payment_store
from src.services.mock.resident import store as resident_store
from src.services.mock.transport import store as transport_store


@pytest.fixture(autouse=True)
def reset_store():
    """Reset store singleton trước mỗi test để đảm bảo cô lập.

    Reset cả store chung (src.mock) lẫn 3 store riêng của provider độc lập
    (src/services/mock/*). Các store rời nhau nên không xung đột; reset store
    rỗng là no-op.
    """
    store.reset()
    resident_store.reset()
    transport_store.reset()
    payment_store.reset()
    yield
