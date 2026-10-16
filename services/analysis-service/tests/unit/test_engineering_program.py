# services/analysis-service/tests/unit/test_engineering_program.py

"""Проверки инженерных зависимостей на исходных фактах, без готовых model findings."""

from dataclasses import replace
from decimal import Decimal
from time import perf_counter

import pytest
from pdrd_analysis_service.application.engineering_program import (
    audit_engineering_candidates,
    program_findings,
    requirement_evidence,
    run_engineering_program,
    verified_programmatic_check,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    merge_cross_page_findings,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.application.use_cases.finding_localization import (
    LocalizeFindings,
)
from pdrd_analysis_service.domain.analysis import (
    NormativeSource,
    TechnicalAssignmentSource,
)
from pdrd_analysis_service.domain.design_decisions import build_decision_registry
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
)
from pdrd_analysis_service.domain.engineering_assertion import assertion_from_facts
from pdrd_analysis_service.domain.engineering_calculations import arithmetic
from pdrd_analysis_service.domain.project_dependencies import (
    explicit_project_dependencies,
)
from pdrd_analysis_service.domain.requirement_applicability import (
    RequirementEvidence,
    check_requirement,
)
from pdrd_analysis_service.domain.visualization import FindingLocalizationTarget


def fact(kind, value, unit="", **extra):
    """Сохраняет проектное значение с буквальной цитатой и режимом, без ответа проверяющего."""
    quote = extra.pop("evidence_text", f"S1 {kind}: {value} {unit}")
    return document_fact_from_mapping(
        {
            "kind": "parameter",
            "subject_type": "system",
            "subject_name": "Система S1",
            "identifier": "S1",
            "scope_system": "S1",
            "scope_operating_mode": "fire",
            "property_type": kind,
            "property_name": kind,
            "value_raw": value,
            "unit_raw": unit,
            "evidence_text": quote,
            "extraction_method": "pdf_text",
            **extra,
        }
    )


def report(values, *, enabled=True, sources=()):
    """Передаёт настоящую предметную модель и полный доступный текст каждой страницы."""
    located, counts = [], {}
    for index, item in enumerate(values):
        page = item.source_pages[0] if item.source_pages else 1 + index // 4
        counts[page] = counts.get(page, 0) + 1
        located.append(LocatedDocumentFact(page, counts[page], item))
    located = tuple(located)
    texts = {
        page: "\n".join(
            item.fact.evidence_text for item in located if item.page == page
        )
        for page in {item.page for item in located}
    }
    registry = build_decision_registry(
        document_id="project-A",
        facts=located,
        page_texts=texts,
        document_context_enabled=enabled,
    )
    return run_engineering_program(
        registry, page_texts=texts, requirement_sources=sources
    )


@pytest.mark.parametrize(
    "expression,expected",
    [("2+3*4", "14"), ("0,738*1000/3600", ".205"), ("2×2.5", "5"), ("-2+3", "1")],
)
def test_safe_decimal_arithmetic(expression, expected):
    """Числа не проходят через двоичную плавающую арифметику или eval."""
    assert arithmetic(expression) == Decimal(expected)


@pytest.mark.parametrize(
    "expression",
    ["1/0", "2**100000", "__import__('os')", "f(1)", "a+1", "[1]", "9*" * 1000],
)
def test_arithmetic_rejects_unsafe_or_unbounded_syntax(expression):
    """Недопустимая запись не выполняет код и не создаёт вычисленный результат."""
    assert arithmetic(expression) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Q = 2+3=6 м³/ч", "confirmed"),
        ("Q=2×2,5=5 л/с", "no_finding"),
        ("2×2,5 л/с", None),
    ],
)
def test_literal_equations_create_only_reproducible_findings(text, expected):
    """Проверяется записанное равенство; обозначение 2×2,5 само по себе не ошибка."""
    result = report(explicit_project_dependencies(text, 1))
    checks = [check for check in result.checks if check.rule == "explicit_arithmetic"]
    assert ([check.status for check in checks] or [None]) == [expected]
    findings = program_findings(result)
    assert len(findings) == int(expected == "confirmed")
    if findings:
        assert verified_programmatic_check(findings[0])


