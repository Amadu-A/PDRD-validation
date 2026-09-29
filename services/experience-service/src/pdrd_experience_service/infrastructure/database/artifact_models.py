# services/experience-service/src/pdrd_experience_service/infrastructure/database/artifact_models.py

"""Собственный реестр артефактов, состава, событий и применённых версий."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from pdrd_experience_service.infrastructure.database.models import Base


class ArtifactVersionModel(Base):
    """Версия и аренда worker; коллекция Qdrant никогда не задаётся браузером."""

    __tablename__ = "artifact_versions"
    __table_args__ = ({"schema": "experience"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    section_id: Mapped[str] = mapped_column(String(128), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), index=True)
    deleted: Mapped[bool] = mapped_column(Boolean)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str] = mapped_column(String(128), default="")
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ArtifactMemberModel(Base):
    """Состав версии позволяет SQL-проверки и сохраняет исходный снимок обучения."""

    __tablename__ = "artifact_members"
    __table_args__ = ({"schema": "experience"},)
    version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("experience.artifact_versions.id"),
        primary_key=True,
    )
    example_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("experience.catalog_examples.id"),
        primary_key=True,
    )
    example_revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB)


class AppliedArtifactModel(Base):
    """Одна применённая версия каждого вида на раздел нормативки."""

    __tablename__ = "applied_artifacts"
    __table_args__ = ({"schema": "experience"},)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    section_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("experience.artifact_versions.id")
    )
    actor: Mapped[str] = mapped_column(String(128))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactEventModel(Base):
    """Аудит запуска, переименования, удаления и применения версии."""

    __tablename__ = "artifact_events"
    __table_args__ = ({"schema": "experience"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("experience.artifact_versions.id")
    )
    operation: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict] = mapped_column(JSONB)
