# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/office/libreoffice.py

"""LibreOffice adapter нормализации DOC/DOCX в PDF."""

import asyncio
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from pdrd_knowledge_service.application.normative_document_formats import (
    DOC_EXTENSION,
    DOCX_EXTENSION,
)
from pdrd_knowledge_service.application.ports.office_conversion import (
    NormativeOfficeConversionError,
)

_PDF_SIGNATURE_WINDOW = 1024

_SUPPORTED_EXTENSIONS = frozenset(
    {
        DOC_EXTENSION,
        DOCX_EXTENSION,
    }
)


class LibreOfficeNormativeOfficeToPdfConverter:
    """Конвертирует Word в PDF через isolated LibreOffice process."""

    def __init__(
        self,
        *,
        executable: str = "soffice",
        timeout_seconds: float = 120.0,
    ) -> None:
        """Сохраняет runtime-настройки LibreOffice."""
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    async def convert_to_pdf(
        self,
        *,
        content: bytes,
        original_name: str,
    ) -> bytes:
        """Выполняет LibreOffice conversion вне asyncio event loop."""
        return await asyncio.to_thread(
            self._convert_sync,
            content,
            original_name,
        )

    def _convert_sync(
        self,
        content: bytes,
        original_name: str,
    ) -> bytes:
        """Синхронно конвертирует Word во временной директории."""
        if not content:
            raise NormativeOfficeConversionError(
                "Word-документ пуст.",
            )

        suffix = Path(
            original_name,
        ).suffix.lower()

        if suffix not in _SUPPORTED_EXTENSIONS:
            raise NormativeOfficeConversionError(
                "LibreOffice converter поддерживает только DOC и DOCX.",
            )

        with TemporaryDirectory(
            prefix="pdrd-normative-office-",
        ) as temporary_directory:
            root = Path(
                temporary_directory,
            )

            source_path = root / f"source{suffix}"

            output_directory = root / "output"

            profile_directory = root / "profile"

            home_directory = root / "home"

            output_directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            profile_directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            home_directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            try:
                source_path.write_bytes(
                    content,
                )

            except OSError as error:
                raise NormativeOfficeConversionError(
                    "Не удалось подготовить Word-документ для преобразования.",
                ) from error

            environment = os.environ.copy()

            environment["HOME"] = str(
                home_directory,
            )

            environment["TMPDIR"] = str(
                root,
            )

            command = [
                self._executable,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--nofirststartwizard",
                (f"-env:UserInstallation={profile_directory.as_uri()}"),
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(
                    output_directory,
                ),
                str(
                    source_path,
                ),
            ]

            try:
                process = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    env=environment,
                )

            except FileNotFoundError as error:
                raise NormativeOfficeConversionError(
                    "LibreOffice executable не найден.",
                ) from error

            except subprocess.TimeoutExpired as error:
                raise NormativeOfficeConversionError(
                    "Превышено время преобразования Word в PDF.",
                ) from error

            except OSError as error:
                raise NormativeOfficeConversionError(
                    "Не удалось запустить LibreOffice.",
                ) from error

            if process.returncode != 0:
                raise NormativeOfficeConversionError(
                    "LibreOffice завершил преобразование "
                    f"с кодом {process.returncode}.",
                )

            pdf_path = output_directory / "source.pdf"

            if not pdf_path.is_file():
                raise NormativeOfficeConversionError(
                    "LibreOffice не сформировал PDF-preview.",
                )

            try:
                pdf_content = pdf_path.read_bytes()

            except OSError as error:
                raise NormativeOfficeConversionError(
                    "Не удалось прочитать PDF-preview.",
                ) from error

        if b"%PDF-" not in pdf_content[:_PDF_SIGNATURE_WINDOW]:
            raise NormativeOfficeConversionError(
                "LibreOffice сформировал некорректный PDF.",
            )

        return pdf_content