def test_reference_resolves_remote_definition_and_ignores_another_mention():
    """Второе «см.» не является определением приложения; полный заголовок разрешает ссылку."""
    refs = explicit_project_dependencies("см. приложение А", 1)
    missing = report((*refs, *explicit_project_dependencies("см. приложение А", 2)))
    (question,) = program_findings(missing)
    assert (
        question.status == "needs_review"
        and "во всём проекте" in question.recommendation_draft
    )
    assert verified_programmatic_check(question)
    resolved = report(
        (*refs, *explicit_project_dependencies("Приложение А\nРасчёт расхода", 2))
    )
    assert program_findings(resolved) == ()
    assert (
        next(
            check for check in resolved.checks if check.rule == "reference_resolution"
        ).status
        == "no_finding"
    )


def test_d_false_does_not_resolve_remote_material_or_create_d_sources():
    """Локальные вычисления разрешены, скрытые межстраничные проверки и источники D — нет."""
    result = report(
        explicit_project_dependencies("2+3=6 м³/ч; см. приложение А", 1), enabled=False
    )
    (finding,) = program_findings(result)
    assert finding.document_context_basis_sources == ()
    assert finding.document_context_source_ids == ()
    assert verified_programmatic_check(finding)


@pytest.mark.parametrize(
    "available,required,expected",
    [("64.82", "15", "no_finding"), ("15", "60", "confirmed")],
)
def test_head_roles_are_explicit_and_available_is_not_required(
    available, required, expected
):
    """Гарантированное и требуемое значения проверяются как разные роли одного баланса."""
    values = [
        fact(
            "available_head",
            available,
            "м",
            evidence_text=f"S1 располагаемый напор {available} м",
        ),
        fact(
            "required_head",
            required,
            "м",
            evidence_text=f"S1 требуемый напор {required} м",
        ),
    ]
    result = report(values)
    check = next(check for check in result.checks if check.rule == "available_head")
    assert check.status == expected
    if expected == "confirmed":
        assert verified_programmatic_check(program_findings(result)[0])
    values[1] = replace(values[1], scope_operating_mode="normal")
    assert not any(check.status == "confirmed" for check in report(values).checks)


def fire_values(volume="90", refill="0"):
    """Содержит все явные исходные данные водного баланса, включая гарантированный приток."""
    return [
        fact(
            "useful_fire_volume",
            volume,
            "м³",
            evidence_text=f"S1 полезный пожарный запас {volume} м³",
        ),
        fact("design_fire_flow", "10", "л/с"),
        fact("fire_duration", "3", "ч"),
        fact("guaranteed_refill_flow", refill, "л/с"),
    ]


def test_fire_reserve_uses_useful_volume_duration_and_explicit_refill():
    """10 л/с × 3 ч дают 108 м³; известная подпитка меняет баланс, геометрический объём не подменяет полезный."""
    result = report(fire_values())
    check = next(check for check in result.checks if check.rule == "fire_reserve")
    assert (
        check.status == "confirmed" and Decimal(check.calculation["required_m3"]) == 108
    )
    assert verified_programmatic_check(program_findings(result)[0])
    assert (
        next(
            check
            for check in report(fire_values(refill="5")).checks
            if check.rule == "fire_reserve"
        ).status
        == "no_finding"
    )
    assert program_findings(report(fire_values()[:-1])) == ()
    values = fire_values()
    values[0] = replace(values[0], property_type="geometric_volume")
    assert program_findings(report(values)) == ()


def table_values(total="155.122", complete=True):
    """Числа исходной продолженной таблицы; источник строк задан отдельно от готового вывода."""
    numbers = ["2.25", "1.479", "17.700", "3.464", "122.071", "8.158"]
    values = [
        fact(
            "water_day",
            value,
            "м³/сут",
            relation="sum_component",
            table_id="Т7",
            row_label=f"row-{index}",
            column_label="м³/сут",
            source_row_ids=[f"r{index}"],
        )
        for index, value in enumerate(numbers)
    ]
    values.append(
        fact(
            "water_day",
            total,
            "м³/сут",
            relation="sum_total",
            table_id="Т7",
            row_label="Итого",
            column_label="м³/сут",
            source_row_ids=["total"],
        )
    )
    if complete:
        values.append(
            fact(
                "table_marker",
                "end",
                relation="table_end",
                table_id="Т7",
                evidence_text="Конец таблицы Т7",
                source_row_ids=[*(f"r{i}" for i in range(6)), "total"],
            )
        )
    return values


