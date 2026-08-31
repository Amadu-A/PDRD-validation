# services/api-gateway/src/pdrd_api_gateway/infrastructure/database/base.py

"""Базовая SQLAlchemy declarative model API Gateway."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс ORM-моделей API Gateway."""
