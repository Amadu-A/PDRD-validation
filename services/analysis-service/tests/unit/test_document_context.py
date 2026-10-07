# services/analysis-service/tests/unit/test_document_context.py

"""Регрессии сопоставления фактов и многостраничных D-доказательств."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
    CrossPageCancelled,
    DocumentContextOptions,
    DocumentPage,
    build_page_document_context,
)
from pdrd_analysis_service.domain.document_context import (
    DocumentFact,
    LocatedDocumentFact,
    discover_conflicts,
    normalized_value,
)


def _fact(
    value: str,
    *,
    identifier: str = "Б-012",
    property_name: str = "Расчётная температура наружного воздуха",
    unit: str = "°C",
    mode: str = "winter_design",
    segment: str = "",
    kind: str = "parameter",
) -> DocumentFact:
    """Создаёт атомарный факт с явной идентичностью и областью."""
    return DocumentFact(
        kind=kind,
        subject_type="room",
        subject_name=f"Помещение {identifier}",
        identifier=identifier,
        property_type="temperature",
        property_name=property_name,
        value_raw=value,
        unit_raw=unit,
        scope_system="ОВ",
        scope_location="подвал",
        scope_segment=segment,
        scope_operating_mode=mode,
        scope_condition="расчётный зимний режим" if mode == "winter_design" else mode,
        relation="",
        table_title="",
        table_id="",
        row_label="",
        column_label="",
        continuation_marker="",
        evidence_text=f"{identifier} {property_name} {value} {unit}",
    )


def _located(*facts: DocumentFact) -> tuple[LocatedDocumentFact, ...]:
    """Размещает факты на удалённых физических страницах."""
    return tuple(
        LocatedDocumentFact(page=page, index=1, fact=fact)
        for page, fact in zip((10, 17, 25, 30, 40), facts, strict=False)
    )


@pytest.mark.parametrize(
    ("second", "expected"),
    [
        (_fact("-35"), 1),
        (_fact("-35", identifier="Б-013"), 0),
        (_fact("+18", property_name="Температура внутри помещения"), 0),
        (_fact("-35", mode="summer"), 0),
        (_fact("600", unit="кПа", property_name="Давление"), 0),
        (_fact("-35", segment="наружный участок"), 0),
        (_fact("-37"), 0),
    ],
)
def test_conflict_requires_same_subject_property_scope_and_value(
    second: DocumentFact, expected: int
) -> None:
    """Разные объекты, свойства, режимы и равные значения не дают finding."""
    first = (
        _fact("0,6", unit="МПа", property_name="Давление")
        if (second.property_name == "Давление")
        else _fact("-37")
    )
    groups = discover_conflicts(_located(first, second), max_candidates=20)
    assert len(groups) == expected


def test_units_preserve_raw_and_convert_comparable_values() -> None:
    """0,6 МПа и 600 кПа равны, а DN и мм не приравниваются."""
    assert normalized_value(_fact("0,6", unit="МПа")) == ("pressure_pa", "6E+5")
    assert normalized_value(_fact("600", unit="кПа")) == ("pressure_pa", "6E+5")
    assert normalized_value(_fact("DN100", unit="")) != normalized_value(
        _fact("100", unit="мм")
    )


def test_three_values_create_one_group_not_pairwise_duplicates() -> None:
    """Три разных значения одного параметра образуют одну группу."""
    groups = discover_conflicts(
        _located(_fact("-37"), _fact("-35"), _fact("-36")),
        max_candidates=20,
    )
    assert len(groups) == 1
    assert len(groups[0].facts) == 3


def _options() -> DocumentContextOptions:
    """Возвращает короткие лимиты тестового D-контура."""
    return DocumentContextOptions(
        max_related_facts_per_page=3,
        max_text_sources_per_page=2,
        semantic_threshold=0.5,
        max_cross_page_candidates=4,
        max_evidence_sources_per_finding=4,
        validation_batch_size=2,
        validation_num_predict=500,
    )


def test_page_context_finds_remote_fact_adjacent_and_semantic_sources() -> None:
    """Одна страница получает контекст с соседней и удалённой страниц."""
    pages = (
        DocumentPage(10, "План", "План насосной", (_fact("-37"),)),
        DocumentPage(11, "Продолжение таблицы 4", "Таблица 4", ()),
        DocumentPage(17, "Экспликация", "Помещения", (_fact("-35"),)),
    )
    result = build_page_document_context(
        pages=pages,
        semantic_by_page={
            10: (
                {"page": 17, "chunk_index": 1, "score": 0.9, "text": "Б-012 -35"},
                {"page": 30, "chunk_index": 2, "score": 0.2, "text": "Нерелевантно"},
            )
        },
        options=_options(),
    )
    kinds = {item.match_type for item in result[10]}
    assert kinds == {"adjacent", "exact_identifier", "semantic"}
    assert {item.page for item in result[10]} == {11, 17}


class _ConfirmingVision:
    """Фейк одного batch semantic validation без внешнего GPU."""

    def __init__(self) -> None:
        """Считает число вызовов модели."""
        self.calls = 0

    async def generate_json(self, **kwargs: object) -> SimpleNamespace:
        """Подтверждает только переданные кандидаты."""
        self.calls += 1
        schema = kwargs["schema"]
        ids = schema["properties"]["decisions"]["items"]["properties"]["candidate_id"][
            "enum"
        ]
        return SimpleNamespace(
            payload={
                "decisions": [
                    {
                        "candidate_id": candidate_id,
                        "status": "confirmed",
                        "reason": "Тот же объект.",
                    }
                    for candidate_id in ids
                ]
            }
        )


@pytest.mark.asyncio
async def test_cross_page_finding_has_two_locations_and_one_vlm_batch() -> None:
    """Одно противоречие сохраняет обе страницы и точные области значения."""
    model = _ConfirmingVision()
    first = _fact("-37")
    second = replace(
        _fact("-35"),
        visual_regions=(
            {
                "x_min": 180,
                "y_min": 540,
                "x_max": 710,
                "y_max": 585,
                "confidence": 0.94,
            },
        ),
    )
    pages = (
        DocumentPage(
            10,
            first.evidence_text,
            "План",
            (first,),
            text_words=(
                {
                    "text": "-37",
                    "bbox": {
                        "x_min": 420,
                        "y_min": 180,
                        "x_max": 480,
                        "y_max": 235,
                    },
                },
            ),
        ),
        DocumentPage(17, second.evidence_text, "Экспликация", (second,)),
    )
    result = await CheckCrossPageConsistency(
        vision_model=model, options=_options()
    ).execute(pages=pages)
    assert model.calls == 1
    assert len(result) == 1
    finding = result[0]
    assert finding.category == "document_consistency"
    assert finding.status == "confirmed"
    assert [item["page"] for item in finding.evidence_locations] == [10, 17]
    assert all(item["visual_regions"] for item in finding.evidence_locations)
    assert finding.normative_source_ids == ()
    assert finding.basis_sources == ()
    assert "-37" in finding.comment and "-35" in finding.comment
    assert "корректное значение" in finding.comment


def test_table_identifier_links_nonadjacent_continuation() -> None:
    """Общий номер таблицы связывает её продолжение без идентификатора объекта."""
    facts = (
        replace(_fact("-37"), identifier="", table_id="Т-7"),
        replace(_fact("-35"), identifier="", table_id="т-7"),
    )
    pages = (
        DocumentPage(10, "Таблица Т-7", "Начало", (facts[0],)),
        DocumentPage(17, "Продолжение Т-7", "Продолжение", (facts[1],)),
    )
    result = build_page_document_context(
        pages=pages, semantic_by_page={}, options=_options()
    )
    assert any(
        source.match_type == "table_identifier" and source.page == 17
        for source in result[10]
    )


@pytest.mark.asyncio
async def test_evidence_limit_keeps_each_distinct_value() -> None:
    """Лимит доказательств не удаляет второе конфликтующее значение."""
    model = _ConfirmingVision()
    pages = (
        DocumentPage(10, "", "", (_fact("-37"),)),
        DocumentPage(17, "", "", (_fact("-37"),)),
        DocumentPage(25, "", "", (_fact("-35"),)),
    )
    findings = await CheckCrossPageConsistency(
        vision_model=model,
        options=replace(_options(), max_evidence_sources_per_finding=2),
    ).execute(pages=pages)
    assert len(findings) == 1
    assert [location["page"] for location in findings[0].evidence_locations] == [
        10,
        17,
        25,
    ]


@pytest.mark.asyncio
async def test_cancelled_cross_page_stage_skips_next_vlm_batch() -> None:
    """Отмена до пакета прекращает дальнейшие обращения к VLM."""
    model = _ConfirmingVision()

    async def cancelled() -> bool:
        """Возвращает отмену задания."""
        return True

    with pytest.raises(CrossPageCancelled):
        await CheckCrossPageConsistency(vision_model=model, options=_options()).execute(
            pages=(
                DocumentPage(10, "", "", (_fact("-37"),)),
                DocumentPage(17, "", "", (_fact("-35"),)),
            ),
            cancel_check=cancelled,
        )
    assert model.calls == 0
