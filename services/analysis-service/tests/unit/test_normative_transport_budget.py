# services/analysis-service/tests/unit/test_normative_transport_budget.py

"""Размер настоящего N-first: полный корпус без выделения строки предельного размера."""

import json
import tracemalloc
from hashlib import sha256
from uuid import UUID

import pytest
from pdrd_analysis_service.application.independent_normative import (
    IndependentNormativeReview,
)
from pdrd_analysis_service.application.ports.normative_requirements import (
    AtomicNormativeDocument,
)
from pdrd_analysis_service.application.use_cases.document_context import DocumentPage

from .test_independent_normative import DAY, SECTION, no_cancel, requirement


class RecordingProtocolWriter:
    """Подмена только внешнего порта для прежних HTTP-тестов, без фиктивной инженерной логики."""

    def __init__(self):
        """Сохраняет полные архивы для проверки публикации."""
        self.archives = []

    async def save(self, *, archive, check_cancellation):
        """Подтверждает запись после контрольной точки отмены."""
        from hashlib import sha256

        from pdrd_analysis_service.domain.normative_protocol import canonical_json

        await check_cancellation()
        self.archives.append(archive)
        return {
            "protocol_id": sha256(canonical_json(archive)).hexdigest(),
            "document_id": archive["document_id"],
            "counts": archive["counts"],
            "state": "ready",
        }


def encoded_size(value):
    """Измеряет реальные UTF-8 байты потокового JSON, не создавая огромную строку."""
    return sum(
        len(part.encode("utf-8"))
        for part in json.JSONEncoder(ensure_ascii=False).iterencode(value)
    )


class CorpusReader:
    """11 фиктивных документов с полными DTO; ничего не генерирует за инженерный алгоритм."""

    def __init__(self, count=10412):
        """Сохраняет повторяемые цитаты/условия как в настоящем атомарном ответе Knowledge."""
        self.count = count

    async def load(self, **kwargs):
        """Каждый атом имеет отдельные ID, документ и неизменяемое поколение."""
        index = int(UUID(kwargs["document_id"])) - 700
        count = self.count // 11 + (index < self.count % 11)
        base = requirement()
        text = "Учебное сложное условие применения нормативного требования. " * 12
        base.update(
            text=text,
            source_text="4.1 " + text,
            object_type="Общий объект",
            document_id=kwargs["document_id"],
            source_sha256=sha256(f"synthetic-{index}".encode()).hexdigest(),
            applicability_conditions=[text],
            exceptions=[text],
            normative_dependencies=["СП-учебный: пункт 2"],
            source_fragments=[{"page": 5, "text": text, "start": 0, "end": len(text)}],
        )
        base["status_evidence"][0]["source_quote"] = text
        base["status_evidence"][0].update(
            document_id=kwargs["document_id"], source_sha256=base["source_sha256"]
        )
        rows = tuple(
            {
                **base,
                "requirement_id": f"N-{index}-{n}",
                "generation_id": str(UUID(int=800 + index)),
            }
            for n in range(count)
        )
        return AtomicNormativeDocument(
            rows,
            {
                "generation_id": str(UUID(int=800 + index)),
                "count": count,
                "source_sha256": base["source_sha256"],
                "extractor_version": "synthetic-contract-v1",
                "manifest": {"coverage": "complete"},
            },
        )


async def corpus_result(count=10412, pages=31):
    """Выполняет production-код independent N-first без GPU и embedding."""
    return await IndependentNormativeReview(CorpusReader(count)).execute(
        document_id=str(UUID(int=900)),
        section_id=SECTION,
        document_ids=tuple(str(UUID(int=700 + n)) for n in range(11)),
        pages=tuple(
            DocumentPage(n, f"Учебный лист {n}", "", ()) for n in range(1, pages + 1)
        ),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )


@pytest.mark.parametrize("pages", [1, 31, 50])
async def test_full_corpus_http_metrics_budget(pages, record_property):
    """RED: тяжёлый протокол не должен становиться HTTP metrics даже на одном листе."""
    result = await corpus_result(pages=pages)
    diagnostics = result.diagnostics
    metrics = {"independent_normative": {"requirement_plan": diagnostics}}
    measured = {key: encoded_size(diagnostics[key]) for key in ("checks", "selection")}
    measured["metrics"] = encoded_size(metrics)
    print(
        json.dumps(
            {
                "pages": pages,
                "records": len(diagnostics["checks"]),
                "bytes": measured,
                "MiB": {k: round(v / 1024**2, 3) for k, v in measured.items()},
            }
        )
    )
    record_property("serialization_bytes", measured)
    assert len(diagnostics["selection"]) == 10412
    assert len(diagnostics["checks"]) == 10412
    # После исправления stage использует отдельную компактную проекцию.
    summary = getattr(result, "summary", diagnostics)
    assert (
        encoded_size({"independent_normative": {"requirement_plan": summary}})
        <= 8 * 1024**2
    )


async def test_larger_corpus_keeps_every_source_and_selection():
    """Корпус больше инцидентного не ограничивается top-K и не растит транспортную проекцию."""
    result = await corpus_result(count=12000, pages=50)
    assert len(result.sources) == len(result.diagnostics["selection"]) == 12000
    assert len(result.diagnostics["checks"]) == 12000
    assert len({row["source_sha256"] for row in result.sources}) == 11
    assert encoded_size(result.summary) < 8 * 1024**2


async def test_one_atomic_requirement_can_create_several_checks():
    """Количество проверок не принимается равным числу выбранных атомов."""
    from .test_independent_normative import DOC, Reader, page

    row = requirement()
    row["text"] = row["source_text"] = (
        "4.1 Для пожарных гидрантов расход не менее 5 л/с; "
        "Для пожарных гидрантов напор не менее 10 м."
    )
    result = await IndependentNormativeReview(Reader(row)).execute(
        document_id="test",
        section_id=SECTION,
        document_ids=(DOC,),
        pages=(page(),),
        assessed_on=DAY,
        check_cancellation=no_cancel,
    )
    assert result.summary["coverage"]["selected_requirements"] == 1
    assert result.summary["check_count"] == 2
    assert len(result.diagnostics["checks"]) == 2


async def test_sample_python_allocation_measurement(tmp_path):
    """Измеряет только Python allocations на 128 атомах; не выдаёт их за RSS полного сервиса."""
    tracemalloc.start()
    try:
        result = await corpus_result(count=128, pages=31)
        legacy = encoded_size({"requirement_plan": result.diagnostics})
        compact = encoded_size(result.summary)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    measured = {
        "sample_atoms": 128,
        "legacy_json_bytes": legacy,
        "compact_json_bytes": compact,
        "python_peak_bytes": peak,
        "scope": "tracemalloc_sample_not_process_rss",
    }
    (tmp_path / "allocation-measurements.json").write_text(
        json.dumps(measured), encoding="utf-8"
    )
    assert peak > 0 and legacy > compact
