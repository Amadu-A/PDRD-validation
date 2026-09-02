# services/document-service/src/pdrd_document_service/infrastructure/cad/processor.py

"""Orchestration CAD infrastructure adapters."""

import json
import tempfile
from pathlib import Path

from pdrd_document_service.domain.cad import (
    CadCapabilities,
    CadDocument,
    CadFormat,
)
from pdrd_document_service.infrastructure.cad.converter import (
    LibreDwgNormalizer,
)
from pdrd_document_service.infrastructure.cad.parser import (
    EzdxfCadParser,
)
from pdrd_document_service.infrastructure.cad.renderer import (
    EzdxfCadRenderer,
)


class EzdxfCadProcessor:
    """Нормализует, анализирует и рендерит CAD."""

    def __init__(
        self,
        *,
        normalizer: LibreDwgNormalizer,
        parser: EzdxfCadParser,
        renderer: EzdxfCadRenderer,
        machine_text_limit: int,
    ) -> None:
        """Сохраняет специализированные CAD adapters."""
        self._normalizer = normalizer
        self._parser = parser
        self._renderer = renderer

        self._machine_text_limit = machine_text_limit

    def capabilities(self) -> CadCapabilities:
        """Возвращает доступность CAD formats."""
        return self._normalizer.capabilities()

    def process(
        self,
        content: bytes,
        *,
        filename: str,
    ) -> CadDocument:
        """Выполняет полный preparation pipeline одного CAD-листа."""
        with tempfile.TemporaryDirectory(
            prefix="pdrd-cad-",
        ) as temporary_directory:
            workdir = Path(
                temporary_directory,
            )

            normalized = self._normalizer.normalize(
                content,
                filename=filename,
                workdir=workdir,
            )

            parsed = self._parser.parse(
                normalized.dxf_path,
            )

            rendered_png = self._renderer.render(
                document=parsed.document,
                selected_layout=(parsed.selected_layout),
            )

        machine_context = json.dumps(
            parsed.machine_data,
            ensure_ascii=False,
            separators=(
                ",",
                ":",
            ),
        )[: self._machine_text_limit]

        warnings = (
            *normalized.warnings,
            *parsed.warnings,
        )

        return CadDocument(
            original_file_name=filename,
            original_format=(normalized.original_format),
            normalized_format=CadFormat.DXF,
            converted_from_dwg=(normalized.converted_from_dwg),
            selected_layout=str(parsed.machine_data["selected_layout"]),
            warnings=warnings,
            machine_data=parsed.machine_data,
            machine_context=machine_context,
            rendered_png=rendered_png,
        )
