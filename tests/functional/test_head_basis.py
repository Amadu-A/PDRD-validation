# tests/functional/test_head_basis.py

"""Напоры разных ролей и ссылки на другие расчёты не подменяют инженерное основание."""

from dataclasses import replace

from pdrd_analysis_service.application.engineering_program import (
    program_findings,
    verified_programmatic_check,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    DocumentPage,
    document_engineering_report,
)
from pdrd_analysis_service.domain.document_context import document_fact_from_mapping
from pdrd_analysis_service.domain.engineering_facts import (
    explicit_head_declarations,
    explicit_pipe_choices,
    merge_text_engineering_facts,
)

HEAD = (
    "Гарантированный напор в сетях хоз-питьевого назначения (Б7)-42,6 м. "
    "Требуемый напор в сети хозяйственно-питьевого водоснабжения составляет 18 м."
)


def basis(pages, dependency, *, enabled=True):
    """Создаёт замечания обычной цепочкой факты → реестр → проверки → findings."""
    report = document_engineering_report(
        pages, "independent-head-control", enabled=enabled
    )
    return [
        f
        for f in program_findings(report)
        if any(
            o.get("check", {}).get("property_key") == dependency
            for o in f.origin_assertions
        )
    ]


def test_native_head_declarations_keep_roles_and_create_only_a_concrete_basis_question():
    """Не сравнивает разные роли как противоречие и не назначает правильный напор."""
    facts = explicit_head_declarations(HEAD)
    assert {f.property_name: f.value_raw for f in facts} == {
        "Гарантированный напор": "42,6",
        "Требуемый напор": "18",
    }
    assert all(
        f.identifier == "Б7" and not f.scope_operating_mode and not f.scope_location
        for f in facts
    )
    result = basis((DocumentPage(7, HEAD, "", ()),), "required_head_basis")
    assert (
        len(result) == 1 and result[0].page == 7 and result[0].status == "needs_review"
    )
    assert "18 м" in result[0].comment and "разные роли" in result[0].comment
    assert verified_programmatic_check(result[0])
    assert not verified_programmatic_check(replace(result[0], status="confirmed"))
    assert not basis(
        (DocumentPage(7, HEAD, "", ()),), "required_head_basis", enabled=False
    )


def test_different_network_purposes_are_not_bound_to_one_tag_by_proximity():
    """Соседняя строка с другим назначением не доказывает принадлежность требуемого напора."""
    text = HEAD.replace(
        "сети хозяйственно-питьевого водоснабжения",
        "сети противопожарного водоснабжения",
    )
    assert not explicit_head_declarations(text)
    assert not basis((DocumentPage(7, text, "", ()),), "required_head_basis")


def test_read_calculation_with_matching_point_mode_and_inputs_closes_basis_question():
    """Действительно связанные исходные данные не превращаются в повторный вопрос о полноте."""
    accepted = explicit_head_declarations(HEAD)[1]
    prefix = "Б7 узел А, расчетный режим: "
    accepted = replace(
        accepted,
        scope_location="узел А",
        scope_operating_mode="расчетный",
        evidence_text=prefix + "требуемый напор 18 м",
    )
    facts = [accepted]
    for kind, value, unit in [
        ("calculation_point", "узел А", ""),
        ("operating_mode", "расчетный", ""),
        ("geodetic_head", "5", "м"),
        ("free_head", "10", "м"),
        ("head_loss", "3", "м"),
        ("calculation_reference", "Р-12", ""),
    ]:
        facts.append(
            replace(
                accepted,
                property_type=kind,
                property_name=kind,
                relation="",
                value_raw=value,
                unit_raw=unit,
                evidence_text=prefix + kind + " " + value + " " + unit,
            )
        )
    text = "\n".join(f.evidence_text for f in facts)
    assert not basis((DocumentPage(7, text, "", tuple(facts)),), "required_head_basis")


def test_unrelated_calculation_reference_does_not_hide_pipe_selection_question():
    """Наличие расчёта электроснабжения не обосновывает гидравлический диаметр той же сети."""
    text = (
        "Система водоснабжения Б7. Противопожарный водопровод Б7 "
        "прокладывается из полиэтиленовых труб ø90 мм. Б7: расчёт электроснабжения Р-12."
    )
    reference = document_fact_from_mapping(
        {
            "kind": "reference",
            "subject_type": "system",
            "identifier": "Б7",
            "property_type": "calculation_reference",
            "property_name": "Расчёт электроснабжения",
            "value_raw": "Р-12",
            "evidence_text": "Б7: расчёт электроснабжения Р-12.",
            "extraction_method": "pdf_text",
        }
    )
    result = basis((DocumentPage(7, text, "", (reference,)),), "pipe_selection_basis")
    assert len(result) == 1 and result[0].status == "needs_review"
    assert verified_programmatic_check(result[0])


def test_native_classification_is_not_lost_behind_same_value_from_model():
    """Совпадение текста и числа не удаляет предметную роль и происхождение native-факта."""
    text = "Противопожарный водопровод Б7 прокладывается из полиэтиленовых труб ø90 мм."
    native = explicit_pipe_choices(text)[0]
    model = replace(native, relation="", extraction_method="vlm", source_pages=(7,))
    merged = merge_text_engineering_facts((model,), text, 7)
    assert model in merged
    assert any(
        f.relation == "accepted_pipe_choice" and f.extraction_method == "pdf_text"
        for f in merged
    )
    assert merge_text_engineering_facts(merged, text, 7) == merged
