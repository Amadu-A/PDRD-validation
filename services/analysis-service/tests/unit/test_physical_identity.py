# services/analysis-service/tests/unit/test_physical_identity.py

"""Предметная идентичность отрезков, отметок и буквальных продолжений ссылок."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
)
from pdrd_analysis_service.domain.engineering_assertion import (
    assertion_from_facts,
    same_assertion,
)
from pdrd_analysis_service.domain.evidence_audit import audit_assertion
from pdrd_analysis_service.domain.physical_identity import (
    bind_graphical_fact,
    comparison_identity,
    pdf_label_facts,
    physical_key,
)
from pdrd_analysis_service.domain.project_dependencies import (
    normative_reference_continuations,
)


def observation(value, page=1, *, prop="Длина", unit="м", **extra):
    """Формирует исходные записи без подсказки ожидаемого инженерного вывода."""
    return LocatedDocumentFact(
        page,
        1,
        document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "system",
                "identifier": "S1",
                "property_type": "parameter",
                "property_name": prop,
                "value_raw": value,
                "unit_raw": unit,
                "evidence_text": f"S1 {prop} {value} {unit}",
                **extra,
            }
        ),
    )


def assess(*items, claim_type="contradiction"):
    """Применяет производственную проверку к исходным страницам."""
    return audit_assertion(
        assertion_from_facts(items, include_related=True, claim_type=claim_type),
        page_texts={item.page: item.fact.evidence_text for item in items},
    )


@pytest.mark.parametrize("tag", ["S1", "ввод S1", "участок S1"])
def test_system_label_never_creates_common_segment(tag):
    """Метка сети с префиксом также не доказывает общий отрезок."""
    first = observation("15", scope_segment=tag, evidence_text=f"S1 {tag} Длина 15 м")
    second = observation(
        "21,35", 2, scope_segment=tag, evidence_text=f"S1 {tag} Длина 21,35 м"
    )
    assert physical_key(first) is None
    result = assess(first, second)
    assert (
        result.disposition == "question"
        and result.reason_code == "same_physical_segment_not_established"
    )


def test_same_explicit_segment_supports_true_cross_page_conflict():
    """Совпадение узлов из подписанного диапазона допускает содержательную проверку."""
    items = tuple(
        observation(
            value,
            page,
            segment_start="У1",
            segment_end="У2",
            evidence_text=f"S1 участок У1—У2 Длина {value} м",
        )
        for page, value in ((1, "15"), (2, "16"))
    )
    assert comparison_identity(items) == "same_physical_object"
    assert assess(*items).disposition == "supported"


def test_node_mentions_without_connection_do_not_establish_segment():
    """Перечень соседних обозначений не считается диапазоном отрезка."""
    item = observation(
        "15",
        segment_start="У1",
        segment_end="У2",
        evidence_text="S1 Длина 15 м; рядом У1 и У2",
    )
    assert physical_key(item) is None


@pytest.mark.parametrize(
    "first,second", [("Ø110x10", "Ø110*10"), ("Ø110х10", "Ø110×10")]
)
def test_equivalent_pipe_sizes_are_diagnostic_before_geometry(first, second):
    """Варианты знака умножения не порождают вопрос или нарушение."""
    result = assess(
        observation(first, prop="Диаметр", unit=""),
        observation(second, 2, prop="Диаметр", unit=""),
    )
    assert (
        result.disposition == "diagnostic"
        and result.reason_code == "equivalent_values_or_compatible_descriptions"
    )


def test_nominal_and_outer_diameters_are_different_definitions():
    """DN100 и Ø110 не являются двумя значениями одного диаметра."""
    result = assess(
        observation("DN100", prop="Диаметр", unit=""),
        observation("Ø110", 2, prop="Диаметр", unit=""),
    )
    assert result.reason_code == "different_parameter_definitions"


def test_different_wall_thickness_requires_same_segment():
    """Одинаковый наружный диаметр не доказывает общую стенку разных отрезков."""
    result = assess(
        observation("Ø110x10", prop="Диаметр", unit=""),
        observation("Ø110x6,6", 2, prop="Диаметр", unit=""),
    )
    assert result.disposition == "question"


def test_dry_pipe_and_main_pipe_are_distinct():
    """Сухотруб не становится основным трубопроводом по совпадению S1."""
    a = observation(
        "Ø89x3,5", prop="Диаметр", unit="", evidence_text="S1 сухотруб Ø89x3,5"
    )
    b = observation("Ø110x10", 2, prop="Диаметр", unit="")
    assert assess(a, b).reason_code == "different_object_kinds"


def test_elevations_need_same_point_and_not_only_same_segment():
    """Отметки ввода и сухотруба, а также двух концов участка не смешиваются."""
    a = observation(
        "509,95",
        prop="Абсолютная отметка",
        segment_start="У1",
        segment_end="У2",
        evidence_text="S1 участок У1—У2 абсолютная отметка 509,95 м",
    )
    b = observation(
        "519,95",
        2,
        prop="Абсолютная отметка",
        segment_start="У1",
        segment_end="У2",
        evidence_text="S1 участок У1—У2 абсолютная отметка 519,95 м",
    )
    assert assess(a, b).disposition == "question"
    assert physical_key(a) is None
    dry = replace(
        b, fact=replace(b.fact, evidence_text=b.fact.evidence_text + " сухотруб")
    )
    assert assess(a, dry).disposition == "diagnostic"


def test_network_total_conflict_survives_and_cannot_be_declared_by_model_alone():
    """147/150 м общей сети сохраняются; роль из ответа без надписи не подтверждается."""
    a = observation("147", evidence_text="S1 протяженность сети 147 м")
    b = observation("150", 2, evidence_text="S1 общая протяженность сети 150 м")
    assert assess(a, b).disposition == "supported"
    assert physical_key(observation("15", value_role="network_total_length")) is None
    assert not same_assertion(
        assertion_from_facts((a, b)), assertion_from_facts((a, observation("15", 2)))
    )


def line(identifier, x1, y1, x2, y2, width=0.2):
    """Описывает прямой путь в системе координат PDF-слов."""
    return {
        "id": identifier,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "width": width,
        "color": [0, 0, 1],
    }


def graphical_binding(lines, *, repeated=False):
    """Передаёт геометрию через производственный алгоритм, не подставляя готовую связь."""
    box = {"x_min": 10, "y_min": 10, "x_max": 20, "y_max": 20, "confidence": 0.9}
    fact = observation("15", visual_regions=(box,)).fact
    words = [
        {
            "text": "S1",
            "block_no": 1,
            "bbox": {"x_min": 1, "y_min": 10, "x_max": 9, "y_max": 20},
        },
        {"text": "15", "block_no": 1, "bbox": box},
    ]
    if repeated:
        words.append({"text": "15", "block_no": 1, "bbox": box})
    return bind_graphical_fact(fact, page=24, text_words=words, graphic_lines=lines)


def test_unique_vector_leader_binds_local_element_and_preserves_regions():
    """Подтверждается связанная цепочка, а не расстояние до ближайшей трубы."""
    lines = (line("leader", 20, 15, 40, 30), line("pipe", 30, 30, 80, 30, 1))
    bound = graphical_binding(lines)
    assert bound.geometry_binding["status"] == "bound"
    assert bound.geometry_binding["target_lines"] == [lines[1]]
    assert bound.visual_regions[0]["x_min"] == 10
    assert physical_key(LocatedDocumentFact(24, 1, bound)) != physical_key(
        LocatedDocumentFact(25, 1, bound)
    )


@pytest.mark.parametrize(
    "lines,repeated",
    [
        ((line("nearby", 30, 30, 80, 30, 1),), False),
        (
            (line("leader", 20, 15, 40, 30), line("long_parallel", 0, 31, 1000, 31, 1)),
            False,
        ),
        (
            (
                line("leader", 20, 15, 40, 30),
                line("pipe", 30, 30, 80, 30, 1),
                line("parallel", 40, 20, 40, 40, 1),
            ),
            False,
        ),
        (
            (
                line("leader", 20, 15, 40, 30),
                line("pipe", 30, 30, 80, 30, 1),
                line("cross", 30, 10, 30, 30, 1),
            ),
            False,
        ),
        ((line("leader", 20, 15, 40, 30), line("pipe", 30, 30, 80, 30, 1)), True),
    ],
)
def test_proximity_parallel_branches_crossings_and_repeated_labels_remain_ambiguous(
    lines, repeated
):
    """Нельзя угадывать принадлежность на неоднозначной геометрии."""
    assert (
        graphical_binding(lines, repeated=repeated).geometry_binding["status"]
        != "bound"
    )


def test_real_reference_continuation_and_missing_neighbor():
    """Буквальная ссылка СП читается через границу страниц 16–17 исходного PDF."""
    path = (
        Path(__file__).resolve().parents[4]
        / "tests/fixtures/engineering/stage3-reference-continuation.json"
    )
    pages = {p["page"]: p["text"] for p in json.loads(path.read_text("utf-8"))["pages"]}
    parts = normative_reference_continuations(pages)
    assert len(parts) == 1
    page, fact = parts[0]
    assert (
        page == 16 and fact.value_raw == "СП 31.13330" and fact.source_pages == (16, 17)
    )
    assert {part["page"] for part in fact.source_fragments} == {16, 17}
    assert normative_reference_continuations({16: pages[16]}) == ()


def test_native_label_facts_keep_all_same_line_values_and_never_join_neighbor_blocks():
    """PDF-слой даёт дополнительные факты без лимита VLM и без догадки о соседней подписи."""
    region = {"x_min": 10, "y_min": 10, "x_max": 20, "y_max": 20}

    def word(text, block, index):
        """Сохраняет исходную группировку слов PyMuPDF."""
        return {
            "text": text,
            "block_no": block,
            "line_no": 0,
            "word_no": index,
            "bbox": region,
        }

    words = [word("S1,", 1, 0), word("Ø110x10L=15,00м", 1, 1), word("L=21,35м", 2, 0)]
    facts = pdf_label_facts(words, page=24, page_text="S1, Ø110x10L=15,00м\nL=21,35м")
    assert {fact.value_raw for fact in facts} == {"Ø110x10", "15,00"}
    assert all(fact.identifier == "S1" and fact.scope_segment == "" for fact in facts)
    assert all(
        fact.source_item_id == "pdf-label-p24-b1-l0" and fact.source_regions
        for fact in facts
    )
    assert (
        pdf_label_facts(
            [{"text": "S1 Ø110x10", "bbox": {}}], page=24, page_text="S1 Ø110x10"
        )
        == ()
    )


def test_invalid_vector_metadata_cannot_crash_or_confirm_binding():
    """Неполный путь из старого DTO оставляет привязку непроверенной."""
    bound = graphical_binding(
        [{"id": "broken", "x1": "bad", "y1": 1, "x2": 3, "y2": 4, "width": 1}]
    )
    assert bound.geometry_binding["status"] == "unresolved"


def test_redundant_system_scope_does_not_split_one_purpose_conflict():
    """Повтор собственного тега в scope_system не создаёт другую инженерную претензию."""
    a = observation(
        "водоотведение",
        prop="Назначение системы",
        unit="",
        evidence_text="S1 водоотведение",
    )
    b = observation(
        "водоснабжение",
        2,
        prop="Назначение системы",
        unit="",
        evidence_text="S1 водоснабжение",
    )
    repeated = replace(a, fact=replace(a.fact, scope_system="S1"))
    assert same_assertion(
        assertion_from_facts((a, b)), assertion_from_facts((repeated, b))
    )
    other = replace(a, fact=replace(a.fact, scope_system="S2"))
    assert not same_assertion(
        assertion_from_facts((a, b)), assertion_from_facts((other, b))
    )


def test_inlet_destination_is_not_a_physical_segment_boundary():
    """Один ввод в здание содержит несколько отрезков даже при точном названии здания."""
    item = observation(
        "15",
        scope_segment="ввод в здание 850/1",
        evidence_text="S1 ввод в здание 850/1 длина 15 м",
    )
    assert physical_key(item) is None
