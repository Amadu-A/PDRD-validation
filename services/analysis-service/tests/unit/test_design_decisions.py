# services/analysis-service/tests/unit/test_design_decisions.py

"""Идентичность решений, полнота доказательств и отсутствие инженерных догадок."""

import json
from dataclasses import asdict, replace

import pytest
from pdrd_analysis_service.application.decision_protocol import (
    attach_decision_protocols,
)
from pdrd_analysis_service.application.prompts import build_finalization_prompt
from pdrd_analysis_service.domain.analysis import FindingDraft
from pdrd_analysis_service.domain.design_decisions import build_decision_registry
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
)
from pdrd_analysis_service.domain.engineering_assertion import assertion_from_facts
from pdrd_analysis_service.domain.signal_connections import build_signal_graph


def fact(**overrides):
    """Создаёт исходную запись без готового замечания или решения проверяющего."""
    raw = {
        "kind": "parameter",
        "subject_type": "system",
        "subject_name": "Система S1",
        "identifier": "S1",
        "property_type": "pressure",
        "property_name": "Напор",
        "value_raw": "20",
        "unit_raw": "м",
        "scope_system": "S1",
        "evidence_text": "S1: напор 20 м",
        "extraction_method": "pdf_text",
        **overrides,
    }
    return document_fact_from_mapping(raw)


def registry(*values, enabled=True, document_id="doc-A", texts=None):
    """Размещает исходные факты на разных страницах в изолированном документе."""
    located = tuple(
        LocatedDocumentFact(index, 1, value) for index, value in enumerate(values, 1)
    )
    return build_decision_registry(
        document_id=document_id,
        facts=located,
        page_texts=texts
        if texts is not None
        else {item.page: item.fact.evidence_text for item in located},
        document_context_enabled=enabled,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"identifier": "S2"},
        {"subject_type": "equipment"},
        {"scope_system": "S2"},
        {"scope_location": "насосная"},
        {"scope_segment": "ответвление"},
        {"scope_operating_mode": "fire"},
        {"scope_condition": "аварийная подпитка"},
        {"property_name": "Располагаемый напор"},
        {"column_label": "Расчётный"},
        {"column_label": "Номинальный"},
        {"column_label": "Располагаемый"},
        {"column_label": "Пожарный режим"},
        {"row_label": "Насос 2", "table_id": "Т2"},
    ],
)
def test_similar_names_do_not_merge_different_objects_scopes_or_roles(change):
    """Совпадение названия не перекрывает различие объекта, области, режима или свойства."""
    result = registry(fact(), fact(**change))
    assert len(result.decisions) == 2
    assert len({item.decision_id for item in result.decisions}) == 2


def test_nominal_and_outer_diameters_are_independent_decisions():
    """DN и наружный размер не превращаются в два конфликтующих значения одного свойства."""
    result = registry(
        fact(property_name="Диаметр", value_raw="DN100", unit_raw=""),
        fact(property_name="Диаметр", value_raw="110", unit_raw="мм"),
    )
    # Общее число 110 без определения вида диаметра не сопоставляется с DN.
    assert len(result.decisions) == 2


def test_decision_id_does_not_depend_on_fact_order_value_or_page_index():
    """Уточнение значения/порядка наблюдений не создаёт новое решение того же объекта."""
    first = registry(fact(), fact(value_raw="25"))
    second = registry(fact(value_raw="25"), fact())
    assert len(first.decisions) == 1
    assert first.decisions[0].decision_id == second.decisions[0].decision_id
    assert first.decisions[0].completeness == second.decisions[0].completeness
    assert "contradictory" not in dict(first.decisions[0].completeness).values()


def test_unknown_identifier_is_retained_without_name_based_merging():
    """Два похожих имени не устанавливают объект; оба исходных факта сохраняются."""
    result = registry(fact(identifier=""), fact(identifier=""))
    assert result.decisions == ()
    assert result.unassigned_evidence_ids == ("p0001-f0001", "p0002-f0001")
    assert len(result.evidence) == 2


def test_local_relay_identifier_without_cabinet_does_not_merge_pages():
    """Одинаковый K1 без шкафа не доказывает идентичность аппаратов разных схем."""
    result = registry(
        fact(identifier="K1", subject_type="relay"),
        fact(identifier="K1", subject_type="relay"),
    )
    assert len(result.decisions) == 2
    assert all(
        dict(item.completeness)["identity"] == "ambiguous" for item in result.decisions
    )


