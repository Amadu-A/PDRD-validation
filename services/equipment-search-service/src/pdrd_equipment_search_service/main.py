# services/equipment-search-service/src/pdrd_equipment_search_service/main.py

"""Внутренний HTTP API поиска документации оборудования."""

import asyncio
import hmac
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import create_async_engine

from pdrd_equipment_search_service.application.jobs import SearchJobService
from pdrd_equipment_search_service.application.resolve import (
    ResolveEquipment,
    SearchLimits,
)
from pdrd_equipment_search_service.core.settings import Settings
from pdrd_equipment_search_service.domain.equipment import EquipmentIdentity
from pdrd_equipment_search_service.infrastructure.inspect_document import (
    DocumentServiceInspector,
)
from pdrd_equipment_search_service.infrastructure.knowledge_facts import (
    KnowledgeEquipmentFactIndex,
)
from pdrd_equipment_search_service.infrastructure.postgres_catalog import (
    PostgresEquipmentCatalog,
)
from pdrd_equipment_search_service.infrastructure.postgres_jobs import (
    PostgresJobRepository,
)
from pdrd_equipment_search_service.infrastructure.postgres_sources import (
    PostgresSourceManager,
)
from pdrd_equipment_search_service.infrastructure.safe_download import (
    SafeDocumentDownloader,
)
from pdrd_equipment_search_service.infrastructure.searxng import SearxngSearch
from pdrd_equipment_search_service.infrastructure.vision_inspection import (
    TargetedVisionInspector,
)


class IdentityInput(BaseModel):
    """Подтверждённая идентификация одной физической модели."""

    manufacturer: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    variant: str = Field(default="", max_length=200)
    status: str = "resolved"
    confidence: float = Field(default=1.0, ge=0, le=1)
    properties: tuple[str, ...] = Field(default_factory=tuple, max_length=8)


class SourceUpdate(BaseModel):
    """Решение администратора по точному домену производителя."""

    manufacturer: str = Field(min_length=1, max_length=200)
    hostname: str = Field(min_length=3, max_length=253)
    status: str
    enabled: bool = True
    allow_http: bool = False
    reason: str = Field(min_length=1, max_length=1000)


class StartRequest(BaseModel):
    """Запрос одного задания с ограниченным числом уникальных моделей."""

    identities: list[IdentityInput] = Field(max_length=100)
    allow_unverified: bool = False


