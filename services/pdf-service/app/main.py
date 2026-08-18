# services/pdf-service/app/main.py

from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any

import fitz
import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from app.rag import (
    search_knowledge,
    search_knowledge_many,
)
from app.validator import (
    build_rag_query,
    build_rejected_prefilter_issue,
    build_unvalidated_issue,
    candidate_passes_prefilter,
    fallback_validation_decision,
    normalize_validated_candidate,
    validate_page_candidates,
)


APP_NAME = "Drawing Validation PDF Service"

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://ollama:11434",
).rstrip("/")

OLLAMA_VISION_MODEL = os.getenv(
    "OLLAMA_VISION_MODEL",
    "qwen3-vl:2b-instruct",
)

# services/pdf-service/app/main.py

OLLAMA_REQUEST_TIMEOUT = float(
    os.getenv(
        "OLLAMA_REQUEST_TIMEOUT",
        "1800",
    )
)

OLLAMA_NUM_CTX = int(
    os.getenv(
        "OLLAMA_NUM_CTX",
        "8192",
    )
)

OLLAMA_NUM_PREDICT = int(
    os.getenv(
        "OLLAMA_NUM_PREDICT",
        "1400",
    )
)

OLLAMA_MAX_ISSUES = int(
    os.getenv(
        "OLLAMA_MAX_ISSUES",
        "3",
    )
)

OLLAMA_MAX_RETRIES = int(
    os.getenv(
        "OLLAMA_MAX_RETRIES",
        "2",
    )
)

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
    version="0.2.0",
)


# services/pdf-service/app/main.py

ISSUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "string",
            "maxLength": 500,
        },
        "issues": {
            "type": "array",
            "maxItems": OLLAMA_MAX_ISSUES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "нормоконтроль",
                            "оборудование",
                            "логика_работы",
                            "соответствие_тз",
                            "текстовые_комментарии",
                            "требует_проверки",
                            "прочее",
                        ],
                    },
                    "severity": {
                        "type": "string",
                        "enum": [
                            "info",
                            "warning",
                            "error",
                        ],
                    },
                    "comment": {
                        "type": "string",
                        "maxLength": 500,
                    },
                    "evidence": {
                        "type": "string",
                        "maxLength": 500,
                    },
                    "recommendation": {
                        "type": "string",
                        "maxLength": 500,
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
                "required": [
                    "category",
                    "severity",
                    "comment",
                    "evidence",
                    "recommendation",
                    "confidence",
                ],
            },
        },
    },
    "required": [
        "summary",
        "issues",
    ],
}


