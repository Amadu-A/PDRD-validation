# services/equipment-search-service/tests/unit/test_resolve_equipment.py

"""Проверки приоритета локального каталога, доверия и ограниченного поиска."""

from dataclasses import dataclass, field

import pytest
from pdrd_equipment_search_service.application.resolve import (
    ResolveEquipment,
    SearchLimits,
)
from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DocumentSnapshot,
    DownloadedDocument,
    EquipmentIdentity,
    SearchHit,
    SourceDomain,
    SourceStatus,
)

IDENTITY = EquipmentIdentity("MEAN WELL", "DRC-100B")


@dataclass
class Catalog:
    """Тестовый каталог без внешних зависимостей."""

    local: DocumentSnapshot | None = None
    sources: dict[str, SourceDomain] = field(default_factory=dict)
    pending: list[str] = field(default_factory=list)
    saves: int = 0
    visual: tuple[dict, ...] = ()

    async def find_document(self, identity: EquipmentIdentity):
        """Находит локальный snapshot."""
        return self.local

    async def source_for(self, manufacturer: str, hostname: str):
        """Возвращает точное разрешение домена."""
        return self.sources.get(hostname)

    async def trusted_hosts(self, manufacturer: str):
        """Возвращает подтверждённые hostname."""
        return tuple(
            host
            for host, source in self.sources.items()
            if source.status is SourceStatus.TRUSTED
        )

    async def register_pending(
        self, manufacturer: str, hostname: str, example_url: str, model: str
    ):
        """Регистрирует неизвестный домен без доверия."""
        self.pending.append(hostname)

    async def document_pages(self, source_id):
        """Читает сохранённую пустую страницу скана."""
        return ((1, ""),)

    async def document_vision_facts(self, source_id):
        """Читает сохранённую визуальную транскрипцию."""
        return self.visual

    async def save_document(
        self,
        identity,
        source_url,
        downloaded,
        inspection,
        trust_status,
    ):
        """Сохраняет снимок для проверки результата."""
        self.saves += 1
        self.visual = inspection.vision_facts
        self.local = DocumentSnapshot(
            "EQ-1",
            identity.manufacturer,
            identity.model,
            identity.variant,
            source_url,
            downloaded.final_url,
            downloaded.sha256,
            "documents/EQ-1.pdf",
            inspection.revision,
            trust_status,
        )
        return self.local


@dataclass
class Search:
    """Тестовый SearXNG без реальной сети."""

    hits: tuple[SearchHit, ...] = ()
    queries: list[str] = field(default_factory=list)

    async def search(self, query: str, limit: int):
        """Запоминает запросы и выдаёт фиксированные ссылки."""
        self.queries.append(query)
        return self.hits[:limit]


@dataclass
class Downloader:
    """Тестовый загрузчик с учётом числа обращений."""

    calls: int = 0

    async def download(self, url: str, *, allow_http: bool = False):
        """Возвращает PDF для проверки."""
        self.calls += 1
        return DownloadedDocument(b"%PDF-1.7", url, "application/pdf")


@dataclass
class Inspector:
    """Тестовый анализатор применимости документа."""

    applicable: bool = True

    async def inspect(self, identity, document):
        """Возвращает фиксированный результат проверки модели."""
        return DocumentInspection(self.applicable, revision="1")


def snapshot() -> DocumentSnapshot:
    """Создаёт локальную сохранённую версию."""
    return DocumentSnapshot(
        "EQ-1",
        "MEAN WELL",
        "DRC-100B",
        "",
        "https://meanwell.com/manual.pdf",
        "https://meanwell.com/manual.pdf",
        "a" * 64,
        "documents/EQ-1.pdf",
        "1",
        "trusted",
    )


@pytest.mark.asyncio
async def test_local_snapshot_skips_web_search() -> None:
    """Локальный документ исключает обращения к SearXNG и сети."""
    catalog = Catalog(
        local=snapshot(),
        sources={
            "meanwell.com": SourceDomain(
                "MEAN WELL", "meanwell.com", SourceStatus.TRUSTED
            )
        },
    )
    search = Search()
    downloader = Downloader()
    result = await ResolveEquipment(catalog, search, downloader, Inspector()).execute(
        IDENTITY
    )
    assert result.status == "local"
    assert result.document == catalog.local
    assert search.queries == []
    assert downloader.calls == 0


@pytest.mark.asyncio
async def test_incomplete_identity_is_not_searched() -> None:
    """Неуверенная модель не порождает предположительный веб-поиск."""
    search = Search()
    result = await ResolveEquipment(
        Catalog(),
        search,
        Downloader(),
        Inspector(),
    ).execute(EquipmentIdentity("MEAN WELL", "DRC-100B", confidence=0.4))
    assert result.status == "needs_review"
    assert search.queries == []


