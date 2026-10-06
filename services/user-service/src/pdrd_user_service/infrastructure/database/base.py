# services/user-service/src/pdrd_user_service/infrastructure/database/base.py

"""Общая метаинформация таблиц, принадлежащих User Service."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Объединяет ORM-модели только схемы users."""
