# services/pdf-service/app/main.py

"""PDF-service: нормативная проверка чертежей с RAG и Базой Опыта."""

from __future__ import annotations

import os
import re
import time
from typing import Any

import fitz
import httpx
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from app.rag import (
    OLLAMA_EMBEDDING_MODEL,
    get_rag_status,
    search_experience_many,
    search_knowledge,
    search_normative,
)
from app.validator import (
    OLLAMA_VISION_MODEL,
    build_experience_query,
    check_page_against_norms,
    finalize_findings,
    understand_page,
)


APP_NAME = (
    "Drawing Validation PDF Service"
)

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://ollama:11434",
).rstrip("/")

PDF_RENDER_MAX_SIDE = int(
    os.getenv(
        "PDF_RENDER_MAX_SIDE",
        "2400",
    )
)

PDF_MAX_ANALYSIS_PAGES = int(
    os.getenv(
        "PDF_MAX_ANALYSIS_PAGES",
        "50",
    )
)

PDF_TEXT_LIMIT = int(
    os.getenv(
        "PDF_TEXT_LIMIT",
        "12000",
    )
)


app = FastAPI(
    title=APP_NAME,
    version="0.3.0",
)


def parse_page_spec(
    page_spec: str | None,
    total_pages: int,
) -> list[int]:
    """Разобрать строку вида ``1,3,5-8`` в физические номера страниц."""

    if (
        not page_spec
        or not page_spec.strip()
    ):
        return list(
            range(
                1,
                total_pages + 1,
            )
        )

    result: set[
        int
    ] = set()

    for raw_part in page_spec.split(
        ","
    ):
        part = raw_part.strip()

        if not part:
            continue

        if "-" in part:
            (
                start_text,
                end_text,
            ) = [
                value.strip()
                for value in part.split(
                    "-",
                    maxsplit=1,
                )
            ]

            start = int(
                start_text
            )

            end = int(
                end_text
            )

            if start > end:
                raise ValueError(
                    "Начало диапазона "
                    f"больше конца: {part}"
                )

            result.update(
                range(
                    start,
                    end + 1,
                )
            )

        else:
            result.add(
                int(
                    part
                )
            )

    if not result:
        raise ValueError(
            "Не удалось определить "
            "страницы для анализа."
        )

    invalid_pages = [
        page_number
        for page_number in sorted(
            result
        )
        if (
            page_number < 1
            or page_number > total_pages
        )
    ]

    if invalid_pages:
        raise ValueError(
            "Страницы выходят за пределы документа: "
            + ", ".join(
                map(
                    str,
                    invalid_pages,
                )
            )
        )

    return sorted(
        result
    )


def classify_page(
    text: str,
    page_number: int,
) -> str:
    """Грубая классификация листа до обращения к VLM."""

    normalized = re.sub(
        r"\s+",
        " ",
        text.lower(),
    )

    if (
        page_number == 1
        and any(
            marker in normalized
            for marker in (
                "рабочая документация",
                "проектная документация",
            )
        )
    ):
        return "title"

    if any(
        marker in normalized
        for marker in (
            "спецификация оборудования",
            "спецификация изделий",
            "спецификация материалов",
        )
    ):
        return "specification"

    if any(
        marker in normalized
        for marker in (
            "кабельный журнал",
            "ведомость объемов",
            "ведомость объёмов",
        )
    ):
        return "table"

    if "общие указания" in normalized:
        return "general_notes"

    if any(
        marker in normalized
        for marker in (
            "общие данные",
            "ведомость документов",
            "ведомость ссылочных документов",
        )
    ):
        return "general_data"

    if "схема" in normalized:
        return "scheme"

    if any(
        marker in normalized
        for marker in (
            "план расположения",
            "план прокладки",
            "чертеж общего вида",
            "чертёж общего вида",
        )
    ):
        return "drawing"

    return "unknown"


