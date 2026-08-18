# services/pdf-service/app/validator.py

"""VLM-этапы: понимание листа, проверка по нормам и оформление результата."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx


OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://ollama:11434",
).rstrip("/")

OLLAMA_VISION_MODEL = os.getenv(
    "OLLAMA_VISION_MODEL",
    "qwen3-vl:8b",
)

OLLAMA_REQUEST_TIMEOUT = float(
    os.getenv(
        "OLLAMA_REQUEST_TIMEOUT",
        "1800",
    )
)

OLLAMA_NUM_CTX = int(
    os.getenv(
        "OLLAMA_NUM_CTX",
        "12288",
    )
)

OLLAMA_MAX_ISSUES = int(
    os.getenv(
        "OLLAMA_MAX_ISSUES",
        "6",
    )
)

OLLAMA_MAX_RETRIES = int(
    os.getenv(
        "OLLAMA_MAX_RETRIES",
        "2",
    )
)

OLLAMA_KEEP_ALIVE = os.getenv(
    "OLLAMA_KEEP_ALIVE",
    "15m",
)

OLLAMA_PAGE_FACTS_NUM_PREDICT = int(
    os.getenv(
        "OLLAMA_PAGE_FACTS_NUM_PREDICT",
        "1200",
    )
)

OLLAMA_NORM_CHECK_NUM_PREDICT = int(
    os.getenv(
        "OLLAMA_NORM_CHECK_NUM_PREDICT",
        "1800",
    )
)

OLLAMA_FINAL_NUM_PREDICT = int(
    os.getenv(
        "OLLAMA_FINAL_NUM_PREDICT",
        "1400",
    )
)

RAG_NORMATIVE_TEXT_LIMIT = int(
    os.getenv(
        "RAG_NORMATIVE_TEXT_LIMIT",
        "700",
    )
)

RAG_EXPERIENCE_CONTEXT_LIMIT = int(
    os.getenv(
        "RAG_EXPERIENCE_CONTEXT_LIMIT",
        "600",
    )
)


PAGE_FACTS_SCHEMA: dict[
    str,
    Any,
] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "discipline": {
            "type": "string",
            "maxLength": 100,
        },
        "page_type": {
            "type": "string",
            "maxLength": 100,
        },
        "summary": {
            "type": "string",
            "maxLength": 800,
        },
        "objects": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "string",
                "maxLength": 250,
            },
        },
        "connections": {
            "type": "array",
            "maxItems": 15,
            "items": {
                "type": "string",
                "maxLength": 300,
            },
        },
        "labels": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "string",
                "maxLength": 200,
            },
        },
        "normative_queries": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "string",
                "maxLength": 300,
            },
        },
    },
    "required": [
        "discipline",
        "page_type",
        "summary",
        "objects",
        "connections",
        "labels",
        "normative_queries",
    ],
}


async def call_vlm_json(
    *,
    prompt: str,
    schema: dict[str, Any],
    num_predict: int,
    seed: int,
    image_bytes: bytes | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    """Вызвать одну и ту же Qwen3-VL в JSON-режиме."""

    timeout = httpx.Timeout(
        timeout=(
            OLLAMA_REQUEST_TIMEOUT
        ),
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
            attempt_prompt += (
                "\n\nПредыдущий ответ не удалось разобрать "
                "как полный JSON. Повтори ответ короче и "
                "строго по JSON-схеме. "
                "Не добавляй текст вне JSON."
            )

        message: dict[
            str,
            Any,
        ] = {
            "role": "user",
            "content": attempt_prompt,
        }

        if image_bytes is not None:
            message[
                "images"
            ] = [
                base64.b64encode(
                    image_bytes
                ).decode(
                    "ascii"
                )
            ]

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
            ) as client:
                response = await client.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json={
                        "model": (
                            OLLAMA_VISION_MODEL
                        ),
                        "messages": [
                            message
                        ],
                        "stream": False,
                        "format": schema,
                        "keep_alive": (
                            OLLAMA_KEEP_ALIVE
                        ),
                        "options": {
                            "temperature": 0.0,
                            "seed": (
                                seed + attempt
                            ),
                            "repeat_penalty": 1.12,
                            "num_ctx": (
                                OLLAMA_NUM_CTX
                            ),
                            "num_predict": (
                                num_predict
                            ),
                        },
                    },
                )

                response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                "Ollama вернул ошибку: "
                f"{exc.response.status_code}: "
                f"{exc.response.text[:1500]}"
            ) from exc

        except httpx.HTTPError as exc:
            raise RuntimeError(
                "Не удалось обратиться к Ollama: "
                f"{exc}"
            ) from exc

        ollama_response = (
            response.json()
        )

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

        last_done_reason = (
            ollama_response.get(
                "done_reason"
            )
        )

        try:
            parsed = json.loads(
                last_content
            )

        except json.JSONDecodeError:
            if (
                attempt
                < OLLAMA_MAX_RETRIES
            ):
                continue

            raise RuntimeError(
                "Модель не смогла сформировать "
                "корректный JSON. "
                f"done_reason={last_done_reason}; "
                f"response={last_content[:1500]}"
            )

        metrics = {
            "attempt": attempt,
            "done_reason": (
                last_done_reason
            ),
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
            "prompt_eval_count": (
                ollama_response.get(
                    "prompt_eval_count"
                )
            ),
            "eval_count": (
                ollama_response.get(
                    "eval_count"
                )
            ),
        }

        return (
            parsed,
            metrics,
        )

    raise RuntimeError(
        "Не удалось получить ответ модели."
    )


def build_page_understanding_prompt(
    *,
    page_number: int,
    heuristic_page_type: str,
    extracted_text: str,
) -> str:
    """Сформировать первый промпт: только факты и темы нормативной проверки."""

    return f"""
