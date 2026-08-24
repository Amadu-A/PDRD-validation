# services/pdf-service/app/main.py

"""Анализ PDF и/или CAD с нормативным RAG, ПЗ-контекстом и Базой Опыта."""

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

from app.cad import (
    analyze_cad_bytes,
    build_cad_augmented_text,
    combine_source_images,
    compact_cad_for_api,
    get_cad_capabilities,
)
from app.project_context import (
    build_augmented_page_text,
    build_project_context_query,
    compact_project_context_for_api,
    create_temporary_project_context,
    delete_temporary_project_context,
    search_project_context,
    validate_explanatory_note_pages,
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


APP_NAME = "Drawing Validation Analysis Service"

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

CAD_MAX_UPLOAD_MB = int(
    os.getenv(
        "CAD_MAX_UPLOAD_MB",
        "200",
    )
)


app = FastAPI(
    title=APP_NAME,
    version="0.5.0",
)


def parse_page_spec(
    page_spec: str | None,
    total_pages: int,
) -> list[int]:
    """Разобрать строку ``1,3,5-8`` в физические номера PDF-страниц."""

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

    result: set[int] = set()

    for raw_part in page_spec.split(","):
        part = raw_part.strip()

        if not part:
            continue

        if "-" in part:
            start_text, end_text = [
                value.strip()
                for value in part.split(
                    "-",
                    maxsplit=1,
                )
            ]

            start = int(start_text)
            end = int(end_text)

            if start > end:
                raise ValueError(
                    "Начало диапазона больше конца: "
                    f"{part}"
                )

            result.update(
                range(
                    start,
                    end + 1,
                )
            )
        else:
            result.add(
                int(part)
            )

    if not result:
        raise ValueError(
            "Не удалось определить страницы для анализа."
        )

    invalid_pages = [
        page_number
        for page_number in sorted(result)
        if (
            page_number < 1
            or page_number > total_pages
        )
    ]

    if invalid_pages:
        raise ValueError(
            "Страницы выходят за пределы документа: "
            + ", ".join(
                map(str, invalid_pages)
            )
        )

    return sorted(result)


def parse_single_pdf_page_for_cad(
    page_spec: str | None,
    total_pages: int,
) -> int:
    """При PDF+CAD потребовать ровно одну явно указанную PDF-страницу."""

    normalized = (
        page_spec
        or ""
    ).strip()

    if not re.fullmatch(
        r"[1-9]\d*",
        normalized,
    ):
        raise ValueError(
            "При совместном анализе PDF + DWG/DXF "
            "нужно явно указать ровно одну страницу PDF, "
            "которая соответствует CAD-файлу."
        )

    page_number = int(normalized)

    if page_number > total_pages:
        raise ValueError(
            "Указанная страница для CAD выходит за пределы PDF: "
            f"{page_number}; всего страниц: {total_pages}."
        )

    return page_number


def validate_explanatory_note_range(
    *,
    enabled: bool,
    start_page: str | None,
    end_page: str | None,
    total_pages: int,
) -> tuple[list[int], int | None, int | None]:
    """Проверить пользовательский диапазон ПЗ."""

    if not enabled:
        return [], None, None

    if not start_page or not end_page:
        raise ValueError(
            "При включённом контексте ПЗ необходимо указать "
            "начальную и конечную страницы."
        )

    try:
        start = int(start_page)
        end = int(end_page)
    except ValueError as exc:
        raise ValueError(
            "Номера страниц ПЗ должны быть целыми числами."
        ) from exc

    if start < 1 or end < 1:
        raise ValueError(
            "Номера страниц ПЗ должны быть положительными."
        )

    if end <= start:
        raise ValueError(
            "Конечная страница ПЗ должна быть больше начальной."
        )

    if start > total_pages or end > total_pages:
        raise ValueError(
            "Диапазон ПЗ выходит за пределы документа. "
            f"В PDF всего страниц: {total_pages}; "
            f"получен диапазон: {start}-{end}."
        )

    return (
        list(
            range(
                start,
                end + 1,
            )
        ),
        start,
        end,
    )


def classify_page(
    text: str,
    page_number: int,
) -> str:
    """Грубая классификация PDF-листа до VLM."""

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
    """Отрендерить PDF-страницу в PNG."""

    largest_side = max(
        page.rect.width,
        page.rect.height,
    )

    if largest_side <= 0:
        raise ValueError(
            "Некорректный размер страницы PDF."
        )

    scale = (
        PDF_RENDER_MAX_SIDE
        / largest_side
    )

    scale = min(
        max(scale, 0.5),
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
    """Получить модели shared Ollama."""

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
        for model in response.json().get(
            "models",
            [],
        )
    ]


def build_normative_queries(
    *,
    page_facts: dict[str, Any],
    extracted_text: str,
    project_context_sources: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Собрать нейтральные запросы к нормативной базе."""

    queries = [
        str(query).strip()
        for query in page_facts.get(
            "normative_queries",
            [],
        )
        if str(query).strip()
    ]

    objects = "; ".join(
        str(item)
        for item in page_facts.get(
            "objects",
            [],
        )[:10]
    )

    connections = "; ".join(
        str(item)
        for item in page_facts.get(
            "connections",
            [],
        )[:8]
    )

    labels = "; ".join(
        str(item)
        for item in page_facts.get(
            "labels",
            [],
        )[:10]
    )

    pz_hint = ""

    if project_context_sources:
        pz_hint = " ".join(
            str(
                source.get(
                    "text",
                    "",
                )
            )[:300]
            for source in project_context_sources[:3]
        )

    queries.append(
        (
            "Подобрать применимые требования для проверки "
            "инженерного листа. "
            f"Дисциплина: {page_facts.get('discipline', '')}. "
            f"Тип листа: {page_facts.get('page_type', '')}. "
            f"Содержание: {page_facts.get('summary', '')}. "
            f"Объекты: {objects}. "
            f"Связи: {connections}. "
            f"Обозначения: {labels}. "
            f"Контекст ПЗ проекта: {pz_hint}"
        ).strip()
    )

    if (
        not queries
        and extracted_text.strip()
    ):
        queries.append(
            "Подобрать применимые нормативные требования: "
            + extracted_text[:1800]
        )

    result: list[str] = []
    seen: set[str] = set()

    for query in queries:
        if not query or query in seen:
            continue
        seen.add(query)
        result.append(query)

    return result[:7]


def _select_by_source_ids(
    sources: list[dict[str, Any]],
    source_ids: list[str],
) -> list[dict[str, Any]]:
    """Выбрать реально существующие N/E ids."""

    by_id = {
        str(source.get("source_id")): source
        for source in sources
        if source.get("source_id")
    }

    result = []

    for source_id in source_ids:
        source = by_id.get(
            str(source_id)
        )
        if (
            source is not None
            and source not in result
        ):
            result.append(source)

    return result


def _basis_from_norms(
    sources: list[dict[str, Any]],
) -> str:
    """Сформировать нормативное основание только из Qdrant sources."""

    parts = []

    for source in sources:
        source_file = source.get(
            "source_file"
        )
        page = source.get("page")

        if not source_file:
            continue

        if page is None:
            parts.append(
                str(source_file)
            )
        else:
            parts.append(
                f"{source_file}, PDF стр. {page}"
            )

    return "; ".join(parts)


def _compact_normative_for_api(
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Сократить нормативные sources для ответа API."""

    return [
        {
            "source_id": source.get("source_id"),
            "score": source.get("score"),
            "source_file": source.get("source_file"),
            "source_path": source.get("source_path"),
            "page": source.get("page"),
            "chunk_index": source.get("chunk_index"),
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
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Сократить Experience results."""

    return [
        {
            "source_id": source.get("source_id"),
            "score": source.get("score"),
            "project_id": source.get("project_id"),
            "issue_id": source.get("issue_id"),
            "issue_text": source.get("issue_text"),
            "verified_fixed": source.get(
                "verified_fixed",
                False,
            ),
            "before_page": source.get("before_page"),
            "after_page": source.get("after_page"),
        }
        for source in sources
    ]


def _source_mode(
    *,
    has_pdf: bool,
    has_cad: bool,
) -> str:
    """Определить режим анализа."""

    if has_pdf and has_cad:
        return "pdf_cad"
    if has_pdf:
        return "pdf_only"
    if has_cad:
        return "cad_only"
    raise ValueError(
        "Необходимо загрузить PDF и/или DWG/DXF."
    )


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    """Liveness probe."""

    return {
        "status": "ok",
        "service": "pdf-service",
    }


@app.get("/health/ready")
async def health_ready() -> dict[str, Any]:
    """Проверить модели, Qdrant и CAD capabilities."""

    models = await get_ollama_models()
    rag_status = await get_rag_status()
    cad_capabilities = get_cad_capabilities()

    vision_available = (
        OLLAMA_VISION_MODEL in models
    )
    embedding_available = (
        OLLAMA_EMBEDDING_MODEL in models
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
        and cad_capabilities["dxf"]
        and cad_capabilities["dwg"]
    )

    return {
        "status": (
            "ready"
            if ready
            else "not_ready"
        ),
        "vision_model": OLLAMA_VISION_MODEL,
        "vision_model_available": vision_available,
        "embedding_model": OLLAMA_EMBEDDING_MODEL,
        "embedding_model_available": embedding_available,
        "installed_models": models,
        "rag": rag_status,
        "cad": cad_capabilities,
    }


@app.get("/rag/search")
async def rag_search(
    q: str,
) -> dict[str, Any]:
    """Диагностический RAG endpoint."""

    try:
        return await search_knowledge(q)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


@app.post("/inspect")
async def inspect_pdf(
    file: UploadFile = File(...),
    pages: str | None = Form(default=None),
) -> dict[str, Any]:
    """Старый диагностический PDF inspect endpoint."""

    pdf_bytes = await file.read()

    if not pdf_bytes:
        raise HTTPException(
            status_code=400,
            detail="Передан пустой PDF-файл.",
        )

    try:
        with fitz.open(
            stream=pdf_bytes,
            filetype="pdf",
        ) as document:
            total_pages = len(document)
            selected_pages = parse_page_spec(
                pages,
                total_pages,
            )

            page_info = []

            for page_number in selected_pages:
                page = document[
                    page_number - 1
                ]
                text = page.get_text(
                    "text",
                    sort=True,
                )

                page_info.append(
                    {
                        "page": page_number,
                        "page_type": classify_page(
                            text,
                            page_number,
                        ),
                        "text_length": len(text),
                        "text_preview": text[:500],
                    }
                )

    except (
        fitz.FileDataError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    return {
        "status": "ok",
        "file_name": file.filename,
        "total_pages": total_pages,
        "selected_pages": selected_pages,
        "pages": page_info,
    }


@app.post("/analyze")
async def analyze_document(
    file: UploadFile | None = File(
        default=None,
    ),
    cad_file: UploadFile | None = File(
        default=None,
    ),
    pages: str | None = Form(
        default=None,
    ),
    use_explanatory_note: bool = Form(
        default=False,
    ),
    note_start_page: str | None = Form(
        default=None,
    ),
    note_end_page: str | None = Form(
        default=None,
    ),
) -> dict[str, Any]:
    """Проанализировать PDF, CAD либо PDF+CAD как один инженерный лист."""

    started_at = time.perf_counter()

    has_pdf = bool(
        file
        and file.filename
    )
    has_cad = bool(
        cad_file
        and cad_file.filename
    )

    try:
        mode = _source_mode(
            has_pdf=has_pdf,
            has_cad=has_cad,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    if (
        use_explanatory_note
        and not has_pdf
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Контекст ПЗ доступен только при загруженном PDF. "
                "Для CAD-only анализа снимите галочку ПЗ."
            ),
        )

    pdf_bytes = (
        await file.read()
        if has_pdf and file
        else None
    )

    cad_bytes = (
        await cad_file.read()
        if has_cad and cad_file
        else None
    )

    if (
        pdf_bytes is not None
        and not pdf_bytes
    ):
        raise HTTPException(
            status_code=400,
            detail="Передан пустой PDF-файл.",
        )

    if (
        cad_bytes is not None
        and not cad_bytes
    ):
        raise HTTPException(
            status_code=400,
            detail="Передан пустой CAD-файл.",
        )

    if (
        cad_bytes is not None
        and len(cad_bytes)
        > CAD_MAX_UPLOAD_MB
        * 1024
        * 1024
    ):
        raise HTTPException(
            status_code=413,
            detail=(
                "CAD-файл слишком большой. "
                f"Лимит: {CAD_MAX_UPLOAD_MB} МБ."
            ),
        )

    models = await get_ollama_models()

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
                    "Не все необходимые Ollama-модели загружены."
                ),
                "missing_models": missing_models,
                "installed_models": models,
            },
        )

    rag_status = await get_rag_status()

    if not rag_status.get(
        "collections_ready"
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Qdrant-коллекции ещё не готовы."
                ),
                "rag": rag_status,
            },
        )

    project_context_collection: str | None = None
    cad_result: dict[str, Any] | None = None
    document: fitz.Document | None = None

    explanatory_note_result: dict[str, Any] = {
        "enabled": False,
    }

    all_issues: list[dict[str, Any]] = []
    page_results: list[dict[str, Any]] = []

    try:
        if cad_bytes is not None and cad_file is not None:
            cad_result = analyze_cad_bytes(
                cad_bytes=cad_bytes,
                filename=(
                    cad_file.filename
                    or "drawing.dxf"
                ),
            )

        if pdf_bytes is not None:
            try:
                document = fitz.open(
                    stream=pdf_bytes,
                    filetype="pdf",
                )
            except fitz.FileDataError as exc:
                raise ValueError(
                    "Файл не удалось открыть как PDF."
                ) from exc

            total_pages = len(document)

            if mode == "pdf_cad":
                selected_pages = [
                    parse_single_pdf_page_for_cad(
                        pages,
                        total_pages,
                    )
                ]
            else:
                selected_pages = parse_page_spec(
                    pages,
                    total_pages,
                )

            if len(selected_pages) > PDF_MAX_ANALYSIS_PAGES:
                raise ValueError(
                    "Слишком много страниц для одного анализа: "
                    f"{len(selected_pages)}; "
                    f"лимит={PDF_MAX_ANALYSIS_PAGES}."
                )

            (
                note_pages,
                validated_note_start,
                validated_note_end,
            ) = validate_explanatory_note_range(
                enabled=use_explanatory_note,
                start_page=note_start_page,
                end_page=note_end_page,
                total_pages=total_pages,
            )

            if note_pages:
                note_texts = {
                    page_number: document[
                        page_number - 1
                    ].get_text(
                        "text",
                        sort=True,
                    )[:PDF_TEXT_LIMIT]
                    for page_number in note_pages
                }

                note_validation = (
                    await validate_explanatory_note_pages(
                        note_texts
                    )
                )

                context_info = (
                    await create_temporary_project_context(
                        note_texts
                    )
                )

                project_context_collection = str(
                    context_info[
                        "collection_name"
                    ]
                )

                explanatory_note_result = {
                    "enabled": True,
                    "start_page": validated_note_start,
                    "end_page": validated_note_end,
                    "pages_count": len(note_pages),
                    "indexed_chunks": context_info[
                        "chunks_count"
                    ],
                    "vector_size": context_info[
                        "vector_size"
                    ],
                    "validation": note_validation,
                }
        else:
            total_pages = 0
            selected_pages = [1]

            if pages and pages.strip():
                raise ValueError(
                    "Для CAD-only анализа поле страниц PDF "
                    "должно быть пустым. CAD-файл считается одним листом."
                )

        for page_number in selected_pages:
            pdf_text: str | None = None
            pdf_image: bytes | None = None

            if document is not None:
                pdf_page = document[
                    page_number - 1
                ]

                pdf_text = pdf_page.get_text(
                    "text",
                    sort=True,
                )[:PDF_TEXT_LIMIT]

                pdf_image = render_page(
                    pdf_page
                )

            if mode == "pdf_only":
                assert pdf_text is not None
                assert pdf_image is not None

                extracted_text = pdf_text
                image_bytes = pdf_image
                heuristic_page_type = classify_page(
                    pdf_text,
                    page_number,
                )

            elif mode == "pdf_cad":
                assert pdf_text is not None
                assert pdf_image is not None
                assert cad_result is not None

                extracted_text = build_cad_augmented_text(
                    pdf_text=pdf_text,
                    cad_result=cad_result,
                )

                image_bytes = combine_source_images(
                    pdf_image_bytes=pdf_image,
                    cad_image_bytes=(
                        cad_result[
                            "render_bytes"
                        ]
                    ),
                )

                heuristic_page_type = (
                    "pdf_cad_drawing"
                )

            else:
                assert cad_result is not None

                extracted_text = build_cad_augmented_text(
                    pdf_text=None,
                    cad_result=cad_result,
                )

                image_bytes = cad_result[
                    "render_bytes"
                ]

                heuristic_page_type = (
                    "cad_drawing"
                )

            (
                page_facts,
                understanding_metrics,
            ) = await understand_page(
                page_number=page_number,
                heuristic_page_type=(
                    heuristic_page_type
                ),
                extracted_text=extracted_text,
                image_bytes=image_bytes,
            )

            project_context_sources: list[
                dict[str, Any]
            ] = []

            if project_context_collection:
                project_query = (
                    build_project_context_query(
                        page_facts=page_facts,
                        extracted_text=(
                            extracted_text
                        ),
                    )
                )

                project_context_sources = (
                    await search_project_context(
                        project_context_collection,
                        project_query,
                    )
                )

            normative_queries = (
                build_normative_queries(
                    page_facts=page_facts,
                    extracted_text=(
                        extracted_text
                    ),
                    project_context_sources=(
                        project_context_sources
                    ),
                )
            )

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

            analysis_text = (
                build_augmented_page_text(
                    extracted_text=extracted_text,
                    project_context_sources=(
                        project_context_sources
                    ),
                )
            )

            (
                norm_check,
                normative_check_metrics,
            ) = await check_page_against_norms(
                page_number=page_number,
                extracted_text=analysis_text,
                page_facts=page_facts,
                normative_sources=(
                    normative_sources
                ),
                image_bytes=image_bytes,
            )

            findings: list[
                dict[str, Any]
            ] = []

            for index, violation in enumerate(
                norm_check.get(
                    "violations",
                    [],
                ),
                start=1,
            ):
                requested_ids = [
                    str(source_id)
                    for source_id in violation.get(
                        "normative_source_ids",
                        [],
                    )
                ]

                selected_norms = (
                    _select_by_source_ids(
                        normative_sources,
                        requested_ids,
                    )
                )

                if not selected_norms:
                    continue

                finding_id = (
                    f"p{page_number}-f{index}"
                )

                findings.append(
                    {
                        **violation,
                        "finding_id": finding_id,
                        "page": page_number,
                        "page_type": (
                            page_facts.get(
                                "page_type"
                            )
                            or heuristic_page_type
                        ),
                        "basis": _basis_from_norms(
                            selected_norms
                        ),
                        "basis_sources": (
                            _compact_normative_for_api(
                                selected_norms
                            )
                        ),
                    }
                )

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

                for finding, experience_result in zip(
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
                    ] = experience_result[
                        "sources"
                    ]

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
                for item in final_result.get(
                    "findings",
                    [],
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

                formatted = final_by_id.get(
                    finding_id,
                    {},
                )

                experience_sources = (
                    experience_by_finding.get(
                        finding_id,
                        [],
                    )
                )

                requested_experience_ids = [
                    str(source_id)
                    for source_id in formatted.get(
                        "experience_source_ids",
                        [],
                    )
                ]

                selected_experience = (
                    _select_by_source_ids(
                        experience_sources,
                        requested_experience_ids,
                    )
                )

                issue = {
                    "finding_id": finding_id,
                    "source_mode": mode,
                    "page": page_number,
                    "page_type": finding[
                        "page_type"
                    ],
                    "category": finding.get(
                        "category"
                    ),
                    "severity": finding.get(
                        "severity"
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
                    "evidence": finding.get(
                        "evidence",
                        "",
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
                    "confidence": finding.get(
                        "confidence",
                        0.0,
                    ),
                    "basis": finding.get(
                        "basis",
                        "",
                    ),
                    "basis_sources": finding.get(
                        "basis_sources",
                        [],
                    ),
                    "experience_sources": (
                        _compact_experience_for_api(
                            selected_experience
                        )
                    ),
                    "project_context_sources": (
                        compact_project_context_for_api(
                            project_context_sources
                        )
                    ),
                }

                page_issues.append(issue)
                all_issues.append(issue)

            page_results.append(
                {
                    "page": page_number,
                    "source_mode": mode,
                    "page_type": (
                        page_facts.get(
                            "page_type"
                        )
                        or heuristic_page_type
                    ),
                    "discipline": page_facts.get(
                        "discipline",
                        "",
                    ),
                    "summary": page_facts.get(
                        "summary",
                        "",
                    ),
                    "page_facts": {
                        "objects": page_facts.get(
                            "objects",
                            [],
                        ),
                        "connections": page_facts.get(
                            "connections",
                            [],
                        ),
                        "labels": page_facts.get(
                            "labels",
                            [],
                        ),
                    },
                    "cad": compact_cad_for_api(
                        cad_result
                    ),
                    "project_context_sources": (
                        compact_project_context_for_api(
                            project_context_sources
                        )
                    ),
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
                    "issues": page_issues,
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

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
    finally:
        if document is not None:
            document.close()

        if project_context_collection:
            await delete_temporary_project_context(
                project_context_collection
            )

    confirmed_count = sum(
        1
        for issue in all_issues
        if issue.get("status") == "confirmed"
    )

    needs_review_count = sum(
        1
        for issue in all_issues
        if issue.get("status") == "needs_review"
    )

    return {
        "status": "completed",
        "stage": (
            "normative_rag_cad_project_context_experience"
            if mode != "pdf_only"
            else "normative_rag_project_context_experience"
        ),
        "source_mode": mode,
        "file_name": (
            file.filename
            if has_pdf and file
            else cad_file.filename
            if cad_file
            else None
        ),
        "pdf_file_name": (
            file.filename
            if has_pdf and file
            else None
        ),
        "cad_file_name": (
            cad_file.filename
            if has_cad and cad_file
            else None
        ),
        "vision_model": OLLAMA_VISION_MODEL,
        "embedding_model": OLLAMA_EMBEDDING_MODEL,
        "total_pages": total_pages,
        "selected_pages": selected_pages,
        "explanatory_note_context": (
            explanatory_note_result
        ),
        "cad": compact_cad_for_api(
            cad_result
        ),
        "issues_count": len(all_issues),
        "confirmed_count": confirmed_count,
        "needs_review_count": needs_review_count,
        "issues": all_issues,
        "pages": page_results,
        "elapsed_seconds": round(
            time.perf_counter()
            - started_at,
            2,
        ),
        "pipeline": [
            "cad_normalization_and_parsing"
            if has_cad
            else "cad_skipped",
            "explanatory_note_validation"
            if use_explanatory_note
            else "explanatory_note_skipped",
            "temporary_project_context_index"
            if use_explanatory_note
            else "temporary_project_context_skipped",
            "visual_and_machine_understanding",
            "project_context_retrieval"
            if use_explanatory_note
            else "project_context_retrieval_skipped",
            "normative_retrieval",
            "normative_compliance_check",
            "experience_retrieval",
            "report_finalization",
        ],
        "limitations": [
            (
                "CAD connectivity в текущем MVP строится по геометрическим "
                "endpoint-координатам с заданным tolerance и пока не заменяет "
                "полноценную семантическую CAD-топологию."
            ),
            (
                "DWG нормализуется через LibreDWG/dwg2dxf. Для production "
                "может потребоваться более совместимый лицензированный converter."
            ),
            (
                "Контекст ПЗ доступен только при наличии PDF и создаётся "
                "во временной Qdrant collection на время одного запроса."
            ),
            (
                "Проверка выполняется по найденным RAG-фрагментам нормативной "
                "базы; ненайденное retrieval-требование может быть пропущено."
            ),
            (
                "База Опыта используется как пример формулировки, "
                "а не как доказательство нарушения."
            ),
        ],
    }
