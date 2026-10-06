# services/auth-service/src/pdrd_auth_service/infrastructure/database/base.py

"""Метаданные ORM только для схемы auth."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Объединяет модели, которыми владеет auth-service."""
