# services/api-gateway/src/pdrd_api_gateway/domain/analysis_history.py

"""Компактные метаданные проверки без исходных файлов, изображений и секретов."""

from dataclasses import dataclass
from typing import Any


def _count(value: object) -> int | None:
    """Принимает только неотрицательное целое число, включая ноль."""
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


@dataclass(frozen=True, slots=True)
class AnalysisHistoryMetadata:
    """Сводка артефактов; отсутствие результата не выдаётся за ноль замечаний."""

    file_name: str = "Документ"
    source_mode: str | None = None
    pages_count: int | None = None
    findings_count: int | None = None
    pdf_available: bool = False
    result_available: bool = False

    @classmethod
    def from_artifacts(
        cls,
        manifest: dict[str, Any],
        result: dict[str, Any] | None,
        *,
        pdf_available: bool,
    ) -> "AnalysisHistoryMetadata":
        """Использует сохранённый результат, сохраняя названия обоих файлов PDF + CAD."""
        result = result or {}
        names = [manifest.get("pdf_file_name"), manifest.get("cad_file_name")]
        names = [value for value in names if isinstance(value, str) and value.strip()]
        name = " + ".join(names) or result.get("file_name") or "Документ"
        pages = _count(result.get("analyzed_pages"))
        if pages is None and isinstance(result.get("selected_pages"), list):
            pages = len(result["selected_pages"])
        return cls(
            file_name=name,
            source_mode=manifest.get("source_mode") or result.get("source_mode"),
            pages_count=pages,
            findings_count=_count(result.get("findings_count")),
            pdf_available=pdf_available,
            result_available=bool(result),
        )