def test_table_sum_requires_all_rows_continuation_and_coverage_manifest():
    """Верная сумма 155,122 не отвергается по двум первым строкам; неполная таблица не доказывает дефект."""
    result = report(table_values())
    assert (
        next(check for check in result.checks if check.rule == "table_sum").status
        == "no_finding"
    )
    assert program_findings(report(table_values(complete=False))) == ()
    result = report(table_values(total="3.729"))
    assert (
        next(check for check in result.checks if check.rule == "table_sum").status
        == "confirmed"
    )
    assert verified_programmatic_check(program_findings(result)[0])
    partial = table_values()[:2] + table_values()[-2:]
    assert program_findings(report(partial)) == ()


def procurement_values(pack=False):
    """Достоверные полные перечни изделия одного артикула/исполнения."""
    values = [
        fact("design_quantity", "53", "шт"),
        fact("purchase_quantity", "2" if pack else "20", "уп." if pack else "шт"),
        fact("inventory_complete", "design", evidence_text="Проектный перечень полный"),
        fact(
            "inventory_complete",
            "procurement",
            evidence_text="Закупочная ведомость полная",
        ),
    ]
    if pack:
        values.append(fact("pack_size", "10", "шт"))
    return values


@pytest.mark.parametrize("pack", [False, True])
def test_procurement_correctly_computes_deficit_and_package_units(pack):
    """53 минус 20 равно 33; две упаковки по 10 дают то же закупленное количество."""
    result = report(procurement_values(pack))
    check = next(
        check for check in result.checks if check.rule == "procurement_quantity"
    )
    assert check.status == "confirmed" and check.calculation["deficit"] == "33"
    assert verified_programmatic_check(program_findings(result)[0])
    incomplete = procurement_values(pack)[:2]
    assert program_findings(report(incomplete)) == ()


def test_source_constraints_and_independent_violations_keep_distinct_identity():
    """Два применимых требования не затираются одинаковым project fact ID."""
    values = [fact("flow", "5", "л/с", property_name="Расход воды")]
    sources = [
        NormativeSource(
            f"N{i}",
            str(i),
            1,
            "rules",
            None,
            3,
            0,
            f"S1 Расход воды не менее {limit} л/с",
        )
        for i, limit in [(1, 8), (2, 10)]
    ]
    result = report(values, sources=requirement_evidence(normative=sources))
    findings = program_findings(result, normative=sources)
    assert len(findings) == 2 and len({finding.finding_id for finding in findings}) == 2
    assert all(verified_programmatic_check(finding) for finding in findings)
    merged = merge_cross_page_findings(page_findings={1: findings}, cross_findings=())
    assert len(merged[1]) == 2
    forged = replace(findings[0], comment="Прибор гарантированно сгорит")
    assert not verified_programmatic_check(forged)


def test_same_proven_claim_merges_pages_without_losing_independent_evidence():
    """Равные расчёт, объект и условия объединяются, исходные страницы остаются."""
    values = [
        fact("available_head", "15", "м", evidence_text="S1 располагаемый напор 15 м"),
        fact("required_head", "60", "м", evidence_text="S1 требуемый напор 60 м"),
    ]
    first = program_findings(report(values))[0]
    second_report = report(values)
    shifted = tuple(
        LocatedDocumentFact(5, index, item.observation.fact)
        for index, item in enumerate(second_report.registry.evidence, 1)
    )
    registry = build_decision_registry(
        document_id="project-A",
        facts=shifted,
        page_texts={5: "\n".join(item.fact.evidence_text for item in shifted)},
    )
    second = program_findings(
        run_engineering_program(
            registry,
            page_texts={5: "\n".join(item.fact.evidence_text for item in shifted)},
        )
    )[0]
    merged = merge_cross_page_findings(
        page_findings={1: (first,), 5: (second,)}, cross_findings=()
    )
    items = [finding for findings in merged.values() for finding in findings]
    assert len(items) == 1
    assert {source.page for source in items[0].document_context_basis_sources} == {1, 5}
    assert verified_programmatic_check(items[0])