Ты анализируешь один лист российской проектной
или рабочей документации.

Физическая страница PDF: {page_number}
Предварительный тип листа: {heuristic_page_type}

Извлечённый из PDF текст:
--- НАЧАЛО ТЕКСТА ---
{extracted_text[:9000]}
--- КОНЕЦ ТЕКСТА ---

ЗАДАЧА ЭТОГО ЭТАПА — НЕ ИСКАТЬ ОШИБКИ.

Сначала объективно опиши, что реально присутствует на листе:
- дисциплина/раздел, если это можно определить;
- тип листа;
- основные устройства, кабели, шкафы, линии,
  таблицы и другие сущности;
- видимые связи и подключения;
- важные марки, теги и обозначения.

После этого сформулируй до 6 НЕЙТРАЛЬНЫХ
тем нормативной проверки, которые логично
применить к такому содержанию.

Пример правильной темы:
"требования к маркировке кабельных линий
на схемах автоматизации".

Пример неправильной темы:
"на листе нарушена маркировка кабеля".

Не утверждай наличие нарушения.
Не придумывай номера ГОСТ, СП, ПУЭ или пунктов.
Не пытайся цитировать нормативы по памяти.
Не используй Базу Опыта на этом этапе.
Верни только JSON по схеме.
""".strip()


async def understand_page(
    *,
    page_number: int,
    heuristic_page_type: str,
    extracted_text: str,
    image_bytes: bytes,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    """Получить структурированное понимание листа без поиска ошибок."""

    return await call_vlm_json(
        prompt=(
            build_page_understanding_prompt(
                page_number=page_number,
                heuristic_page_type=(
                    heuristic_page_type
                ),
                extracted_text=(
                    extracted_text
                ),
            )
        ),
        schema=PAGE_FACTS_SCHEMA,
        num_predict=(
            OLLAMA_PAGE_FACTS_NUM_PREDICT
        ),
        seed=100,
        image_bytes=image_bytes,
    )


def _build_normative_check_schema(
    source_ids: list[str],
) -> dict[str, Any]:
    """Построить schema, где модель может выбрать только реальные N-id."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 700,
            },
            "violations": {
                "type": "array",
                "maxItems": (
                    OLLAMA_MAX_ISSUES
                ),
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
                                "маркировка",
                                "комплектность",
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
                        "status": {
                            "type": "string",
                            "enum": [
                                "confirmed",
                                "needs_review",
                            ],
                        },
                        "comment": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 600,
                        },
                        "evidence": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 700,
                        },
                        "recommendation_draft": {
                            "type": "string",
                            "maxLength": 600,
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "normative_source_ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 3,
                            "items": {
                                "type": "string",
                                "enum": (
                                    source_ids
                                ),
                            },
                        },
                    },
                    "required": [
                        "category",
                        "severity",
                        "status",
                        "comment",
                        "evidence",
                        "recommendation_draft",
                        "confidence",
                        "normative_source_ids",
                    ],
                },
            },
        },
        "required": [
            "summary",
            "violations",
        ],
    }


def _compact_normative_sources(
    sources: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    """Ограничить объём нормативного контекста для VLM."""

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
            "page": source.get(
                "page"
            ),
            "chunk_index": source.get(
                "chunk_index"
            ),
            "text": str(
                source.get(
                    "text",
                    "",
                )
            )[
                :RAG_NORMATIVE_TEXT_LIMIT
            ],
        }
        for source in sources
    ]


