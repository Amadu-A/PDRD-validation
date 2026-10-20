# services/knowledge-service/tests/atomic_normative_support.py

"""Транзакционные имитации атомарного каталога: commit, rollback и отказ отдельной порции."""

import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
)
from pdrd_knowledge_service.domain.normative_indexing import NormativeTextPage

DOCUMENT_ID = UUID("dedecd3b-9a58-4cbb-989c-b8d2abbb9c90")
ACTOR_ID = UUID("e73c7b95-8ae9-45da-aa9e-d8f8b851aff1")
SOURCE = b"native normative source"
PAGES = (
    NormativeTextPage(
        1,
        "СП 99.13330.2026\n5 Требования\n5.1 Для насоса следует предусматривать резерв. "
        "Следует предусматривать сигнализацию.\n"
        "5.2 При расходе более 10 л/с необходимо выполнить расчёт",
    ),
    NormativeTextPage(
        2,
        "обоснования, кроме установок временного назначения.\n5.3 Рекомендуется применять унификацию.",
    ),
)


def document(**changes):
    """Готовый файл имеет проверяемый SHA; READY не задаёт нормативного статуса."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return NormativeDocument(
        **{
            "document_id": DOCUMENT_ID,
            "section_id": UUID("bf7ad170-cf89-4d2d-a7d5-71eae690c122"),
            "category_id": None,
            "original_name": "normative.pdf",
            "storage_key": "source.pdf",
            "mime_type": "application/pdf",
            "size_bytes": len(SOURCE),
            "sha256": hashlib.sha256(SOURCE).hexdigest(),
            "index_status": IndexingStatus.READY,
            "index_error": None,
            "indexed_at": now,
            "created_at": now,
            "updated_at": now,
            **changes,
        }
    )


def proof_payload(**changes):
    """Только фиктивное свидетельство для теста механизма, не доказательство статуса реального СП."""
    return {
        "document_code": "СП 99.13330.2026",
        "edition": "2026",
        "edition_status": "current",
        "requirement_binding": "mandatory",
        "clause_scope": ["5.1"],
        "applicability_scope": "Насосные установки",
        "source_url": "https://example.org/status-record",
        "source_quote": "Стендовое свидетельство для пункта 5.1.",
        "confirmed_on": "2026-01-01",
        "valid_from": "2026-01-01",
        "valid_until": "2026-12-31",
        **changes,
    }


@dataclass
class State:
    """Общее хранилище; изменения рабочей копии становятся видимыми только после commit."""

    documents: dict = field(default_factory=lambda: {str(DOCUMENT_ID): document()})
    generations: dict = field(default_factory=dict)
    rows: dict = field(default_factory=dict)
    proofs: dict = field(default_factory=dict)
    outbox: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    fail_batch: int | None = None
    writes: int = 0
    deleting: bool = False


class Repository:
    """Реализует документ, раздел, outbox и атомарный репозиторий одной рабочей копии."""

    def __init__(self, uow):
        """Сохраняет рабочую транзакцию."""
        self.uow = uow

    async def get(self, entity_id):
        """Раздел моделируется как существующий; документы изолируются по UUID."""
        return self.uow.data["documents"].get(str(entity_id))

    async def get_for_update(self, entity_id):
        """UOW удерживает общий async lock до закрытия транзакции."""
        return await self.get(entity_id)

    async def get_generation(self, generation_id):
        """Возвращает копию строки поколения."""
        return copy.deepcopy(self.uow.data["generations"].get(generation_id))

    async def active_generation(self, document_id):
        """Выбирает единственное опубликованное поколение текущего документа."""
        return next(
            (
                copy.deepcopy(row)
                for row in self.uow.data["generations"].values()
                if row["document_id"] == document_id and row["active"]
            ),
            None,
        )

    async def save_generation(self, generation):
        """Изменения видимы вне UOW только после commit."""
        self.uow.data["generations"][generation["generation_id"]] = copy.deepcopy(
            generation
        )

    async def write_batch(self, generation_id, offset, requirements):
        """Отказ порции проверяет сохранность старого активного индекса."""
        self.uow.state.writes += 1
        if self.uow.state.writes == self.uow.state.fail_batch:
            raise RuntimeError("batch failure")
        rows = self.uow.data["rows"].setdefault(generation_id, {})
        for index, requirement in enumerate(requirements, offset):
            rows[requirement.requirement_id] = (
                index,
                json.loads(json.dumps(requirement.as_dict())),
            )

    async def publish(self, generation):
        """Смена указателя и нового lifecycle фиксируется одной транзакцией."""
        for old in self.uow.data["generations"].values():
            if old["document_id"] == generation["document_id"]:
                old["active"] = False
        await self.save_generation({**generation, "active": True, "state": "ready"})

    async def list_requirements(self, generation_id, offset, limit):
        """Повторяет стабильную SQL-сортировку по ordinal."""
        values = sorted(self.uow.data["rows"].get(generation_id, {}).values())
        return [copy.deepcopy(row) for _, row in values[offset : offset + limit]]

    async def count_requirements(self, generation_id):
        """Считает реальные строки, а не ожидаемый размер поколения."""
        return len(self.uow.data["rows"].get(generation_id, {}))

    async def status_evidence(self, document_id, source_sha256):
        """Доказательство не переносится на другой исходник."""
        return tuple(
            sorted(
                (
                    item
                    for item in self.uow.data["proofs"].values()
                    if item.document_id == document_id
                    and item.source_sha256 == source_sha256
                ),
                key=lambda item: item.evidence_id,
            )
        )

    async def add_status_evidence(self, evidence):
        """Журнал допускает только добавление и идемпотентный повтор."""
        self.uow.data["proofs"].setdefault(evidence.evidence_id, evidence)

    async def add(self, message):
        """Outbox входит в ту же транзакцию, что новое поколение."""
        self.uow.data["outbox"].append(message)


class UnitOfWork:
    """Откат незавершённой операции не требует ручного удаления строк."""

    def __init__(self, state):
        """Создаёт ещё не открытую транзакцию."""
        self.state = state
        self.documents = self.requirements = self.outbox = Repository(self)
        self.sections = SimpleNamespace(get=self.section)

    async def section(self, section_id):
        """Показывает tombstone раздела."""
        return SimpleNamespace(deleting=self.state.deleting)

    async def __aenter__(self):
        """Сериализует транзакции и снимает snapshot."""
        await self.state.lock.acquire()
        self.data = {
            name: copy.deepcopy(getattr(self.state, name))
            for name in ("documents", "generations", "rows", "proofs", "outbox")
        }
        return self

    async def __aexit__(self, *args):
        """Отбрасывает незакоммиченную копию."""
        self.state.lock.release()

    async def commit(self):
        """Атомарно публикует изменения в общее состояние."""
        for name, value in self.data.items():
            setattr(self.state, name, copy.deepcopy(value))


class Storage:
    """Возвращает неизменяемый источник; изменённые байты выявляет production SHA-проверка."""

    def __init__(self, content=SOURCE):
        """Позволяет проверить отказ при подмене файла."""
        self.content = content

    async def read(self, *, storage_key):
        """Не обращается к файловой системе."""
        return self.content


class Extractor:
    """Заменяет только PDF-адаптер, сохраняя настоящий доменный разбор."""

    def __init__(self, pages=PAGES, *, content=SOURCE):
        """Передаёт исходные страницы, не готовые требования."""
        self.pages = pages
        self.content = content
        self.calls = 0

    async def extract_pages(self, *, content):
        """Фиксирует соответствие извлекаемого содержимого."""
        assert content == self.content
        self.calls += 1
        return self.pages
