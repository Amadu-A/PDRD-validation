# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/office/libreoffice.py

"""LibreOffice adapter нормализации DOC/DOCX в PDF."""

import asyncio
import logging
import os
import signal
import subprocess
from contextlib import suppress
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
_PROCESS_TERMINATION_GRACE_SECONDS = 3.0
_DIAGNOSTIC_LIMIT = 800

logger = logging.getLogger(__name__)

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
        """Нормализует Word через ODT и затем экспортирует PDF."""
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

            try:
                source_path.write_bytes(
                    content,
                )

            except OSError as error:
                raise NormativeOfficeConversionError(
                    "Не удалось подготовить Word-документ для преобразования.",
                ) from error

            odt_output_directory = root / "odt-output"
            odt_profile_directory = root / "odt-profile"
            odt_home_directory = root / "odt-home"

            for directory in (
                odt_output_directory,
                odt_profile_directory,
                odt_home_directory,
            ):
                directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )

            odt_path = odt_output_directory / "source.odt"

            self._run_conversion(
                source_path=source_path,
                output_directory=odt_output_directory,
                profile_directory=odt_profile_directory,
                home_directory=odt_home_directory,
                convert_to="odt",
                expected_output_path=odt_path,
                stage="Word→ODT",
            )

            pdf_output_directory = root / "pdf-output"
            pdf_profile_directory = root / "pdf-profile"
            pdf_home_directory = root / "pdf-home"

            for directory in (
                pdf_output_directory,
                pdf_profile_directory,
                pdf_home_directory,
            ):
                directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )

            pdf_path = pdf_output_directory / "source.pdf"

            self._run_conversion(
                source_path=odt_path,
                output_directory=pdf_output_directory,
                profile_directory=pdf_profile_directory,
                home_directory=pdf_home_directory,
                convert_to="pdf:writer_pdf_Export",
                expected_output_path=pdf_path,
                stage="ODT→PDF",
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

    def _run_conversion(
        self,
        *,
        source_path: Path,
        output_directory: Path,
        profile_directory: Path,
        home_directory: Path,
        convert_to: str,
        expected_output_path: Path,
        stage: str,
    ) -> None:
        """Выполняет один isolated LibreOffice conversion stage."""
        environment = os.environ.copy()

        environment["HOME"] = str(
            home_directory,
        )

        environment["TMPDIR"] = str(
            output_directory.parent,
        )

        command = [
            self._executable,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile_directory.as_uri()}",
            "--convert-to",
            convert_to,
            "--outdir",
            str(
                output_directory,
            ),
            str(
                source_path,
            ),
        ]

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                start_new_session=(os.name == "posix"),
            )

        except FileNotFoundError as error:
            raise NormativeOfficeConversionError(
                "LibreOffice executable не найден.",
            ) from error

        except OSError as error:
            raise NormativeOfficeConversionError(
                f"Не удалось запустить LibreOffice на этапе {stage}.",
            ) from error

        try:
            stdout, stderr = process.communicate(
                timeout=self._timeout_seconds,
            )

        except subprocess.TimeoutExpired as error:
            self._terminate_process(
                process,
            )

            stdout, stderr = process.communicate()

            self._log_process_failure(
                event="timeout",
                stage=stage,
                stdout=stdout,
                stderr=stderr,
            )

            raise NormativeOfficeConversionError(
                f"Превышено время преобразования Word в PDF на этапе {stage}.",
            ) from error

        if process.returncode != 0:
            self._log_process_failure(
                event="nonzero_exit",
                stage=stage,
                stdout=stdout,
                stderr=stderr,
            )

            raise NormativeOfficeConversionError(
                "LibreOffice завершил преобразование "
                f"на этапе {stage} с кодом {process.returncode}.",
            )

        if not expected_output_path.is_file():
            self._log_process_failure(
                event="missing_output",
                stage=stage,
                stdout=stdout,
                stderr=stderr,
            )

            raise NormativeOfficeConversionError(
                f"LibreOffice не сформировал ожидаемый файл на этапе {stage}.",
            )

    @staticmethod
    def _terminate_process(
        process: subprocess.Popen[str],
    ) -> None:
        """Гарантированно завершает LibreOffice после timeout."""
        if process.poll() is not None:
            return

        if os.name == "posix":
            with suppress(
                ProcessLookupError,
            ):
                os.killpg(
                    process.pid,
                    signal.SIGTERM,
                )

        else:
            process.terminate()

        try:
            process.wait(
                timeout=_PROCESS_TERMINATION_GRACE_SECONDS,
            )
            return

        except subprocess.TimeoutExpired:
            pass

        if os.name == "posix":
            with suppress(
                ProcessLookupError,
            ):
                os.killpg(
                    process.pid,
                    signal.SIGKILL,
                )

        else:
            process.kill()

        try:
            process.wait(
                timeout=_PROCESS_TERMINATION_GRACE_SECONDS,
            )

        except subprocess.TimeoutExpired:
            return

    @staticmethod
    def _log_process_failure(
        *,
        event: str,
        stage: str,
        stdout: str,
        stderr: str,
    ) -> None:
        """Пишет bounded LibreOffice diagnostics без утечки в API response."""
        logger.warning(
            "libreoffice_conversion_%s stage=%s stdout=%r stderr=%r",
            event,
            stage,
            stdout.strip()[:_DIAGNOSTIC_LIMIT],
            stderr.strip()[:_DIAGNOSTIC_LIMIT],
        )