def build_normative_check_prompt(
    *,
    page_number: int,
    extracted_text: str,
    page_facts: dict[str, Any],
    normative_sources: list[
        dict[str, Any]
    ],
) -> str:
    """Сформировать промпт проверки листа именно по извлечённым нормам."""

    facts_json = json.dumps(
        page_facts,
        ensure_ascii=False,
        indent=2,
    )

    sources_json = json.dumps(
        _compact_normative_sources(
            normative_sources
        ),
        ensure_ascii=False,
        indent=2,
    )

    return f"""
Ты выполняешь нормативную проверку одного
листа инженерной документации.

Физическая страница PDF: {page_number}

На предыдущем этапе лист был описан
БЕЗ поиска ошибок:

--- PAGE FACTS ---
{facts_json}
--- END PAGE FACTS ---

Извлечённый текст листа:

--- PAGE TEXT ---
{extracted_text[:6000]}
--- END PAGE TEXT ---

По нейтральным темам проверки из Qdrant
были найдены следующие фрагменты
нормативной базы:

--- NORMATIVE SOURCES ---
{sources_json}
--- END NORMATIVE SOURCES ---

ТЕПЕРЬ проверь изображение листа и PAGE FACTS
ИМЕННО ПО ЭТИМ нормативным фрагментам.

Правила:

1. Нельзя придумывать нормативы по памяти.
2. Можно ссылаться только на source_id,
   реально присутствующие выше.
3. Similarity score — только качество поиска,
   а не доказательство нарушения.
4. Игнорируй нормативный фрагмент,
   если он не относится к текущему листу.
5. confirmed — требование применимо и на листе
   виден конкретный факт, который ему противоречит.
6. needs_review — требование выглядит применимым,
   но изображения/текста недостаточно
   для уверенного вывода.
7. Если норматив говорит о требовании,
   а на этом листе просто нет данных,
   не называй это ошибкой автоматически:
   обычно это needs_review или вообще
   отсутствие нарушения.
8. evidence должно описывать конкретный
   видимый факт на листе.
9. Для каждого нарушения обязательно укажи
   1-3 реальные N-id.
10. Если по найденным нормативам нарушений
    не видно — violations должен быть [].
11. Не используй Базу Опыта для решения,
    существует ли ошибка.
12. Не добавляй текст вне JSON.
""".strip()


async def check_page_against_norms(
    *,
    page_number: int,
    extracted_text: str,
    page_facts: dict[str, Any],
    normative_sources: list[
        dict[str, Any]
    ],
    image_bytes: bytes,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    """Проверить лист по найденным нормативным фрагментам."""

    source_ids = [
        str(
            source.get(
                "source_id"
            )
        )
        for source in normative_sources
        if source.get(
            "source_id"
        )
    ]

    if not source_ids:
        return (
            {
                "summary": (
                    "Нормативные источники "
                    "для листа не найдены."
                ),
                "violations": [],
            },
            {
                "attempt": 0,
                "done_reason": (
                    "no_normative_sources"
                ),
                "total_duration_ms": 0.0,
                "load_duration_ms": 0.0,
                "prompt_eval_count": 0,
                "eval_count": 0,
            },
        )

    return await call_vlm_json(
        prompt=(
            build_normative_check_prompt(
                page_number=page_number,
                extracted_text=(
                    extracted_text
                ),
                page_facts=page_facts,
                normative_sources=(
                    normative_sources
                ),
            )
        ),
        schema=(
            _build_normative_check_schema(
                source_ids
            )
        ),
        num_predict=(
            OLLAMA_NORM_CHECK_NUM_PREDICT
        ),
        seed=200,
        image_bytes=image_bytes,
    )


def build_experience_query(
    finding: dict[str, Any],
) -> str:
    """Сформировать запрос в Базу Опыта уже после выявления нарушения."""

    return "\n".join(
        [
            (
                "Категория: "
                f"{finding.get('category', '')}"
            ),
            (
                "Замечание: "
                f"{finding.get('comment', '')}"
            ),
            (
                "Факт на листе: "
                f"{finding.get('evidence', '')}"
            ),
            (
                "Черновая рекомендация: "
                f"{finding.get('recommendation_draft', '')}"
            ),
        ]
    ).strip()


def _compact_experience_sources(
    sources: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    """Подготовить few-shot опыт без чрезмерного контекста."""

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
            "before_context": str(
                source.get(
                    "before_context",
                    "",
                )
            )[
                :RAG_EXPERIENCE_CONTEXT_LIMIT
            ],
            "after_context": str(
                source.get(
                    "after_context",
                    "",
                )
            )[
                :RAG_EXPERIENCE_CONTEXT_LIMIT
            ],
        }
        for source in sources
    ]


