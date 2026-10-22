# services/analysis-service/tests/unit/test_independent_normative.py

"""Полный корпус, самостоятельная проверка без модельных findings и защита доказательств."""

from dataclasses import replace
from uuid import UUID

import httpx
import pytest
from pdrd_analysis_service.application.engineering_program import (
    verified_programmatic_check,
)
from pdrd_analysis_service.application.independent_normative import (
    IndependentNormativeReview,
)
from pdrd_analysis_service.application.ports.normative_requirements import (
    AtomicNormativeDocument,
    NormativeRequirementsUnavailable,
)
from pdrd_analysis_service.application.use_cases.document_context import DocumentPage
from pdrd_analysis_service.domain.document_context import document_fact_from_mapping
from pdrd_analysis_service.infrastructure.normative_requirements import (
    HttpNormativeRequirementsReader,
)

DOC, SECTION, GENERATION = (str(UUID(int=n)) for n in (501, 502, 503))
DAY = "2026-10-20"


def requirement():
    """Учебная норма: она не объявляется реальным действующим СП."""
    return {
        "requirement_id": "N-R-test",
        "point_id": str(UUID(int=504)),
        "document_id": DOC,
        "source_sha256": "a" * 64,
        "source_file": "Учебный СП 100.13330.2025.pdf",
        "document_code": "СП 100.13330.2025",
        "edition": "2025",
        "clause": "4.1",
        "subclause": "",
        "page": 5,
        "source_text": "4.1 Для пожарных гидрантов расход не менее 5 л/с.",
        "text": "Для пожарных гидрантов расход не менее 5 л/с.",
        "object_type": "гидрантов",
        "extraction_status": "extracted",
        "modality": "mandatory",
        "verification_type": "numeric_limit",
        "applicability_conditions": [],
        "exceptions": [],
        "normative_dependencies": [],
        "limitations": [],
        "required_parameters": ["расход"],
        "source_fragments": [
            {
                "page": 5,
                "start": 0,
                "end": 61,
                "text": "4.1 Для пожарных гидрантов расход не менее 5 л/с.",
            }
        ],
        "normative_status": {
            "assessed_on": DAY,
            "edition_status": "current",
            "requirement_binding": "mandatory",
            "evidence_ids": ["proof"],
        },
        "status_evidence": [
            {
                "evidence_id": "proof",
                "document_id": DOC,
                "source_sha256": "a" * 64,
                "document_code": "СП 100.13330.2025",
                "edition": "2025",
                "edition_status": "current",
                "requirement_binding": "mandatory",
                "clause_scope": ["4.1"],
                "verified_by": str(UUID(int=505)),
                "valid_from": "2026-01-01",
                "valid_until": "2026-12-31",
                "confirmed_on": DAY,
                "source_url": "https://example.test/test-status",
                "source_quote": "Учебное подтверждение.",
            }
        ],
    }


def page(number=13, value="4"):
    """Исходные факты без предварительного замечания, с буквальной привязкой к объекту."""
    text = f"Пожарный гидрант ПГ7. Расход: {value} л/с."
    fact = document_fact_from_mapping(
        {
            "kind": "parameter",
            "subject_type": "system",
            "identifier": "ПГ7",
            "property_type": "parameter",
            "property_name": "Расход",
            "value_raw": value,
            "unit_raw": "л/с",
            "evidence_text": text,
            "extraction_method": "pdf_text",
        }
    )
    return DocumentPage(number, text, "", (fact,))


async def no_cancel():
    """Штатная контрольная точка без отмены."""


async def test_confirmed_visual_object_selects_same_norm_without_native_text():
    """Предмет с происхождением и областью доступен N-first и на скане."""
    original = page()
    fact = replace(
        original.facts[0],
        subject_name="Пожарный гидрант ПГ7",
        extraction_method="vlm",
        source_pages=(13,),
        visual_regions=(
            {"x_min": 10, "y_min": 10, "x_max": 80, "y_max": 80, "confidence": 0.95},
        ),
    )
    results = []
    for text in [original.text, ""]:
        result = await IndependentNormativeReview(Reader(requirement())).execute(
            document_id="scan",
            section_id=SECTION,
            document_ids=(DOC,),
            pages=(replace(original, text=text, facts=(fact,)),),
            assessed_on=DAY,
            check_cancellation=no_cancel,
        )
        results.append(result)
    assert all(r.diagnostics["selection"][0]["selected"] for r in results)
    assert not any(f.status == "confirmed" for f in results[1].findings)


