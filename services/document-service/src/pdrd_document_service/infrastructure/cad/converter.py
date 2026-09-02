# services/document-service/src/pdrd_document_service/infrastructure/cad/converter.py

"""Нормализация DWG/DXF в DXF."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pdrd_document_service.application.ports.cad import (
    CadProcessingError,
    DwgConverterUnavailableError,
)
from pdrd_document_service.domain.cad import (
    CadCapabilities,
    CadFormat,
    detect_cad_format,
)


@dataclass(frozen=True, slots=True)
class NormalizedCadSource:
    """Нормализованный CAD source."""

    dxf_path: Path

    original_format: CadFormat
    converted_from_dwg: bool

    warnings: tuple[str, ...]


class LibreDwgNormalizer:
    """Нормализует DWG/DXF через LibreDWG."""

    def __init__(
        self,
        *,
        converter_command: str,
        converter_timeout_seconds: int,
    ) -> None:
        """Сохраняет параметры LibreDWG."""
        self._converter_command = converter_command
        self._converter_timeout_seconds = converter_timeout_seconds

    def capabilities(self) -> CadCapabilities:
        """Возвращает доступность DXF и DWG."""
        converter = shutil.which(
            self._converter_command,
        )

        return CadCapabilities(
            dxf=True,
            dwg=converter is not None,
            dwg_converter=converter,
            dwg_converter_command=(self._converter_command),
        )

    def normalize(
        self,
        content: bytes,
        *,
        filename: str,
        workdir: Path,
    ) -> NormalizedCadSource:
        """Сохраняет DXF либо преобразует DWG в DXF."""
        cad_format = detect_cad_format(
            filename,
        )

        if cad_format is CadFormat.DXF:
            dxf_path = workdir / "input.dxf"

            dxf_path.write_bytes(
                content,
            )

            return NormalizedCadSource(
                dxf_path=dxf_path,
                original_format=cad_format,
                converted_from_dwg=False,
                warnings=(),
            )

        return self._convert_dwg(
            content,
            workdir=workdir,
        )

    def _convert_dwg(
        self,
        content: bytes,
        *,
        workdir: Path,
    ) -> NormalizedCadSource:
        converter = shutil.which(
            self._converter_command,
        )

        if converter is None:
            raise DwgConverterUnavailableError(
                "DWG-конвертер недоступен. "
                f"Ожидалась команда {self._converter_command}.",
            )

        dwg_path = workdir / "input.dwg"

        dwg_path.write_bytes(
            content,
        )

        try:
            completed = subprocess.run(
                [
                    converter,
                    "--overwrite",
                    dwg_path.name,
                ],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=(self._converter_timeout_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise CadProcessingError(
                "Конвертация DWG → DXF превысила "
                f"{self._converter_timeout_seconds} секунд.",
            ) from error

        if completed.returncode != 0:
            raise CadProcessingError(
                "Не удалось преобразовать DWG в DXF. "
                f"exit_code={completed.returncode}; "
                f"stdout={completed.stdout[-1000:]}; "
                f"stderr={completed.stderr[-1000:]}",
            )

        expected_path = workdir / "input.dxf"

        if expected_path.is_file():
            dxf_path = expected_path
        else:
            candidates = sorted(
                workdir.glob(
                    "*.dxf",
                )
            )

            if not candidates:
                raise CadProcessingError(
                    "DWG-конвертер завершился без ошибки, но DXF-файл не был создан.",
                )

            dxf_path = candidates[0]

        return NormalizedCadSource(
            dxf_path=dxf_path,
            original_format=CadFormat.DWG,
            converted_from_dwg=True,
            warnings=("Исходный DWG автоматически преобразован в DXF перед анализом.",),
        )
