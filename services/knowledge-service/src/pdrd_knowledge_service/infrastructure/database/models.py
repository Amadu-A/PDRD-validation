# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/models.py

"""SQLAlchemy persistence models managed catalog."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from pdrd_knowledge_service.infrastructure.database.base import (
    KNOWLEDGE_SCHEMA,
    Base,
)


class NormativeSectionModel(Base):
    """ORM-представление раздела managed catalog."""

    __tablename__ = "normative_sections"

    __table_args__ = (
        CheckConstraint(
            "btrim(name) <> ''",
            name="ck_normative_sections_name_not_blank",
        ),
        Index(
            "ix_normative_sections_name",
            "name",
        ),
        {
            "schema": KNOWLEDGE_SCHEMA,
        },
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    system_prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class NormativeCategoryModel(Base):
    """ORM-представление категории managed catalog."""

    __tablename__ = "normative_categories"

    __table_args__ = (
        CheckConstraint(
            "btrim(name) <> ''",
            name="ck_normative_categories_name_not_blank",
        ),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_normative_categories_not_self_parent",
        ),
        CheckConstraint(
            "catalog_area IN ('normative', 'user_package')",
            name="ck_normative_categories_catalog_area",
        ),
        Index(
            "ix_normative_categories_section_id",
            "section_id",
        ),
        Index(
            "ix_normative_categories_parent_id",
            "parent_id",
        ),
        Index(
            "ix_normative_categories_section_area",
            "section_id",
            "catalog_area",
        ),
        {
            "schema": KNOWLEDGE_SCHEMA,
        },
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )

    section_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            f"{KNOWLEDGE_SCHEMA}.normative_sections.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            f"{KNOWLEDGE_SCHEMA}.normative_categories.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    catalog_area: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text(
            "'normative'",
        ),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class NormativeDocumentModel(Base):
    """ORM-представление managed PDF/DOC/DOCX документа."""

    __tablename__ = "normative_documents"

    __table_args__ = (
        CheckConstraint(
            "btrim(original_name) <> ''",
            name="ck_normative_documents_name_not_blank",
        ),
        CheckConstraint(
            "btrim(storage_key) <> ''",
            name="ck_normative_documents_storage_key_not_blank",
        ),
        CheckConstraint(
            "btrim(mime_type) <> ''",
            name="ck_normative_documents_mime_not_blank",
        ),
        CheckConstraint(
            "size_bytes > 0",
            name="ck_normative_documents_size_positive",
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-fA-F]{64}$'",
            name="ck_normative_documents_sha256",
        ),
        CheckConstraint(
            "catalog_area IN ('normative', 'user_package')",
            name="ck_normative_documents_catalog_area",
        ),
        CheckConstraint(
            "index_status IN ("
            "'uploaded', "
            "'queued', "
            "'indexing', "
            "'ready', "
            "'failed', "
            "'deleting'"
            ")",
            name="ck_normative_documents_index_status",
        ),
        CheckConstraint(
            "("
            "index_status = 'failed' "
            "AND index_error IS NOT NULL "
            "AND btrim(index_error) <> ''"
            ") OR ("
            "index_status <> 'failed' "
            "AND index_error IS NULL"
            ")",
            name="ck_normative_documents_index_error",
        ),
        CheckConstraint(
            "("
            "index_status = 'ready' "
            "AND indexed_at IS NOT NULL"
            ") OR ("
            "index_status <> 'ready' "
            "AND indexed_at IS NULL"
            ")",
            name="ck_normative_documents_indexed_at",
        ),
        UniqueConstraint(
            "storage_key",
            name="uq_normative_documents_storage_key",
        ),
        Index(
            "ix_normative_documents_section_id",
            "section_id",
        ),
        Index(
            "ix_normative_documents_category_id",
            "category_id",
        ),
        Index(
            "ix_normative_documents_index_status",
            "index_status",
        ),
        Index(
            "ix_normative_documents_sha256",
            "sha256",
        ),
        Index(
            "ix_normative_documents_section_area",
            "section_id",
            "catalog_area",
        ),
        {
            "schema": KNOWLEDGE_SCHEMA,
        },
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )

    section_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            f"{KNOWLEDGE_SCHEMA}.normative_sections.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    category_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            f"{KNOWLEDGE_SCHEMA}.normative_categories.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    original_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    storage_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    mime_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    catalog_area: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text(
            "'normative'",
        ),
    )

    index_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text(
            "'uploaded'",
        ),
    )

    index_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
