# services/pdf-service/app/validator.py

"""Финальная RAG-проверка и нормализация кандидатов замечаний."""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import httpx


OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://ollama:11434",
).rstrip("/")

OLLAMA_VISION_MODEL = os.getenv(
    "OLLAMA_VISION_MODEL",
    "qwen3-vl:2b-instruct",
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
        "8192",
    )
)

OLLAMA_VALIDATION_NUM_PREDICT = int(
    os.getenv(
        "OLLAMA_VALIDATION_NUM_PREDICT",
        "1000",
    )
)

OLLAMA_MAX_RETRIES = int(
    os.getenv(
        "OLLAMA_MAX_RETRIES",
        "2",
    )
)

RAG_VALIDATOR_NORMATIVE_LIMIT = int(
    os.getenv(
        "RAG_VALIDATOR_NORMATIVE_LIMIT",
        "3",
    )
)

RAG_VALIDATOR_NORMATIVE_TEXT_LIMIT = int(
    os.getenv(
        "RAG_VALIDATOR_NORMATIVE_TEXT_LIMIT",
        "1200",
    )
)


def candidate_confidence(
    candidate: dict[str, Any],
) -> float:
    """Безопасно привести confidence к диапазону 0..1."""

    try:
        value = float(
            candidate.get(
                "confidence",
                0.0,
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0.0

    return min(
        max(
            value,
            0.0,
        ),
        1.0,
    )


def candidate_passes_prefilter(
    candidate: dict[str, Any],
) -> tuple[bool, str]:
    """Убрать очевидно бесполезные кандидаты до retrieval."""

    comment = str(
        candidate.get(
            "comment",
            "",
        )
    ).strip()

    if not comment:
        return (
            False,
            "Пустая формулировка замечания.",
        )

    if candidate_confidence(
        candidate
    ) <= 0:
        return (
            False,
            "Первичная модель вернула confidence=0.",
        )

    normalized = re.sub(
        r"\s+",
        " ",
        comment.lower(),
    )

    negative_markers = (
        "это не противоречие",
        "не является противоречием",
        "это не является ошибкой",
        "не является ошибкой",
        "ошибка отсутствует",
        "противоречия нет",
        "нарушения нет",
    )

    if any(
        marker in normalized
        for marker in negative_markers
    ):
        return (
            False,
            "Формулировка кандидата "
            "сама отрицает наличие проблемы.",
        )

    return True, ""


def build_rag_query(
    candidate: dict[str, Any],
) -> str:
    """Сформировать поисковый запрос из первичного замечания."""

    values = [
        (
            "Категория",
            candidate.get(
                "category"
            ),
        ),
        (
            "Тип листа",
            candidate.get(
                "page_type"
            ),
        ),
        (
            "Замечание",
            candidate.get(
                "comment"
            ),
        ),
        (
            "Основание на листе",
            candidate.get(
                "evidence"
            ),
        ),
        (
            "Рекомендация",
            candidate.get(
                "recommendation"
            ),
        ),
    ]

    return "\n".join(
        f"{label}: {value}"
        for label, value in values
        if value is not None
        and str(
            value
        ).strip()
    )


def compact_retrieval_debug(
    rag_result: dict[str, Any],
) -> dict[str, Any]:
    """Оставить в API только метаданные retrieval."""

    return {
        "normative": [
            {
                "source_id": item.get(
                    "source_id"
                ),
                "score": item.get(
                    "score"
                ),
                "source_file": item.get(
                    "source_file"
                ),
                "page": item.get(
                    "page"
                ),
            }
            for item in rag_result.get(
                "normative",
                [],
            )
        ],
        "experience": [
            {
                "source_id": item.get(
                    "source_id"
                ),
                "score": item.get(
                    "score"
                ),
                "project_id": item.get(
                    "project_id"
                ),
                "issue_id": item.get(
                    "issue_id"
                ),
                "issue_text": item.get(
                    "issue_text"
                ),
            }
            for item in rag_result.get(
                "experience",
                [],
            )
        ],
    }


def sanitize_source_ids(
    requested: Any,
    sources: list[dict[str, Any]],
    max_items: int,
) -> list[str]:
    """Оставить только реально существующие source_id."""

    if not isinstance(
        requested,
        list,
    ):
        return []

    available = {
        str(
            source.get(
                "source_id"
            )
        )
        for source in sources
        if source.get(
            "source_id"
        )
    }

    result = []

    for raw_source_id in requested:
        source_id = str(
            raw_source_id
        )

        if (
            source_id not in available
            or source_id in result
        ):
            continue

        result.append(
            source_id
        )

        if len(
            result
        ) >= max_items:
            break

    return result


def select_sources(
    sources: list[dict[str, Any]],
    source_ids: list[str],
) -> list[dict[str, Any]]:
    """Выбрать retrieval-источники по source_id."""

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

    return [
        by_id[
            source_id
        ]
        for source_id in source_ids
        if source_id in by_id
    ]


def build_basis(
    sources: list[dict[str, Any]],
) -> str:
    """Сформировать basis только из выбранных Qdrant-источников."""

    result = []

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
            result.append(
                str(
                    source_file
                )
            )
        else:
            result.append(
                f"{source_file}, "
                f"PDF стр. {page}"
            )

    return "; ".join(
        result
    )


def compact_basis_sources(
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Сформировать нормативные источники для API."""

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
            )[:700],
        }
        for source in sources
    ]


def compact_experience_sources(
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Сформировать выбранные экспертные примеры."""

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
            "before_page": source.get(
                "before_page"
            ),
            "after_page": source.get(
                "after_page"
            ),
        }
        for source in sources
    ]


def build_unvalidated_issue(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Сохранить старое поведение при use_rag=false."""

    return {
        "candidate_id": candidate.get(
            "candidate_id"
        ),
        "category": candidate.get(
            "category"
        ),
        "severity": candidate.get(
            "severity"
        ),
        "comment": candidate.get(
            "comment"
        ),
        "evidence": candidate.get(
            "evidence"
        ),
        "recommendation": candidate.get(
            "recommendation"
        ),
        "confidence": (
            candidate_confidence(
                candidate
            )
        ),
        "basis": "",
        "basis_sources": [],
        "experience_sources": [],
        "validation_status": "not_run",
        "validation_reason": (
            "RAG-валидация отключена."
        ),
        "page": candidate.get(
            "page"
        ),
        "page_type": candidate.get(
            "page_type"
        ),
    }


def build_rejected_prefilter_issue(
    candidate: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Описать кандидата, удалённого до retrieval."""

    return {
        **build_unvalidated_issue(
            candidate
        ),
        "validation_status": "rejected",
        "validation_reason": reason,
        "retrieval": {
            "normative": [],
            "experience": [],
        },
    }


def fallback_validation_decision(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Решение на случай, если модель пропустила candidate_id."""

    return {
        "candidate_id": candidate.get(
            "candidate_id"
        ),
        "status": "needs_review",
        "comment": candidate.get(
            "comment",
            "",
        ),
        "evidence": candidate.get(
            "evidence",
            "",
        ),
        "recommendation": candidate.get(
            "recommendation",
            "",
        ),
        "confidence": min(
            candidate_confidence(
                candidate
            ),
            0.5,
        ),
        "normative_source_ids": [],
        "experience_source_ids": [],
        "reason": (
            "Финальный валидатор не вернул "
            "отдельное решение для кандидата."
        ),
    }


def normalize_validated_candidate(
    *,
    candidate: dict[str, Any],
    decision: dict[str, Any],
    rag_result: dict[str, Any],
) -> dict[str, Any]:
    """Собрать финальное замечание из решения валидатора."""

    status = str(
        decision.get(
            "status",
            "needs_review",
        )
    )

    if status not in {
        "confirmed",
        "needs_review",
        "rejected",
    }:
        status = "needs_review"

    normative_sources = rag_result.get(
        "normative",
        [],
    )

    experience_sources = rag_result.get(
        "experience",
        [],
    )

    normative_ids = sanitize_source_ids(
        decision.get(
            "normative_source_ids",
            [],
        ),
        normative_sources,
        max_items=3,
    )

    experience_ids = sanitize_source_ids(
        decision.get(
            "experience_source_ids",
            [],
        ),
        experience_sources,
        max_items=2,
    )

    selected_normative = select_sources(
        normative_sources,
        normative_ids,
    )

    selected_experience = select_sources(
        experience_sources,
        experience_ids,
    )

    comment = str(
        decision.get(
            "comment",
            "",
        )
    ).strip()

    evidence = str(
        decision.get(
            "evidence",
            "",
        )
    ).strip()

    recommendation = str(
        decision.get(
            "recommendation",
            "",
        )
    ).strip()

    reason = str(
        decision.get(
            "reason",
            "",
        )
    ).strip()

    if not comment:
        comment = str(
            candidate.get(
                "comment",
                "",
            )
        )

    if not evidence:
        evidence = str(
            candidate.get(
                "evidence",
                "",
            )
        )

    if not recommendation:
        recommendation = str(
            candidate.get(
                "recommendation",
                "",
            )
        )

    try:
        confidence = float(
            decision.get(
                "confidence",
                candidate_confidence(
                    candidate
                ),
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        confidence = candidate_confidence(
            candidate
        )

    confidence = min(
        max(
            confidence,
            0.0,
        ),
        1.0,
    )

    return {
        "candidate_id": candidate.get(
            "candidate_id"
        ),
        "category": candidate.get(
            "category"
        ),
        "severity": candidate.get(
            "severity"
        ),
        "comment": comment,
        "evidence": evidence,
        "recommendation": recommendation,
        "confidence": confidence,
        "basis": build_basis(
            selected_normative
        ),
        "basis_sources": (
            compact_basis_sources(
                selected_normative
            )
        ),
        "experience_sources": (
            compact_experience_sources(
                selected_experience
            )
        ),
        "validation_status": status,
        "validation_reason": reason,
        "retrieval": (
            compact_retrieval_debug(
                rag_result
            )
        ),
        "page": candidate.get(
            "page"
        ),
        "page_type": candidate.get(
            "page_type"
        ),
    }


def build_validation_schema(
    candidate_ids: list[str],
) -> dict[str, Any]:
    """Построить JSON-схему под конкретные candidate_id."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 500,
            },
            "decisions": {
                "type": "array",
                "maxItems": len(
                    candidate_ids
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "candidate_id": {
                            "type": "string",
                            "enum": candidate_ids,
                        },
                        "status": {
                            "type": "string",
                            "enum": [
                                "confirmed",
                                "needs_review",
                                "rejected",
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
                        "normative_source_ids": {
                            "type": "array",
                            "maxItems": 3,
                            "items": {
                                "type": "string"
                            },
                        },
                        "experience_source_ids": {
                            "type": "array",
                            "maxItems": 2,
                            "items": {
                                "type": "string"
                            },
                        },
                        "reason": {
                            "type": "string",
                            "maxLength": 500,
                        },
                    },
                    "required": [
                        "candidate_id",
                        "status",
                        "comment",
                        "evidence",
                        "recommendation",
                        "confidence",
                        "normative_source_ids",
                        "experience_source_ids",
                        "reason",
                    ],
                },
            },
        },
        "required": [
            "summary",
            "decisions",
        ],
    }


def compact_rag_for_prompt(
    rag_result: dict[str, Any],
) -> dict[str, Any]:
    """Сократить retrieval-контекст для промпта."""

    normative = []

    for source in rag_result.get(
        "normative",
        [],
    )[:RAG_VALIDATOR_NORMATIVE_LIMIT]:
        normative.append(
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
                "text": str(
                    source.get(
                        "text",
                        "",
                    )
                )[
                    :RAG_VALIDATOR_NORMATIVE_TEXT_LIMIT
                ],
            }
        )

    experience = []

    for source in rag_result.get(
        "experience",
        [],
    ):
        experience.append(
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
                "before_page": source.get(
                    "before_page"
                ),
                "after_page": source.get(
                    "after_page"
                ),
            }
        )

    return {
        "query": rag_result.get(
            "query"
        ),
        "normative": normative,
        "experience": experience,
    }