def render_page(
    page: fitz.Page,
) -> bytes:
    """Отрендерить PDF-страницу в PNG для Qwen3-VL."""

    page_rect = page.rect

    largest_side = max(
        page_rect.width,
        page_rect.height,
    )

    if largest_side <= 0:
        raise ValueError(
            "Некорректный размер "
            "страницы PDF."
        )

    scale = (
        PDF_RENDER_MAX_SIDE
        / largest_side
    )

    scale = min(
        max(
            scale,
            0.5,
        ),
        3.0,
    )

    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(
            scale,
            scale,
        ),
        alpha=False,
        colorspace=fitz.csRGB,
    )

    return pixmap.tobytes(
        "png"
    )


async def get_ollama_models() -> list[str]:
    """Получить список уже загруженных моделей Ollama."""

    try:
        async with httpx.AsyncClient(
            timeout=10.0,
        ) as client:
            response = await client.get(
                f"{OLLAMA_BASE_URL}/api/tags"
            )

            response.raise_for_status()

    except httpx.HTTPError:
        return []

    return [
        str(
            model.get(
                "name",
                "",
            )
        )
        for model in (
            response.json().get(
                "models",
                [],
            )
        )
    ]


def build_normative_queries(
    *,
    page_facts: dict[str, Any],
    extracted_text: str,
) -> list[str]:
    """Собрать VLM-темы плюс один широкий fact-based запрос."""

    queries = [
        str(
            query
        ).strip()
        for query in (
            page_facts.get(
                "normative_queries",
                [],
            )
        )
        if str(
            query
        ).strip()
    ]

    objects = "; ".join(
        str(
            item
        )
        for item in (
            page_facts.get(
                "objects",
                [],
            )[:10]
        )
    )

    connections = "; ".join(
        str(
            item
        )
        for item in (
            page_facts.get(
                "connections",
                [],
            )[:8]
        )
    )

    labels = "; ".join(
        str(
            item
        )
        for item in (
            page_facts.get(
                "labels",
                [],
            )[:10]
        )
    )

    broad_query = (
        "Подобрать применимые требования "
        "для проверки инженерного листа. "
        f"Дисциплина: "
        f"{page_facts.get('discipline', '')}. "
        f"Тип листа: "
        f"{page_facts.get('page_type', '')}. "
        f"Содержание: "
        f"{page_facts.get('summary', '')}. "
        f"Объекты: {objects}. "
        f"Связи: {connections}. "
        f"Обозначения: {labels}."
    ).strip()

    if broad_query:
        queries.append(
            broad_query
        )

    if (
        not queries
        and extracted_text.strip()
    ):
        queries.append(
            "Подобрать применимые нормативные "
            "требования для листа: "
            + extracted_text[:1800]
        )

    result: list[
        str
    ] = []

    seen: set[
        str
    ] = set()

    for query in queries:
        if query in seen:
            continue

        seen.add(
            query
        )

        result.append(
            query
        )

    return result[:7]


def _select_by_source_ids(
    sources: list[
        dict[str, Any]
    ],
    source_ids: list[str],
) -> list[dict[str, Any]]:
    """Выбрать только реально найденные источники по локальным N/E-id."""

    by_id = {
        str(
            source.get(
                "source_id"
            )
        ): source
        for source in sources
        if source.get(
            "source_id"
        )
    }

    result = []

    for source_id in source_ids:
        source = by_id.get(
            str(
                source_id
            )
        )

        if (
            source is not None
            and source not in result
        ):
            result.append(
                source
            )

    return result


def _basis_from_norms(
    sources: list[
        dict[str, Any]
    ],
) -> str:
    """Сформировать основание только из реально выбранных Qdrant-источников."""

    parts = []

    for source in sources:
        source_file = source.get(
            "source_file"
        )

        page = source.get(
            "page"
        )

        if not source_file:
            continue

        if page is None:
            parts.append(
                str(
                    source_file
                )
            )

        else:
            parts.append(
                f"{source_file}, "
                f"PDF стр. {page}"
            )

    return "; ".join(
        parts
    )


