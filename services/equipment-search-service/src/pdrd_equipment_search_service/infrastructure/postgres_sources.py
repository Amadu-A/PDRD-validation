# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/postgres_sources.py

"""Администрирование точных источников с транзакционным аудитом решений."""

import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from pdrd_equipment_search_service.domain.equipment import normalized_token


def validate_hostname(hostname: str) -> str:
    """Разрешает только полный DNS hostname без схемы, wildcard и порта."""
    host = hostname.strip().casefold().rstrip(".")
    if (
        len(host) > 253
        or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?",
            host,
        )
        or "." not in host
        or ".." in host
        or any(
            len(part) > 63 or part.startswith("-") or part.endswith("-")
            for part in host.split(".")
        )
    ):
        raise ValueError("Требуется точный публичный DNS hostname.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise ValueError("IP-адрес нельзя регистрировать как доверенный hostname.")


@dataclass(slots=True)
class PostgresSourceManager:
    """Ведёт каталог и аудит без расширения доверия на поддомены."""

    engine: AsyncEngine

    async def list_sources(
        self,
        status: str | None = None,
        query: str = "",
    ) -> list[dict]:
        """Возвращает источники с причиной появления и числом использований."""
        if status is not None and status not in {"trusted", "pending", "blocked"}:
            raise ValueError("Недопустимый статус источника.")
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("""
                        SELECT s.id, m.name AS manufacturer, m.aliases,
                               s.hostname, s.status, s.enabled, s.allow_http,
                               s.registration_source, s.example_url,
                               s.example_model, s.updated_at, s.updated_by,
                               (SELECT count(*) FROM equipment_search.documents d
                                WHERE d.source_domain_id = s.id) AS uses_count
                        FROM equipment_search.source_domains s
                        JOIN equipment_search.manufacturers m
                          ON m.normalized_name = s.manufacturer_key
                        WHERE (:status IS NULL OR s.status = :status)
                          AND (:query = '' OR m.name ILIKE :pattern
                               OR s.hostname ILIKE :pattern)
                        ORDER BY m.name, s.hostname
                        LIMIT 500
                    """),
                    {
                        "status": status,
                        "query": query,
                        "pattern": f"%{query[:100]}%",
                    },
                )
            ).all()
        return [dict(row._mapping) for row in rows]

    async def list_manufacturers(self) -> list[dict]:
        """Возвращает производителей и нормализованные aliases."""
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("""
                        SELECT id, name, normalized_name, aliases, enabled,
                               registration_source, created_at
                        FROM equipment_search.manufacturers
                        ORDER BY name LIMIT 500
                    """)
                )
            ).all()
        return [dict(row._mapping) for row in rows]

    async def audit(self, source_id: UUID) -> list[dict]:
        """Возвращает историю изменения доверия и транспорта."""
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("""
                        SELECT previous_status, new_status,
                               previous_enabled, new_enabled,
                               previous_allow_http, new_allow_http,
                               reason, actor, occurred_at
                        FROM equipment_search.source_audit
                        WHERE source_domain_id = :source_id
                        ORDER BY occurred_at, id
                    """),
                    {"source_id": source_id},
                )
            ).all()
        return [dict(row._mapping) for row in rows]

    async def update_source(
        self,
        *,
        manufacturer: str,
        hostname: str,
        status: str,
        enabled: bool,
        allow_http: bool,
        reason: str,
        actor: str,
    ) -> dict:
        """Добавляет либо меняет решение и всегда пишет аудит."""
        if status not in {"trusted", "pending", "blocked"}:
            raise ValueError("Недопустимый статус источника.")
        if not manufacturer.strip() or not reason.strip() or not actor.strip():
            raise ValueError("Нужны производитель, причина и инициатор.")
        hostname = validate_hostname(hostname)
        key = normalized_token(manufacturer)
        now = datetime.now(UTC)
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    text("""
                        SELECT normalized_name FROM equipment_search.manufacturers
                        WHERE normalized_name = :key OR aliases ? :key
                        LIMIT 1
                    """),
                    {"key": key},
                )
            ).first()
            if row is not None:
                key = row.normalized_name
            else:
                await connection.execute(
                    text("""
                        INSERT INTO equipment_search.manufacturers
                        (id, name, normalized_name, aliases, enabled,
                         registration_source, created_at)
                        VALUES (:id, :name, :key, CAST(:aliases AS JSONB), true,
                                'manual', :now)
                    """),
                    {
                        "id": uuid4(),
                        "name": manufacturer.strip(),
                        "key": key,
                        "aliases": json.dumps([]),
                        "now": now,
                    },
                )
            previous = (
                await connection.execute(
                    text("""
                        SELECT id, status, enabled, allow_http
                        FROM equipment_search.source_domains
                        WHERE manufacturer_key = :key AND hostname = :hostname
                        FOR UPDATE
                    """),
                    {"key": key, "hostname": hostname},
                )
            ).first()
            source_id = previous.id if previous else uuid4()
            if previous:
                await connection.execute(
                    text("""
                        UPDATE equipment_search.source_domains
                        SET status = :status, enabled = :enabled,
                            allow_http = :allow_http, updated_at = :now,
                            updated_by = :actor
                        WHERE id = :id
                    """),
                    {
                        "id": source_id,
                        "status": status,
                        "enabled": enabled,
                        "allow_http": allow_http,
                        "now": now,
                        "actor": actor,
                    },
                )
            else:
                await connection.execute(
                    text("""
                        INSERT INTO equipment_search.source_domains
                        (id, manufacturer_key, hostname, status, enabled,
                         allow_http, registration_source, example_url,
                         example_model, updated_at, updated_by)
                        VALUES (:id, :key, :hostname, :status, :enabled,
                                :allow_http, 'manual', '', '', :now, :actor)
                    """),
                    {
                        "id": source_id,
                        "key": key,
                        "hostname": hostname,
                        "status": status,
                        "enabled": enabled,
                        "allow_http": allow_http,
                        "now": now,
                        "actor": actor,
                    },
                )
            await connection.execute(
                text("""
                    INSERT INTO equipment_search.source_audit
                    (id, source_domain_id, previous_status, new_status,
                     previous_enabled, new_enabled, previous_allow_http,
                     new_allow_http, reason, actor, occurred_at)
                    VALUES (:id, :source_id, :previous_status, :status,
                            :previous_enabled, :enabled, :previous_allow_http,
                            :allow_http, :reason, :actor, :now)
                """),
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "previous_status": previous.status if previous else None,
                    "status": status,
                    "previous_enabled": previous.enabled if previous else None,
                    "enabled": enabled,
                    "previous_allow_http": previous.allow_http if previous else None,
                    "allow_http": allow_http,
                    "reason": reason.strip()[:1000],
                    "actor": actor.strip()[:200],
                    "now": now,
                },
            )
        return {
            "id": source_id,
            "manufacturer": manufacturer.strip(),
            "hostname": hostname,
            "status": status,
            "enabled": enabled,
            "allow_http": allow_http,
            "updated_at": now,
            "updated_by": actor,
        }
