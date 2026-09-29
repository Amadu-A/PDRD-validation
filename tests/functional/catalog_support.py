# tests/functional/catalog_support.py

"""Тестовые порты каталога для HTTP-проверок; настоящие транзакции проверяются отдельно."""

from dataclasses import replace
from datetime import UTC, datetime

from pdrd_experience_service.application.catalog_snapshot import example_to_json
from pdrd_experience_service.domain.area_confirmation import content_signature
from pdrd_experience_service.domain.catalog import CatalogEntry
from pdrd_experience_service.domain.catalog_identity import content_key
from pdrd_experience_service.domain.catalog_repair import repair_example
from pdrd_experience_service.domain.experience_selection import ConfirmedFindingArea
from pdrd_experience_service.domain.review import Origin, ReviewConflictError

from tests.functional.reviewed_pdf_support import MemoryAreas


class CatalogAreas(MemoryAreas):
    """Дополняет уже проверенный CAS-порт чтением пригодных областей."""

    async def load_confirmed(self, *, job_id):
        """Отбрасывает отозванные и устаревшие подписи, как реальный SQL-адаптер."""
        review = await self.reviews.load(job_id)
        review.accepted_for_pdf()
        statuses = await self.load_status(review=review)
        result = []
        for status in statuses:
            if status.valid:
                area = self.rows[status.finding_id][0]
                result.append(
                    ConfirmedFindingArea(
                        area.job_id,
                        area.finding_id,
                        area.page_number,
                        area.regions,
                        area.confirmed_by,
                        area.confirmed_at,
                    )
                )
        return tuple(result)