def _compact_normative_for_api(
    sources: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    """Не отдавать во frontend весь текст нормативного chunk."""

    return [
        {
            "source_id": source.get(
                "source_id"
            ),
            "score": source.get(
                "score"
            ),
            "source_file": source.get(
                "source_file"
            ),
            "source_path": source.get(
                "source_path"
            ),
            "page": source.get(
                "page"
            ),
            "chunk_index": source.get(
                "chunk_index"
            ),
            "text_excerpt": str(
                source.get(
                    "text",
                    "",
                )
            )[:650],
        }
        for source in sources
    ]


def _compact_experience_for_api(
    sources: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    """Отдать только полезные метаданные похожего экспертного опыта."""

    return [
        {
            "source_id": source.get(
                "source_id"
            ),
            "score": source.get(
                "score"
            ),
            "project_id": source.get(
                "project_id"
            ),
            "issue_id": source.get(
                "issue_id"
            ),
            "issue_text": source.get(
                "issue_text"
            ),
            "verified_fixed": source.get(
                "verified_fixed",
                False,
            ),
            "before_page": source.get(
                "before_page"
            ),
            "after_page": source.get(
                "after_page"
            ),
        }
        for source in sources
    ]


@app.get(
    "/health/live",
)
async def health_live() -> dict[
    str,
    str,
]:
    """Liveness probe."""

    return {
        "status": "ok",
        "service": "pdf-service",
    }


@app.get(
    "/health/ready",
)
async def health_ready() -> dict[
    str,
    Any,
]:
    """Проверить обе модели, Qdrant и требуемые коллекции."""

    models = await get_ollama_models()

    rag_status = (
        await get_rag_status()
    )

    vision_available = (
        OLLAMA_VISION_MODEL
        in models
    )

    embedding_available = (
        OLLAMA_EMBEDDING_MODEL
        in models
    )

    collections_ready = bool(
        rag_status.get(
            "collections_ready"
        )
    )

    ready = (
        vision_available
        and embedding_available
        and collections_ready
    )

    return {
        "status": (
            "ready"
            if ready
            else "not_ready"
        ),
        "vision_model": (
            OLLAMA_VISION_MODEL
        ),
        "vision_model_available": (
            vision_available
        ),
        "embedding_model": (
            OLLAMA_EMBEDDING_MODEL
        ),
        "embedding_model_available": (
            embedding_available
        ),
        "installed_models": models,
        "rag": rag_status,
    }


@app.get(
    "/rag/search",
)
async def rag_search(
    q: str,
) -> dict[str, Any]:
    """Оставить диагностический endpoint ручного RAG-поиска."""

    try:
        return await search_knowledge(
            q
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(
                exc
            ),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(
                exc
            ),
        ) from exc


@app.post(
    "/inspect",
)
async def inspect_pdf(
    file: UploadFile = File(...),
    pages: str | None = Form(
        default=None,
    ),
) -> dict[str, Any]:
    """Проверить PDF и извлечённый текст без LLM/RAG."""

    pdf_bytes = await file.read()

    if not pdf_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                "Передан пустой PDF-файл."
            ),
        )

    try:
        with fitz.open(
            stream=pdf_bytes,
            filetype="pdf",
        ) as document:
            total_pages = len(
                document
            )

            selected_pages = (
                parse_page_spec(
                    pages,
                    total_pages,
                )
            )

            page_info = []

            for page_number in (
                selected_pages
            ):
                page = document[
                    page_number - 1
                ]

                text = page.get_text(
                    "text",
                    sort=True,
                )

                page_info.append(
                    {
                        "page": (
                            page_number
                        ),
                        "page_type": (
                            classify_page(
                                text,
                                page_number,
                            )
                        ),
                        "text_length": (
                            len(
                                text
                            )
                        ),
                        "text_preview": (
                            text[:500]
                        ),
                    }
                )

    except (
        fitz.FileDataError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(
                exc
            ),
        ) from exc

    return {
        "status": "ok",
        "file_name": (
            file.filename
        ),
        "total_pages": (
            total_pages
        ),
        "selected_pages": (
            selected_pages
        ),
        "pages": page_info,
    }