def test_document_namespace_and_d_switch_prevent_cross_job_and_cross_page_linking():
    """Реестр не разделяется заданиями; D=false не включает скрытое объединение листов."""
    first, second = registry(fact()), registry(fact(), document_id="doc-B")
    assert first.decisions[0].decision_id != second.decisions[0].decision_id
    local = registry(fact(), fact(), enabled=False)
    assert len(local.decisions) == 2
    assert all(
        dict(item.completeness)["related_documents"] == "unavailable"
        for item in local.decisions
    )


def test_missing_justification_is_not_reported_as_missing_project_calculation():
    """Прочитанное значение не доказывает отсутствие расчёта во всём проекте."""
    decision = registry(fact()).decisions[0]
    assert dict(decision.completeness)["calculation_refs"] == "not_observed"
    assert decision.as_protocol()["checks"][0]["status"] == "not_evaluable"
    assert "calculation_refs" in decision.unresolved_dependencies


def test_explicit_global_system_purpose_links_modes_without_merging_their_parameters():
    """Общее назначение одного явного тега доступно разным режимам; расходы режимов остаются отдельными."""
    result = registry(
        fact(
            property_type="relationship",
            property_name="Назначение системы",
            value_raw="водоснабжение",
            evidence_text="S1 Назначение системы: водоснабжение",
        ),
        fact(scope_operating_mode="fire"),
        fact(scope_operating_mode="normal"),
    )
    heads = [
        item for item in result.decisions if item.decision_type == "pressure:напор"
    ]
    assert len(heads) == 2
    assert all(item.purpose_evidence_ids == ("p0001-f0001",) for item in heads)


def test_all_original_geometry_and_table_continuations_survive_json():
    """Неполная таблица не получает арифметического вердикта; ни одна ячейка не теряется."""
    region = {"x_min": 1, "y_min": 2, "x_max": 20, "y_max": 30, "confidence": 1.0}
    values = [
        fact(
            table_id="Т8",
            row_label="Потребитель",
            column_label="м³/сут",
            continuation_marker=marker,
            source_pages=[page],
            source_row_ids=[f"row-{page}"],
            visual_regions=[region],
        )
        for page, marker in [(1, "начало"), (2, "продолжение")]
    ]
    source = registry(*values)
    encoded = json.loads(json.dumps(source.as_dict(), ensure_ascii=False))
    assert len(encoded["evidence"]) == 2
    assert encoded["evidence"][0]["observation"]["fact"] == json.loads(
        json.dumps(asdict(values[0]))
    )
    (table,) = encoded["tables"]
    assert table["observed_pages"] == [1, 2]
    assert table["completeness"] == "ambiguous"
    assert [cell["continuation_marker"] for cell in table["cells"]] == [
        "начало",
        "продолжение",
    ]


def test_ungrounded_extraction_remains_ambiguous_and_is_not_deleted():
    """Ошибка извлечения не становится дефектом документа и сохраняется для разбора."""
    result = registry(fact(), texts={1: "Другой текст"})
    assert result.evidence[0].grounding == "ambiguous"
    assert dict(result.decisions[0].completeness)["accepted_parameters"] == "ambiguous"
    assert len(result.evidence) == 1


def test_same_object_references_are_evidence_not_verified_requirements():
    """Ссылка на расчёт остаётся ссылкой; её применимость ещё должна быть установлена."""
    result = registry(
        fact(),
        fact(
            property_type="calculation_reference",
            property_name="Расчёт",
            value_raw="Р1",
        ),
        fact(
            property_type="requirement_reference",
            property_name="Требование",
            value_raw="N1",
        ),
    )
    pressure = next(
        item for item in result.decisions if item.decision_type == "pressure:напор"
    )
    assert pressure.calculation_ref_ids == ("p0002-f0001",)
    assert pressure.requirement_ref_ids == ("p0003-f0001",)
    assert pressure.as_protocol()["checks"][0]["status"] == "not_evaluable"


def test_duplicate_fact_ids_and_empty_namespace_are_rejected():
    """Ошибочная нумерация не может молча затереть доказательство в словаре."""
    item = LocatedDocumentFact(1, 1, fact())
    with pytest.raises(ValueError, match="повторены"):
        build_decision_registry(document_id="doc", facts=(item, item), page_texts={})
    with pytest.raises(ValueError, match="идентификатор"):
        build_decision_registry(document_id="", facts=(item,), page_texts={})


def connection(source, target, **extra):
    """Содержит явно прочитанный адрес каждого конца физической связи."""
    return fact(
        identifier=source,
        subject_type="terminal",
        property_type="connection",
        property_name="Соединение",
        relation="connected_to",
        value_raw=target,
        unit_raw="",
        evidence_text=f"{source} -> {target}",
        **extra,
    )