def parse_page_spec(
    page_spec: str | None,
    total_pages: int,
) -> list[int]:
    """Разобрать строку вида ``1,3,5-8`` в номера страниц."""

    if not page_spec or not page_spec.strip():
        return list(range(1, total_pages + 1))

    result: set[int] = set()

    for raw_part in page_spec.split(","):
        part = raw_part.strip()

        if not part:
            continue

        if "-" in part:
            bounds = [
                value.strip()
                for value in part.split("-", maxsplit=1)
            ]

            if len(bounds) != 2:
                raise ValueError(
                    f"Некорректный диапазон страниц: {part}",
                )

            start = int(bounds[0])
            end = int(bounds[1])

            if start > end:
                raise ValueError(
                    f"Начало диапазона больше конца: {part}",
                )

            result.update(
                range(
                    start,
                    end + 1,
                )
            )

        else:
            result.add(int(part))

    if not result:
        raise ValueError(
            "Не удалось определить страницы для анализа.",
        )

    invalid_pages = [
        page_number
        for page_number in sorted(result)
        if page_number < 1
        or page_number > total_pages
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

    return sorted(result)


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

    if page_number == 1 and any(
        marker in normalized
        for marker in (
            "рабочая документация",
            "проектная документация",
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
    """Отрендерить страницу в PNG с ограничением максимального размера."""

    page_rect = page.rect

    largest_side = max(
        page_rect.width,
        page_rect.height,
    )

    if largest_side <= 0:
        raise ValueError(
            "Некорректный размер страницы PDF.",
        )

    scale = PDF_RENDER_MAX_SIDE / largest_side

    # Не увеличиваем маленькие страницы чрезмерно.
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

    return pixmap.tobytes("png")



def build_prompt(
    *,
    page_number: int,
    page_type: str,
    extracted_text: str,
) -> str:
    """Сформировать промпт первичной визуальной проверки."""

    text_fragment = extracted_text[:PDF_TEXT_LIMIT]

    return f"""
Ты выполняешь первичную техническую проверку одного листа
проектной или рабочей документации.

Страница PDF: {page_number}
Предварительный тип страницы: {page_type}

Извлечённый программно текст:

--- НАЧАЛО ТЕКСТА ---
{text_fragment}
--- КОНЕЦ ТЕКСТА ---

Анализируй изображение страницы и приведённый текст.

Найди только конкретные проблемы, которые действительно можно
обосновать содержанием этого листа:

1. внутренние противоречия;
2. несогласованные обозначения;
3. очевидно пропущенные соединения или элементы;
4. противоречивые технические указания;
5. неполные или неоднозначные комментарии;
6. места, которые действительно требуют проверки инженером.

КРИТИЧЕСКИЕ ПРАВИЛА:

- верни максимум {OLLAMA_MAX_ISSUES} уникальных замечания;
- не дублируй одно и то же замечание;
- каждое поле должно быть кратким: максимум 1-2 предложения;
- обычное техническое указание на листе само по себе НЕ является ошибкой;
- отсутствие на этом же листе доказательства некоторого утверждения
  само по себе НЕ является ошибкой;
- не придумывай отсутствующие данные;
- не придумывай и не проверяй ГОСТ, СП, ПУЭ и другие нормативы;
- не указывай номера нормативных документов вообще;
- нормативная база будет проверяться отдельным этапом;
- если данных недостаточно, используй категорию "требует_проверки";
- если реальных замечаний нет, верни пустой массив issues;
- не добавляй пояснений вне JSON;
- обязательно закончи полный корректный JSON.

Ответ должен строго соответствовать JSON-схеме.
""".strip()


async def call_ollama(
    *,
    prompt: str,
    image_bytes: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Отправить страницу в VLM с повтором при повреждённом JSON."""

    encoded_image = base64.b64encode(
        image_bytes,
    ).decode("ascii")

    timeout = httpx.Timeout(
        timeout=OLLAMA_REQUEST_TIMEOUT,
        connect=20.0,
    )

    last_content = ""
    last_done_reason: str | None = None

    for attempt in range(
        1,
        OLLAMA_MAX_RETRIES + 1,
    ):
        attempt_prompt = prompt

        if attempt > 1:
            attempt_prompt += """

ПРЕДЫДУЩАЯ ПОПЫТКА НЕ СФОРМИРОВАЛА ЗАВЕРШЁННЫЙ JSON.

Повтори анализ в максимально кратком виде.

Дополнительные ограничения:
- максимум 2 замечания;
- только самые существенные;
- никаких повторов;
- каждое текстовое поле максимум одно короткое предложение;
- обязательно закрой все JSON-массивы и объекты.
""".strip()

        payload = {
            "model": OLLAMA_VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": attempt_prompt,
                    "images": [
                        encoded_image,
                    ],
                }
            ],
            "stream": False,
            "format": ISSUE_SCHEMA,
            "options": {
                "temperature": 0.0,
                "seed": 42 + attempt,
                "repeat_penalty": 1.2,
                "num_ctx": OLLAMA_NUM_CTX,
                "num_predict": OLLAMA_NUM_PREDICT,
            },
        }

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
            ) as client:
                response = await client.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json=payload,
                )

                response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Ollama вернул ошибку.",
                    "status_code": exc.response.status_code,
                    "response": exc.response.text[:2000],
                },
            ) from exc

        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Не удалось обратиться к Ollama: "
                    f"{exc}"
                ),
            ) from exc

        ollama_response = response.json()

        last_content = (
            ollama_response
            .get(
                "message",
                {},
            )
            .get(
                "content",
                "",
            )
        )

        last_done_reason = ollama_response.get(
            "done_reason",
        )

        try:
            parsed_result = json.loads(
                last_content,
            )

        except json.JSONDecodeError:
            if attempt < OLLAMA_MAX_RETRIES:
                continue

            raise HTTPException(
                status_code=502,
                detail={
                    "message": (
                        "Модель не смогла сформировать "
                        "корректный JSON после повторной попытки."
                    ),
                    "attempts": OLLAMA_MAX_RETRIES,
                    "done_reason": last_done_reason,
                    "raw_response": last_content[:3000],
                },
            )

        metrics = {
            "attempt": attempt,
            "done_reason": last_done_reason,
            "total_duration_ms": round(
                ollama_response.get(
                    "total_duration",
                    0,
                )
                / 1_000_000,
                2,
            ),
            "load_duration_ms": round(
                ollama_response.get(
                    "load_duration",
                    0,
                )
                / 1_000_000,
                2,
            ),
            "prompt_eval_count": ollama_response.get(
                "prompt_eval_count",
            ),
            "eval_count": ollama_response.get(
                "eval_count",
            ),
        }

        return (
            parsed_result,
            metrics,
        )

    raise HTTPException(
        status_code=502,
        detail="Не удалось получить ответ от модели.",
    )


async def get_ollama_models() -> list[str]:
    """Получить список моделей, уже загруженных в Ollama."""

    try:
        async with httpx.AsyncClient(
            timeout=10.0,
        ) as client:
            response = await client.get(
                f"{OLLAMA_BASE_URL}/api/tags",
            )

            response.raise_for_status()

    except httpx.HTTPError:
        return []

    payload = response.json()

    return [
        model.get(
            "name",
            "",
        )
        for model in payload.get(
            "models",
            [],
        )
    ]


@app.get(
    "/health/live",
)
async def health_live() -> dict[str, str]:
    """Liveness probe."""

    return {
        "status": "ok",
        "service": "pdf-service",
    }


@app.get(
    "/health/ready",
)
async def health_ready() -> dict[str, Any]:
    """Проверить доступность Ollama и нужной модели."""

    models = await get_ollama_models()

    model_available = any(
        model == OLLAMA_VISION_MODEL
        for model in models
    )

    return {
        "status": (
            "ready"
            if model_available
            else "model_missing"
        ),
        "ollama": bool(models),
        "model": OLLAMA_VISION_MODEL,
        "model_available": model_available,
        "installed_models": models,
    }


@app.get(
    "/rag/search",
)
async def rag_search(
    q: str,
) -> dict[str, Any]:
    """Проверить RAG-поиск из самого pdf-service."""

    try:
        return await search_knowledge(
            q
        )

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


@app.post(
    "/inspect",
)
async def inspect_pdf(
    file: UploadFile = File(...),
    pages: str | None = Form(default=None),
) -> dict[str, Any]:
    """Проверить разбор PDF без обращения к LLM."""

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

            page_info: list[dict[str, Any]] = []

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


@app.post(
    "/analyze",
)
async def analyze_pdf(
    file: UploadFile = File(...),
    pages: str | None = Form(
        default=None,
    ),
    use_rag: bool = Form(
        default=True,
    ),
) -> dict[str, Any]:
    """Выполнить VLM-анализ и при необходимости RAG-валидацию."""

    started_at = time.perf_counter()

    pdf_bytes = await file.read()

    if not pdf_bytes:
        raise HTTPException(
            status_code=400,
            detail="Передан пустой PDF-файл.",
        )

    installed_models = await get_ollama_models()

    if OLLAMA_VISION_MODEL not in installed_models:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "VLM ещё не загружена в Ollama.",
                "required_model": OLLAMA_VISION_MODEL,
                "installed_models": installed_models,
            },
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

            if (
                len(selected_pages)
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

            page_drafts = []
            all_candidates = []

            # ЭТАП 1.
            # Сначала VLM проходит по всем выбранным страницам.
            # Пока никаких нормативов ей не передаём.
            for page_number in selected_pages:
                page = document[
                    page_number - 1
                ]

                extracted_text = page.get_text(
                    "text",
                    sort=True,
                )

                page_type = classify_page(
                    extracted_text,
                    page_number,
                )

                image_bytes = render_page(
                    page,
                )

                (
                    model_result,
                    initial_metrics,
                ) = await call_ollama(
                    prompt=build_prompt(
                        page_number=page_number,
                        page_type=page_type,
                        extracted_text=extracted_text,
                    ),
                    image_bytes=image_bytes,
                )

                candidates = []

                for index, issue in enumerate(
                    model_result.get(
                        "issues",
                        [],
                    ),
                    start=1,
                ):
                    candidate = {
                        **issue,
                        "candidate_id": (
                            f"p{page_number}-c{index}"
                        ),
                        "page": page_number,
                        "page_type": page_type,
                    }

                    candidates.append(
                        candidate
                    )

                    all_candidates.append(
                        candidate
                    )

                page_drafts.append(
                    {
                        "page": page_number,
                        "page_type": page_type,
                        "extracted_text": extracted_text,
                        "summary_initial": (
                            model_result.get(
                                "summary",
                                "",
                            )
                        ),
                        "candidates": candidates,
                        "initial_metrics": (
                            initial_metrics
                        ),
                    }
                )

            # Старый режим оставляем для A/B-сравнения.
            if not use_rag:
                page_results = []
                all_issues = []

                for draft in page_drafts:
                    issues = [
                        build_unvalidated_issue(
                            candidate
                        )
                        for candidate in draft[
                            "candidates"
                        ]
                    ]

                    all_issues.extend(
                        issues
                    )

                    page_results.append(
                        {
                            "page": draft[
                                "page"
                            ],
                            "page_type": draft[
                                "page_type"
                            ],
                            "summary": draft[
                                "summary_initial"
                            ],
                            "issues": issues,
                            "rejected_issues": [],
                            "metrics": {
                                "initial": draft[
                                    "initial_metrics"
                                ],
                                "validation": None,
                            },
                        }
                    )

            else:
                eligible_candidates = []

                rejected_by_page = {}

                # ЭТАП 2a.
                # Дешёвый фильтр до embeddings/Qdrant.
                for candidate in all_candidates:
                    (
                        passes,
                        reason,
                    ) = (
                        candidate_passes_prefilter(
                            candidate
                        )
                    )

                    if passes:
                        eligible_candidates.append(
                            candidate
                        )

                    else:
                        page_number = int(
                            candidate[
                                "page"
                            ]
                        )

                        rejected_by_page.setdefault(
                            page_number,
                            [],
                        ).append(
                            build_rejected_prefilter_issue(
                                candidate,
                                reason,
                            )
                        )

                # ЭТАП 2b.
                # Все embeddings считаются одним batch-вызовом.
                rag_by_candidate = {}

                if eligible_candidates:
                    rag_queries = [
                        build_rag_query(
                            candidate
                        )
                        for candidate
                        in eligible_candidates
                    ]

                    rag_results = (
                        await search_knowledge_many(
                            rag_queries
                        )
                    )

                    for (
                        candidate,
                        rag_result,
                    ) in zip(
                        eligible_candidates,
                        rag_results,
                        strict=True,
                    ):
                        rag_by_candidate[
                            str(
                                candidate[
                                    "candidate_id"
                                ]
                            )
                        ] = rag_result

                page_results = []
                all_issues = []

                # ЭТАП 3.
                # Для каждого листа второй VLM-проход.
                # Модель получает изображение + найденные нормы + опыт.
                for draft in page_drafts:
                    page_number = int(
                        draft[
                            "page"
                        ]
                    )

                    page_candidates = [
                        candidate
                        for candidate in draft[
                            "candidates"
                        ]
                        if str(
                            candidate[
                                "candidate_id"
                            ]
                        )
                        in rag_by_candidate
                    ]

                    rejected_issues = list(
                        rejected_by_page.get(
                            page_number,
                            [],
                        )
                    )

                    validated_issues = []

                    validation_metrics = None

                    validation_summary = ""

                    if page_candidates:
                        image_bytes = render_page(
                            document[
                                page_number - 1
                            ]
                        )

                        page_rag = {
                            str(
                                candidate[
                                    "candidate_id"
                                ]
                            ): rag_by_candidate[
                                str(
                                    candidate[
                                        "candidate_id"
                                    ]
                                )
                            ]
                            for candidate
                            in page_candidates
                        }

                        (
                            validation_result,
                            validation_metrics,
                        ) = (
                            await validate_page_candidates(
                                page_number=page_number,
                                page_type=draft[
                                    "page_type"
                                ],
                                extracted_text=draft[
                                    "extracted_text"
                                ],
                                image_bytes=image_bytes,
                                candidates=page_candidates,
                                rag_by_candidate=page_rag,
                            )
                        )

                        validation_summary = str(
                            validation_result.get(
                                "summary",
                                "",
                            )
                        )

                        decisions_by_id = {
                            str(
                                decision.get(
                                    "candidate_id"
                                )
                            ): decision
                            for decision
                            in validation_result.get(
                                "decisions",
                                [],
                            )
                            if decision.get(
                                "candidate_id"
                            )
                        }

                        for candidate in page_candidates:
                            candidate_id = str(
                                candidate[
                                    "candidate_id"
                                ]
                            )

                            decision = (
                                decisions_by_id.get(
                                    candidate_id
                                )
                                or (
                                    fallback_validation_decision(
                                        candidate
                                    )
                                )
                            )

                            normalized = (
                                normalize_validated_candidate(
                                    candidate=candidate,
                                    decision=decision,
                                    rag_result=(
                                        rag_by_candidate[
                                            candidate_id
                                        ]
                                    ),
                                )
                            )

                            if (
                                normalized[
                                    "validation_status"
                                ]
                                == "rejected"
                            ):
                                rejected_issues.append(
                                    normalized
                                )

                            else:
                                validated_issues.append(
                                    normalized
                                )

                                all_issues.append(
                                    normalized
                                )

                    page_results.append(
                        {
                            "page": page_number,
                            "page_type": draft[
                                "page_type"
                            ],
                            "summary": (
                                validation_summary
                                or draft[
                                    "summary_initial"
                                ]
                            ),
                            "summary_initial": draft[
                                "summary_initial"
                            ],
                            "issues": validated_issues,
                            "rejected_issues": (
                                rejected_issues
                            ),
                            "metrics": {
                                "initial": draft[
                                    "initial_metrics"
                                ],
                                "validation": (
                                    validation_metrics
                                ),
                            },
                        }
                    )

    except fitz.FileDataError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "Файл не удалось открыть как PDF."
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

    elapsed_seconds = round(
        time.perf_counter()
        - started_at,
        2,
    )

    rejected_count = sum(
        len(
            page.get(
                "rejected_issues",
                [],
            )
        )
        for page in page_results
    )

    return {
        "status": "completed",
        "stage": (
            "pdf_vlm_rag_validation"
            if use_rag
            else "pdf_vlm"
        ),
        "file_name": file.filename,
        "model": OLLAMA_VISION_MODEL,
        "total_pages": total_pages,
        "selected_pages": selected_pages,
        "use_rag": use_rag,
        "candidates_count": len(
            all_candidates
        ),
        "issues_count": len(
            all_issues
        ),
        "rejected_count": (
            rejected_count
        ),
        "issues": all_issues,
        "pages": page_results,
        "elapsed_seconds": (
            elapsed_seconds
        ),
        "limitations": [
            (
                "DXF на этом этапе ещё "
                "не участвует в анализе."
            ),
            (
                "Большие чертежи пока "
                "анализируются как одно "
                "изображение без тайлинга."
            ),
            (
                "База Опыта является "
                "дополнительным сигналом "
                "и не заменяет нормативное основание."
            ),
        ],
    }
