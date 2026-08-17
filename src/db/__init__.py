"""
src/db/__init__.py
P-118 — Database module exports

Import chuẩn:
    from src.db import get_pool, PostgreSQLWorkflowStateRepository
    from src.db import NoAvailabilityError, BookingAlreadyExistsError
"""

from src.db.capacity_repository import BookingAlreadyExistsError, NoAvailabilityError
from src.db.connection import close_pool, create_pool, get_pool, lifespan
from src.db.migrations import create_test_db, run_migrations
from src.db.postgres_repository import (
    PostgreSQLWorkflowStateRepository,
)
from src.db.user_repository import UserAlreadyExistsError, UserRepository

__all__ = [
    # Connection / lifecycle
    "create_pool",
    "close_pool",
    "get_pool",
    "lifespan",
    # Migration
    "run_migrations",
    "create_test_db",
    # Repository
    "PostgreSQLWorkflowStateRepository",
    "UserRepository",
    # Domain errors
    "NoAvailabilityError",
    "BookingAlreadyExistsError",
    "UserAlreadyExistsError",
]