def _build_final_schema(
    finding_ids: list[str],
) -> dict[str, Any]:
    """Schema финального оформления без права менять факт нарушения."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 700,
            },
            "findings": {
                "type": "array",
                "maxItems": len(
                    finding_ids
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {
                            "type": "string",
                            "enum": finding_ids,
                        },
                        "comment": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 600,
                        },
                        "recommendation": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 700,
                        },
                        "experience_source_ids": {
                            "type": "array",
                            "maxItems": 2,
                            "items": {
                                "type": "string"
                            },
                        },
                    },
                    "required": [
                        "finding_id",
                        "comment",
                        "recommendation",
                        "experience_source_ids",
                    ],
                },
            },
        },
        "required": [
            "summary",
            "findings",
        ],
    }


def build_finalization_prompt(
    *,
    findings: list[
        dict[str, Any]
    ],
    experience_by_finding: dict[
        str,
        list[dict[str, Any]],
    ],
) -> str:
    """Сформировать промпт, где опыт используется только как пример."""

    payload = []

    for finding in findings:
        finding_id = str(
            finding[
                "finding_id"
            ]
        )

        payload.append(
            {
                "finding": {
                    "finding_id": (
                        finding_id
                    ),
                    "category": finding.get(
                        "category"
                    ),
                    "severity": finding.get(
                        "severity"
                    ),
                    "status": finding.get(
                        "status"
                    ),
                    "comment": finding.get(
                        "comment"
                    ),
                    "evidence": finding.get(
                        "evidence"
                    ),
                    "recommendation_draft": (
                        finding.get(
                            "recommendation_draft"
                        )
                    ),
                    "normative_basis": (
                        finding.get(
                            "basis"
                        )
                    ),
                },
                "experience_examples": (
                    _compact_experience_sources(
                        experience_by_finding.get(
                            finding_id,
                            [],
                        )
                    )
                ),
            }
        )

    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )

    return f"""
Нормативная проверка уже выполнена.
Ниже находятся установленные или требующие
инженерной проверки замечания и похожие
примеры из Базы Опыта.

--- FINDINGS AND EXPERIENCE ---
{payload_json}
--- END FINDINGS AND EXPERIENCE ---

ТВОЯ ЗАДАЧА — ТОЛЬКО оформить итоговые
замечания понятно для инженера и предложить
практическое действие по исправлению.

Правила:

1. Не решай заново, есть нарушение или нет.
2. Не меняй status, severity, evidence
   и нормативные источники.
3. База Опыта НЕ является нормативным основанием.
4. Используй прошлые issue_text как примеры
   инженерной формулировки.
5. AFTER-контекст можно считать подтверждённым
   способом исправления ТОЛЬКО если
   verified_fixed=true.
   Если false — это лишь связанный контекст проекта.
6. Не придумывай ГОСТ, СП, ПУЭ, пункты и страницы.
7. Если опыт нерелевантен, верни пустой
   experience_source_ids.
8. source_id E1/E2/E3 локальны для конкретного
   finding; не переноси их между finding_id.
9. recommendation должна быть конкретной,
   но не должна придумывать данные,
   которых нет в нормативном основании
   или в текущем замечании.
10. Верни ровно по одному результату
    для каждого finding_id.
11. Не добавляй текст вне JSON.
""".strip()


async def finalize_findings(
    *,
    findings: list[
        dict[str, Any]
    ],
    experience_by_finding: dict[
        str,
        list[dict[str, Any]],
    ],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    """Оформить замечания с использованием Базы Опыта как few-shot примеров."""

    if not findings:
        return (
            {
                "summary": "",
                "findings": [],
            },
            {
                "attempt": 0,
                "done_reason": (
                    "no_findings"
                ),
                "total_duration_ms": 0.0,
                "load_duration_ms": 0.0,
                "prompt_eval_count": 0,
                "eval_count": 0,
            },
        )

    finding_ids = [
        str(
            finding[
                "finding_id"
            ]
        )
        for finding in findings
    ]

    return await call_vlm_json(
        prompt=(
            build_finalization_prompt(
                findings=findings,
                experience_by_finding=(
                    experience_by_finding
                ),
            )
        ),
        schema=(
            _build_final_schema(
                finding_ids
            )
        ),
        num_predict=(
            OLLAMA_FINAL_NUM_PREDICT
        ),
        seed=300,
        image_bytes=None,
    )