class MemoryCatalog:
    """Идемпотентность и CAS в HTTP-фикстуре, без имитации SQL синтаксиса."""

    def __init__(self, reviews, areas):
        """Источник Review и подтверждений остаётся отдельным портом."""
        self.reviews, self.areas = reviews, areas
        self.examples, self.events = {}, {}
        self.occurrences = {}

    async def _entry(self, example):
        """Актуальность определяется текущим источником и подписью, а не UI."""
        source = example.source
        review = await self.reviews.load(source.job_id)
        current = (
            review is not None
            and review.revision == source.approved_revision
            and review.approved_revision == review.revision
        )
        if (
            current
            and source.origin is Origin.VLM
            and source.issue_regions
            and source.area_source == "engineer_confirmed"
        ):
            area = self.areas.rows.get(source.finding_id)
            finding = next(
                item for item in review.findings if item.finding_id == source.finding_id
            )
            current = bool(
                area
                and area[2]
                and area[0].content_signature == content_signature(review, finding)
                and area[0].confirmed_at == source.confirmed_at
            )
        return CatalogEntry(example, current)

    async def capture(
        self, *, review, area_versions, examples, actor, expected_revisions=()
    ):
        """Фикстура проверяет тот же ожидаемый набор версий перед вставкой."""
        if await self.reviews.load(review.job_id) != review:
            raise ReviewConflictError("Stale Review.")
        statuses = await self.areas.load_status(review=review)
        if (
            tuple((area.finding_id, area.revision) for area in statuses)
            != area_versions
        ):
            raise ReviewConflictError("Stale areas.")
        created = 0
        repaired = []
        if any(
            self.examples[key].revision != revision or self.examples[key].deleted
            for key, revision in expected_revisions
        ):
            raise ReviewConflictError("Stale catalog.")
        for example in examples:
            prior = next(
                (
                    item
                    for item in self.examples.values()
                    if item.id == example.id
                    or content_key(item.source) == content_key(example.source)
                ),
                None,
            )
            if prior is not None:
                keep_confirmation = bool(prior.source.issue_regions) and (
                    prior.source.area_source == "engineer_confirmed"
                    and example.source.area_source == "unlocated"
                    and prior.source.decision.value == "accepted"
                )
                updated = repair_example(prior, example, actor)
                if updated != prior:
                    self.examples[prior.id] = updated
                    self.events[prior.id].append(
                        {
                            "revision": updated.revision,
                            "actor": actor,
                            "occurred_at": updated.updated_at.isoformat(),
                            "snapshot": example_to_json(updated),
                        }
                    )
                    repaired.append(
                        {
                            "id": str(prior.id),
                            "previous_revision": prior.revision,
                            "revision": updated.revision,
                        }
                    )
                if not keep_confirmation:
                    self.occurrences.setdefault(prior.id, []).append(example.source)
                continue
            if example.id not in self.examples:
                self.examples[example.id] = example
                self.events[example.id] = [
                    {
                        "revision": 0,
                        "actor": actor,
                        "occurred_at": example.created_at.isoformat(),
                        "snapshot": example_to_json(example),
                    }
                ]
                created += 1
        return {
            "created": created,
            "job_id": str(review.job_id),
            "revision": review.revision,
            "repaired": repaired,
        }

    async def find_source(self, source):
        """Находит старый снимок по утверждённому происхождению либо содержимому."""
        for item in self.examples.values():
            known = (item.source, *self.occurrences.get(item.id, ()))
            if content_key(item.source) == content_key(source) or any(
                (candidate.job_id, candidate.finding_id, candidate.approved_revision)
                == (source.job_id, source.finding_id, source.approved_revision)
                for candidate in known
            ):
                return await self._entry(item)
        return None

    async def get(self, example_id):
        """Возвращает только существующий пример."""
        record = self.examples.get(example_id)
        return await self._entry(record) if record else None

    async def scan(self, *, after, limit):
        """Обходит все строки по UUID, включая исключённые из индекса."""
        examples = sorted(self.examples.values(), key=lambda example: example.id)
        page = [example for example in examples if after is None or example.id > after][
            :limit
        ]
        return tuple([await self._entry(example) for example in page])

    async def get_many(self, example_ids):
        """Проверяет набор идентификаторов независимо от порядка в запросе."""
        return tuple(
            [
                await self._entry(example)
                for example_id in example_ids
                if (example := self.examples.get(example_id)) is not None
            ]
        )

    async def list(self, criteria):
        """HTTP проверяет передачу критериев, реальные SQL-фильтры проверяет PostgreSQL."""
        result = []
        for example in self.examples.values():
            entry = await self._entry(example)
            tag, _, decision = criteria.tag.partition(":")
            haystack = " ".join(
                [
                    example.text,
                    example.document_title,
                    example.normative_basis,
                    example.source.source_filename,
                ]
            ).lower()
            if (
                example.deleted
                or (criteria.section_id and example.section_id != criteria.section_id)
                or (criteria.query and criteria.query.lower() not in haystack)
                or (tag and example.tag != tag)
                or (decision and example.source.decision.value != decision)
                or (
                    criteria.decision
                    and example.source.decision.value != criteria.decision
                )
                or (criteria.active is not None and entry.active != criteria.active)
                or (
                    criteria.learning_use
                    and example.learning_use != criteria.learning_use
                )
                or (criteria.job_id and example.source.job_id != criteria.job_id)
            ):
                continue
            result.append(entry)
        result.sort(key=lambda entry: (entry.example.created_at, str(entry.example.id)))
        return tuple(result[criteria.offset : criteria.offset + criteria.limit]), len(
            result
        )

    async def update(self, *, example, expected_revision):
        """Старая редакция не перезаписывает новую."""
        previous = self.examples[example.id]
        if previous.revision != expected_revision:
            raise ReviewConflictError("Stale example.")
        self.examples[example.id] = example
        self.events[example.id].append(
            {
                "revision": example.revision,
                "actor": example.curated_by,
                "occurred_at": example.updated_at.isoformat(),
                "snapshot": example_to_json(example),
            }
        )
        return await self._entry(example)

    async def history(self, example_id):
        """История фиксирует все редакции без изменения старого снимка."""
        return tuple(self.events[example_id])

    async def delete_many(self, *, references, actor):
        """Сверяет весь набор до изменения какой-либо строки."""
        if any(item not in self.examples for item, _ in references):
            raise LookupError("Missing examples.")
        if any(
            self.examples[item].revision != revision or self.examples[item].deleted
            for item, revision in references
        ):
            raise ReviewConflictError("Stale selection.")
        for item, revision in references:
            revised = replace(
                self.examples[item],
                deleted=True,
                active=False,
                revision=revision + 1,
                updated_at=datetime.now(UTC),
                curated_by=actor,
            )
            await self.update(example=revised, expected_revision=revision)
        return len(references)
