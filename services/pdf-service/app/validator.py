# services/pdf-service/app/validator.py

"""VLM-этапы: понимание листа, нормативная проверка и оформление отчёта."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

import httpx


logger = logging.getLogger("uvicorn.error")


OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://ollama:11434",
).rstrip("/")

OLLAMA_VISION_MODEL = os.getenv(
    "OLLAMA_VISION_MODEL",
    "qwen3-vl:8b-instruct",
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
        "16384",
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
        "1600",
    )
)

OLLAMA_NORM_CHECK_NUM_PREDICT = int(
    os.getenv(
        "OLLAMA_NORM_CHECK_NUM_PREDICT",
        "2600",
    )
)

OLLAMA_FINAL_NUM_PREDICT = int(
    os.getenv(
        "OLLAMA_FINAL_NUM_PREDICT",
        "1800",
    )
)

# Финальное оформление выполняем небольшими batches.
# Даже если на листе 6 замечаний, модель не должна
# генерировать огромный JSON за один вызов.
OLLAMA_FINAL_BATCH_SIZE = 2

RAG_NORMATIVE_TEXT_LIMIT = int(
    os.getenv(
        "RAG_NORMATIVE_TEXT_LIMIT",
        "700",
    )
)

RAG_EXPERIENCE_CONTEXT_LIMIT = int(
    os.getenv(
        "RAG_EXPERIENCE_CONTEXT_LIMIT",
        "500",
    )
)


PAGE_FACTS_SCHEMA: dict[str, Any] = {
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
            "maxLength": 600,
        },
        "objects": {
            "type": "array",
            "maxItems": 15,
            "items": {
                "type": "string",
                "maxLength": 200,
            },
        },
        "connections": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "string",
                "maxLength": 250,
            },
        },
        "labels": {
            "type": "array",
            "maxItems": 15,
            "items": {
                "type": "string",
                "maxLength": 160,
            },
        },
        "normative_queries": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "string",
                "maxLength": 240,
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


def _response_metrics(
    response: dict[str, Any],
    *,
    attempt: int,
    requested_num_predict: int,
) -> dict[str, Any]:
    """Собрать диагностические метрики ответа Ollama."""

    message = response.get(
        "message",
        {},
    )

    content = str(
        message.get(
            "content",
            "",
        )
    )

    thinking = str(
        message.get(
            "thinking",
            "",
        )
    )

    return {
        "attempt": attempt,
        "done_reason": response.get(
            "done_reason"
        ),
        "requested_num_predict": (
            requested_num_predict
        ),
        "total_duration_ms": round(
            response.get(
                "total_duration",
                0,
            )
            / 1_000_000,
            2,
        ),
        "load_duration_ms": round(
            response.get(
                "load_duration",
                0,
            )
            / 1_000_000,
            2,
        ),
        "prompt_eval_count": (
            response.get(
                "prompt_eval_count"
            )
        ),
        "eval_count": (
            response.get(
                "eval_count"
            )
        ),
        "content_length": len(
            content
        ),
        "thinking_length": len(
            thinking
        ),
    }


async def call_vlm_json(
    *,
    prompt: str,
    schema: dict[str, Any],
    num_predict: int,
    seed: int,
    stage: str,
    image_bytes: bytes | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    """Вызвать Qwen3-VL и получить полный JSON."""

    timeout = httpx.Timeout(
        timeout=OLLAMA_REQUEST_TIMEOUT,
        connect=20.0,
    )

    encoded_image: str | None = None

    if image_bytes is not None:
        encoded_image = base64.b64encode(
            image_bytes
        ).decode(
            "ascii"
        )

    last_content = ""
    last_metrics: dict[str, Any] = {}

    for attempt in range(
        1,
        OLLAMA_MAX_RETRIES + 1,
    ):
        # При повторе увеличиваем budget.
        attempt_num_predict = (
            num_predict
            if attempt == 1
            else min(
                num_predict * 2,
                6000,
            )
        )

        attempt_prompt = prompt

        if attempt > 1:
            attempt_prompt += (
                "\n\nПРЕДЫДУЩИЙ ОТВЕТ БЫЛ ОБРЕЗАН "
                "ИЛИ НЕ ЯВЛЯЛСЯ ПОЛНЫМ JSON. "
                "Ответь существенно короче. "
                "Не повторяй рассуждения. "
                "Верни только полный JSON."
            )

        message: dict[str, Any] = {
            "role": "user",
            "content": attempt_prompt,
        }

        if encoded_image is not None:
            message[
                "images"
            ] = [
                encoded_image
            ]

        logger.info(
            "[VLM:%s] START attempt=%s "
            "num_predict=%s image=%s",
            stage,
            attempt,
            attempt_num_predict,
            image_bytes is not None,
        )

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

                        # Для structured pipeline
                        # reasoning-текст нам не нужен.
                        "think": False,

                        "format": schema,

                        "keep_alive": (
                            OLLAMA_KEEP_ALIVE
                        ),

                        "options": {
                            "temperature": 0.0,

                            "seed": (
                                seed
                                + attempt
                            ),

                            "repeat_penalty": 1.10,

                            "num_ctx": (
                                OLLAMA_NUM_CTX
                            ),

                            "num_predict": (
                                attempt_num_predict
                            ),
                        },
                    },
                )

                response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                "Ollama вернул ошибку "
                f"на этапе {stage}: "
                f"{exc.response.status_code}: "
                f"{exc.response.text[:1500]}"
            ) from exc

        except httpx.HTTPError as exc:
            raise RuntimeError(
                "Не удалось обратиться к Ollama "
                f"на этапе {stage}: "
                f"{exc}"
            ) from exc

        ollama_response = (
            response.json()
        )

        message_data = (
            ollama_response.get(
                "message",
                {},
            )
        )

        last_content = str(
            message_data.get(
                "content",
                "",
            )
        )

        last_metrics = (
            _response_metrics(
                ollama_response,
                attempt=attempt,
                requested_num_predict=(
                    attempt_num_predict
                ),
            )
        )

        logger.info(
            "[VLM:%s] DONE attempt=%s "
            "reason=%s prompt_tokens=%s "
            "output_tokens=%s content_chars=%s "
            "thinking_chars=%s",
            stage,
            attempt,
            last_metrics.get(
                "done_reason"
            ),
            last_metrics.get(
                "prompt_eval_count"
            ),
            last_metrics.get(
                "eval_count"
            ),
            last_metrics.get(
                "content_length"
            ),
            last_metrics.get(
                "thinking_length"
            ),
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
                "корректный JSON "
                f"на этапе {stage}. "
                f"done_reason="
                f"{last_metrics.get('done_reason')}; "
                f"prompt_eval_count="
                f"{last_metrics.get('prompt_eval_count')}; "
                f"eval_count="
                f"{last_metrics.get('eval_count')}; "
                f"content_length="
                f"{last_metrics.get('content_length')}; "
                f"thinking_length="
                f"{last_metrics.get('thinking_length')}; "
                f"response="
                f"{last_content[:1800]}"
            )

        return (
            parsed,
            last_metrics,
        )

    raise RuntimeError(
        "Не удалось получить JSON "
        f"на этапе {stage}."
    )


def build_page_understanding_prompt(
    *,
    page_number: int,
    heuristic_page_type: str,
    extracted_text: str,
) -> str:
    """Сформировать промпт понимания листа."""

    return f"""
