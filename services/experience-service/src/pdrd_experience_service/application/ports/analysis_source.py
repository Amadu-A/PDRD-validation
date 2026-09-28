# services/experience-service/src/pdrd_experience_service/application/ports/analysis_source.py

"""Контракт получения исходных данных завершённого PDF-анализа.

Назначение файла:
- описать данные, необходимые для первоначального открытия Human Review;
- отделить Experience Service от реализации API Gateway;
- потребовать исходный PDF, сохранённый результат и визуализацию
  одного и того же серверного задания;
- исключить зависимость application layer от HTTP и файлового хранилища.

Реализацию этого контракта позднее предоставит защищённый
серверный адаптер API Gateway. Данные из тела браузерного запроса
не должны использоваться для создания данного объекта.
"""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


class AnalysisSourceUnavailableError(RuntimeError):
    """Доверенный серверный источник недоступен или нарушил свой контракт."""


@dataclass(frozen=True, slots=True)
class CompletedAnalysisArtifacts:
    """Набор серверных артефактов одного задания анализа PDF.

    Поле source_sha256 должно быть получено из серверного
    хранилища и сверяется с фактическими байтами исходного PDF.

    Статус относится к заданию API Gateway, а не к отдельному
    вызову визуализации. Визуализация может формироваться позже
    завершения самого анализа.
    """

    job_id: UUID
    document_id: UUID
    status: str
    source_filename: str
    source_sha256: str
    pdf_content: bytes
    result: dict[str, Any]
    visualization: dict[str, Any]


class AnalysisSourceReader(Protocol):
    """Получает артефакты исключительно из доверенного backend."""

    async def load_completed(
        self,
        job_id: UUID,
    ) -> CompletedAnalysisArtifacts:
        """Возвращает серверный набор либо сообщает об отсутствии задания.

        Реализация обязана:
        - проверить фактический статус задания;
        - получить оригинал из серверного хранилища;
        - загрузить результат и визуализацию того же задания;
        - не принимать идентичность задания от браузера как доказательство.
        """
        ...
