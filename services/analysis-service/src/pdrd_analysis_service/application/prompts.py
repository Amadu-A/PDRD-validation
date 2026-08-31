# services/analysis-service/src/pdrd_analysis_service/application/prompts.py

"""Промпты structured VLM pipeline."""

import json

from pdrd_analysis_service.domain.analysis import (
    ExperienceSource,
    FindingDraft,
    NormativeSource,
    PageFacts,
)


def build_page_understanding_prompt(
    *,
    page_number: int,
    heuristic_page_type: str,
    extracted_text: str,
) -> str:
    """Формирует промпт объективного понимания листа."""
    return f"""
Ты анализируешь один лист российской проектной
или рабочей документации.

Физическая страница: {page_number}
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


def build_normative_check_prompt(
    *,
    page_number: int,
    extracted_text: str,
    page_facts: PageFacts,
    normative_sources: tuple[
        NormativeSource,
        ...,
    ],
    normative_text_limit: int,
) -> str:
    """Формирует промпт нормативной проверки."""
    facts_payload = {
        "discipline": page_facts.discipline,
        "page_type": page_facts.page_type,
        "summary": page_facts.summary,
        "objects": list(
            page_facts.objects,
        ),
        "connections": list(
            page_facts.connections,
        ),
        "labels": list(
            page_facts.labels,
        ),
    }

    sources_payload = [
        {
            "source_id": source.source_id,
            "score": source.score,
            "source_file": source.source_file,
            "page": source.page,
            "chunk_index": source.chunk_index,
            "text": source.text[:normative_text_limit],
        }
        for source in normative_sources
    ]

    facts_json = json.dumps(
        facts_payload,
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
    )

    sources_json = json.dumps(
        sources_payload,
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
    )

    return f"""
Ты выполняешь нормативную проверку одного
листа инженерной документации.

Страница: {page_number}

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

- если нарушений нет:
  violations=[];

- confirmed:
  требование применимо и виден конкретный факт,
  который ему противоречит;

- needs_review:
  есть конкретное подозрение, но данных
  недостаточно для подтверждения;

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

Категории возвращай только машинными кодами
из JSON Schema.

Верни только JSON.
""".strip()


def build_experience_query(
    *,
    category: str,
    comment: str,
    evidence: str,
    recommendation_draft: str,
) -> str:
    """Формирует запрос поиска похожего опыта."""
    return "\n".join(
        [
            f"Категория: {category}",
            f"Замечание: {comment}",
            f"Факт на листе: {evidence}",
            (f"Черновая рекомендация: {recommendation_draft}"),
        ]
    ).strip()


def build_finalization_prompt(
    *,
    findings: tuple[
        FindingDraft,
        ...,
    ],
    experience_by_finding: dict[
        str,
        tuple[
            ExperienceSource,
            ...,
        ],
    ],
    experience_context_limit: int,
) -> str:
    """Формирует промпт финального оформления."""
    payload: list[dict[str, object]] = []

    for finding in findings:
        experience_examples = [
            {
                "source_id": source.source_id,
                "score": source.score,
                "project_id": source.project_id,
                "issue_id": source.issue_id,
                "issue_text": source.issue_text,
                "verified_fixed": (source.verified_fixed),
                "before_page": (source.before_page),
                "after_page": (source.after_page),
                "before_context": (source.before_context[:experience_context_limit]),
                "after_context": (source.after_context[:experience_context_limit]),
            }
            for source in (
                experience_by_finding.get(
                    finding.finding_id,
                    (),
                )
            )
        ]

        payload.append(
            {
                "finding": {
                    "finding_id": (finding.finding_id),
                    "category": (finding.category),
                    "status": (finding.status),
                    "comment": (finding.comment),
                    "evidence": (finding.evidence),
                    "recommendation_draft": (finding.recommendation_draft),
                    "normative_basis": (finding.basis),
                },
                "experience_examples": (experience_examples),
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
