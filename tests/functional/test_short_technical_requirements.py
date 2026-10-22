# tests/functional/test_short_technical_requirements.py

"""Короткое предписание проходит атомизацию, штатный индекс и независимый T-first."""

from types import SimpleNamespace
from uuid import UUID

from pdrd_analysis_service.application.use_cases.common import zero_metrics
from pdrd_analysis_service.application.use_cases.technical_assignment_validation import (
    CheckPageAgainstTechnicalAssignment,
)
from pdrd_analysis_service.domain.analysis import GenerationResult, PageFacts
from pdrd_analysis_service.transport.http.technical_assignment_schemas import (
    TechnicalAssignmentRequirementPayload,
)
from pdrd_knowledge_service.application.use_cases.index_technical_assignment import (
    IndexTechnicalAssignment,
)
from pdrd_knowledge_service.domain.technical_assignment_indexing import (
    extract_technical_assignment_requirements,
)


class Embedding:
    """Подменяет внешний порт, не меняя входы индексации."""

    async def embed(self, inputs):
        """Возвращает векторы тестовой размерности без GPU."""
        return [[1.0, 0.0] for _ in inputs]


class Store:
    """Проверяет повторный upsert по штатным устойчивым point_id."""

    def __init__(self):
        """Хранит последнюю запись каждого ключа."""
        self.records = {}

    async def upsert(self, *, collection, records):
        """Повторная индексация заменяет запись вместо создания дубликата."""
        self.records.update({record.point_id: record for record in records})


class Vision:
    """Фиксирует передачу всех коротких требований в настоящий T-first use case."""

    async def generate_json(self, **request):
        """Ответ не объявляет отсутствие сведений нарушением."""
        self.prompt = request["prompt"]
        return GenerationResult(
            payload={
                "decisions": {
                    key: {"status": "not_applicable", "confidence": 0.8}
                    for key in ("T-R1", "T-R2")
                },
                "issues": [],
            },
            metrics=zero_metrics("test"),
        )


async def test_short_requirements_reindex_without_duplicates_and_reach_t_first():
    """Идентификаторы, цитаты и физическая страница сохраняются между сервисами."""
    requirements = extract_technical_assignment_requirements(
        page_number=7, text="Предусмотреть АВР. Установить УЗО."
    )
    assert len(requirements) == 2
    store = Store()
    index = IndexTechnicalAssignment(
        None, None, None, Embedding(), store, None, "test", 2, 20, 72, 10000, 1200
    )
    assignment = SimpleNamespace(
        technical_assignment_id=UUID(int=11),
        analysis_document_id=UUID(int=12),
        section_id=UUID(int=13),
        original_name="ТЗ.pdf",
        sha256="a" * 64,
    )
    for _ in range(2):
        assert (
            await index._index_requirements(
                assignment=assignment,
                page_point_id="page7",
                requirements=requirements,
                start_index=0,
            )
            == 2
        )
    assert len(store.records) == 2
    fields = TechnicalAssignmentRequirementPayload.model_fields
    indexed = tuple(
        TechnicalAssignmentRequirementPayload.model_validate(
            {
                "point_id": record.point_id,
                **{k: v for k, v in record.payload.items() if k in fields},
            }
        ).to_domain()
        for record in store.records.values()
    )
    assert [r.requirement_id for r in indexed] == ["T-R1", "T-R2"]
    assert [r.source_text for r in indexed] == ["Предусмотреть АВР.", "Установить УЗО."]
    assert all(r.page == 7 for r in indexed)
    model = Vision()
    _, decisions, findings, _ = await CheckPageAgainstTechnicalAssignment(
        model, 1000, 10, 1200
    ).execute(
        page_number=3,
        page_type="scheme",
        extracted_text="Схема щита ЩР1.",
        page_facts=PageFacts("ЭОМ", "scheme", "Щит", ("ЩР1",), (), (), ()),
        image_bytes=b"test",
        technical_assignment_id=str(assignment.technical_assignment_id),
        analysis_document_id=str(assignment.analysis_document_id),
        section_id=str(assignment.section_id),
        source_file="ТЗ.pdf",
        source_sha256="a" * 64,
        requirements=indexed,
    )
    assert [d.requirement_id for d in decisions] == ["T-R1", "T-R2"]
    assert "Предусмотреть АВР." in model.prompt and "Установить УЗО." in model.prompt
    assert findings == ()
