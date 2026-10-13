# services/equipment-search-service/src/pdrd_equipment_search_service/application/resolve.py

"""Поиск документации по уникальной подтверждённой модели оборудования."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from hashlib import sha256
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
from pdrd_equipment_search_service.domain.diagnostics import (
    DocumentOperationError,
    ReasonCode,
)
from pdrd_equipment_search_service.domain.equipment import (
    EquipmentIdentity,
    ResolveResult,
    SearchHit,
    SourceDomain,
    SourceStatus,
    safe_search_term,
)
from pdrd_equipment_search_service.domain.search_candidates import clearly_other_model


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
        round_index: int | None = None,
        previous: dict | None = None,
    ) -> ResolveResult:
        """Ищет применимый документ без передачи текста проекта во внешний поиск."""
        if not identity.searchable:
            return ResolveResult(
                identity,
                "needs_review",
                warning="Модель или производитель не подтверждены.",
                reason_code=ReasonCode.MODEL_UNCERTAIN,
            )

        vision_budget = vision_budget or EquipmentVisionBudget(self.limits.vision_calls)
        search_budget = search_budget or EquipmentSearchBudget(
            self.limits.total_queries, self.limits.downloads
        )
        await _check_stop(should_stop)
        local = await self.catalog.find_document(identity)
        if local is not None and await self._local_allowed(
            replace(identity, manufacturer=local.manufacturer),
            local.final_url,
            allow_unverified,
        ):
            if self.fact_index is None:
                return ResolveResult(identity, "local", document=local)
            try:
                pages = await self.catalog.document_pages(local.source_id)
                visual = await self.catalog.document_vision_facts(local.source_id)
                fact_status, facts = await self.fact_index.extract_or_get(
                    replace(identity, manufacturer=local.manufacturer),
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
        tried: set[str] = set((previous or {}).get("tried_url_hashes", ()))
        attempts: list[dict] = []
        if round_index is not None:
            return await self._execute_round(
                identity,
                hosts,
                tried,
                attempts,
                round_index,
                allow_unverified,
                should_stop,
                vision_budget,
                search_budget,
            )
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
                    attempts,
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
                attempts,
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
            reason_code=attempts[-1]["code"] if attempts else ReasonCode.NOT_FOUND,
            attempts=tuple(attempts),
            tried_url_hashes=tuple(sorted(tried)),
        )

    async def _execute_round(
        self,
        identity,
        hosts,
        tried,
        attempts,
        round_index,
        allow_unverified,
        should_stop,
        vision_budget,
        search_budget,
    ) -> ResolveResult:
        """Выполняет одну попытку модели, оставляя первоначальный проход остальным."""
        hostname = hosts[0] if hosts and round_index == 0 else None
        queries = self._queries(identity, hostname)
        query = queries[
            0 if hostname else min(round_index - bool(hosts), len(queries) - 1)
        ]
        if not hosts and round_index == 2:
            query = f"{queries[0]} filetype:pdf"
        if not search_budget.claim_query():
            return ResolveResult(
                identity, "not_checked", reason_code=ReasonCode.QUERY_BUDGET
            )
        try:
            hits = await self.search.search(query, self.limits.urls_per_query)
        except Exception:
            return ResolveResult(
                identity,
                "incomplete",
                reason_code=ReasonCode.NETWORK_ERROR,
                attempts=({"stage": "search", "code": ReasonCode.NETWORK_ERROR},),
                retryable=True,
            )
        result, _, warning = await self._try_hits(
            identity,
            hits,
            tried,
            0,
            allow_unverified,
            should_stop,
            vision_budget,
            search_budget,
            attempts,
        )
        if result is not None:
            return replace(
                result, attempts=tuple(attempts), tried_url_hashes=tuple(sorted(tried))
            )
        return ResolveResult(
            identity,
            "incomplete" if attempts else "not_found",
            warning=warning,
            searched_urls=len(tried),
            reason_code=attempts[-1]["code"] if attempts else ReasonCode.NOT_FOUND,
            attempts=tuple(attempts),
            tried_url_hashes=tuple(sorted(tried)),
            retryable=True,
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
            safe_search_term(identity.article),
            safe_search_term(identity.variant)
            if identity.variant_kind != "interface"
            else "",
        ]
        base = " ".join(part for part in parts if part)
        suffix = f" site:{hostname}" if hostname else ""
        return (
            f"{base} datasheet manual{suffix}",
            f"{base} паспорт руководство{suffix}",
        )[: self.limits.queries_per_stage]

    async def _rank_hits(
        self, identity: EquipmentIdentity, hits: tuple[SearchHit, ...]
    ) -> list[tuple[SearchHit, EquipmentIdentity, SourceDomain | None]]:
        """Ставит официальные записи реестра перед другими trusted и pending.

        Приоритет не выдаёт доверие. Источник проверяется перед загрузкой и после
        redirect; происхождение из реестра читается из существующих данных БД.
        """
        candidates = []
        for hit in hits[: self.limits.urls_per_query]:
            if not hit.hostname:
                continue
            effective = identity
            if not identity.manufacturer:
                lookup = getattr(self.catalog, "manufacturer_for_host", None)
                manufacturer = (
                    await lookup(hit.hostname) if lookup is not None else None
                )
                effective = replace(identity, manufacturer=manufacturer or "")
            source = await self.catalog.source_for(effective.manufacturer, hit.hostname)
            trusted = bool(
                source
                and source.matches(effective.manufacturer, hit.hostname)
                and source.status is SourceStatus.TRUSTED
            )
            priority = (0 if source.registry_official else 1) if trusted else 2
            candidates.append((priority, hit, effective, source))
        candidates.sort(key=lambda candidate: candidate[0])
        return [(hit, effective, source) for _, hit, effective, source in candidates]

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
        attempts: list[dict] | None = None,
    ) -> tuple[ResolveResult | None, int, str]:
        """Проверяет доверие, скачивает и валидирует ограниченные кандидаты."""
        inspection_warning = ""
        attempts = attempts if attempts is not None else []
        for hit, effective, source in await self._rank_hits(identity, hits):
            await _check_stop(should_stop)
            if downloads >= self.limits.downloads:
                break
            fingerprint = sha256(hit.url.encode()).hexdigest()
            if not hit.hostname or fingerprint in tried:
                continue
            if clearly_other_model(identity, hit):
                tried.add(fingerprint)
                attempts.append(
                    {
                        "stage": "candidate",
                        "code": ReasonCode.LINK_MODEL_MISMATCH,
                        "hostname": hit.hostname,
                    }
                )
                continue
            if not identity.manufacturer:
                blocker = getattr(self.catalog, "source_blocked", None)
                if blocker is not None and await blocker(hit.hostname):
                    attempts.append(
                        {
                            "stage": "source",
                            "code": ReasonCode.SOURCE_BLOCKED,
                            "hostname": hit.hostname,
                        }
                    )
                    continue
            if source is None and effective.manufacturer:
                await self.catalog.register_pending(
                    effective.manufacturer, hit.hostname, hit.url, identity.model
                )
            if source is not None and (
                source.status is SourceStatus.BLOCKED or not source.enabled
            ):
                attempts.append(
                    {
                        "stage": "source",
                        "code": ReasonCode.SOURCE_BLOCKED,
                        "hostname": hit.hostname,
                    }
                )
                continue
            trusted = bool(
                source is not None
                and source.matches(effective.manufacturer, hit.hostname)
                and source.status is SourceStatus.TRUSTED
            )
            if not trusted and not allow_unverified:
                attempts.append(
                    {
                        "stage": "source",
                        "code": ReasonCode.SOURCE_UNVERIFIED,
                        "hostname": hit.hostname,
                    }
                )
                continue

            allow_http = bool(source is not None and source.allow_http)
            if urlsplit(hit.url).scheme != "https" and not allow_http:
                attempts.append(
                    {
                        "stage": "download",
                        "code": ReasonCode.URL_BLOCKED,
                        "hostname": hit.hostname,
                    }
                )
                continue
            await _check_stop(should_stop)
            if not search_budget.claim_download():
                inspection_warning = "Исчерпан общий бюджет загрузки документов."
                if not attempts:
                    attempts.append(
                        {"stage": "download", "code": ReasonCode.DOWNLOAD_BUDGET}
                    )
                break
            downloads += 1
            tried.add(fingerprint)
            stage = "download"
            try:
                approved = (
                    await self.catalog.trusted_hosts(effective.manufacturer)
                    if effective.manufacturer
                    else ()
                )
                download_options = {"allow_http": allow_http}
                if any(host != hit.hostname for host in approved):
                    download_options["allowed_redirect_hosts"] = approved
                document = await self.downloader.download(hit.url, **download_options)
                final_host = SearchHit(document.final_url).hostname
                final_source = await self.catalog.source_for(
                    effective.manufacturer,
                    final_host,
                )
                if final_source is not None and (
                    final_source.status is SourceStatus.BLOCKED
                    or not final_source.enabled
                ):
                    attempts.append(
                        {
                            "stage": "source",
                            "code": ReasonCode.SOURCE_BLOCKED,
                            "hostname": final_host,
                        }
                    )
                    continue
                final_trusted = bool(
                    final_source is not None
                    and final_source.matches(effective.manufacturer, final_host)
                    and final_source.status is SourceStatus.TRUSTED
                )
                if not final_trusted and not allow_unverified:
                    attempts.append(
                        {
                            "stage": "source",
                            "code": ReasonCode.SOURCE_UNVERIFIED,
                            "hostname": final_host,
                        }
                    )
                    continue
                await _check_stop(should_stop)
                stage = "extract"
                inspection = await self.inspector.inspect(effective, document)
                if inspection.requires_vision and not effective.manufacturer:
                    inspection = replace(
                        inspection,
                        reason_code=ReasonCode.MANUFACTURER_UNKNOWN,
                        reason="Для визуальной проверки требуется подтверждение производителя.",
                    )
                elif inspection.requires_vision and self.vision_inspector is not None:
                    await _check_stop(should_stop)
                    alias_reader = getattr(self.catalog, "manufacturer_aliases", None)
                    if alias_reader is not None:
                        inspection = replace(
                            inspection,
                            manufacturer_aliases=await alias_reader(
                                effective.manufacturer
                            ),
                        )
                    inspection = await self.vision_inspector.inspect(
                        effective,
                        document,
                        inspection,
                        budget=vision_budget,
                        should_stop=should_stop,
                    )
                if not inspection.applicable:
                    attempts.append(
                        {
                            "stage": "verify",
                            "code": inspection.reason_code
                            or (
                                ReasonCode.VISION_BUDGET
                                if inspection.requires_vision
                                else ReasonCode.MODEL_MISMATCH
                            ),
                            "hostname": final_host,
                        }
                    )
                    if inspection.requires_vision:
                        inspection_warning = inspection.reason
                    continue
                effective = replace(
                    effective,
                    manufacturer=inspection.manufacturer or effective.manufacturer,
                )
                if not effective.manufacturer:
                    attempts.append(
                        {
                            "stage": "verify",
                            "code": ReasonCode.MANUFACTURER_UNKNOWN,
                            "hostname": final_host,
                        }
                    )
                    continue
                attempts.append(
                    {"stage": "verify", "code": "verified", "hostname": final_host}
                )
                if not identity.manufacturer:
                    await self.catalog.register_pending(
                        effective.manufacturer,
                        final_host,
                        document.final_url,
                        identity.model,
                    )
                trust_status = (
                    "trusted"
                    if trusted and final_trusted
                    else "unverified_user_override"
                )
                snapshot = await self.catalog.save_document(
                    effective,
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
                            effective,
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
                                reason_code=ReasonCode.FACTS_UNAVAILABLE,
                                attempts=tuple(attempts),
                                resolved_manufacturer=effective.manufacturer,
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
                        attempts=tuple(attempts),
                        resolved_manufacturer=effective.manufacturer,
                    ),
                    downloads,
                    inspection_warning,
                )
            except SearchCancelled:
                raise
            except Exception as error:
                code = (
                    error.code
                    if isinstance(error, DocumentOperationError)
                    else (
                        ReasonCode.NETWORK_ERROR
                        if stage == "download"
                        else ReasonCode.EXTRACTION_ERROR
                    )
                )
                attempts.append(
                    {"stage": stage, "code": code, "hostname": hit.hostname}
                )
                inspection_warning = (
                    "Не удалось загрузить или обработать документацию производителя."
                )
                continue
        return None, downloads, inspection_warning