Ты анализируешь один лист российской проектной
или рабочей документации.

Физическая страница PDF: {page_number}
Предварительный тип листа: {heuristic_page_type}

Извлечённый текст:

--- PAGE TEXT ---
{extracted_text[:8000]}
--- END PAGE TEXT ---

ЭТОТ ЭТАП НЕ ИЩЕТ ОШИБКИ.

Кратко и объективно опиши:
- дисциплину или раздел;
- тип листа;
- основные устройства;
- кабели;
- линии;
- таблицы;
- видимые связи;
- важные марки, теги и обозначения.

Затем сформулируй до 6 НЕЙТРАЛЬНЫХ тем,
по которым следует подобрать нормативные требования.

Правильно:
"требования к маркировке кабельных линий
на схемах автоматизации".

Неправильно:
"на листе нарушена маркировка кабеля".

Не утверждай наличие нарушения.
Не вспоминай ГОСТ, СП или ПУЭ по памяти.
Ответ должен быть кратким.

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
    """Получить факты листа без поиска ошибок."""

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
        stage=(
            f"page_understanding:"
            f"{page_number}"
        ),
        image_bytes=image_bytes,
    )


def _build_normative_check_schema(
    source_ids: list[str],
) -> dict[str, Any]:
    """Схема только реальных нарушений."""

    return {
        "type": "object",
        "additionalProperties": False,

        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 400,
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
                            "maxLength": 420,
                        },

                        "evidence": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 420,
                        },

                        "recommendation_draft": {
                            "type": "string",
                            "maxLength": 420,
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
    """Сократить нормативный контекст."""

    return [
        {
            "source_id": (
                source.get(
                    "source_id"
                )
            ),

            "score": (
                source.get(
                    "score"
                )
            ),

            "source_file": (
                source.get(
                    "source_file"
                )
            ),

            "page": (
                source.get(
                    "page"
                )
            ),

            "chunk_index": (
                source.get(
                    "chunk_index"
                )
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
    """Сформировать нормативный промпт."""

    facts_json = json.dumps(
        page_facts,
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
    )

    sources_json = json.dumps(
        _compact_normative_sources(
            normative_sources
        ),
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
    )

    return f"""
Ты выполняешь нормативную проверку одного
листа инженерной документации.

Страница PDF: {page_number}

PAGE FACTS:
{facts_json}

PAGE TEXT:
{extracted_text[:5500]}

NORMATIVE SOURCES:
{sources_json}

Проверь изображение и факты листа
ТОЛЬКО по приведённым нормативным фрагментам.

КРИТИЧЕСКИ ВАЖНО:

- violations содержит ТОЛЬКО нарушения
  или места, где действительно нужна
  проверка инженера;

- если лист СООТВЕТСТВУЕТ требованию,
  НЕ добавляй это в violations;

- фразы:
  "соответствует",
  "выполнено правильно",
  "требование соблюдено",
  "как это сделано на листе"
  НЕ являются замечаниями;

- не создавай рекомендацию
  "убедиться, что всё остаётся как сейчас",
  если нарушения нет;

- если нарушений нет:
  violations=[];

- confirmed:
  требование применимо,
  и виден конкретный факт,
  который ему противоречит;

- needs_review:
  есть конкретное подозрение,
  но данных недостаточно
  для подтверждения;

- просто отсутствие информации
  не является автоматически нарушением;

- каждый элемент может ссылаться
  только на реальные N-id;

- similarity score не доказывает
  применимость нормы;

- не придумывай ГОСТ, СП, ПУЭ,
  номера пунктов и страницы;

- comment, evidence и recommendation_draft:
  максимум 1-2 коротких предложения;

- База Опыта на этом этапе не используется.

Верни только JSON.
""".strip()


_POSITIVE_COMPLIANCE_MARKERS = (
    "соответствует требован",
    "соответствует рекоменда",
    "соответствует норм",
    "выполнено в соответствии",
    "выполнено правильно",
    "требование выполнено",
    "требования выполнены",
    "требование соблюдено",
    "требования соблюдены",
    "нарушений не выявлено",
    "нарушение отсутствует",
    "как это сделано на листе",
    "что соответствует",
)


_NEGATIVE_VIOLATION_MARKERS = (
    "не соответствует",
    "не выполн",
    "не указан",
    "не указана",
    "не указаны",
    "не соблюд",
    "отсутств",
    "противореч",
    "недостаточ",
    "ошиб",
    "невер",
    "некоррект",
    "требуется исправ",
    "необходимо исправ",
    "необходимо добавить",
    "требуется добавить",
    "нарушено",
    "нарушены",
    "выявлено нарушение",
    "выявлены нарушения",
)


def _normalize_text(
    value: Any,
) -> str:
    """Нормализовать строку для фильтрации."""

    return re.sub(
        r"\s+",
        " ",
        str(
            value
            or ""
        ).lower(),
    ).strip()


def _looks_like_compliance_confirmation(
    violation: dict[str, Any],
) -> bool:
    """Найти элемент, который подтверждает соответствие вместо ошибки."""

    combined = " ".join(
        [
            _normalize_text(
                violation.get(
                    "comment"
                )
            ),

            _normalize_text(
                violation.get(
                    "evidence"
                )
            ),

            _normalize_text(
                violation.get(
                    "recommendation_draft"
                )
            ),
        ]
    )

    has_positive = any(
        marker in combined
        for marker in (
            _POSITIVE_COMPLIANCE_MARKERS
        )
    )

    has_negative = any(
        marker in combined
        for marker in (
            _NEGATIVE_VIOLATION_MARKERS
        )
    )

    return (
        has_positive
        and not has_negative
    )


def _filter_violations(
    violations: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    """Убрать подтверждения соответствия и точные дубли."""

    result: list[
        dict[str, Any]
    ] = []

    seen: set[
        str
    ] = set()

    for violation in violations:
        if not isinstance(
            violation,
            dict,
        ):
            continue

        if (
            _looks_like_compliance_confirmation(
                violation
            )
        ):
            logger.info(
                "[normative_filter] "
                "DROP compliance: %s",
                str(
                    violation.get(
                        "comment",
                        "",
                    )
                )[:300],
            )

            continue

        comment = _normalize_text(
            violation.get(
                "comment"
            )
        )

        if not comment:
            continue

        dedupe_key = re.sub(
            r"[^a-zа-яё0-9]+",
            " ",
            comment,
            flags=re.IGNORECASE,
        ).strip()

        if dedupe_key in seen:
            continue

        seen.add(
            dedupe_key
        )

        result.append(
            violation
        )

    return result


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
    """Проверить лист и удалить ложные positive findings."""

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
                "content_length": 0,
                "thinking_length": 0,
            },
        )

    (
        result,
        metrics,
    ) = await call_vlm_json(
        prompt=(
            build_normative_check_prompt(
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

        stage=(
            f"normative_check:"
            f"{page_number}"
        ),

        image_bytes=image_bytes,
    )

    raw_violations = (
        result.get(
            "violations",
            [],
        )
    )

    if not isinstance(
        raw_violations,
        list,
    ):
        raw_violations = []

    filtered_violations = (
        _filter_violations(
            raw_violations
        )
    )

    metrics[
        "raw_violations_count"
    ] = len(
        raw_violations
    )

    metrics[
        "filtered_violations_count"
    ] = len(
        filtered_violations
    )

    result[
        "violations"
    ] = (
        filtered_violations
    )

    return (
        result,
        metrics,
    )


def build_experience_query(
    finding: dict[str, Any],
) -> str:
    """Сформировать запрос в Базу Опыта после выявления нарушения."""

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
    """Сократить few-shot примеры Базы Опыта."""

    return [
        {
            "source_id": (
                source.get(
                    "source_id"
                )
            ),

            "score": (
                source.get(
                    "score"
                )
            ),

            "project_id": (
                source.get(
                    "project_id"
                )
            ),

            "issue_id": (
                source.get(
                    "issue_id"
                )
            ),

            "issue_text": (
                source.get(
                    "issue_text"
                )
            ),

            "verified_fixed": (
                source.get(
                    "verified_fixed",
                    False,
                )
            ),

            "before_page": (
                source.get(
                    "before_page"
                )
            ),

            "after_page": (
                source.get(
                    "after_page"
                )
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
    """Схема финального оформления небольшого batch."""

    return {
        "type": "object",
        "additionalProperties": False,

        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 300,
            },

            "findings": {
                "type": "array",

                "minItems": len(
                    finding_ids
                ),

                "maxItems": len(
                    finding_ids
                ),

                "items": {
                    "type": "object",
                    "additionalProperties": False,

                    "properties": {
                        "finding_id": {
                            "type": "string",
                            "enum": (
                                finding_ids
                            ),
                        },

                        "comment": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 350,
                        },

                        "recommendation": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 400,
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
    """Сформировать компактный финальный промпт."""

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

                    "category": (
                        finding.get(
                            "category"
                        )
                    ),

                    "status": (
                        finding.get(
                            "status"
                        )
                    ),

                    "comment": (
                        finding.get(
                            "comment"
                        )
                    ),

                    "evidence": (
                        finding.get(
                            "evidence"
                        )
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
        separators=(
            ",",
            ":",
        ),
    )

    return f"""
Нормативная проверка уже выполнена.

DATA:
{payload_json}

Для каждого finding_id:

- НЕ решай заново,
  существует ли нарушение;

- НЕ меняй смысл нарушения;

- НЕ пиши в comment,
  что проект "соответствует";
  на этот этап приходят только замечания;

- кратко переформулируй comment
  как инженерное замечание;

- recommendation сделай
  конкретной и короткой;

- Базу Опыта используй только
  как пример формулировки;

- Experience не является
  нормативным основанием;

- AFTER можно считать
  подтверждённым исправлением
  только при verified_fixed=true;

- не придумывай нормы,
  пункты и страницы;

- не вставляй N1/N2/E1/E2
  в пользовательскую формулировку;

- если опыт нерелевантен:
  experience_source_ids=[];

- comment и recommendation:
  максимум 1-2 предложения;

- верни ровно один элемент
  на каждый finding_id;

- только JSON.
""".strip()


def _fallback_final_item(
    finding: dict[str, Any],
) -> dict[str, Any]:
    """Вернуть нормативное замечание без stylistic VLM, если она упала."""

    recommendation = str(
        finding.get(
            "recommendation_draft",
            "",
        )
    ).strip()

    if not recommendation:
        recommendation = (
            "Проверить указанное несоответствие "
            "и скорректировать проектное решение "
            "по приведённому нормативному основанию."
        )

    return {
        "finding_id": str(
            finding[
                "finding_id"
            ]
        ),

        "comment": str(
            finding.get(
                "comment",
                "",
            )
        ).strip(),

        "recommendation": (
            recommendation
        ),

        "experience_source_ids": [],
    }


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
    """Оформить замечания batches и не ронять анализ при ошибке финализации."""

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
                "batch_size": (
                    OLLAMA_FINAL_BATCH_SIZE
                ),
                "fallback_count": 0,
                "batches": [],
            },
        )

    final_items: list[
        dict[str, Any]
    ] = []

    batch_metrics: list[
        dict[str, Any]
    ] = []

    fallback_count = 0

    for start in range(
        0,
        len(
            findings
        ),
        OLLAMA_FINAL_BATCH_SIZE,
    ):
        batch = findings[
            start:
            start + OLLAMA_FINAL_BATCH_SIZE
        ]

        finding_ids = [
            str(
                finding[
                    "finding_id"
                ]
            )
            for finding in batch
        ]

        try:
            (
                result,
                metrics,
            ) = await call_vlm_json(
                prompt=(
                    build_finalization_prompt(
                        findings=batch,
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

                seed=(
                    300
                    + start
                ),

                stage=(
                    "finalization:"
                    + ",".join(
                        finding_ids
                    )
                ),

                image_bytes=None,
            )

            returned = {
                str(
                    item.get(
                        "finding_id"
                    )
                ): item
                for item in (
                    result.get(
                        "findings",
                        [],
                    )
                )
                if (
                    isinstance(
                        item,
                        dict,
                    )
                    and item.get(
                        "finding_id"
                    )
                )
            }

            for finding in batch:
                finding_id = str(
                    finding[
                        "finding_id"
                    ]
                )

                item = returned.get(
                    finding_id
                )

                if item is None:
                    item = (
                        _fallback_final_item(
                            finding
                        )
                    )

                    fallback_count += 1

                final_items.append(
                    item
                )

            batch_metrics.append(
                {
                    "finding_ids": (
                        finding_ids
                    ),

                    "fallback": False,

                    **metrics,
                }
            )

        except RuntimeError as exc:
            logger.warning(
                "[finalization] "
                "fallback ids=%s error=%s",
                finding_ids,
                exc,
            )

            fallback_count += len(
                batch
            )

            for finding in batch:
                final_items.append(
                    _fallback_final_item(
                        finding
                    )
                )

            batch_metrics.append(
                {
                    "finding_ids": (
                        finding_ids
                    ),

                    "fallback": True,

                    "error": str(
                        exc
                    )[:1200],
                }
            )

    return (
        {
            "summary": (
                "Замечания сформированы "
                "по результатам нормативной проверки."
            ),

            "findings": (
                final_items
            ),
        },

        {
            "attempt": 1,

            "done_reason": (
                "completed_with_fallback"
                if fallback_count
                else "stop"
            ),

            "batch_size": (
                OLLAMA_FINAL_BATCH_SIZE
            ),

            "fallback_count": (
                fallback_count
            ),

            "batches": (
                batch_metrics
            ),
        },
    )