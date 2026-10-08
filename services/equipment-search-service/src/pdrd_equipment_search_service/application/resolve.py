# services/equipment-search-service/src/pdrd_equipment_search_service/application/resolve.py

"""Поиск документации по уникальной подтверждённой модели оборудования."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from pdrd_equipment_search_service.application.ports import (
    DocumentDownloader,
    DocumentInspector,
    EquipmentCatalog,
    EquipmentFactIndex,
    EquipmentVisionInspector,
    WebSearch,
)
from pdrd_equipment_search_service.application.search_budget import (
    EquipmentSearchBudget,
)
from pdrd_equipment_search_service.application.vision_budget import (
    EquipmentVisionBudget,
)
from pdrd_equipment_search_service.core.observability import log_execution_time
from pdrd_equipment_search_service.domain.equipment import (
    EquipmentIdentity,
    ResolveResult,
    SearchHit,
    SourceStatus,
    safe_search_term,
)


@dataclass(frozen=True, slots=True)
class SearchLimits:
    """Бюджет операций Equipment Search одного задания."""

    queries_per_stage: int = 2
    urls_per_query: int = 8
    downloads: int = 3
    total_queries: int = 8
    vision_calls: int = 2


class SearchCancelled(Exception):
    """Остановка до запуска следующей затратной операции."""


async def _check_stop(should_stop: Callable[[], Awaitable[bool]] | None) -> None:
    """Не допускает новых внешних операций после отмены задания."""
    if should_stop is not None and await should_stop():
        raise SearchCancelled


@dataclass(slots=True)
class ResolveEquipment:
    """Сначала использует локальный каталог, затем строго ограниченный поиск."""

    catalog: EquipmentCatalog
    search: WebSearch
    downloader: DocumentDownloader
    inspector: DocumentInspector
    limits: SearchLimits = SearchLimits()
    fact_index: EquipmentFactIndex | None = None
    vision_inspector: EquipmentVisionInspector | None = None

    @log_execution_time(operation="equipment.resolve")
    async def execute(
        self,
        identity: EquipmentIdentity,
        *,
        allow_unverified: bool = False,
        should_stop: Callable[[], Awaitable[bool]] | None = None,
        vision_budget: EquipmentVisionBudget | None = None,
        search_budget: EquipmentSearchBudget | None = None,
    ) -> ResolveResult:
        """Ищет применимый документ без передачи текста проекта во внешний поиск."""
        if not identity.searchable:
            return ResolveResult(
                identity,
                "needs_review",
                warning="Модель или производитель не подтверждены.",
            )

        vision_budget = vision_budget or EquipmentVisionBudget(self.limits.vision_calls)
        search_budget = search_budget or EquipmentSearchBudget(
            self.limits.total_queries, self.limits.downloads
        )
        await _check_stop(should_stop)
        local = await self.catalog.find_document(identity)
        if local is not None and await self._local_allowed(
            identity,
            local.final_url,
            allow_unverified,
        ):
            if self.fact_index is None:
                return ResolveResult(identity, "local", document=local)
            try:
                pages = await self.catalog.document_pages(local.source_id)
                visual = await self.catalog.document_vision_facts(local.source_id)
                fact_status, facts = await self.fact_index.extract_or_get(
                    identity,
                    local,
                    pages,
                    vision_facts=visual,
                )
                return ResolveResult(
                    identity,
                    "local",
                    document=local,
                    facts=facts,
                    facts_status=fact_status,
                )
            except Exception:
                return ResolveResult(
                    identity,
                    "incomplete",
                    document=local,
                    warning="EQ Facts сохранённого документа недоступны.",
                    facts_status="incomplete",
                )

        hosts = await self.catalog.trusted_hosts(identity.manufacturer)
        tried: set[str] = set()
        downloads = 0
        queries = 0
        warning = ""

        # Общий поиск сохраняет минимум один запрос после доверенных доменов.
        general_reserved = min(2, self.limits.total_queries)
        trusted_budget = self.limits.total_queries - general_reserved
        for host in hosts:
            for query in self._queries(identity, host):
                await _check_stop(should_stop)
                if queries >= trusted_budget:
                    break
                if not search_budget.claim_query():
                    warning = "Исчерпан общий бюджет поисковых запросов."
                    break
                queries += 1
                try:
                    hits = await self.search.search(query, self.limits.urls_per_query)
                except Exception:
                    warning = "Поиск документации временно недоступен."
                    continue
                result, downloads, inspection_warning = await self._try_hits(
                    identity,
                    hits,
                    tried,
                    downloads,
                    allow_unverified,
                    should_stop,
                    vision_budget,
                    search_budget,
                )
                if result is not None:
                    return result
                if inspection_warning:
                    warning = inspection_warning

        for query in self._queries(identity, None):
            await _check_stop(should_stop)
            if queries >= self.limits.total_queries:
                break
            if not search_budget.claim_query():
                warning = "Исчерпан общий бюджет поисковых запросов."
                break
            queries += 1
            try:
                hits = await self.search.search(query, self.limits.urls_per_query)
            except Exception:
                warning = "Поиск документации временно недоступен."
                continue
            result, downloads, inspection_warning = await self._try_hits(
                identity,
                hits,
                tried,
                downloads,
                allow_unverified,
                should_stop,
                vision_budget,
                search_budget,
            )
            if result is not None:
                return result
            if inspection_warning:
                warning = inspection_warning

        return ResolveResult(
            identity,
            "not_found" if not warning else "incomplete",
            warning=warning or "Применимая документация не найдена.",
            searched_urls=len(tried),
        )

    async def _local_allowed(
        self,
        identity: EquipmentIdentity,
        final_url: str,
        allow_unverified: bool,
    ) -> bool:
        """Повторно проверяет текущее доверие к сохранённому источнику."""
        host = SearchHit(final_url).hostname
        source = await self.catalog.source_for(identity.manufacturer, host)
        if source is None or not source.enabled:
            return False
        if source.status is SourceStatus.BLOCKED:
            return False
        return source.matches(identity.manufacturer, host) and (
            source.status is SourceStatus.TRUSTED or allow_unverified
        )

    def _queries(
        self, identity: EquipmentIdentity, hostname: str | None
    ) -> tuple[str, ...]:
        """Формирует только нейтральные запросы из маркировки оборудования."""
        parts = [
            safe_search_term(identity.manufacturer),
            safe_search_term(identity.model),
            safe_search_term(identity.variant),
        ]
        base = " ".join(part for part in parts if part)
        suffix = f" site:{hostname}" if hostname else ""
        return (
            f"{base} datasheet manual{suffix}",
            f"{base} паспорт руководство{suffix}",
        )[: self.limits.queries_per_stage]

    async def _try_hits(
        self,
        identity: EquipmentIdentity,
        hits: tuple[SearchHit, ...],
        tried: set[str],
        downloads: int,
        allow_unverified: bool,
        should_stop: Callable[[], Awaitable[bool]] | None,
        vision_budget: EquipmentVisionBudget,
        search_budget: EquipmentSearchBudget,
    ) -> tuple[ResolveResult | None, int, str]:
        """Проверяет доверие, скачивает и валидирует ограниченные кандидаты."""
        inspection_warning = ""
        for hit in hits[: self.limits.urls_per_query]:
            await _check_stop(should_stop)
            if downloads >= self.limits.downloads:
                break
            if not hit.hostname or hit.url in tried:
                continue
            tried.add(hit.url)
            source = await self.catalog.source_for(identity.manufacturer, hit.hostname)
            if source is None:
                await self.catalog.register_pending(
                    identity.manufacturer, hit.hostname, hit.url, identity.model
                )
            if source is not None and (
                source.status is SourceStatus.BLOCKED or not source.enabled
            ):
                continue
            trusted = bool(
                source is not None
                and source.matches(identity.manufacturer, hit.hostname)
                and source.status is SourceStatus.TRUSTED
            )
            if not trusted and not allow_unverified:
                continue

            allow_http = bool(source is not None and source.allow_http)
            if urlsplit(hit.url).scheme != "https" and not allow_http:
                continue
            await _check_stop(should_stop)
            if not search_budget.claim_download():
                inspection_warning = "Исчерпан общий бюджет загрузки документов."
                break
            downloads += 1
            try:
                document = await self.downloader.download(
                    hit.url,
                    allow_http=allow_http,
                )
                final_host = SearchHit(document.final_url).hostname
                final_source = await self.catalog.source_for(
                    identity.manufacturer,
                    final_host,
                )
                if final_source is not None and (
                    final_source.status is SourceStatus.BLOCKED
                    or not final_source.enabled
                ):
                    continue
                final_trusted = bool(
                    final_source is not None
                    and final_source.matches(identity.manufacturer, final_host)
                    and final_source.status is SourceStatus.TRUSTED
                )
                if not final_trusted and not allow_unverified:
                    continue
                await _check_stop(should_stop)
                inspection = await self.inspector.inspect(identity, document)
                if inspection.requires_vision and self.vision_inspector is not None:
                    await _check_stop(should_stop)
                    inspection = await self.vision_inspector.inspect(
                        identity,
                        document,
                        inspection,
                        budget=vision_budget,
                        should_stop=should_stop,
                    )
                if not inspection.applicable:
                    if inspection.requires_vision:
                        inspection_warning = inspection.reason
                    continue
                trust_status = (
                    "trusted"
                    if trusted and final_trusted
                    else "unverified_user_override"
                )
                snapshot = await self.catalog.save_document(
                    identity,
                    hit.url,
                    document,
                    inspection,
                    trust_status,
                )
                fact_status = ""
                facts = ()
                if self.fact_index is not None:
                    await _check_stop(should_stop)
                    try:
                        fact_status, facts = await self.fact_index.extract_or_get(
                            identity,
                            snapshot,
                            inspection.pages,
                            vision_facts=inspection.vision_facts,
                        )
                    except Exception:
                        return (
                            ResolveResult(
                                identity,
                                "incomplete",
                                document=snapshot,
                                warning="EQ Facts документа недоступны.",
                                searched_urls=len(tried),
                                facts_status="incomplete",
                            ),
                            downloads,
                            inspection_warning,
                        )
                return (
                    ResolveResult(
                        identity,
                        "found",
                        document=snapshot,
                        searched_urls=len(tried),
                        facts=facts,
                        facts_status=fact_status,
                    ),
                    downloads,
                    inspection_warning,
                )
            except SearchCancelled:
                raise
            except Exception:
                inspection_warning = (
                    "Не удалось загрузить или обработать документацию производителя."
                )
                continue
        return None, downloads, inspection_warning
