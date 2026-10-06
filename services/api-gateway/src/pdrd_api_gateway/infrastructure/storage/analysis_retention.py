# services/api-gateway/src/pdrd_api_gateway/infrastructure/storage/analysis_retention.py

"""Удаляет только известные артефакты в выделенном томе анализа."""

import asyncio
import shutil
from pathlib import Path
from uuid import UUID


class LocalAnalysisRetentionArtifacts:
    """Не обращается к томам Experience, нормативов и пользовательских пакетов."""

    def __init__(self, *, root_path: Path) -> None:
        """Фиксирует абсолютный корень, который запрещено покидать при удалении."""
        self.root = root_path.resolve()

    def _checked(self, *parts: str) -> Path:
        """Отвергает ссылки и выход из корня, включая промежуточные каталоги."""
        path = self.root.joinpath(*parts)
        if self.root not in path.resolve().parents:
            raise ValueError("Путь очистки выходит из каталога анализов.")
        current = path
        while current != self.root:
            if current.is_symlink() or (
                hasattr(current, "is_junction") and current.is_junction()
            ):
                raise ValueError("Очистка не допускает символические ссылки.")
            current = current.parent
        return path

    @staticmethod
    def _remove_tree(path: Path) -> None:
        """Отсутствие допускается, ошибки доступа не скрываются."""
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            if path.exists():
                raise

    def _sources(self, document_id: UUID | None, job_id: UUID) -> None:
        """Сохраняет request/result/history JSON и остальные неизвестные файлы."""
        if document_id is not None:
            for name in ("pdf.bin", "cad.bin", "technical_assignment.bin"):
                self._checked(str(document_id), name).unlink(missing_ok=True)
            self._remove_tree(self._checked(str(document_id), "visualization"))
            directory = self._checked(str(document_id))
            for path in directory.glob("annotated-*.pdf"):
                self._checked(str(document_id), path.name).unlink(missing_ok=True)
        self._remove_tree(self._checked("reviewed-pdf", str(job_id)))

    async def remove_sources(self, *, document_id: UUID | None, job_id: UUID) -> None:
        """Выполняет ограниченное удаление вне цикла событий."""
        await asyncio.to_thread(self._sources, document_id, job_id)

    def _guest(self, document_id: UUID | None, job_id: UUID) -> None:
        """Удаляет каталог гостя и его кэш; ошибки сохраняют строку для повтора."""
        if document_id is not None:
            self._remove_tree(self._checked(str(document_id)))
        self._remove_tree(self._checked("reviewed-pdf", str(job_id)))

    async def remove_guest(self, *, document_id: UUID | None, job_id: UUID) -> None:
        """Выполняет полную очистку файлов гостя вне цикла событий."""
        await asyncio.to_thread(self._guest, document_id, job_id)