def test_specific_missing_calculation_is_not_a_generic_engineer_question():
    """У вопроса есть принятое значение, исходные данные, конкретная ссылка и проверенная область."""
    values = [
        fact("capacity", "100", "м³", property_name="Принятый запас"),
        fact("design_input", "10", "л/с", evidence_text="S1 исходный расход 10 л/с"),
        fact("calculation_reference", "Р1", evidence_text="S1 обоснование — расчёт Р1"),
    ]
    findings = program_findings(report(values))
    assert len(findings) == 1
    assert (
        findings[0].status == "needs_review"
        and "100" in findings[0].comment
        and "10" in findings[0].comment
        and "Р1" in findings[0].comment
    )
    assert verified_programmatic_check(findings[0])


@pytest.mark.parametrize("index", [0, 1])
def test_missing_calculation_question_requires_grounded_value_and_inputs(index):
    """Ни принятый параметр, ни исходные данные нельзя домыслить из цитаты с другим числом."""
    values = [
        fact("capacity", "100", "м³", property_name="Принятый запас"),
        fact("design_input", "10", "л/с", evidence_text="S1 исходный расход 10 л/с"),
        fact("calculation_reference", "Р1", evidence_text="S1 обоснование — расчёт Р1"),
    ]
    values[index] = replace(values[index], value_raw="999")
    result = report(values)
    assert not any(
        item.rule == "justification_chain" and item.status == "needs_review"
        for item in result.checks
    )
    assert all("999" not in item.comment for item in program_findings(result))


def test_customer_requirement_cannot_weaken_or_conflict_with_normative_source():
    """Несовместимое ТЗ становится конкретным вопросом, нормативное ограничение сохраняется."""
    values = [fact("flow", "15", "л/с", property_name="Расход воды")]
    normative = NormativeSource(
        "N1", "n", 1, "rules", None, 3, 0, "S1 Расход воды не более 10 л/с"
    )
    technical = TechnicalAssignmentSource(
        "T1",
        "t",
        1,
        "tz",
        None,
        None,
        None,
        "tz.pdf",
        2,
        "S1 Расход воды не менее 20 л/с",
    )
    result = report(
        values,
        sources=requirement_evidence(normative=(normative,), technical=(technical,)),
    )
    checks = [check for check in result.checks if check.rule == "requirement_conflict"]
    assert len(checks) == 1 and checks[0].status == "needs_review"
    findings = program_findings(result, normative=(normative,), technical=(technical,))
    assert len(findings) == 2
    assert all(verified_programmatic_check(finding) for finding in findings)
    assert {finding.status for finding in findings} == {"confirmed", "needs_review"}


def test_large_registry_has_bounded_cpu_cost_and_does_not_generate_generic_findings():
    """2000 отдельных решений не создают 2000 запросов либо общих вопросов инженеру."""
    started = perf_counter()
    values = [
        fact(
            "parameter",
            "20",
            "м",
            identifier=f"S{index}",
            evidence_text=f"S{index} параметр 20 м",
        )
        for index in range(2000)
    ]
    result = report(values)
    assert len(result.registry.decisions) == 2000
    assert program_findings(result) == ()
    assert perf_counter() - started < 15


def test_literal_grounded_vlm_facts_are_checked_without_rereading_model():
    """VLM-наблюдение можно вычислять, когда объект, роли и режим подтверждаются PAGE TEXT."""
    values = [
        fact(
            "available_head",
            "15",
            "м",
            extraction_method="",
            evidence_text="S1 располагаемый напор 15 м, пожарный режим",
        ),
        fact(
            "required_head",
            "60",
            "м",
            extraction_method="",
            evidence_text="S1 требуемый напор 60 м, пожарный режим",
        ),
    ]
    result = report(values)
    assert program_findings(result)[0].status == "confirmed"
    assert verified_programmatic_check(program_findings(result)[0])
    values[1] = replace(values[1], identifier="S2")
    assert program_findings(report(values)) == ()