@pytest.mark.asyncio
async def test_trusted_exact_host_document_is_saved_once() -> None:
    """Подтверждённый hostname допускает подходящий документ."""
    catalog = Catalog(
        sources={
            "meanwell.com": SourceDomain(
                "MEAN WELL", "meanwell.com", SourceStatus.TRUSTED
            )
        }
    )
    search = Search((SearchHit("https://meanwell.com/manual.pdf"),))
    downloader = Downloader()
    result = await ResolveEquipment(catalog, search, downloader, Inspector()).execute(
        IDENTITY
    )
    assert result.status == "found"
    assert result.document.trust_status == "trusted"
    assert catalog.saves == 1
    assert downloader.calls == 1
    assert all("site:meanwell.com" in query for query in search.queries)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SourceStatus.PENDING, SourceStatus.BLOCKED])
async def test_untrusted_source_is_not_downloaded_by_default(status) -> None:
    """Pending и blocked не проходят без явного разрешения."""
    catalog = Catalog(
        sources={"example.com": SourceDomain("MEAN WELL", "example.com", status)}
    )
    downloader = Downloader()
    result = await ResolveEquipment(
        catalog,
        Search((SearchHit("https://example.com/manual.pdf"),)),
        downloader,
        Inspector(),
    ).execute(IDENTITY)
    assert result.document is None
    assert downloader.calls == 0


@pytest.mark.asyncio
async def test_pending_override_is_local_to_result_and_blocked_stays_forbidden() -> (
    None
):
    """Unsafe использует pending лишь в текущем анализе, не меняя каталог."""
    source = SourceDomain("MEAN WELL", "example.com", SourceStatus.PENDING)
    catalog = Catalog(sources={"example.com": source})
    result = await ResolveEquipment(
        catalog,
        Search((SearchHit("https://example.com/manual.pdf"),)),
        Downloader(),
        Inspector(),
    ).execute(IDENTITY, allow_unverified=True)
    assert result.document.trust_status == "unverified_user_override"
    assert catalog.sources["example.com"] == source

    blocked_catalog = Catalog(
        sources={
            "example.com": SourceDomain(
                "MEAN WELL", "example.com", SourceStatus.BLOCKED
            )
        }
    )
    blocked = await ResolveEquipment(
        blocked_catalog,
        Search((SearchHit("https://example.com/manual.pdf"),)),
        Downloader(),
        Inspector(),
    ).execute(IDENTITY, allow_unverified=True)
    assert blocked.document is None


@pytest.mark.asyncio
async def test_wrong_model_cannot_become_evidence() -> None:
    """Неприменимый документ не сохраняется даже на доверенном сайте."""
    catalog = Catalog(
        sources={
            "meanwell.com": SourceDomain(
                "MEAN WELL", "meanwell.com", SourceStatus.TRUSTED
            )
        }
    )
    result = await ResolveEquipment(
        catalog,
        Search((SearchHit("https://meanwell.com/other.pdf"),)),
        Downloader(),
        Inspector(applicable=False),
    ).execute(IDENTITY)
    assert result.document is None
    assert catalog.saves == 0


def test_source_trust_is_bound_to_exact_hostname_and_manufacturer() -> None:
    """Поддомен и другой производитель не наследуют подтверждение."""
    source = SourceDomain("MEAN WELL", "meanwell.com", SourceStatus.TRUSTED)
    assert source.matches("MEAN WELL", "meanwell.com")
    assert not source.matches("MEAN WELL", "cdn.meanwell.com")
    assert not source.matches("CHINT", "meanwell.com")


@pytest.mark.asyncio
async def test_cached_unverified_document_requires_new_override() -> None:
    """Кэш от unsafe задания не становится доверенным для следующего."""
    catalog = Catalog(
        local=snapshot(),
        sources={
            "meanwell.com": SourceDomain(
                "MEAN WELL", "meanwell.com", SourceStatus.PENDING
            )
        },
    )
    result = await ResolveEquipment(
        catalog,
        Search(),
        Downloader(),
        Inspector(),
    ).execute(IDENTITY)
    assert result.document is None
    allowed = await ResolveEquipment(
        catalog,
        Search(),
        Downloader(),
        Inspector(),
    ).execute(IDENTITY, allow_unverified=True)
    assert allowed.status == "local"


@pytest.mark.asyncio
async def test_general_search_keeps_budget_after_two_trusted_hosts() -> None:
    """Без результата на официальных доменах запускается общий поиск."""
    catalog = Catalog(
        sources={
            "one.example": SourceDomain(
                "MEAN WELL", "one.example", SourceStatus.TRUSTED
            ),
            "two.example": SourceDomain(
                "MEAN WELL", "two.example", SourceStatus.TRUSTED
            ),
        }
    )
    search = Search()
    result = await ResolveEquipment(
        catalog,
        search,
        Downloader(),
        Inspector(),
        limits=SearchLimits(total_queries=6),
    ).execute(IDENTITY)

    assert result.status == "not_found"
    assert len(search.queries) == 6
    assert sum("site:" in query for query in search.queries) == 4
    assert sum("site:" not in query for query in search.queries) == 2