def test_signal_path_preserves_intermediate_relay_and_different_terminal_names():
    """Программно прослеживается путь через реле; разные клеммники не объявляются ошибкой."""
    values = [
        connection("A/XT9:1", "A/K1:11"),
        connection("A/K1:11", "A/K1:14"),
        connection("A/K1:14", "B/1XT3:7"),
    ]
    graph = build_signal_graph(registry(*values))
    report = graph.trace("A/XT9:1", "B/1XT3:7", scope=graph.connections[0].scope)
    assert [edge["target"] for edge in report["path"]] == [
        "A/K1:11",
        "A/K1:14",
        "B/1XT3:7",
    ]
    assert report["status"] == "not_evaluable"
    assert report["reason_code"] == "electrical_compatibility_not_checked"


@pytest.mark.parametrize(
    "alteration", ["gap", "direction", "scope", "unqualified", "ungrounded", "d_off"]
)
def test_unknown_or_incomparable_connection_does_not_make_a_complete_path(alteration):
    """Недостающая связь, обратное направление и иной режим не обходятся догадкой."""
    first = connection("A/XT1:1", "A/K1:11")
    second = connection("A/K1:11", "B/XT2:1")
    if alteration == "gap":
        second = replace(second, identifier="A/K2:11")
    if alteration == "direction":
        second = replace(
            second, identifier=second.value_raw, value_raw=second.identifier
        )
    if alteration == "scope":
        second = replace(second, scope_operating_mode="аварийный")
    if alteration == "unqualified":
        second = replace(second, value_raw="XT2:1")
    source = registry(
        first,
        second,
        enabled=alteration != "d_off",
        texts={1: first.evidence_text, 2: ""} if alteration == "ungrounded" else None,
    )
    graph = build_signal_graph(source)
    report = graph.trace("A/XT1:1", "B/XT2:1", scope=graph.connections[0].scope)
    assert report["status"] == "not_evaluable" and report["path"] == []


def test_dry_contact_does_not_acquire_voltage_from_model_name_or_neighbor():
    """Тип контакта сохраняется; модель ELHART не используется как код напряжения."""
    source = registry(
        connection("A/ECV1:1", "B/IN:1"),
        fact(
            identifier="A/ECV1:1",
            value_raw="dry_contact",
            unit_raw="",
            property_type="signal_type",
        ),
        fact(
            identifier="A/ECV1:1",
            value_raw="ELHART ECV1-L-RR-RS",
            unit_raw="",
            property_type="equipment_model",
        ),
    )
    graph = build_signal_graph(source)
    edge = graph.connections[0]
    assert {item.fact.value_raw for item in edge.source_attributes} == {
        "dry_contact",
        "ELHART ECV1-L-RR-RS",
    }
    assert not any(
        item.fact.property_type == "voltage" for item in edge.source_attributes
    )


def test_table_protocol_separates_operating_modes_and_disabled_document_context():
    """Даже обзор таблиц не объединяет разные режимы либо страницы при выключенном D."""
    first = fact(table_id="T1", scope_operating_mode="fire")
    second = replace(first, scope_operating_mode="normal")
    assert len(registry(first, second).table_observations()) == 2
    assert len(registry(first, first, enabled=False).table_observations()) == 2
    assert len(registry(first, first).table_observations()) == 1


def test_protocol_attachment_preserves_report_fields_and_finalization_context_size():
    """Новый реестр не меняет промпт, статус, текст, источники и геометрию finding."""
    source = registry(fact(), fact(value_raw="25"))
    assertion = assertion_from_facts(
        tuple(item.observation for item in source.evidence)
    )
    finding = FindingDraft(
        "f1",
        1,
        "text",
        "document_consistency",
        "warning",
        "needs_review",
        "Уточнить напор",
        "20 / 25",
        "Сопоставить",
        0.7,
        (),
        "",
        (),
        "",
        origin_assertions=(assertion.as_origin(),),
    )
    (attached,) = attach_decision_protocols((finding,), source)
    assert replace(attached, origin_assertions=finding.origin_assertions) == finding
    origin = attached.origin_assertions[-1]
    assert (
        origin["document_id"] == "doc-A"
        and origin["assessment_stage"] == "registry_only"
    )
    assert len(origin["decisions"]) == 1
    kwargs = {"experience_by_finding": {}, "experience_context_limit": 0}
    assert build_finalization_prompt(
        findings=(finding,), **kwargs
    ) == build_finalization_prompt(findings=(attached,), **kwargs)
    assert attach_decision_protocols((attached,), source) == (attached,)