@app.post(
    "/analyze",
)
async def analyze_pdf(
    file: UploadFile = File(...),
    pages: str | None = Form(
        default=None,
    ),
) -> dict[str, Any]:
    """Проверить PDF по нормативной базе и оформить замечания по Базе Опыта."""

    started_at = (
        time.perf_counter()
    )

    pdf_bytes = await file.read()

    if not pdf_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                "Передан пустой PDF-файл."
            ),
        )

    models = (
        await get_ollama_models()
    )

    missing_models = [
        model
        for model in (
            OLLAMA_VISION_MODEL,
            OLLAMA_EMBEDDING_MODEL,
        )
        if model not in models
    ]

    if missing_models:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Не все необходимые "
                    "Ollama-модели загружены."
                ),
                "missing_models": (
                    missing_models
                ),
                "installed_models": (
                    models
                ),
            },
        )

    rag_status = (
        await get_rag_status()
    )

    if not rag_status.get(
        "collections_ready"
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Qdrant-коллекции "
                    "ещё не готовы."
                ),
                "rag": rag_status,
            },
        )

    try:
        with fitz.open(
            stream=pdf_bytes,
            filetype="pdf",
        ) as document:
            total_pages = len(
                document
            )

            selected_pages = (
                parse_page_spec(
                    pages,
                    total_pages,
                )
            )

            if (
                len(
                    selected_pages
                )
                > PDF_MAX_ANALYSIS_PAGES
            ):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": (
                            "Слишком много страниц "
                            "для одного анализа."
                        ),
                        "selected": len(
                            selected_pages
                        ),
                        "limit": (
                            PDF_MAX_ANALYSIS_PAGES
                        ),
                    },
                )

            page_results: list[
                dict[str, Any]
            ] = []

            all_issues: list[
                dict[str, Any]
            ] = []

            for page_number in (
                selected_pages
            ):
                page = document[
                    page_number - 1
                ]

                extracted_text = (
                    page.get_text(
                        "text",
                        sort=True,
                    )[:PDF_TEXT_LIMIT]
                )

                heuristic_page_type = (
                    classify_page(
                        extracted_text,
                        page_number,
                    )
                )

                image_bytes = (
                    render_page(
                        page
                    )
                )

                # Этап 1:
                # понять содержание листа,
                # но не искать ошибки.
                (
                    page_facts,
                    understanding_metrics,
                ) = await understand_page(
                    page_number=(
                        page_number
                    ),
                    heuristic_page_type=(
                        heuristic_page_type
                    ),
                    extracted_text=(
                        extracted_text
                    ),
                    image_bytes=(
                        image_bytes
                    ),
                )

                normative_queries = (
                    build_normative_queries(
                        page_facts=(
                            page_facts
                        ),
                        extracted_text=(
                            extracted_text
                        ),
                    )
                )

                # Этап 2:
                # найти применимые нормы
                # по содержанию листа.
                normative_result = (
                    await search_normative(
                        normative_queries
                    )
                )

                normative_sources = (
                    normative_result[
                        "sources"
                    ]
                )

                # Этап 3:
                # только теперь проверить
                # лист по найденным нормам.
                (
                    norm_check,
                    normative_check_metrics,
                ) = (
                    await check_page_against_norms(
                        page_number=(
                            page_number
                        ),
                        extracted_text=(
                            extracted_text
                        ),
                        page_facts=(
                            page_facts
                        ),
                        normative_sources=(
                            normative_sources
                        ),
                        image_bytes=(
                            image_bytes
                        ),
                    )
                )

                findings: list[
                    dict[str, Any]
                ] = []

                for (
                    index,
                    violation,
                ) in enumerate(
                    norm_check.get(
                        "violations",
                        [],
                    ),
                    start=1,
                ):
                    requested_ids = [
                        str(
                            source_id
                        )
                        for source_id in (
                            violation.get(
                                "normative_source_ids",
                                [],
                            )
                        )
                    ]

                    selected_norms = (
                        _select_by_source_ids(
                            normative_sources,
                            requested_ids,
                        )
                    )

                    # В финальный отчёт
                    # не пропускаем норму,
                    # которой нет в Qdrant.
                    if not selected_norms:
                        continue

                    finding_id = (
                        f"p{page_number}"
                        f"-f{index}"
                    )

                    findings.append(
                        {
                            **violation,
                            "finding_id": (
                                finding_id
                            ),
                            "page": (
                                page_number
                            ),
                            "page_type": (
                                page_facts.get(
                                    "page_type"
                                )
                                or (
                                    heuristic_page_type
                                )
                            ),
                            "basis": (
                                _basis_from_norms(
                                    selected_norms
                                )
                            ),
                            "basis_sources": (
                                _compact_normative_for_api(
                                    selected_norms
                                )
                            ),
                        }
                    )

                # Этап 4:
                # опыт ищется только ПОСЛЕ
                # нормативной проверки.
                experience_by_finding: dict[
                    str,
                    list[dict[str, Any]],
                ] = {}

                if findings:
                    experience_queries = [
                        build_experience_query(
                            finding
                        )
                        for finding in findings
                    ]

                    experience_results = (
                        await search_experience_many(
                            experience_queries
                        )
                    )

                    for (
                        finding,
                        experience_result,
                    ) in zip(
                        findings,
                        experience_results,
                        strict=True,
                    ):
                        experience_by_finding[
                            str(
                                finding[
                                    "finding_id"
                                ]
                            )
                        ] = (
                            experience_result[
                                "sources"
                            ]
                        )

                # Этап 5:
                # опыт используется только
                # для формулировки
                # и рекомендации.
                (
                    final_result,
                    final_metrics,
                ) = await finalize_findings(
                    findings=findings,
                    experience_by_finding=(
                        experience_by_finding
                    ),
                )

                final_by_id = {
                    str(
                        item.get(
                            "finding_id"
                        )
                    ): item
                    for item in (
                        final_result.get(
                            "findings",
                            [],
                        )
                    )
                    if item.get(
                        "finding_id"
                    )
                }

                page_issues: list[
                    dict[str, Any]
                ] = []

                for finding in findings:
                    finding_id = str(
                        finding[
                            "finding_id"
                        ]
                    )

                    formatted = (
                        final_by_id.get(
                            finding_id,
                            {},
                        )
                    )

                    experience_sources = (
                        experience_by_finding.get(
                            finding_id,
                            [],
                        )
                    )

                    requested_experience_ids = [
                        str(
                            source_id
                        )
                        for source_id in (
                            formatted.get(
                                "experience_source_ids",
                                [],
                            )
                        )
                    ]

                    selected_experience = (
                        _select_by_source_ids(
                            experience_sources,
                            requested_experience_ids,
                        )
                    )

                    issue = {
                        "finding_id": (
                            finding_id
                        ),
                        "page": finding[
                            "page"
                        ],
                        "page_type": finding[
                            "page_type"
                        ],
                        "category": (
                            finding.get(
                                "category"
                            )
                        ),
                        "severity": (
                            finding.get(
                                "severity"
                            )
                        ),
                        "status": finding.get(
                            "status"
                        ),
                        "comment": (
                            formatted.get(
                                "comment"
                            )
                            or finding.get(
                                "comment",
                                "",
                            )
                        ),
                        "evidence": (
                            finding.get(
                                "evidence",
                                "",
                            )
                        ),
                        "recommendation": (
                            formatted.get(
                                "recommendation"
                            )
                            or finding.get(
                                "recommendation_draft",
                                "",
                            )
                        ),
                        "confidence": (
                            finding.get(
                                "confidence",
                                0.0,
                            )
                        ),
                        "basis": finding.get(
                            "basis",
                            "",
                        ),
                        "basis_sources": (
                            finding.get(
                                "basis_sources",
                                [],
                            )
                        ),
                        "experience_sources": (
                            _compact_experience_for_api(
                                selected_experience
                            )
                        ),
                    }

                    page_issues.append(
                        issue
                    )

                    all_issues.append(
                        issue
                    )

                page_results.append(
                    {
                        "page": (
                            page_number
                        ),
                        "page_type": (
                            page_facts.get(
                                "page_type"
                            )
                            or heuristic_page_type
                        ),
                        "discipline": (
                            page_facts.get(
                                "discipline",
                                "",
                            )
                        ),
                        "summary": (
                            page_facts.get(
                                "summary",
                                "",
                            )
                        ),
                        "page_facts": {
                            "objects": (
                                page_facts.get(
                                    "objects",
                                    [],
                                )
                            ),
                            "connections": (
                                page_facts.get(
                                    "connections",
                                    [],
                                )
                            ),
                            "labels": (
                                page_facts.get(
                                    "labels",
                                    [],
                                )
                            ),
                        },
                        "normative_queries": (
                            normative_queries
                        ),
                        "normative_sources": (
                            _compact_normative_for_api(
                                normative_sources
                            )
                        ),
                        "normative_check_summary": (
                            norm_check.get(
                                "summary",
                                "",
                            )
                        ),
                        "final_summary": (
                            final_result.get(
                                "summary",
                                "",
                            )
                        ),
                        "issues": (
                            page_issues
                        ),
                        "metrics": {
                            "understanding": (
                                understanding_metrics
                            ),
                            "normative_check": (
                                normative_check_metrics
                            ),
                            "finalization": (
                                final_metrics
                            ),
                        },
                    }
                )

    except fitz.FileDataError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "Файл не удалось открыть "
                "как PDF."
            ),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(
                exc
            ),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(
                exc
            ),
        ) from exc

    confirmed_count = sum(
        1
        for issue in all_issues
        if (
            issue.get(
                "status"
            )
            == "confirmed"
        )
    )

    needs_review_count = sum(
        1
        for issue in all_issues
        if (
            issue.get(
                "status"
            )
            == "needs_review"
        )
    )

    return {
        "status": "completed",
        "stage": (
            "normative_rag_experience"
        ),
        "file_name": (
            file.filename
        ),
        "vision_model": (
            OLLAMA_VISION_MODEL
        ),
        "embedding_model": (
            OLLAMA_EMBEDDING_MODEL
        ),
        "total_pages": (
            total_pages
        ),
        "selected_pages": (
            selected_pages
        ),
        "issues_count": len(
            all_issues
        ),
        "confirmed_count": (
            confirmed_count
        ),
        "needs_review_count": (
            needs_review_count
        ),
        "issues": (
            all_issues
        ),
        "pages": (
            page_results
        ),
        "elapsed_seconds": round(
            time.perf_counter()
            - started_at,
            2,
        ),
        "pipeline": [
            "page_understanding",
            "normative_retrieval",
            "normative_compliance_check",
            "experience_retrieval",
            "report_finalization",
        ],
        "limitations": [
            (
                "DXF пока не участвует в анализе; "
                "проверка связности выполняется "
                "по PDF-изображению и "
                "извлечённому тексту."
            ),
            (
                "Проверка выполняется по найденным "
                "RAG-фрагментам нормативной базы; "
                "если retrieval не нашёл применимую "
                "норму, соответствующая проверка "
                "может быть пропущена."
            ),
            (
                "База Опыта используется как пример "
                "формулировки и инженерного контекста, "
                "а не как доказательство нарушения."
            ),
            (
                "AFTER-лист из Базы Опыта "
                "не считается подтверждённым способом "
                "исправления, пока "
                "verified_fixed=false."
            ),
        ],
    }
