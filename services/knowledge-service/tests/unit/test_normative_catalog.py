# services/knowledge-service/tests/unit/test_normative_catalog.py

"""Unit tests domain-модели нормативного каталога."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeCatalogError,
    NormativeCategory,
    NormativeDocument,
    NormativeSection,
)

BASE_TIME = datetime(
    2026,
    9,
    2,
    9,
    0,
    tzinfo=UTC,
)

VALID_SHA256 = "a" * 64


def make_section() -> NormativeSection:
    """Создаёт валидный раздел для unit tests."""
    return NormativeSection(
        section_id=uuid4(),
        name="Электроснабжение",
        system_prompt="Проверяй документацию по выбранным нормативам.",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def make_document() -> NormativeDocument:
    """Создаёт валидный загруженный нормативный документ."""
    return NormativeDocument(
        document_id=uuid4(),
        section_id=uuid4(),
        category_id=None,
        original_name="СП 256.1325800.2016.pdf",
        storage_key="document.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        sha256=VALID_SHA256,
        index_status=IndexingStatus.UPLOADED,
        index_error=None,
        indexed_at=None,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def test_section_can_be_renamed_and_prompt_replaced() -> None:
    """Раздел поддерживает независимое изменение имени и system prompt."""
    section = make_section()

    renamed_at = BASE_TIME + timedelta(
        minutes=1,
    )

    renamed = section.renamed(
        name="ЭОМ",
        changed_at=renamed_at,
    )

    prompt_changed_at = BASE_TIME + timedelta(
        minutes=2,
    )

    updated = renamed.with_system_prompt(
        system_prompt="Новый системный prompt раздела.",
        changed_at=prompt_changed_at,
    )

    assert section.name == "Электроснабжение"
    assert renamed.name == "ЭОМ"

    assert updated.system_prompt == ("Новый системный prompt раздела.")

    assert updated.updated_at == prompt_changed_at


def test_section_rejects_blank_name() -> None:
    """Раздел без имени нарушает domain invariant."""
    with pytest.raises(
        NormativeCatalogError,
        match="Название раздела",
    ):
        NormativeSection(
            section_id=uuid4(),
            name="   ",
            system_prompt="Prompt",
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )


def test_category_rejects_self_parent() -> None:
    """Категория не может непосредственно ссылаться сама на себя."""
    category_id = uuid4()

    with pytest.raises(
        NormativeCatalogError,
        match="родителем самой себя",
    ):
        NormativeCategory(
            category_id=category_id,
            section_id=uuid4(),
            parent_id=category_id,
            name="ГОСТ",
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )


def test_document_rejects_invalid_sha256() -> None:
    """Документ требует корректный SHA-256."""
    with pytest.raises(
        NormativeCatalogError,
        match="SHA-256",
    ):
        NormativeDocument(
            document_id=uuid4(),
            section_id=uuid4(),
            category_id=None,
            original_name="document.pdf",
            storage_key="document.pdf",
            mime_type="application/pdf",
            size_bytes=100,
            sha256="invalid",
            index_status=IndexingStatus.UPLOADED,
            index_error=None,
            indexed_at=None,
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )


def test_document_indexing_happy_path() -> None:
    """Документ проходит uploaded -> queued -> indexing -> ready."""
    document = make_document()

    queued = document.transition_indexing(
        target_status=IndexingStatus.QUEUED,
        changed_at=BASE_TIME
        + timedelta(
            minutes=1,
        ),
    )

    indexing = queued.transition_indexing(
        target_status=IndexingStatus.INDEXING,
        changed_at=BASE_TIME
        + timedelta(
            minutes=2,
        ),
    )

    ready_at = BASE_TIME + timedelta(
        minutes=3,
    )

    ready = indexing.transition_indexing(
        target_status=IndexingStatus.READY,
        changed_at=ready_at,
    )

    assert document.ready_for_analysis is False
    assert queued.index_status is IndexingStatus.QUEUED
    assert indexing.index_status is IndexingStatus.INDEXING

    assert ready.index_status is IndexingStatus.READY
    assert ready.ready_for_analysis is True
    assert ready.index_error is None
    assert ready.indexed_at == ready_at


def test_failed_document_can_be_retried() -> None:
    """Failed document допускает повторную постановку в очередь."""
    document = make_document()

    queued = document.transition_indexing(
        target_status=IndexingStatus.QUEUED,
        changed_at=BASE_TIME
        + timedelta(
            minutes=1,
        ),
    )

    indexing = queued.transition_indexing(
        target_status=IndexingStatus.INDEXING,
        changed_at=BASE_TIME
        + timedelta(
            minutes=2,
        ),
    )

    failed = indexing.transition_indexing(
        target_status=IndexingStatus.FAILED,
        changed_at=BASE_TIME
        + timedelta(
            minutes=3,
        ),
        error="Ollama embedding request failed.",
    )

    retried = failed.transition_indexing(
        target_status=IndexingStatus.QUEUED,
        changed_at=BASE_TIME
        + timedelta(
            minutes=4,
        ),
    )

    assert failed.index_status is IndexingStatus.FAILED
    assert failed.index_error == "Ollama embedding request failed."
    assert failed.ready_for_analysis is False

    assert retried.index_status is IndexingStatus.QUEUED
    assert retried.index_error is None
    assert retried.indexed_at is None


def test_document_rejects_invalid_indexing_transition() -> None:
    """Нельзя объявить только загруженный документ сразу ready."""
    document = make_document()

    with pytest.raises(
        NormativeCatalogError,
        match="uploaded -> ready",
    ):
        document.transition_indexing(
            target_status=IndexingStatus.READY,
            changed_at=BASE_TIME
            + timedelta(
                minutes=1,
            ),
        )


def test_deleting_state_is_terminal() -> None:
    """После начала удаления lifecycle документа не продолжается."""
    document = make_document()

    deleting = document.transition_indexing(
        target_status=IndexingStatus.DELETING,
        changed_at=BASE_TIME
        + timedelta(
            minutes=1,
        ),
    )

    with pytest.raises(
        NormativeCatalogError,
        match="deleting -> queued",
    ):
        deleting.transition_indexing(
            target_status=IndexingStatus.QUEUED,
            changed_at=BASE_TIME
            + timedelta(
                minutes=2,
            ),
        )


def test_document_can_move_to_category() -> None:
    """Документ можно перемещать между категориями каталога."""
    document = make_document()

    category_id = uuid4()

    moved = document.moved_to_category(
        category_id=category_id,
        changed_at=BASE_TIME
        + timedelta(
            minutes=1,
        ),
    )

    assert document.category_id is None
    assert moved.category_id == category_id


def test_entity_change_cannot_move_time_backwards() -> None:
    """Изменение entity не допускает timestamp раньше updated_at."""
    section = make_section()

    with pytest.raises(
        NormativeCatalogError,
        match="changed_at",
    ):
        section.renamed(
            name="Новое название",
            changed_at=BASE_TIME
            - timedelta(
                seconds=1,
            ),
        )
