# tests/functional/test_inventory_description.py

"""Состав проверяется по исходным подписям без выдуманного количества или новых VLM-вызовов."""

from dataclasses import replace

from pdrd_analysis_service.application.engineering_program import (
    program_findings,
    verified_programmatic_check,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    DocumentPage,
    document_engineering_report,
)


def discover(pages, enabled=True):
    """Проходит существующий реестр и построение findings вместо подстановки эталонов."""
    return tuple(
        f
        for f in program_findings(
            document_engineering_report(
                tuple(pages), "inventory-control", enabled=enabled
            )
        )
        if any(
            o.get("check", {}).get("rule") == "inventory_description"
            for o in f.origin_assertions
        )
    )


def test_generic_singular_and_position_labels_produce_mapping_question_not_count_mismatch():
    """Единственное число не превращается в количество 1; две метки не равны двум гидрантам."""
    pages = [
        DocumentPage(4, "Противопожарный водопровод В7 с пожарным гидрантом.", "", ()),
        DocumentPage(12, "План водоснабжения\nПГ101 ПГ102", "", ()),
        DocumentPage(13, "Схема водоснабжения\nПГ101 ПГ102", "", ()),
    ]
    (finding,) = discover(pages)
    assert finding.page == 4 and finding.status == "needs_review"
    assert all(
        value in finding.comment
        for value in ["ПГ101", "ПГ102", "принадлежность", "количество", "назначение"]
    )
    assert "1 гидрант" not in finding.comment and "2 гидрант" not in finding.comment
    assert {s.page for s in finding.document_context_basis_sources} == {4, 12, 13}
    assert verified_programmatic_check(finding)
    assert not verified_programmatic_check(replace(finding, status="confirmed"))
    assert not discover(pages, enabled=False)


def test_explicit_inventory_and_unrelated_process_markers_do_not_raise_generic_question():
    """Развёрнутый состав не заменяется шаблонным вопросом; подписи другого процесса не привязываются к В7."""
    base = DocumentPage(
        4, "Противопожарный водопровод В7 с пожарным гидрантом. ПГ101 ПГ102.", "", ()
    )
    plan = DocumentPage(12, "План водоснабжения\nПГ101 ПГ102", "", ())
    assert not discover([base, plan])
    base = replace(base, text="Противопожарный водопровод В7 с пожарным гидрантом.")
    assert not discover([base, replace(plan, text="Схема парогенераторов ПГ101 ПГ102")])
    assert not discover([base, replace(plan, text="План водоснабжения ПГ101")])