def signal_values(
    *, dry=False, feeding=True, output="230", target_type="DC", functional=False
):
    """Описание реального ребра и отдельно прочитанных характеристик его концов."""
    source, target = "A/OUT:1", "B/IN:1"
    values = [
        fact(
            "connection",
            target,
            identifier=source,
            subject_type="terminal",
            relation="connected_to",
            evidence_text=f"{source} -> {target}",
        )
    ]

    def attribute(endpoint, kind, value, unit=""):
        return fact(
            kind,
            value,
            unit,
            identifier=endpoint,
            subject_type="terminal",
            evidence_text=f"{endpoint} {kind} {value} {unit}",
        )

    if dry:
        values.append(attribute(source, "signal_type", "dry_contact"))
    if feeding:
        values += [
            attribute(
                source, "feeding_voltage" if dry else "output_voltage", output, "V"
            ),
            attribute(
                source, "feeding_current_type" if dry else "output_current_type", "AC"
            ),
        ]
    values += [
        attribute(target, "allowed_input_voltage", "20...30", "V"),
        attribute(target, "input_current_type", target_type),
    ]
    if functional:
        values += [
            attribute(source, "signal_function", "fuel_close"),
            attribute(target, "signal_function", "electrical_disconnect"),
        ]
    return values


def test_signal_electrical_proof_and_separate_protective_actions():
    """Прямой 230 AC входу 20…30 DC несовместим; газовая и электрическая защита не сливаются."""
    result = report(signal_values(functional=True))
    checks = {
        check.rule: check for check in result.checks if check.rule.startswith("signal_")
    }
    assert checks["signal_compatibility"].status == "confirmed"
    assert checks["signal_function"].status == "needs_review"
    assert all(
        verified_programmatic_check(finding) for finding in program_findings(result)
    )
    unknown = report(signal_values(dry=True, feeding=False, functional=True))
    assert not any(check.status == "confirmed" for check in unknown.checks)
    assert any(check.rule == "signal_function" for check in unknown.checks)
    assert (
        next(
            check for check in unknown.checks if check.rule == "signal_compatibility"
        ).reason_code
        == "dry_contact_supply_not_observed"
    )


def test_signal_different_modes_and_conflicting_characteristics_do_not_confirm_defect():
    """Свойства соседнего режима и противоречащие значения не выбираются произвольно."""
    values = signal_values()
    values[-1] = replace(values[-1], scope_operating_mode="normal")
    assert not any(check.status == "confirmed" for check in report(values).checks)
    values = signal_values()
    values.append(
        fact(
            "output_voltage",
            "24",
            "V",
            identifier="A/OUT:1",
            subject_type="terminal",
            evidence_text="A/OUT:1 output_voltage 24 V",
        )
    )
    assert not any(check.status == "confirmed" for check in report(values).checks)


def test_false_table_sum_candidate_is_not_promoted_to_review_without_full_proof():
    """Исходный кандидат сохраняется как гипотеза с причиной, а не как доказанная ошибка суммы."""
    from pdrd_analysis_service.domain.analysis import FindingDraft

    result = report(table_values(complete=False))
    observations = tuple(item.observation for item in result.registry.evidence)
    assertion = assertion_from_facts(observations, include_related=True)
    finding = FindingDraft(
        "model-1",
        1,
        "table",
        "document_consistency",
        "warning",
        "confirmed",
        "Неверная сумма строк",
        "155.122",
        "Проверить",
        0.9,
        (),
        "",
        (),
        "",
        origin_assertions=(assertion.as_origin(),),
    )
    (guarded,) = audit_engineering_candidates((finding,), result)
    assert guarded.status == "hypothesis" and guarded.evidence == finding.evidence
    assert (
        guarded.origin_assertions[-1]["reason_code"]
        == "table_calculation_not_established"
    )


@pytest.mark.parametrize(
    "text,status",
    [
        ("S1 Расход воды не менее 8 л/с", "confirmed"),
        ("S1 Расход воды не более 8 л/с", "no_finding"),
        ("S2 Расход воды не менее 8 л/с", "not_evaluable"),
        ("S1 Расход воды не менее 8 л/с, кроме резервного режима", "not_evaluable"),
    ],
)
def test_requirement_needs_exact_object_property_and_conditions(text, status):
    """Похожий абзац или требование к соседнему объекту не подтверждает нарушение."""
    item = LocatedDocumentFact(
        1, 1, fact("flow", "5", "л/с", property_name="Расход воды")
    )
    assert (
        check_requirement(item, RequirementEvidence("N", "N1", text, 3)).status
        == status
    )
    assert (
        check_requirement(item, RequirementEvidence("E", "E1", text, 3)).status
        == "not_evaluable"
    )


