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


ISSUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
        },
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
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
                    },
                    "evidence": {
                        "type": "string",
                    },
                    "recommendation": {
                        "type": "string",
                    },
                    "basis": {
                        "type": "string",
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
                    "basis",
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
    """Сформировать ограниченный промпт первого MVP."""

    text_fragment = extracted_text[:PDF_TEXT_LIMIT]

    return f"""
Ты выполняешь первичную техническую проверку одного листа
проектной или рабочей документации.

Это экспериментальный MVP.

Страница PDF: {page_number}
Предварительный тип страницы: {page_type}

Текст, который удалось программно извлечь из PDF:

--- НАЧАЛО ТЕКСТА ---
{text_fragment}
--- КОНЕЦ ТЕКСТА ---

Проверь только то, что реально видно на изображении страницы
или непосредственно следует из извлечённого текста.

Ищи:
1. очевидные противоречия в обозначениях;
2. пропущенные или подозрительные соединения;
3. несогласованность маркировок;
4. технически сомнительные решения;
5. недостаточные или противоречивые пояснения;
6. ошибки или неполноту оформления;
7. места, которые требуют проверки инженером.

ВАЖНО:

- пока нормативная база ГОСТ/СП/ПУЭ НЕ подключена;
- НЕ придумывай номера нормативных документов и пунктов;
- поле basis оставляй пустой строкой, если основание
  невозможно подтвердить по данным страницы;
- не утверждай наличие нарушения, если данных недостаточно;
- в таком случае используй категорию "требует_проверки";
- замечание должно быть конкретным;
- evidence должно описывать, что именно на листе послужило
  основанием для замечания;
- если замечаний нет, верни пустой массив issues.

Ответ должен строго соответствовать переданной JSON-схеме.
""".strip()


async def call_ollama(
    *,
    prompt: str,
    image_bytes: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Отправить страницу и текст в локальную VLM."""

    encoded_image = base64.b64encode(
        image_bytes,
    ).decode("ascii")

    payload = {
        "model": OLLAMA_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [
                    encoded_image,
                ],
            }
        ],
        "stream": False,
        "format": ISSUE_SCHEMA,
        "options": {
            "temperature": 0.1,
            "num_ctx": OLLAMA_NUM_CTX,
        },
    }

    timeout = httpx.Timeout(
        timeout=OLLAMA_REQUEST_TIMEOUT,
        connect=20.0,
    )

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

    content = (
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

    try:
        parsed_result = json.loads(
            content,
        )

    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": (
                    "Модель вернула ответ, "
                    "который не удалось разобрать как JSON."
                ),
                "raw_response": content[:3000],
            },
        ) from exc

    metrics = {
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
    pages: str | None = Form(default=None),
) -> dict[str, Any]:
    """Выполнить первый реальный VLM-анализ PDF."""

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
                            selected_pages,
                        ),
                        "limit": PDF_MAX_ANALYSIS_PAGES,
                    },
                )

            page_results: list[dict[str, Any]] = []
            all_issues: list[dict[str, Any]] = []

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

                model_result, metrics = await call_ollama(
                    prompt=build_prompt(
                        page_number=page_number,
                        page_type=page_type,
                        extracted_text=extracted_text,
                    ),
                    image_bytes=image_bytes,
                )

                issues = model_result.get(
                    "issues",
                    [],
                )

                normalized_issues: list[
                    dict[str, Any]
                ] = []

                for issue in issues:
                    normalized_issue = {
                        **issue,
                        "page": page_number,
                        "page_type": page_type,
                    }

                    normalized_issues.append(
                        normalized_issue,
                    )

                    all_issues.append(
                        normalized_issue,
                    )

                page_results.append(
                    {
                        "page": page_number,
                        "page_type": page_type,
                        "summary": model_result.get(
                            "summary",
                            "",
                        ),
                        "issues": normalized_issues,
                        "metrics": metrics,
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
            detail=str(exc),
        ) from exc

    elapsed_seconds = round(
        time.perf_counter() - started_at,
        2,
    )

    return {
        "status": "completed",
        "stage": "pdf_vlm",
        "file_name": file.filename,
        "model": OLLAMA_VISION_MODEL,
        "total_pages": total_pages,
        "selected_pages": selected_pages,
        "issues_count": len(all_issues),
        "issues": all_issues,
        "pages": page_results,
        "elapsed_seconds": elapsed_seconds,
        "limitations": [
            (
                "Нормативная база ГОСТ/СП/ПУЭ "
                "на этом этапе ещё не подключена."
            ),
            (
                "DXF на этом этапе ещё не участвует "
                "в анализе."
            ),
            (
                "Большие чертежи пока анализируются "
                "как одно изображение без тайлинга."
            ),
        ],
    }