def build_validation_prompt(
    *,
    page_number: int,
    page_type: str,
    extracted_text: str,
    candidates: list[dict[str, Any]],
    rag_by_candidate: dict[
        str,
        dict[str, Any],
    ],
) -> str:
    """Сформировать промпт финальной проверки листа."""

    payload = []

    for candidate in candidates:
        candidate_id = str(
            candidate[
                "candidate_id"
            ]
        )

        payload.append(
            {
                "candidate": {
                    "candidate_id": (
                        candidate_id
                    ),
                    "category": candidate.get(
                        "category"
                    ),
                    "severity": candidate.get(
                        "severity"
                    ),
                    "comment": candidate.get(
                        "comment"
                    ),
                    "evidence": candidate.get(
                        "evidence"
                    ),
                    "recommendation": (
                        candidate.get(
                            "recommendation"
                        )
                    ),
                    "confidence": candidate.get(
                        "confidence"
                    ),
                },
                "retrieval": (
                    compact_rag_for_prompt(
                        rag_by_candidate[
                            candidate_id
                        ]
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
Ты выполняешь ВТОРУЮ, финальную проверку замечаний к одному листу
проектной или рабочей документации.

Страница PDF: {page_number}
Тип страницы: {page_type}

На изображении перед тобой тот же лист, который уже был проверен
на первом этапе.

Извлечённый текст страницы:
--- НАЧАЛО ТЕКСТА ---
{extracted_text[:4000]}
--- КОНЕЦ ТЕКСТА ---

Ниже находятся кандидаты первого этапа и результаты поиска
по нормативной базе и Базе Опыта.

--- КАНДИДАТЫ И ИСТОЧНИКИ ---
{payload_json}
--- КОНЕЦ КАНДИДАТОВ И ИСТОЧНИКОВ ---

Для КАЖДОГО candidate_id вынеси одно решение.

confirmed:
- проблема действительно видна на листе;
- и/или является явным внутренним противоречием;
- и/или нормативный фрагмент прямо подтверждает требование.

needs_review:
- подозрение разумное, но данных листа или найденных источников
  недостаточно для уверенного подтверждения.

rejected:
- это не ошибка;
- кандидат противоречит изображению;
- кандидат сам отрицает наличие проблемы;
- это явная галлюцинация первого этапа.

КРИТИЧЕСКИЕ ПРАВИЛА:
- similarity score — только качество поиска, НЕ доказательство нарушения;
- normative выбирай только если текст прямо относится к замечанию;
- совпадение слов "кабель", "шкаф", "заземление" само по себе недостаточно;
- experience — инженерный опыт, но НЕ нормативное доказательство;
- внутреннее противоречие листа можно подтвердить без нормативного источника;
- если кандидат утверждает нарушение нормы, а подходящий норматив не найден,
  не придумывай норматив: используй needs_review или rejected;
- не придумывай названия ГОСТ, СП, ПУЭ, номера пунктов и страницы;
- нормативы выбираются только через source_id N1, N2, N3;
- опыт выбирается только через source_id E1, E2, E3;
- не используй source_id от другого кандидата;
- source_id может быть не выбран вообще;
- верни решение для каждого candidate_id;
- comment должен описывать проблему;
- evidence — что именно видно на листе;
- recommendation — конкретное действие проектировщика;
- никакого текста вне JSON.

Ответ должен строго соответствовать JSON-схеме.
""".strip()


async def validate_page_candidates(
    *,
    page_number: int,
    page_type: str,
    extracted_text: str,
    image_bytes: bytes,
    candidates: list[dict[str, Any]],
    rag_by_candidate: dict[
        str,
        dict[str, Any],
    ],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    """Проверить все кандидаты одного листа одним вызовом VLM."""

    if not candidates:
        return (
            {
                "summary": "",
                "decisions": [],
            },
            {
                "attempt": 0,
                "done_reason": "no_candidates",
                "total_duration_ms": 0.0,
                "load_duration_ms": 0.0,
                "prompt_eval_count": 0,
                "eval_count": 0,
            },
        )

    candidate_ids = [
        str(
            item[
                "candidate_id"
            ]
        )
        for item in candidates
    ]

    schema = build_validation_schema(
        candidate_ids
    )

    prompt = build_validation_prompt(
        page_number=page_number,
        page_type=page_type,
        extracted_text=extracted_text,
        candidates=candidates,
        rag_by_candidate=rag_by_candidate,
    )

    encoded_image = base64.b64encode(
        image_bytes
    ).decode(
        "ascii"
    )

    timeout = httpx.Timeout(
        timeout=OLLAMA_REQUEST_TIMEOUT,
        connect=20.0,
    )

    last_content = ""
    last_done_reason = None

    for attempt in range(
        1,
        OLLAMA_MAX_RETRIES + 1,
    ):
        attempt_prompt = prompt

        if attempt > 1:
            attempt_prompt += """

ПРЕДЫДУЩИЙ ОТВЕТ НЕ БЫЛ КОРРЕКТНЫМ JSON.
Повтори финальную проверку.
Верни ровно одно короткое решение для каждого candidate_id.
Не добавляй текст вне JSON.
""".strip()

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
                            {
                                "role": "user",
                                "content": (
                                    attempt_prompt
                                ),
                                "images": [
                                    encoded_image
                                ],
                            }
                        ],
                        "stream": False,
                        "format": schema,
                        "options": {
                            "temperature": 0.0,
                            "seed": (
                                142 + attempt
                            ),
                            "repeat_penalty": 1.15,
                            "num_ctx": (
                                OLLAMA_NUM_CTX
                            ),
                            "num_predict": (
                                OLLAMA_VALIDATION_NUM_PREDICT
                            ),
                        },
                    },
                )

                response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                "Ollama вернул ошибку "
                "на этапе финальной проверки: "
                f"{exc.response.status_code}: "
                f"{exc.response.text[:1500]}"
            ) from exc

        except httpx.HTTPError as exc:
            raise RuntimeError(
                "Не удалось обратиться к Ollama "
                "на этапе финальной проверки: "
                f"{exc}"
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
            if attempt < OLLAMA_MAX_RETRIES:
                continue

            raise RuntimeError(
                "Финальный валидатор не смог "
                "сформировать корректный JSON. "
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

        return parsed, metrics

    raise RuntimeError(
        "Не удалось получить ответ "
        "финального валидатора."
    )