@pytest.mark.parametrize("citation", ["в таблице 2.", "по таблице 2", "см. таблицу 2"])
def test_inflected_reference_and_terminal_punctuation_resolve(citation):
    """Окончание слова и точка предложения не создают ложную потерянную ссылку."""
    values = (
        *explicit_project_dependencies(citation, 1),
        *explicit_project_dependencies("Таблица 2\nПараметры", 2),
    )
    assert program_findings(report(values)) == ()


def test_normative_table_is_not_misclassified_as_missing_project_table():
    """Явная ссылка на таблицу внешнего СП не требует её наличия в проверяемом PDF."""
    assert explicit_project_dependencies("см. таблицу 1 СП 131.13330.2020", 1) == ()


@pytest.mark.parametrize(
    "quote",
    [
        "Закупочная ведомость не полная",
        "Закупочная ведомость неполная",
        "procurement not complete",
    ],
)
def test_explicit_incomplete_inventory_never_counts_as_complete(quote):
    """Отрицание полноты не исчезает из проверки по совпадению части слова."""
    values = procurement_values()
    values[-1] = replace(values[-1], evidence_text=quote)
    assert program_findings(report(values)) == ()


class ForbiddenVision:
    """Любой дополнительный запрос новой проверки — ошибка бюджета."""

    async def generate_json(self, **kwargs):
        """Падает при попытке заменить программную проверку генерацией."""
        raise AssertionError("Новый VLM-вызов запрещён")


def pipe_values(*, speed="1.27", loss=None):
    """Задаёт один явно известный участок и режим, не подменяя DN внутренним диаметром."""
    values = [
        fact("design_flow", "36", "м³/ч"),
        fact("internal_diameter", "100", "мм"),
        fact("flow_velocity", speed, "м/с"),
    ]
    if loss is not None:
        values.extend(
            [
                fact("pipe_length", "100", "м"),
                fact(
                    "darcy_friction_factor",
                    "0.020",
                    evidence_text="S1 коэффициент Дарси 0.020",
                ),
                fact("local_resistance_sum", "2"),
                fact(
                    "head_loss",
                    loss,
                    "м",
                    evidence_text=f"S1 суммарные потери напора {loss} м",
                ),
            ]
        )
    return [
        replace(item, subject_type="pipeline_segment", scope_segment="ввод")
        for item in values
    ]


@pytest.mark.parametrize(
    "speed,status", [("1.27", "no_finding"), ("2.00", "confirmed")]
)
def test_pipe_velocity_uses_flow_and_actual_internal_diameter(speed, status):
    """Расход переводится в м³/с, проверяется площадь круглой трубы с допуском округления."""
    result = report(pipe_values(speed=speed))
    check = next(item for item in result.checks if item.rule == "velocity_balance")
    assert check.status == status
    assert all(verified_programmatic_check(item) for item in program_findings(result))
    if status == "confirmed":
        assert "скорость" in program_findings(result)[0].comment


def test_pipe_outer_diameter_and_wall_are_not_nominal_diameter():
    """114×7 даёт внутренние 100 мм; номинальное обозначение такой подстановки не даёт."""
    values = pipe_values()
    values[1] = replace(
        values[1],
        property_type="outer_diameter",
        property_name="наружный диаметр",
        value_raw="114",
        evidence_text="S1 наружный диаметр 114 мм",
    )
    values.append(
        replace(
            fact("wall_thickness", "7", "мм"),
            subject_type="pipeline_segment",
            scope_segment="ввод",
        )
    )
    assert (
        next(
            item for item in report(values).checks if item.rule == "velocity_balance"
        ).status
        == "no_finding"
    )
    values[1] = replace(
        values[1],
        property_type="nominal_diameter",
        property_name="DN",
        value_raw="100",
        evidence_text="S1 DN 100 мм",
    )
    result = report(values)
    assert not any(item.rule == "velocity_balance" for item in result.checks)
    (question,) = program_findings(result)
    assert (
        question.status == "needs_review" and "без подстановки DN" in question.comment
    )
    assert verified_programmatic_check(question)