def create_app(
    settings: Settings | None = None,
    service: SearchJobService | None = None,
    source_manager: PostgresSourceManager | None = None,
    document_catalog: PostgresEquipmentCatalog | None = None,
) -> FastAPI:
    """Собирает сервис и его внутренние маршруты."""
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Открывает клиентов и восстанавливает прерванные задания."""
        if service is not None:
            app.state.jobs = service
            app.state.sources = source_manager
            app.state.catalog = document_catalog
            yield
            return
        if not config.internal_key.get_secret_value():
            raise RuntimeError("Не задан внутренний ключ Equipment Search Service.")
        engine = create_async_engine(config.database.url(), pool_pre_ping=True)
        client = httpx.AsyncClient(trust_env=False, follow_redirects=False)
        repository = PostgresJobRepository(engine)
        await repository.recover_interrupted()
        catalog = PostgresEquipmentCatalog(engine, config.storage_root)
        resolver = ResolveEquipment(
            catalog=catalog,
            search=SearxngSearch(client, config.searxng_url),
            downloader=SafeDocumentDownloader(max_bytes=config.max_download_bytes),
            inspector=DocumentServiceInspector(client, config.document_service_url),
            fact_index=KnowledgeEquipmentFactIndex(
                client,
                config.knowledge_service_url,
            ),
            vision_inspector=TargetedVisionInspector(
                client,
                config.document_service_url,
                config.analysis_service_url,
            ),
            limits=SearchLimits(
                downloads=config.max_downloads,
                total_queries=config.max_queries,
                vision_calls=config.max_vision_calls,
                urls_per_query=config.max_urls_per_query,
            ),
        )
        jobs = SearchJobService(
            repository,
            resolver,
            max_models=config.max_models,
            max_seconds=config.max_seconds,
            max_concurrent=config.max_concurrent_searches,
            max_vision_calls=config.max_vision_calls,
            max_queries=config.max_queries,
            max_downloads=config.max_downloads,
        )
        app.state.jobs = jobs
        app.state.sources = PostgresSourceManager(engine)
        app.state.catalog = catalog
        try:
            yield
        finally:
            await jobs.shutdown()
            await client.aclose()
            await engine.dispose()

    app = FastAPI(
        title="PDRD Equipment Search Service",
        lifespan=lifespan,
    )

    def authorize(x_internal_key: str | None = Header(default=None)) -> None:
        """Принимает только вызовы доверенного внутреннего клиента."""
        expected = config.internal_key.get_secret_value()
        if (
            not expected
            or not x_internal_key
            or not hmac.compare_digest(
                expected,
                x_internal_key,
            )
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Нет доступа к внутреннему API."
            )

    def jobs(request: Request) -> SearchJobService:
        """Возвращает обработчик из lifecycle приложения."""
        return request.app.state.jobs

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Проверяет запущенный процесс."""
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(request: Request) -> dict[str, str]:
        """Проверяет доступность PostgreSQL и конфигурации."""
        if service is not None:
            return {"status": "ready"}
        if not config.internal_key.get_secret_value():
            raise HTTPException(503, "Внутренний ключ не настроен.")
        repository = request.app.state.jobs.repository
        try:
            if not await repository.is_ready():
                raise RuntimeError("Хранилище не готово.")
        except Exception as error:
            raise HTTPException(503, "Хранилище оборудования недоступно.") from error
        return {"status": "ready"}

    def sources(request: Request) -> PostgresSourceManager:
        """Возвращает каталог источников из lifecycle приложения."""
        manager = request.app.state.sources
        if manager is None:
            raise HTTPException(503, "Каталог источников недоступен.")
        return manager

    @app.get(
        "/internal/v1/equipment-search/documents/{source_id}",
        dependencies=[Depends(authorize)],
    )
    async def document(source_id: str, request: Request) -> Response:
        """Возвращает неизменённые байты документа по его EQ source ID."""
        if re.fullmatch(r"EQ-[0-9a-f]{32}", source_id) is None:
            raise HTTPException(404, "Документ не найден.")
        catalog = request.app.state.catalog
        if catalog is None:
            raise HTTPException(503, "Каталог документов недоступен.")
        try:
            snapshot, content, media_type = await catalog.load_document(source_id)
        except LookupError as error:
            raise HTTPException(404, "Документ не найден.") from error
        except (OSError, ValueError) as error:
            raise HTTPException(503, "Сохранённый документ недоступен.") from error
        return Response(
            content,
            media_type=media_type,
            headers={
                "X-Document-SHA256": snapshot.sha256,
                "X-Document-Revision": snapshot.revision,
                "Content-Disposition": (
                    "inline; filename=equipment-"
                    + source_id
                    + (".pdf" if media_type == "application/pdf" else ".html")
                ),
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(
        "/internal/v1/equipment-search/documents/{source_id}/metadata",
        dependencies=[Depends(authorize)],
    )
    async def document_metadata(source_id: str, request: Request) -> dict:
        """Показывает provenance сохранённой версии документа."""
        if re.fullmatch(r"EQ-[0-9a-f]{32}", source_id) is None:
            raise HTTPException(404, "Документ не найден.")
        catalog = request.app.state.catalog
        if catalog is None:
            raise HTTPException(503, "Каталог документов недоступен.")
        try:
            snapshot, _, media_type = await catalog.load_document(source_id)
        except LookupError as error:
            raise HTTPException(404, "Документ не найден.") from error
        except (OSError, ValueError) as error:
            raise HTTPException(503, "Сохранённый документ недоступен.") from error
        return {**asdict(snapshot), "media_type": media_type}

    prefix = "/internal/v1/equipment-search/jobs"
    Worker = Annotated[SearchJobService, Depends(jobs)]

    @app.post(prefix + "/{job_id}", dependencies=[Depends(authorize)])
    async def start(
        job_id: UUID,
        body: StartRequest,
        worker: Worker,
    ) -> dict:
        """Идемпотентно запускает поиск без повторных запросов по модели."""
        identities = [
            EquipmentIdentity(**item.model_dump()) for item in body.identities
        ]
        try:
            state = await worker.start(job_id, identities, body.allow_unverified)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return jsonable_encoder(state)

    @app.get(prefix + "/{job_id}", dependencies=[Depends(authorize)])
    async def state(
        job_id: UUID,
        worker: Worker,
    ) -> dict:
        """Возвращает результаты и статус одного задания."""
        current = await worker.repository.get(job_id)
        if current is None:
            raise HTTPException(404, "Задание не найдено.")
        return jsonable_encoder(current)

    @app.post(prefix + "/{job_id}/cancel", dependencies=[Depends(authorize)])
    async def cancel(
        job_id: UUID,
        worker: Worker,
    ) -> dict:
        """Останавливает запуск следующих внешних операций."""
        current = await worker.repository.get(job_id)
        if current is None:
            raise HTTPException(404, "Задание не найдено.")
        accepted = await worker.cancel(job_id)
        return {"cancel_requested": accepted}

    @app.get(prefix + "/{job_id}/events", dependencies=[Depends(authorize)])
    async def events(
        job_id: UUID,
        worker: Worker,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        """Воспроизводит журнал SSE с указанного монотонного ID."""
        current = await worker.repository.get(job_id)
        if current is None:
            raise HTTPException(404, "Задание не найдено.")
        try:
            last = max(0, int(last_event_id or "0"))
        except ValueError as error:
            raise HTTPException(400, "Некорректный Last-Event-ID.") from error

        async def stream() -> AsyncIterator[str]:
            sequence = last
            idle = 0
            while True:
                rows = await worker.repository.events_after(job_id, sequence)
                for row in rows:
                    sequence = int(row["sequence"])
                    payload = json.dumps(
                        {
                            "type": row["event_type"],
                            "message": row["message"],
                        },
                        ensure_ascii=False,
                    )
                    yield f"id: {sequence}\nevent: equipment_search\ndata: {payload}\n\n"
                current_state = await worker.repository.get(job_id)
                if current_state is None or (
                    current_state["status"] in {"completed", "incomplete", "cancelled"}
                    and sequence >= current_state["event_sequence"]
                ):
                    break
                await asyncio.sleep(1)
                idle += 1
                if idle >= 15:
                    yield ": heartbeat\n\n"
                    idle = 0

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    source_prefix = "/internal/v1/equipment-search"

    @app.get(source_prefix + "/manufacturers", dependencies=[Depends(authorize)])
    async def manufacturers(request: Request) -> list[dict]:
        """Перечисляет производителей с aliases."""
        return jsonable_encoder(await sources(request).list_manufacturers())

    @app.get(source_prefix + "/sources", dependencies=[Depends(authorize)])
    async def list_sources(
        request: Request,
        source_status: str | None = None,
        query: str = "",
    ) -> list[dict]:
        """Фильтрует точные домены и показывает число использований."""
        try:
            rows = await sources(request).list_sources(source_status, query)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return jsonable_encoder(rows)

    @app.put(source_prefix + "/sources", dependencies=[Depends(authorize)])
    async def update_source(
        request: Request,
        body: SourceUpdate,
        x_actor: str | None = Header(default=None),
    ) -> dict:
        """Записывает решение администратора и причину в аудит."""
        if not x_actor:
            raise HTTPException(422, "Не указан инициатор решения.")
        try:
            row = await sources(request).update_source(
                **body.model_dump(),
                actor=x_actor,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return jsonable_encoder(row)

    @app.get(
        source_prefix + "/sources/{source_id}/audit",
        dependencies=[Depends(authorize)],
    )
    async def source_audit(request: Request, source_id: UUID) -> list[dict]:
        """Возвращает историю доверия по конкретному источнику."""
        return jsonable_encoder(await sources(request).audit(source_id))

    return app


app = create_app()
