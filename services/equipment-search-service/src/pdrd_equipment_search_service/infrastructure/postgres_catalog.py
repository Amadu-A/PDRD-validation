# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/postgres_catalog.py

"""Каталог точных доменов и неизменяемых версий документов в проектном PostgreSQL."""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DocumentSnapshot,
    DownloadedDocument,
    EquipmentIdentity,
    SearchHit,
    SourceDomain,
    SourceStatus,
    normalized_token,
)


def _snapshot(row) -> DocumentSnapshot:
    """Восстанавливает доменную ссылку на неизменяемую версию."""
    return DocumentSnapshot(
        source_id=row.source_id,
        manufacturer=row.manufacturer,
        model=row.model,
        variant=row.variant,
        source_url=row.source_url,
        final_url=row.final_url,
        sha256=row.sha256,
        storage_reference=row.storage_reference,
        revision=row.revision,
        trust_status=row.trust_status,
        revision_ambiguous=bool(getattr(row, "revision_ambiguous", False)),
        publication_date=getattr(row, "publication_date", "") or "",
        fetched_at=(
            row.fetched_at.isoformat()
            if getattr(row, "fetched_at", None) is not None
            else ""
        ),
    )


@dataclass(slots=True)
class PostgresEquipmentCatalog:
    """Владеет только схемой equipment_search и проектным volume документов."""

    engine: AsyncEngine
    storage_root: Path

    async def _canonical_key(self, manufacturer: str) -> str:
        """Разрешает официальное имя по alias без угадывания модели."""
        key = normalized_token(manufacturer)
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""
                        SELECT normalized_name
                        FROM equipment_search.manufacturers
                        WHERE enabled = true
                          AND (normalized_name = :key OR aliases ? :key)
                        LIMIT 1
                    """),
                    {"key": key},
                )
            ).first()
        return row.normalized_name if row else key

    async def find_document(
        self,
        identity: EquipmentIdentity,
    ) -> DocumentSnapshot | None:
        """Находит последний сохранённый snapshot точной модели и исполнения."""
        manufacturer_key = await self._canonical_key(identity.manufacturer)
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""
                        SELECT d.source_id, d.manufacturer, d.model, d.variant,
                               d.source_url, d.final_url, d.sha256,
                               d.storage_reference, d.revision, d.trust_status,
                               d.publication_date, d.fetched_at,
                               EXISTS (
                                   SELECT 1 FROM equipment_search.documents AS prior
                                   WHERE prior.manufacturer_key = d.manufacturer_key
                                     AND prior.model_key = d.model_key
                                     AND prior.variant_key = d.variant_key
                                     AND prior.sha256 <> d.sha256
                               ) AS revision_ambiguous
                        FROM equipment_search.documents AS d
                        WHERE d.manufacturer_key = :manufacturer_key
                          AND d.model_key = :model_key
                          AND d.variant_key = :variant_key
                        ORDER BY d.fetched_at DESC LIMIT 1
                    """),
                    {
                        "manufacturer_key": manufacturer_key,
                        "model_key": identity.key[1],
                        "variant_key": identity.key[2],
                    },
                )
            ).first()
        return _snapshot(row) if row else None

    async def source_for(
        self,
        manufacturer: str,
        hostname: str,
    ) -> SourceDomain | None:
        """Проверяет только точный hostname указанного производителя."""
        manufacturer_key = await self._canonical_key(manufacturer)
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""
                        SELECT status, enabled, allow_http
                        FROM equipment_search.source_domains
                        WHERE manufacturer_key = :manufacturer_key
                          AND hostname = :hostname
                    """),
                    {
                        "manufacturer_key": manufacturer_key,
                        "hostname": hostname.casefold().rstrip("."),
                    },
                )
            ).first()
        return (
            SourceDomain(
                manufacturer,
                hostname,
                SourceStatus(row.status),
                enabled=row.enabled,
                allow_http=row.allow_http,
            )
            if row
            else None
        )

    async def trusted_hosts(self, manufacturer: str) -> tuple[str, ...]:
        """Возвращает ограниченный набор явно подтверждённых hostnames."""
        manufacturer_key = await self._canonical_key(manufacturer)
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("""
                        SELECT hostname FROM equipment_search.source_domains
                        WHERE manufacturer_key = :manufacturer_key
                          AND status = 'trusted' AND enabled = true
                        ORDER BY hostname LIMIT 20
                    """),
                    {"manufacturer_key": manufacturer_key},
                )
            ).all()
        return tuple(row.hostname for row in rows)

    async def register_pending(
        self,
        manufacturer: str,
        hostname: str,
        example_url: str,
        model: str,
    ) -> None:
        """Регистрирует неизвестный hostname как pending с первой причиной."""
        normalized = await self._canonical_key(manufacturer)
        source_id = uuid4()
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO equipment_search.manufacturers
                    (id, name, normalized_name, aliases, enabled,
                     registration_source, created_at)
                    VALUES (:id, :name, :normalized_name,
                            CAST('[]' AS JSONB), true, 'web_search', :now)
                    ON CONFLICT (normalized_name) DO NOTHING
                """),
                {
                    "id": uuid4(),
                    "name": manufacturer,
                    "normalized_name": normalized,
                    "now": datetime.now(UTC),
                },
            )
            row = (
                await connection.execute(
                    text("""
                        INSERT INTO equipment_search.source_domains
                        (id, manufacturer_key, hostname, status, enabled,
                         allow_http, registration_source, example_url,
                         example_model, updated_at, updated_by)
                        VALUES (:id, :manufacturer_key, :hostname, 'pending',
                                true, false, 'web_search', :example_url,
                                :example_model, :now, 'system')
                        ON CONFLICT (manufacturer_key, hostname) DO NOTHING
                        RETURNING id
                    """),
                    {
                        "id": source_id,
                        "manufacturer_key": normalized,
                        "hostname": hostname.casefold().rstrip("."),
                        "example_url": example_url[:2000],
                        "example_model": model[:200],
                        "now": datetime.now(UTC),
                    },
                )
            ).first()
            if row:
                await connection.execute(
                    text("""
                        INSERT INTO equipment_search.source_audit
                        (id, source_domain_id, previous_status, new_status,
                         previous_enabled, new_enabled,
                         previous_allow_http, new_allow_http,
                         reason, actor, occurred_at)
                        VALUES (:id, :source_domain_id, NULL, 'pending',
                                NULL, true, NULL, false,
                                'Найдено при поиске документации',
                                'system', :now)
                    """),
                    {
                        "id": uuid4(),
                        "source_domain_id": source_id,
                        "now": datetime.now(UTC),
                    },
                )

    async def document_pages(
        self,
        source_id: str,
    ) -> tuple[tuple[int, str], ...]:
        """Возвращает страницы immutable snapshot для EQ Facts cache."""
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""
                        SELECT extracted_pages
                        FROM equipment_search.documents
                        WHERE source_id = :source_id
                    """),
                    {"source_id": source_id},
                )
            ).first()
        if row is None:
            raise ValueError("Snapshot документа не найден.")
        return tuple((int(page), str(content)) for page, content in row.extracted_pages)

    async def document_vision_facts(self, source_id: str) -> tuple[dict, ...]:
        """Читает исходные визуальные строки snapshot без повторного VLM."""
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT vision_facts FROM equipment_search.documents WHERE source_id = :source_id"
                    ),
                    {"source_id": source_id},
                )
            ).first()
        if row is None:
            raise ValueError("Snapshot документа не найден.")
        return tuple(row.vision_facts)

    async def save_document(
        self,
        identity: EquipmentIdentity,
        source_url: str,
        downloaded: DownloadedDocument,
        inspection: DocumentInspection,
        trust_status: str,
    ) -> DocumentSnapshot:
        """Сохраняет байты по SHA-256 и новую версию при изменении документа."""
        if not inspection.applicable:
            raise ValueError("Неприменимый документ нельзя сохранить.")
        reference = self._write_content(downloaded)
        manufacturer_key = await self._canonical_key(identity.manufacturer)
        source_id = f"EQ-{uuid4().hex}"
        async with self.engine.begin() as connection:
            source_row = (
                await connection.execute(
                    text("""
                        SELECT id FROM equipment_search.source_domains
                        WHERE manufacturer_key = :manufacturer_key
                          AND hostname = :hostname AND enabled = true
                          AND status != 'blocked'
                    """),
                    {
                        "manufacturer_key": manufacturer_key,
                        "hostname": SearchHit(downloaded.final_url).hostname,
                    },
                )
            ).first()
            if source_row is None:
                raise ValueError("Источник документа больше не разрешён.")
            await connection.execute(
                text("""
                    INSERT INTO equipment_search.documents
                    (id, source_id, source_domain_id, manufacturer_key, manufacturer,
                     model_key, model, variant_key, variant, source_url,
                     final_url, sha256, media_type, storage_reference,
                     revision, publication_date, trust_status, fetched_at,
                     extracted_pages, vision_facts)
                    VALUES (:id, :source_id, :source_domain_id, :manufacturer_key, :manufacturer,
                            :model_key, :model, :variant_key, :variant,
                            :source_url, :final_url, :sha256, :media_type,
                            :storage_reference, :revision, :publication_date,
                            :trust_status, :fetched_at,
                            CAST(:extracted_pages AS JSONB), CAST(:vision_facts AS JSONB))
                    ON CONFLICT (manufacturer_key, model_key, variant_key, sha256)
                    DO NOTHING
                """),
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "source_domain_id": source_row.id,
                    "manufacturer_key": manufacturer_key,
                    "manufacturer": identity.manufacturer,
                    "model_key": identity.key[1],
                    "model": identity.model,
                    "variant_key": identity.key[2],
                    "variant": identity.variant,
                    "source_url": source_url,
                    "final_url": downloaded.final_url,
                    "sha256": downloaded.sha256,
                    "media_type": downloaded.media_type,
                    "storage_reference": reference,
                    "revision": inspection.revision,
                    "publication_date": inspection.publication_date,
                    "trust_status": trust_status,
                    "fetched_at": datetime.now(UTC),
                    "extracted_pages": json.dumps(inspection.pages, ensure_ascii=False),
                    "vision_facts": json.dumps(
                        inspection.vision_facts, ensure_ascii=False
                    ),
                },
            )
            row = (
                await connection.execute(
                    text("""
                        SELECT d.source_id, d.manufacturer, d.model, d.variant,
                               d.source_url, d.final_url, d.sha256,
                               d.storage_reference, d.revision, d.trust_status,
                               d.publication_date, d.fetched_at,
                               EXISTS (
                                   SELECT 1 FROM equipment_search.documents AS prior
                                   WHERE prior.manufacturer_key = d.manufacturer_key
                                     AND prior.model_key = d.model_key
                                     AND prior.variant_key = d.variant_key
                                     AND prior.sha256 <> d.sha256
                               ) AS revision_ambiguous
                        FROM equipment_search.documents AS d
                        WHERE d.manufacturer_key = :manufacturer_key
                          AND d.model_key = :model_key
                          AND d.variant_key = :variant_key
                          AND d.sha256 = :sha256
                    """),
                    {
                        "manufacturer_key": manufacturer_key,
                        "model_key": identity.key[1],
                        "variant_key": identity.key[2],
                        "sha256": downloaded.sha256,
                    },
                )
            ).one()
        return _snapshot(row)

    async def load_document(
        self,
        source_id: str,
    ) -> tuple[DocumentSnapshot, bytes, str]:
        """Читает точный сохранённый snapshot и повторно проверяет его SHA-256."""
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""
                        SELECT source_id, manufacturer, model, variant,
                               source_url, final_url, sha256, storage_reference,
                               revision, trust_status, media_type,
                               publication_date, fetched_at
                        FROM equipment_search.documents
                        WHERE source_id = :source_id
                    """),
                    {"source_id": source_id},
                )
            ).first()
        if row is None:
            raise LookupError("Snapshot документа не найден.")
        if row.media_type not in {"application/pdf", "text/html"}:
            raise ValueError("Недопустимый формат snapshot.")
        suffix = ".pdf" if row.media_type == "application/pdf" else ".html"
        expected = Path(row.sha256[:2]) / f"{row.sha256}{suffix}"
        if Path(row.storage_reference) != expected:
            raise ValueError("Некорректная ссылка на snapshot.")
        path = self.storage_root / expected
        content = path.read_bytes()
        if sha256(content).hexdigest() != row.sha256:
            raise ValueError("Контрольная сумма snapshot не совпала.")
        return _snapshot(row), content, row.media_type

    def _write_content(self, document: DownloadedDocument) -> str:
        """Записывает immutable bytes в volume без перезаписи другой версии."""
        suffix = ".pdf" if document.media_type == "application/pdf" else ".html"
        relative = Path(document.sha256[:2]) / f"{document.sha256}{suffix}"
        target = self.storage_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as stream:
                    stream.write(document.content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return relative.as_posix()