@pytest.mark.parametrize(
    "loss,status", [("1.81", "no_finding"), ("30.00", "confirmed")]
)
def test_total_head_loss_requires_darcy_factor_and_local_resistances(loss, status):
    """Суммарные потери воспроизводятся из длины, сечения, скорости, Дарси и местных сопротивлений."""
    result = report(pipe_values(loss=loss))
    check = next(item for item in result.checks if item.rule == "head_loss_balance")
    assert check.status == status
    assert all(verified_programmatic_check(item) for item in program_findings(result))
    if status == "confirmed":
        assert "суммарные потери напора" in program_findings(result)[0].comment


@pytest.mark.parametrize("omission", ["local", "darcy", "total", "mode", "units"])
def test_hydraulic_unknowns_never_prove_loss_error(omission):
    """Неизвестные местные потери не нулевые; Фаннинг, другой режим и единицы не взаимозаменяемы."""
    values = pipe_values(loss="30.00")
    if omission == "local":
        values = [
            item for item in values if item.property_type != "local_resistance_sum"
        ]
    elif omission == "darcy":
        values = [
            replace(item, evidence_text="S1 коэффициент Фаннинга 0.020")
            if item.property_type == "darcy_friction_factor"
            else item
            for item in values
        ]
    elif omission == "total":
        values = [
            replace(item, evidence_text="S1 линейные потери напора 30.00 м")
            if item.property_type == "head_loss"
            else item
            for item in values
        ]
    elif omission == "mode":
        values = [
            replace(item, scope_operating_mode="normal")
            if item.property_type == "pipe_length"
            else item
            for item in values
        ]
    else:
        values = [
            replace(
                item, unit_raw="Па", evidence_text="S1 суммарные потери напора 30.00 Па"
            )
            if item.property_type == "head_loss"
            else item
            for item in values
        ]
    result = report(values)
    assert not any(item.status == "confirmed" for item in result.checks)
    assert all(verified_programmatic_check(item) for item in program_findings(result))


def test_isolated_velocity_has_no_generic_calculation_finding():
    """Одно прочитанное число без конкретных исходных данных не создаёт общего замечания."""
    result = report((pipe_values()[2],))
    assert program_findings(result) == ()


async def test_full_programmatic_finalization_and_localization_use_zero_model_calls():
    """Новый подтверждённый finding проходит весь путь без генерации, включая N enrichment и bbox fallback."""
    result = report(explicit_project_dependencies("Q=2+3=6 м³/ч", 1))
    findings = program_findings(result)
    finalizer = FinalizeFindings(ForbiddenVision(), 2000, 8, 1000, 0.5)
    _, final, metrics = await finalizer.execute(
        findings=findings,
        experience_by_finding={},
        normative_candidates_by_finding={
            findings[0].finding_id: (
                NormativeSource(
                    "N1", "p", 1, "rules", None, 1, 0, "Случайный найденный абзац"
                ),
            )
        },
    )
    assert (
        len(final) == 1
        and final[0].status == "confirmed"
        and metrics["vlm_call_count"] == 0
    )
    locations, metrics = await LocalizeFindings(ForbiddenVision(), 1000).execute(
        page_number=1,
        extracted_text="Q=2+3=6 м³/ч",
        image_bytes=b"img",
        findings=tuple(
            FindingLocalizationTarget(item.finding_id, item.comment, item.evidence)
            for item in findings
        ),
    )
    assert locations[0].status == "unlocated" and metrics["attempt"] == 0
    bad = replace(
        findings[0],
        origin_assertions=tuple(
            {**origin, "check": {**origin["check"], "calculation": {"expected": "999"}}}
            if origin.get("origin") == "programmatic_engineering_check"
            else origin
            for origin in findings[0].origin_assertions
        ),
    )
    assert not verified_programmatic_check(bad)
    _, final, metrics = await finalizer.execute(
        findings=(bad,), experience_by_finding={}
    )
    assert (
        final == ()
        and metrics["rejected_findings"][0]["reason"] == "invalid_programmatic_proof"
    )