def test_internal_retrieval_properties_never_enter_searxng_query() -> None:
    """Типы характеристик и данные проекта не передаются во внешний поиск."""
    identity = EquipmentIdentity(
        "MEAN WELL",
        "DRC-100B",
        properties=("output_voltage", "PS1 project only"),
    )
    resolver = ResolveEquipment(
        catalog=Catalog(),
        search=Search(),
        downloader=Downloader(),
        inspector=Inspector(),
    )
    query_text = " ".join(resolver._queries(identity, None))
    assert "MEAN WELL DRC-100B" in query_text
    assert "output_voltage" not in query_text
    assert "PS1" not in query_text


@pytest.mark.asyncio
async def test_scanned_candidate_keeps_explicit_incomplete_warning() -> None:
    """Скан без проверенной модели не становится evidence и не теряется молча."""

    class ScanInspector:
        """Возвращает безопасную диагностику вместо неподтверждённого документа."""

        async def inspect(self, identity, document):
            """Обозначает необходимость адресного визуального извлечения."""
            return DocumentInspection(
                False,
                reason="Сканированный PDF требует адресного визуального извлечения.",
                requires_vision=True,
            )

    catalog = Catalog(
        sources={
            "meanwell.com": SourceDomain(
                "MEAN WELL", "meanwell.com", SourceStatus.TRUSTED
            )
        }
    )
    result = await ResolveEquipment(
        catalog,
        Search((SearchHit("https://meanwell.com/scan.pdf"),)),
        Downloader(),
        ScanInspector(),
    ).execute(IDENTITY)

    assert result.status == "incomplete"
    assert "Сканированный PDF" in result.warning
    assert result.document is None
    assert catalog.saves == 0


@pytest.mark.asyncio
async def test_scanned_document_is_saved_and_replayed_without_second_vlm() -> None:
    """Первый анализ сохраняет транскрипцию; повтор берёт тот же snapshot."""

    class ScanInspector:
        """Помечает выбранную физическую страницу как скан."""

        async def inspect(self, identity, document):
            """Не подтверждает модель без адресного VLM."""
            return DocumentInspection(False, pages=((1, ""),), requires_vision=True)

    class Vision:
        """Возвращает подтверждённую маркировку и визуальную строку."""

        calls = 0

        async def inspect(
            self, identity, document, inspection, *, budget, should_stop=None
        ):
            """Расходует один общий VLM вызов."""
            self.calls += 1
            assert budget.claim()
            return DocumentInspection(
                True,
                pages=inspection.pages,
                vision_facts=({"page": 1, "snippet": "24 V"},),
            )

    class Facts:
        """Имитирует отдельный Knowledge кэш визуальной строки."""

        calls = 0

        async def extract_or_get(self, identity, snapshot, pages, *, vision_facts=()):
            """Проверяет передачу неизменяемого evidence и статуса review."""
            self.calls += 1
            assert snapshot.source_id == "EQ-1" and vision_facts[0]["page"] == 1
            return "needs_review", vision_facts

    catalog = Catalog(
        sources={
            "meanwell.com": SourceDomain(
                "MEAN WELL", "meanwell.com", SourceStatus.TRUSTED
            )
        }
    )
    search = Search((SearchHit("https://meanwell.com/scan.pdf"),))
    downloader, vision, facts = Downloader(), Vision(), Facts()
    resolver = ResolveEquipment(
        catalog,
        search,
        downloader,
        ScanInspector(),
        fact_index=facts,
        vision_inspector=vision,
    )
    first = await resolver.execute(IDENTITY)
    second = await resolver.execute(IDENTITY)
    assert first.status == "found" and second.status == "local"
    assert first.facts_status == second.facts_status == "needs_review"
    assert vision.calls == downloader.calls == catalog.saves == 1
    assert facts.calls == 2 and len(search.queries) == 1


@pytest.mark.asyncio
async def test_next_model_cannot_restart_exhausted_search_budget() -> None:
    """Лимит запросов общий, исчерпание сохраняется диагностическим статусом."""
    from pdrd_equipment_search_service.application.search_budget import (
        EquipmentSearchBudget,
    )

    search = Search()
    resolver = ResolveEquipment(
        Catalog(),
        search,
        Downloader(),
        Inspector(),
        limits=SearchLimits(total_queries=2),
    )
    budget = EquipmentSearchBudget(queries_remaining=2, downloads_remaining=1)
    first = await resolver.execute(IDENTITY, search_budget=budget)
    second = await resolver.execute(
        EquipmentIdentity("IEK", "KM20"), search_budget=budget
    )
    assert first.status == "not_found"
    assert second.status == "incomplete" and "бюджет" in second.warning
    assert len(search.queries) == 2