@pytest.mark.parametrize("kind", ["unlocalized", "wrong_page", "unrelated"])
async def test_unbound_or_unrelated_fact_does_not_select_object_class(kind):
    """Неподтверждённое извлечение и иной объект не включают нормы о гидранте."""
    original = page()
    changes = {"subject_name": "Пожарный гидрант ПГ7", "extraction_method": "vlm"}
    if kind == "wrong_page":
        changes.update(
            source_pages=(99,),
            visual_regions=({"x_min": 1, "y_min": 1, "x_max": 90, "y_max": 90},),
        )
    if kind == "unrelated":
        changes.update(
            subject_name="Насос P7",
            identifier="P7",
            evidence_text="Насос P7: 4 л/с.",
            visual_regions=({"x_min": 1, "y_min": 1, "x_max": 90, "y_max": 90},),
        )
    result = await IndependentNormativeReview(Reader(requirement())).execute(
        document_id="scan",
        section_id=SECTION,
        document_ids=(DOC,),
        pages=(
            replace(original, text="", facts=(replace(original.facts[0], **changes),)),
        ),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert not result.diagnostics["selection"][0]["selected"]
    assert not result.findings


class Reader:
    """Подменяет только сетевой порт; расчёт, связывание и финальное доказательство настоящие."""

    def __init__(self, row):
        """Не создаёт findings или ожидаемые ответы модели."""
        self.row, self.calls = row, 0

    async def load(self, **kwargs):
        """Возвращает одну полную атомарную запись."""
        self.calls += 1
        await kwargs["check_cancellation"]()
        return AtomicNormativeDocument(
            (self.row,), {"generation_id": GENERATION, "count": 1}
        )


@pytest.mark.parametrize("value,count", [("4", 1), ("5", 0), ("7", 0)])
async def test_independent_requirement_reaches_candidate_and_revalidates_without_vlm(
    value, count
):
    """Норма порождает кандидата без исходного finding, источник проходит повторную проверку."""
    row = requirement()
    reader = Reader(row)
    result = await IndependentNormativeReview(reader).execute(
        document_id="job",
        section_id=SECTION,
        document_ids=(DOC,),
        pages=(page(value=value),),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert reader.calls == 1
    assert len(result.findings) == count
    assert (
        result.diagnostics["new_vlm_calls"]
        == result.diagnostics["new_embedding_calls"]
        == 0
    )
    assert result.diagnostics["coverage"]["scope"] == "selected_atomic_generations"
    assert result.diagnostics["corpus_received"] is True
    assert result.diagnostics["corpus_state"] == "ready"
    assert result.diagnostics["active_generation_ids"] == [GENERATION]
    assert result.diagnostics["interpretation_counts"]["interpreted"] > 0
    assert result.diagnostics["confirmed_candidates_to_finalization"] == sum(
        f.status == "confirmed" for f in result.findings
    )
    if count:
        finding = result.findings[0]
        assert finding.basis_sources[0].atomic_requirement == row
        assert finding.normative_source_ids == (row["requirement_id"],)
        assert verified_programmatic_check(finding)
        assert not verified_programmatic_check(
            replace(finding, comment="Произвольный вывод без доказательства")
        )


@pytest.mark.parametrize(
    "corruption",
    ["status", "sha", "clause", "edition", "date", "extraction", "exceptions"],
)
async def test_unknown_or_incomplete_norm_never_becomes_confirmed_violation(corruption):
    """Дата, предмет действия нормы и полнота извлечения не заменяются флагом READY."""
    row = requirement()
    if corruption == "status":
        row["status_evidence"] = []
    elif corruption == "sha":
        row["status_evidence"][0]["source_sha256"] = "b" * 64
    elif corruption == "clause":
        row["status_evidence"][0]["clause_scope"] = ["9.1"]
    elif corruption == "edition":
        row["status_evidence"][0]["edition"] = "2024"
    elif corruption == "date":
        row["status_evidence"][0]["valid_until"] = "2026-10-19"
    elif corruption == "extraction":
        row["extraction_status"] = "needs_review"
    elif corruption == "exceptions":
        row["exceptions"] = ["кроме гидрантов специального исполнения"]
        row["extraction_status"] = "needs_review"
    result = await IndependentNormativeReview(Reader(row)).execute(
        document_id="job",
        section_id=SECTION,
        document_ids=(DOC,),
        pages=(page(),),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert result.findings == ()
    assert result.diagnostics["checks"]


async def test_pagination_is_complete_and_generation_is_pinned():
    """Второй HTTP-запрос закрепляет поколение, а потеря последней записи отклоняется."""
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/documents"):
            return httpx.Response(
                200, json=[{"document_id": DOC, "section_id": SECTION}]
            )
        offset = int(request.url.params["offset"])
        row = requirement()
        row["requirement_id"] += str(offset)
        return httpx.Response(
            200,
            json={
                "document_id": DOC,
                "generation_id": GENERATION,
                "state": "ready",
                "source_sha256": "a" * 64,
                "extractor_version": "normative-native-v1",
                "status_evidence": [],
                "count": 2,
                "offset": offset,
                "next_offset": 1 if offset == 0 else None,
                "requirements": [row],
            },
        )

    reader = HttpNormativeRequirementsReader(
        "http://knowledge.test", transport=httpx.MockTransport(respond)
    )
    result = await reader.load(
        section_id=SECTION,
        document_id=DOC,
        generation_id=None,
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert len(result.requirements) == 2
    assert requests[-1].url.params["generation_id"] == GENERATION


@pytest.mark.parametrize("kind", ["conditional", "table", "formula", "component"])
async def test_compound_rule_uses_all_bound_inputs_and_survives_final_proof(kind):
    """Составное правило вычисляется из буквальных условий/таблицы/формулы, без VLM."""
    row = requirement()
    row.update(
        extraction_status="needs_review",
        limitations=["structured_interpretation_required"],
    )
    values = [("Объем", "500", "м³"), ("Расход", "4", "л/с")]
    if kind == "conditional":
        row.update(
            verification_type="conditional",
            source_text="4.1 Для пожарных гидрантов при объеме не более 1000 м³, расход не менее 5 л/с.",
        )
    elif kind == "table":
        row.update(
            verification_type="table",
            source_text="4.1 Для пожарных гидрантов:\nОбъем, м³ | Расход, л/с, не менее\nдо 1000 | 5\nсвыше 1000 | 10",
        )
    elif kind == "formula":
        row.update(
            verification_type="formula",
            source_text=(
                "4.1 Для пожарных гидрантов:\nV = Q * t\nV — Объем, м³\n"
                "Q — Расход, м³/ч\nt — Продолжительность, ч"
            ),
        )
        values = [
            ("Объем", "8", "м³"),
            ("Расход", "5", "м³/ч"),
            ("Продолжительность", "2", "ч"),
        ]
    else:
        row.update(
            verification_type="system_composition",
            source_text="4.1 Для пожарных гидрантов необходимо предусмотреть резервный насос.",
        )
        values = [("Наличие резервный насос", "отсутствует", "")]
    row["text"] = row["source_text"]
    facts = tuple(
        document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "system",
                "identifier": "ПГ7",
                "property_type": "parameter",
                "property_name": prop,
                "value_raw": value,
                "unit_raw": unit,
                "evidence_text": f"Пожарный гидрант ПГ7. {prop}: {value} {unit}.",
                "extraction_method": "pdf_text",
            }
        )
        for prop, value, unit in values
    )
    source_page = DocumentPage(
        13, "\n".join(fact.evidence_text for fact in facts), "", facts
    )
    result = await IndependentNormativeReview(Reader(row)).execute(
        document_id="job",
        section_id=SECTION,
        document_ids=(DOC,),
        pages=(source_page,),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert len(result.findings) == 1, result.diagnostics["checks"]
    assert result.findings[0].status == "confirmed"
    assert verified_programmatic_check(result.findings[0])


async def test_missing_condition_or_variable_is_not_a_violation():
    """Известный расход не отменяет обязательность исходного объёма/классификации объекта."""
    row = requirement()
    row.update(
        verification_type="conditional",
        extraction_status="needs_review",
        limitations=["structured_interpretation_required"],
        source_text="4.1 Для пожарных гидрантов при объеме не более 1000 м³, расход не менее 5 л/с.",
    )
    row["text"] = row["source_text"]
    result = await IndependentNormativeReview(Reader(row)).execute(
        document_id="job",
        section_id=SECTION,
        document_ids=(DOC,),
        pages=(page(),),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert result.findings == ()
    assert result.diagnostics["checks"][0]["result"] == "applicability_unknown"


async def test_incomplete_catalog_is_diagnostic_and_does_not_mask_programming_errors():
    """Сетевая недоступность сохраняется явно; непредвиденное исключение не становится пустым успехом."""

    class Missing:
        async def load(self, **kwargs):
            """Имитирует только ожидаемую недоступность источника."""
            raise NormativeRequirementsUnavailable("not_indexed")

    result = await IndependentNormativeReview(Missing()).execute(
        document_id="job",
        section_id=SECTION,
        document_ids=(DOC,),
        pages=(page(),),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert result.diagnostics["coverage"]["source_complete"] is False
    assert result.diagnostics["unavailable_documents"][0]["result"] == "source_missing"

    class Broken:
        async def load(self, **kwargs):
            """Программный дефект должен дойти до теста."""
            raise RuntimeError("implementation_bug")

    with pytest.raises(RuntimeError, match="implementation_bug"):
        await IndependentNormativeReview(Broken()).execute(
            document_id="job",
            section_id=SECTION,
            document_ids=(DOC,),
            pages=(page(),),
            assessed_on=DAY,
            check_cancellation=no_cancel,
        )


@pytest.mark.parametrize(
    "change",
    [
        "no_status_key",
        "bad_pagination",
        "missing_generation",
        "count_bool",
        "missing_dependency",
    ],
)
async def test_malformed_catalog_or_status_is_explicitly_unavailable_or_unknown(change):
    """Повреждённый внешний контракт не создаёт нарушение и не вызывает KeyError."""
    row = requirement()
    if change == "no_status_key":
        del row["status_evidence"][0]["edition_status"]
        result = await IndependentNormativeReview(Reader(row)).execute(
            document_id="job",
            section_id=SECTION,
            document_ids=(DOC,),
            pages=(page(),),
            assessed_on=DAY,
            check_cancellation=no_cancel,
        )
        assert not result.findings
        return
    if change == "missing_dependency":
        row.update(
            verification_type="conditional",
            normative_dependencies=["пункт 9.2"],
            source_text="4.1 Для пожарных гидрантов при объеме не более 1000 м³, расход не менее 5 л/с.",
        )
        result = await IndependentNormativeReview(Reader(row)).execute(
            document_id="job",
            section_id=SECTION,
            document_ids=(DOC,),
            pages=(page(),),
            assessed_on=DAY,
            check_cancellation=no_cancel,
        )
        assert not result.findings
        return

    def respond(request):
        if request.url.path.endswith("/documents"):
            return httpx.Response(
                200, json=[{"document_id": DOC, "section_id": SECTION}]
            )
        data = {
            "document_id": DOC,
            "generation_id": GENERATION,
            "state": "ready",
            "source_sha256": "a" * 64,
            "extractor_version": "normative-native-v1",
            "status_evidence": [],
            "count": 2,
            "offset": 0,
            "next_offset": 0,
            "requirements": [row],
        }
        if change == "missing_generation":
            del data["generation_id"]
        if change == "count_bool":
            data["count"] = True
        return httpx.Response(200, json=data)

    reader = HttpNormativeRequirementsReader(
        "http://knowledge.test", transport=httpx.MockTransport(respond)
    )
    with pytest.raises(NormativeRequirementsUnavailable):
        await reader.load(
            section_id=SECTION,
            document_id=DOC,
            generation_id=None,
            assessed_on=DAY,
            check_cancellation=no_cancel,
        )


def test_formula_intervals_cover_nonmonotonic_values_and_refuse_singularity():
    """Углы диапазона не заменяют распространение неопределённости через формулу."""
    from decimal import Decimal

    from pdrd_analysis_service.domain.compound_normative import _formula_interval

    variables = {"Q": (Decimal("0.5"), Decimal("0.5"), ())}
    low, high = _formula_interval("Q * (1 - Q)", variables)
    assert low <= Decimal("0.25") <= high
    assert _formula_interval("1 / (Q - 0.5)", variables) is None
    assert _formula_interval("__import__('os')", variables) is None


async def test_large_unsupported_corpus_keeps_every_requirement_without_vlm():
    """Охват полного корпуса не сокращается до top-K; неподдержанные атомы остаются в диагностике."""
    import asyncio
    import time

    rows = []
    for index in range(6000):
        row = requirement()
        row.update(
            requirement_id=f"N-corpus-{index}",
            verification_type="unsupported",
            extraction_status="needs_review",
        )
        rows.append(row)

    class Corpus:
        async def load(self, **kwargs):
            """Возвращает весь корпус, без предварительных замечаний."""
            return AtomicNormativeDocument(
                tuple(rows), {"generation_id": GENERATION, "count": len(rows)}
            )

    started = time.perf_counter()
    neighbouring_job = asyncio.create_task(asyncio.sleep(0))
    result = await IndependentNormativeReview(Corpus()).execute(
        document_id="job",
        section_id=SECTION,
        document_ids=(DOC,),
        pages=(page(),),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert neighbouring_job.done(), (
        "Полный корпус не должен блокировать соседнее async-задание."
    )
    assert len(result.diagnostics["checks"]) == 6000
    assert result.diagnostics["coverage"]["corpus_requirements"] == 6000
    assert {check["source_ids"][0] for check in result.diagnostics["checks"]} == {
        row["requirement_id"] for row in rows
    }
    assert result.findings == () and result.diagnostics["new_vlm_calls"] == 0
    assert time.perf_counter() - started < 30


@pytest.mark.parametrize(
    "case", ["required_parameter", "different_object_class", "table_without_operator"]
)
async def test_compound_requirement_does_not_reinterpret_parameter_role_or_object(case):
    """Требуемый расход не заменяет принятый, упоминание гидранта не меняет класс системы."""
    row = requirement()
    row.update(
        verification_type="conditional",
        extraction_status="needs_review",
        limitations=["structured_interpretation_required"],
        source_text="4.1 Для пожарных гидрантов при объеме не более 1000 м³, расход не менее 5 л/с.",
    )
    tag = "В2" if case == "different_object_class" else "ПГ7"
    prefix = (
        "Система В2, подключён пожарный гидрант ПГ7."
        if case == "different_object_class"
        else "Пожарный гидрант ПГ7."
    )
    property_name = "Требуемый расход" if case == "required_parameter" else "Расход"
    if case == "table_without_operator":
        row.update(
            verification_type="table",
            source_text="4.1 Для пожарных гидрантов:\nОбъем, м³ | Расход, л/с\nдо 1000 | 5",
        )
    row["text"] = row["source_text"]
    facts = tuple(
        document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "system",
                "identifier": tag,
                "property_type": "parameter",
                "property_name": prop,
                "value_raw": value,
                "unit_raw": unit,
                "evidence_text": f"{prefix} {prop}: {value} {unit}.",
                "extraction_method": "pdf_text",
            }
        )
        for prop, value, unit in [("Объем", "500", "м³"), (property_name, "4", "л/с")]
    )
    source_page = DocumentPage(13, "\n".join(f.evidence_text for f in facts), "", facts)
    result = await IndependentNormativeReview(Reader(row)).execute(
        document_id="job",
        section_id=SECTION,
        document_ids=(DOC,),
        pages=(source_page,),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert not result.findings
    assert result.diagnostics["checks"]


async def test_programming_value_error_in_cancellation_is_not_masked_as_missing_source():
    """Порт обрабатывает ошибки HTTP/JSON, но не скрывает ошибки вызываемого кода."""

    async def broken_cancellation():
        """Имитирует программную ошибку вне сетевого контракта."""
        raise ValueError("cancellation_implementation_bug")

    reader = HttpNormativeRequirementsReader(
        "http://knowledge.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )
    with pytest.raises(ValueError, match="cancellation_implementation_bug"):
        await reader.load(
            section_id=SECTION,
            document_id=DOC,
            generation_id=None,
            assessed_on=DAY,
            check_cancellation=broken_cancellation,
        )


@pytest.mark.parametrize(
    "condition,property_name",
    [("категории", "Категория здания"), ("класса", "Класс здания")],
)
async def test_classification_condition_uses_same_building_and_correct_property(
    condition, property_name
):
    """Класс и категория — разные параметры; буквальное условие проверяется для того же здания."""
    row = requirement()
    row.update(
        object_type="здания",
        verification_type="classification",
        extraction_status="needs_review",
        limitations=["structured_interpretation_required"],
        source_text=f"4.1 Для зданий при здание {condition} А, расход не менее 5 л/с.",
    )
    row["text"] = row["source_text"]
    facts = tuple(
        document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "building",
                "identifier": "З7",
                "property_type": "parameter",
                "property_name": prop,
                "value_raw": value,
                "unit_raw": unit,
                "evidence_text": f"Здание З7. {prop}: {value} {unit}.",
                "extraction_method": "pdf_text",
            }
        )
        for prop, value, unit in [(property_name, "А", ""), ("Расход", "4", "л/с")]
    )
    source_page = DocumentPage(13, "\n".join(f.evidence_text for f in facts), "", facts)
    result = await IndependentNormativeReview(Reader(row)).execute(
        document_id="job",
        section_id=SECTION,
        document_ids=(DOC,),
        pages=(source_page,),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert len(result.findings) == 1 and verified_programmatic_check(result.findings[0])
