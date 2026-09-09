# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/base.py

"""Базовая SQLAlchemy metadata Knowledge Service."""

from sqlalchemy.orm import DeclarativeBase

KNOWLEDGE_SCHEMA = "knowledge"


class Base(DeclarativeBase):
    """Базовый класс ORM-моделей Knowledge Service."""
