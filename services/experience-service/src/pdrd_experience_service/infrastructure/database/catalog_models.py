# services/experience-service/src/pdrd_experience_service/infrastructure/database/catalog_models.py

"""Таблицы постоянного Experience: PNG хранится в собственном volume, здесь метаданные."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from pdrd_experience_service.infrastructure.database.models import Base


class CatalogExampleModel(Base):
    """Неизменяемая идентичность примера и текущая инженерная редакция."""

    __tablename__ = "catalog_examples"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_catalog_revision"),
        {"schema": "experience"},
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    example_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    finding_id: Mapped[str] = mapped_column(String(256))
    approved_revision: Mapped[int] = mapped_column(Integer)
    area_revision: Mapped[int] = mapped_column(Integer)
    area_signature: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(16))
    tag: Mapped[str] = mapped_column(String(16), index=True)
    decision: Mapped[str] = mapped_column(String(16))
    learning_use: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean)
    document_title: Mapped[str] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    normative_basis: Mapped[str] = mapped_column(Text)
    source_filename: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    content_key: Mapped[str] = mapped_column(String(64), index=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    section_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    section_title: Mapped[str] = mapped_column(String(300), default="")


class CatalogOccurrenceModel(Base):
    """Повторное утверждение того же примера: собственное происхождение каждого прогона."""

    __tablename__ = "catalog_occurrences"
    __table_args__ = ({"schema": "experience"},)
    example_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("experience.catalog_examples.id", ondelete="CASCADE"),
        primary_key=True,
    )
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    finding_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    approved_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_revision: Mapped[int] = mapped_column(Integer)
    area_signature: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128))
    snapshot: Mapped[dict] = mapped_column(JSONB)


class CatalogEventModel(Base):
    """Каждая редакция имеет отдельный полный снимок и серверного автора."""

    __tablename__ = "catalog_events"
    __table_args__ = ({"schema": "experience"},)
    example_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("experience.catalog_examples.id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict] = mapped_column(JSONB)
