# services/experience-service/src/pdrd_experience_service/transport/http/catalog_views.py

"""HTTP-проекция каталога: полный текст, происхождение, состояния и метаданные crop."""

from pdrd_experience_service.application.catalog_snapshot import example_to_json
from pdrd_experience_service.domain.catalog import CatalogEntry
from pdrd_experience_service.domain.index_projection import index_projection


def entry_view(entry: CatalogEntry) -> dict:
    """Вычисленные теги не подменяют неизменяемый source и исходное решение."""
    example = entry.example
    result = example_to_json(example)
    result.update(
        job_id=str(example.source.job_id),
        document_id=str(example.source.document_id),
        source_filename=example.source.source_filename,
        page_number=example.source.page_number,
        original_text=example.source.original_text,
        origin=example.source.origin.value,
        tag=example.tag,
        decision=example.source.decision.value,
        learning_use=example.learning_use,
        active=entry.active,
        requested_active=example.active,
        source_current=entry.source_current,
        training_eligible=index_projection(entry) is not None,
    )
    return result
