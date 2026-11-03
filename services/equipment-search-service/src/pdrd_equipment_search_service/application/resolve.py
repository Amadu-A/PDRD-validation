# services/equipment-search-service/src/pdrd_equipment_search_service/application/resolve.py

"""Поиск документации по уникальной подтверждённой модели оборудования."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from urllib.parse import urlsplit

from pdrd_equipment_search_service.application.document_facts import (
    extract_document_facts,
)
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
from pdrd_equipment_search_service.domain.discovery import (
    candidate_id,
    discover_links,
    html_access_issue,
    public_url,
    search_queries,
)
from pdrd_equipment_search_service.domain.equipment import (
    EquipmentIdentity,
    ResolveResult,
    SearchHit,
    SourceDomain,
    SourceStatus,
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
    discovery_depth: int = 2
    links_per_page: int = 5


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
                stage_status="comparison_unavailable",
            )

        vision_budget = vision_budget or EquipmentVisionBudget(self.limits.vision_calls)
        search_budget = search_budget or EquipmentSearchBudget(
            self.limits.total_queries, self.limits.downloads
        )
        await _check_stop(should_stop)
        local = await self.catalog.find_document(identity)
        local_source = (
            await self._local_source(
                replace(identity, manufacturer=local.manufacturer),
                local.final_url,
                allow_unverified,
            )
            if local is not None
            else None
        )
        local_fallback = None
        if local is not None and local_source is not None:
            effective_local = replace(identity, manufacturer=local.manufacturer)
            # Решение источника относится к новому заданию; исторические байты и
            # trust_status сохранённого snapshot не переписываются.
            local = replace(
                local,
                source_url=public_url(local.source_url),
                final_url=public_url(local.final_url),
                trust_status=(
                    "trusted"
                    if local_source.status is SourceStatus.TRUSTED
                    else "unverified_user_override"
                ),
            )
            manufacturer_site = _manufacturer_site(
                identity, local.final_url, local_source
            )
            if self.fact_index is None:
                return ResolveResult(
                    identity,
                    "local",
                    document=local,
                    manufacturer_site=manufacturer_site,
                    stage_status="document_verified",
                )
            try:
                pages = await self.catalog.document_pages(local.source_id)
                visual = await self.catalog.document_vision_facts(local.source_id)
                fact_status, facts, reason = await extract_document_facts(
                    catalog=self.catalog,
                    index=self.fact_index,
                    vision=self.vision_inspector,
                    identity=effective_local,
                    snapshot=local,
                    pages=pages,
                    visual=visual,
                    budget=vision_budget,
                    should_stop=should_stop,
                    inspector=self.inspector,
                )
                local_result = ResolveResult(
                    identity,
                    "local" if facts else "incomplete",
                    document=local,
                    facts=facts,
                    facts_status=fact_status,
                    reason_code=reason,
                    warning=""
                    if facts
                    else "Характеристики сохранённого документа не извлечены.",
                    retryable=not bool(facts),
                    manufacturer_site=manufacturer_site,
                    stage_status="facts_extracted"
                    if facts
                    else "comparison_unavailable",
                    candidates=tuple((previous or {}).get("candidates", ())),
                )
                if facts:
                    return local_result
                local_fallback = local_result
            except Exception:
                return ResolveResult(
                    identity,
                    "incomplete",
                    document=local,
                    warning="EQ Facts сохранённого документа недоступны.",
                    facts_status="incomplete",
                    manufacturer_site=manufacturer_site,
                )

        hosts = await self.catalog.trusted_hosts(identity.manufacturer)
        tried: set[str] = set((previous or {}).get("tried_url_hashes", ()))
        attempts: list[dict] = []
        candidates = [dict(row) for row in (previous or {}).get("candidates", ())]
        if round_index is not None:
            result = await self._execute_round(
                identity,
                hosts,
                tried,
                attempts,
                round_index,
                allow_unverified,
                should_stop,
                vision_budget,
                search_budget,
                candidates,
            )
            return _keep_saved_document(result, local_fallback)
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
                    candidates,
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
                candidates,
            )
            if result is not None:
                return result
            if inspection_warning:
                warning = inspection_warning

        result = ResolveResult(
            identity,
            "not_found" if not warning else "incomplete",
            warning=warning or "Применимая документация не найдена.",
            searched_urls=len(tried),
            reason_code=attempts[-1]["code"] if attempts else ReasonCode.NOT_FOUND,
            attempts=tuple(attempts),
            tried_url_hashes=tuple(sorted(tried)),
            candidates=tuple(candidates),
            stage_status="document_rejected" if candidates else "document_not_found",
        )
        return _keep_saved_document(result, local_fallback)

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
        candidates,
    ) -> ResolveResult:
        """Выполняет одну попытку модели, оставляя первоначальный проход остальным."""
        hostname = hosts[0] if hosts and round_index == 0 else None
        queries = search_queries(identity, hostname)
        query = queries[
            0 if hostname else min(round_index - bool(hosts), len(queries) - 1)
        ]
        hits = tuple(
            SearchHit(row["document_url"], row.get("title", ""))
            for row in candidates
            if row.get("download_status") == "downloaded"
            and row.get("retryable")
            and row.get("document_url")
        )
        if not hits:
            if not search_budget.claim_query():
                return ResolveResult(
                    identity,
                    "not_checked",
                    reason_code=ReasonCode.QUERY_BUDGET,
                    candidates=tuple(candidates),
                    stage_status="budget_exhausted",
                )
            try:
                hits = await self.search.search(query, self.limits.urls_per_query)
            except Exception as error:
                return ResolveResult(
                    identity,
                    "incomplete",
                    reason_code="search_temporarily_unavailable",
                    attempts=(
                        {
                            "stage": "search",
                            "code": getattr(error, "code", ReasonCode.NETWORK_ERROR),
                            "engines": getattr(error, "engines", ()),
                        },
                    ),
                    retryable=True,
                    candidates=tuple(candidates),
                    stage_status="search_temporarily_unavailable",
                )
        if getattr(hits, "engines", ()):
            attempts.append(
                {
                    "stage": "search",
                    "code": "search_partial_results",
                    "engines": hits.engines,
                }
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
            candidates,
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
            candidates=tuple(candidates),
            stage_status=(
                "budget_exhausted"
                if "бюджет" in warning.lower()
                else "document_rejected"
            )
            if candidates
            else "document_not_found",
        )

    async def _local_source(
        self,
        identity: EquipmentIdentity,
        final_url: str,
        allow_unverified: bool,
    ) -> SourceDomain | None:
        """Один раз читает актуальное разрешение сохранённого источника."""
        host = SearchHit(final_url).hostname
        source = await self.catalog.source_for(identity.manufacturer, host)
        if source is None or not source.enabled:
            return None
        if source.status is SourceStatus.BLOCKED:
            return None
        return (
            source
            if source.matches(identity.manufacturer, host)
            and (source.status is SourceStatus.TRUSTED or allow_unverified)
            else None
        )

    def _queries(
        self, identity: EquipmentIdentity, hostname: str | None
    ) -> tuple[str, ...]:
        """Формирует только нейтральные запросы из маркировки оборудования."""
        return search_queries(identity, hostname)[: self.limits.queries_per_stage]

    async def _rank_hits(
        self, identity: EquipmentIdentity, hits: tuple[SearchHit, ...]
    ) -> list[tuple[SearchHit, EquipmentIdentity, SourceDomain | None]]:
        """Ставит официальные записи реестра перед другими trusted и pending.

        Приоритет не выдаёт доверие. Источник проверяется перед загрузкой и после
        redirect; происхождение из реестра читается из существующих данных БД.
        """
        candidates = []
        for hit in hits[: self.limits.urls_per_query * 3]:
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
            priority = ((0 if source.registry_official else 1) if trusted else 2) * 10
            if re.search(
                r"паспорт|datasheet|\.pdf(?:\?|$)", hit.url + " " + hit.title, re.I
            ):
                priority -= 3
            candidates.append((priority, hit, effective, source))
        candidates.sort(key=lambda candidate: candidate[0])
        return [
            (hit, effective, source)
            for _, hit, effective, source in candidates[: self.limits.urls_per_query]
        ]

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
        candidates: list[dict] | None = None,
    ) -> tuple[ResolveResult | None, int, str]:
        """Проверяет доверие, скачивает и валидирует ограниченные кандидаты."""
        inspection_warning = ""
        attempts = attempts if attempts is not None else []
        candidates = candidates if candidates is not None else []
        # После рестарта адрес найденного дочернего PDF остаётся доступен даже
        # когда исходная HTML-страница уже просмотрена и исключена из повторов.
        pending = tuple(
            SearchHit(row["document_url"], row.get("title", ""))
            for row in candidates
            if row.get("retryable")
            and row.get("document_url")
            and int(row.get("attempt_count", 0)) < 2
        )
        hits = tuple(dict.fromkeys((*pending, *hits)))
        queue = [
            (hit, effective, source, "", 0)
            for hit, effective, source in await self._rank_hits(identity, hits)
        ]
        queued = {hit.url for hit, *_ in queue}
        for hit, _, _, _, _ in queue:
            _remember_candidate(candidates, hit, "", 0)
        cursor = 0
        saved_result = None
        while cursor < len(queue) and cursor < self.limits.urls_per_query * (
            self.limits.discovery_depth + 1
        ):
            hit, effective, source, parent_url, depth = queue[cursor]
            cursor += 1
            await _check_stop(should_stop)
            fingerprint = sha256(hit.url.encode()).hexdigest()
            if not hit.hostname:
                continue
            identifier = candidate_id(hit.url)
            old = next(
                (row for row in candidates if row.get("candidate_id") == identifier),
                None,
            )
            cached_bytes = bool(
                old and old.get("download_status") == "downloaded" and old.get("sha256")
            )
            if fingerprint in tried and not (cached_bytes and old.get("retryable")):
                continue
            if old and (
                (
                    old.get("inspection_status") == "rejected"
                    and not old.get("retryable")
                )
                or int(old.get("attempt_count", 0)) >= 2
            ):
                continue
            candidate = _remember_candidate(candidates, hit, parent_url, depth)
            candidate["trust_status"] = (
                "trusted"
                if source and source.status is SourceStatus.TRUSTED
                else "unverified_user_override"
                if allow_unverified
                else "pending"
            )
            if clearly_other_model(identity, hit):
                tried.add(fingerprint)
                attempts.append(
                    {
                        "stage": "candidate",
                        "code": ReasonCode.LINK_MODEL_MISMATCH,
                        "hostname": hit.hostname,
                    }
                )
                candidate.update(
                    applicability="wrong_model",
                    reason_codes=[ReasonCode.LINK_MODEL_MISMATCH],
                )
                continue
            if not identity.manufacturer:
                blocker = getattr(self.catalog, "source_blocked", None)
                if blocker is not None and await blocker(hit.hostname):
                    candidate["reason_codes"] = [ReasonCode.SOURCE_BLOCKED]
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
                candidate["reason_codes"] = [ReasonCode.SOURCE_BLOCKED]
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
                candidate["reason_codes"] = [ReasonCode.SOURCE_UNVERIFIED]
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
            cache_credit = cached_bytes and search_budget.downloads_remaining <= 0
            if cache_credit:
                search_budget.downloads_remaining += 1
            if not search_budget.claim_download():
                candidate.update(
                    reason_codes=[ReasonCode.DOWNLOAD_BUDGET], retryable=True
                )
                inspection_warning = "Исчерпан общий бюджет загрузки документов."
                if not attempts:
                    attempts.append(
                        {"stage": "download", "code": ReasonCode.DOWNLOAD_BUDGET}
                    )
                break
            page_reserved = not (
                (cached_bytes and old.get("media_type") == "application/pdf")
                or re.search(r"\.pdf(?:\?|$)", hit.url, re.I)
            )
            if page_reserved and not search_budget.claim_page():
                search_budget.downloads_remaining += 1
                if cache_credit:
                    search_budget.downloads_remaining -= 1
                inspection_warning = "Исчерпан бюджет просмотра страниц."
                candidate["reason_codes"] = ["page_budget_exhausted"]
                candidate["retryable"] = True
                break
            downloads += 1
            tried.add(fingerprint)
            candidate["attempt_count"] = int(candidate.get("attempt_count", 0)) + 1
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
                document = None
                loader = getattr(self.catalog, "load_candidate_download", None)
                if cached_bytes and loader is not None:
                    document = await loader(
                        candidate["sha256"], candidate.get("media_type", ""), hit.url
                    )
                network_download = document is None
                if network_download and cache_credit:
                    downloads -= 1
                    candidate.update(
                        reason_codes=[ReasonCode.DOWNLOAD_BUDGET], retryable=True
                    )
                    break
                if network_download:
                    search_budget.download_attempts += 1
                    document = await self.downloader.download(
                        hit.url, **download_options
                    )
                    cache = getattr(self.catalog, "cache_candidate_download", None)
                    if cache is not None:
                        await cache(document)
                else:
                    search_budget.downloads_remaining += 1
                    downloads -= 1
                if cache_credit:
                    search_budget.downloads_remaining -= 1
                candidate.update(
                    download_status="downloaded",
                    sha256=document.sha256,
                    document_url=public_url(document.final_url),
                    media_type=document.media_type,
                )
                if search_budget.on_progress is not None:
                    await search_budget.on_progress(
                        {"candidates": candidates, "tried_url_hashes": sorted(tried)}
                    )
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
                if document.media_type == "text/html":
                    # Просмотр HTML и загрузка документа имеют разные квоты.
                    if network_download:
                        search_budget.downloads_remaining += 1
                        downloads -= 1
                    access_issue = html_access_issue(document.content)
                    if access_issue:
                        candidate.update(
                            inspection_status="rejected",
                            reason_codes=[access_issue],
                            applicability="unreadable_document",
                        )
                        attempts.append(
                            {
                                "stage": "discovery",
                                "code": access_issue,
                                "hostname": final_host,
                            }
                        )
                        continue
                    links = discover_links(
                        document.content,
                        document.final_url,
                        self.limits.links_per_page,
                        identity=identity,
                    )
                    if depth < self.limits.discovery_depth:
                        children = await self._rank_hits(identity, links)
                        for child, child_identity, child_source in reversed(children):
                            if child.url not in queued:
                                queued.add(child.url)
                                _remember_candidate(
                                    candidates, child, document.final_url, depth + 1
                                )
                                queue.insert(
                                    cursor,
                                    (
                                        child,
                                        child_identity,
                                        child_source,
                                        document.final_url,
                                        depth + 1,
                                    ),
                                )
                    candidate["links_found"] = len(links)
                    if links and depth < self.limits.discovery_depth:
                        candidate.update(
                            inspection_status="discovery_page",
                            document_type="html_page",
                        )
                        continue
                elif page_reserved:
                    search_budget.pages_remaining += 1
                await _check_stop(should_stop)
                stage = "extract"
                if not search_budget.claim_extraction():
                    candidate["reason_codes"] = ["extraction_budget_exhausted"]
                    inspection_warning = "Исчерпан бюджет извлечения документации."
                    candidate["retryable"] = True
                    candidate["attempt_count"] = max(0, candidate["attempt_count"] - 1)
                    break
                inspection = await self.inspector.inspect(effective, document)
                if inspection.requires_vision and self.vision_inspector is not None:
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
                    candidate.update(
                        inspection_status="rejected",
                        applicability=_applicability(inspection),
                        reason_codes=[
                            inspection.reason_code or ReasonCode.MODEL_MISMATCH
                        ],
                        document_type=inspection.document_type,
                    )
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
                if (
                    not effective.manufacturer
                    and identity.item_type == "serial_equipment"
                ):
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
                snapshot = replace(
                    snapshot,
                    source_url=public_url(snapshot.source_url),
                    final_url=public_url(snapshot.final_url),
                    applicability=inspection.applicability,
                    document_type=inspection.document_type,
                    reference=identity.reference,
                )
                candidate.update(
                    inspection_status="accepted",
                    applicability=inspection.applicability,
                    document_type=inspection.document_type,
                    manufacturer=inspection.manufacturer,
                    manufacturer_evidence=inspection.manufacturer_evidence,
                    model=identity.model,
                    source_id=snapshot.source_id,
                    evidence_pages=sorted(
                        set(inspection.vision_pages)
                        | (
                            {inspection.manufacturer_evidence_page}
                            if inspection.manufacturer_evidence_page
                            else set()
                        )
                    ),
                    trust_status=trust_status,
                    reason_codes=[],
                    retryable=False,
                )
                fact_status = ""
                facts = ()
                if self.fact_index is not None:
                    await _check_stop(should_stop)
                    try:
                        fact_status, facts, fact_reason = await extract_document_facts(
                            catalog=self.catalog,
                            index=self.fact_index,
                            vision=self.vision_inspector,
                            identity=effective,
                            snapshot=snapshot,
                            pages=inspection.pages,
                            visual=inspection.vision_facts,
                            budget=vision_budget,
                            should_stop=should_stop,
                            document=document,
                            inspection=inspection,
                            inspector=self.inspector,
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
                                manufacturer_site=_manufacturer_site(
                                    identity, snapshot.final_url, final_source
                                ),
                            ),
                            downloads,
                            inspection_warning,
                        )
                saved_result = ResolveResult(
                    identity,
                    "found" if self.fact_index is None or facts else "incomplete",
                    document=snapshot,
                    searched_urls=len(tried),
                    facts=facts,
                    facts_status=fact_status,
                    warning=""
                    if self.fact_index is None or facts
                    else "Документ сохранён, но характеристики не извлечены.",
                    reason_code="" if self.fact_index is None or facts else fact_reason,
                    retryable=self.fact_index is not None and not bool(facts),
                    attempts=tuple(attempts),
                    resolved_manufacturer=effective.manufacturer,
                    manufacturer_site=_manufacturer_site(
                        identity, snapshot.final_url, final_source
                    ),
                    candidates=tuple(candidates),
                    stage_status="facts_extracted" if facts else "document_verified",
                )
                if self.fact_index is None or facts:
                    return saved_result, downloads, inspection_warning
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
                transient = code in {
                    ReasonCode.NETWORK_ERROR,
                    ReasonCode.DNS_ERROR,
                    ReasonCode.TIMEOUT,
                    ReasonCode.HTTP_ERROR,
                }
                candidate.update(
                    download_status="failed" if stage == "download" else "downloaded",
                    inspection_status="failed",
                    reason_codes=[code],
                    retryable=transient,
                )
                if stage == "download" and code in {
                    ReasonCode.NETWORK_ERROR,
                    ReasonCode.DNS_ERROR,
                    ReasonCode.TLS_ERROR,
                    ReasonCode.URL_BLOCKED,
                    ReasonCode.REDIRECT_BLOCKED,
                }:
                    search_budget.downloads_remaining += 1
                    downloads -= 1
                if transient:
                    tried.discard(fingerprint)
                inspection_warning = (
                    "Не удалось загрузить или обработать документацию производителя."
                )
                continue
        return saved_result, downloads, inspection_warning


def _applicability(inspection) -> str:
    """Преобразует прежние причины отказа в отдельную предметную оценку."""
    if inspection.applicability != "exact_match":
        return inspection.applicability
    return {
        ReasonCode.MODEL_MISMATCH: "wrong_model",
        ReasonCode.VARIANT_MISMATCH: "wrong_variant",
        ReasonCode.ARTICLE_MISMATCH: "wrong_variant",
        ReasonCode.MANUFACTURER_MISMATCH: "wrong_manufacturer",
        "scan_requires_vision": "unreadable_document",
    }.get(inspection.reason_code, "insufficient_evidence")


def _keep_saved_document(
    result: ResolveResult, fallback: ResolveResult | None
) -> ResolveResult:
    """Не теряет сохранённый документ, если дополнительные поисковые попытки не помогли."""
    if result.document is not None or fallback is None:
        return result
    return replace(
        fallback,
        candidates=result.candidates,
        attempts=result.attempts,
        tried_url_hashes=result.tried_url_hashes,
        reason_code=result.reason_code or fallback.reason_code,
        retryable=result.retryable,
        stage_status="comparison_unavailable",
    )


def _remember_candidate(
    rows: list[dict], hit: SearchHit, parent: str, depth: int
) -> dict:
    """Сохраняет даже ещё не просмотренные ссылки до исчерпания бюджета или отмены."""
    identifier = candidate_id(hit.url)
    existing = next((row for row in rows if row["candidate_id"] == identifier), None)
    if existing is not None:
        return existing
    ancestor = next(
        (row for row in rows if row.get("document_url") == public_url(parent)), {}
    )
    chain = [*ancestor.get("discovery_chain", []), public_url(hit.url)]
    row = {
        "candidate_id": identifier,
        "source_url": public_url(parent or hit.url),
        "document_url": public_url(hit.url),
        "discovery_method": "html_link" if parent else "search",
        "discovery_chain": chain[-4:],
        "depth": depth,
        "title": hit.title[:200],
        "trust_status": "pending",
        "download_status": "not_started",
        "inspection_status": "not_started",
        "applicability": "insufficient_evidence",
        "reason_codes": [],
        "retryable": True,
    }
    rows.append(row)
    return row


def _manufacturer_site(
    identity: EquipmentIdentity, final_url: str, source: SourceDomain | None
) -> dict[str, str] | None:
    """Публикует официальный сайт только для явно установленного проектного изготовителя."""
    host = SearchHit(final_url).hostname
    if (
        not identity.manufacturer
        or not source
        or not source.matches(identity.manufacturer, host)
    ):
        return None
    if source.status is not SourceStatus.TRUSTED or not source.registry_official:
        return None
    parsed = urlsplit(final_url)
    return {
        "name": source.manufacturer,
        "url": f"{parsed.scheme}://{parsed.netloc}/",
        "relationship": "project",
        "verified": "registry_official",
    }
