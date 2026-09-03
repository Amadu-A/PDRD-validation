# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/repositories.py

"""SQLAlchemy repositories нормативного каталога."""

from uuid import UUID

from sqlalchemy import (
    delete,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeCategory,
    NormativeDocument,
    NormativeSection,
)
from pdrd_knowledge_service.infrastructure.database.models import (
    NormativeCategoryModel,
    NormativeDocumentModel,
    NormativeSectionModel,
)


class SqlAlchemyNormativeSectionRepository:
    """SQLAlchemy repository разделов нормативной базы."""

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        """Сохраняет session текущего Unit of Work."""
        self._session = session

    async def add(
        self,
        section: NormativeSection,
    ) -> None:
        """Добавляет новый раздел и flush-ит его внутри transaction."""
        self._session.add(
            NormativeSectionModel(
                id=section.section_id,
                name=section.name,
                system_prompt=section.system_prompt,
                created_at=section.created_at,
                updated_at=section.updated_at,
            )
        )

        await self._session.flush()

    async def get(
        self,
        section_id: UUID,
    ) -> NormativeSection | None:
        """Возвращает раздел по UUID."""
        model = await self._session.get(
            NormativeSectionModel,
            section_id,
        )

        if model is None:
            return None

        return self._to_domain(
            model,
        )

    async def list_all(
        self,
    ) -> list[NormativeSection]:
        """Возвращает разделы в стабильном порядке."""
        result = await self._session.scalars(
            select(
                NormativeSectionModel,
            ).order_by(
                NormativeSectionModel.name,
                NormativeSectionModel.id,
            )
        )

        return [
            self._to_domain(
                model,
            )
            for model in result.all()
        ]

    async def update(
        self,
        section: NormativeSection,
    ) -> None:
        """Обновляет persistence model раздела."""
        model = await self._session.get(
            NormativeSectionModel,
            section.section_id,
        )

        if model is None:
            raise LookupError(
                f"Normative section {section.section_id} not found.",
            )

        model.name = section.name
        model.system_prompt = section.system_prompt
        model.updated_at = section.updated_at

    async def delete(
        self,
        section_id: UUID,
    ) -> None:
        """Удаляет раздел."""
        await self._session.execute(
            delete(
                NormativeSectionModel,
            ).where(
                NormativeSectionModel.id == section_id,
            )
        )

    @staticmethod
    def _to_domain(
        model: NormativeSectionModel,
    ) -> NormativeSection:
        """Преобразует ORM model в domain entity."""
        return NormativeSection(
            section_id=model.id,
            name=model.name,
            system_prompt=model.system_prompt,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class SqlAlchemyNormativeCategoryRepository:
    """SQLAlchemy repository категорий нормативов."""

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        """Сохраняет session текущего Unit of Work."""
        self._session = session

    async def add(
        self,
        category: NormativeCategory,
    ) -> None:
        """Добавляет категорию и flush-ит её внутри transaction."""
        self._session.add(
            NormativeCategoryModel(
                id=category.category_id,
                section_id=category.section_id,
                parent_id=category.parent_id,
                name=category.name,
                created_at=category.created_at,
                updated_at=category.updated_at,
            )
        )

        await self._session.flush()

    async def get(
        self,
        category_id: UUID,
    ) -> NormativeCategory | None:
        """Возвращает категорию по UUID."""
        model = await self._session.get(
            NormativeCategoryModel,
            category_id,
        )

        if model is None:
            return None

        return self._to_domain(
            model,
        )

    async def list_by_section(
        self,
        section_id: UUID,
    ) -> list[NormativeCategory]:
        """Возвращает категории раздела."""
        result = await self._session.scalars(
            select(
                NormativeCategoryModel,
            )
            .where(
                NormativeCategoryModel.section_id == section_id,
            )
            .order_by(
                NormativeCategoryModel.name,
                NormativeCategoryModel.id,
            )
        )

        return [
            self._to_domain(
                model,
            )
            for model in result.all()
        ]

    async def update(
        self,
        category: NormativeCategory,
    ) -> None:
        """Обновляет persistence model категории."""
        model = await self._session.get(
            NormativeCategoryModel,
            category.category_id,
        )

        if model is None:
            raise LookupError(
                f"Normative category {category.category_id} not found.",
            )

        model.section_id = category.section_id
        model.parent_id = category.parent_id
        model.name = category.name
        model.updated_at = category.updated_at

    async def delete(
        self,
        category_id: UUID,
    ) -> None:
        """Удаляет категорию."""
        await self._session.execute(
            delete(
                NormativeCategoryModel,
            ).where(
                NormativeCategoryModel.id == category_id,
            )
        )

    @staticmethod
    def _to_domain(
        model: NormativeCategoryModel,
    ) -> NormativeCategory:
        """Преобразует ORM model в domain entity."""
        return NormativeCategory(
            category_id=model.id,
            section_id=model.section_id,
            parent_id=model.parent_id,
            name=model.name,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class SqlAlchemyNormativeDocumentRepository:
    """SQLAlchemy repository нормативных документов."""

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        """Сохраняет session текущего Unit of Work."""
        self._session = session

    async def add(
        self,
        document: NormativeDocument,
    ) -> None:
        """Добавляет metadata документа и flush-ит внутри transaction."""
        self._session.add(
            NormativeDocumentModel(
                id=document.document_id,
                section_id=document.section_id,
                category_id=document.category_id,
                original_name=document.original_name,
                storage_key=document.storage_key,
                mime_type=document.mime_type,
                size_bytes=document.size_bytes,
                sha256=document.sha256,
                index_status=document.index_status.value,
                index_error=document.index_error,
                indexed_at=document.indexed_at,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
        )

        await self._session.flush()

    async def get(
        self,
        document_id: UUID,
    ) -> NormativeDocument | None:
        """Возвращает документ по UUID."""
        model = await self._session.get(
            NormativeDocumentModel,
            document_id,
        )

        if model is None:
            return None

        return self._to_domain(
            model,
        )

    async def get_for_update(
        self,
        document_id: UUID,
    ) -> NormativeDocument | None:
        """Возвращает документ и блокирует строку до конца transaction."""
        model = await self._session.scalar(
            select(
                NormativeDocumentModel,
            )
            .where(
                NormativeDocumentModel.id == document_id,
            )
            .with_for_update()
        )

        if model is None:
            return None

        return self._to_domain(
            model,
        )

    async def list_by_ids(
        self,
        document_ids: tuple[
            UUID,
            ...,
        ],
    ) -> list[NormativeDocument]:
        """Возвращает документы по набору UUID одним SQL query."""
        if not document_ids:
            return []

        result = await self._session.scalars(
            select(
                NormativeDocumentModel,
            ).where(
                NormativeDocumentModel.id.in_(
                    document_ids,
                )
            )
        )

        return [
            self._to_domain(
                model,
            )
            for model in result.all()
        ]

    async def list_by_section(
        self,
        section_id: UUID,
    ) -> list[NormativeDocument]:
        """Возвращает документы раздела."""
        result = await self._session.scalars(
            select(
                NormativeDocumentModel,
            )
            .where(
                NormativeDocumentModel.section_id == section_id,
            )
            .order_by(
                NormativeDocumentModel.original_name,
                NormativeDocumentModel.id,
            )
        )

        return [
            self._to_domain(
                model,
            )
            for model in result.all()
        ]

    async def update(
        self,
        document: NormativeDocument,
    ) -> None:
        """Обновляет metadata документа."""
        model = await self._session.get(
            NormativeDocumentModel,
            document.document_id,
        )

        if model is None:
            raise LookupError(
                f"Normative document {document.document_id} not found.",
            )

        model.section_id = document.section_id
        model.category_id = document.category_id
        model.original_name = document.original_name
        model.storage_key = document.storage_key
        model.mime_type = document.mime_type
        model.size_bytes = document.size_bytes
        model.sha256 = document.sha256
        model.index_status = document.index_status.value
        model.index_error = document.index_error
        model.indexed_at = document.indexed_at
        model.updated_at = document.updated_at

    async def delete(
        self,
        document_id: UUID,
    ) -> None:
        """Удаляет metadata документа."""
        await self._session.execute(
            delete(
                NormativeDocumentModel,
            ).where(
                NormativeDocumentModel.id == document_id,
            )
        )

    @staticmethod
    def _to_domain(
        model: NormativeDocumentModel,
    ) -> NormativeDocument:
        """Преобразует ORM model в domain entity."""
        return NormativeDocument(
            document_id=model.id,
            section_id=model.section_id,
            category_id=model.category_id,
            original_name=model.original_name,
            storage_key=model.storage_key,
            mime_type=model.mime_type,
            size_bytes=model.size_bytes,
            sha256=model.sha256,
            index_status=IndexingStatus(
                model.index_status,
            ),
            index_error=model.index_error,
            indexed_at=model.indexed_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
