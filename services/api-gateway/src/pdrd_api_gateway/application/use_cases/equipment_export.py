# services/api-gateway/src/pdrd_api_gateway/application/use_cases/equipment_export.py

"""Выгрузка закреплённого EQ и использованных документов с проверкой SHA-256."""

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from pdrd_api_gateway.application.ports.equipment_export import EquipmentExportDocuments
from pdrd_api_gateway.application.ports.review import ReviewRequestError


@dataclass(frozen=True, slots=True)
class ExportEquipmentBundle:
    """Не подменяет новый EQ исходным устаревшим PDF анализа."""

    documents: EquipmentExportDocuments
    max_bytes: int = 100_000_000

    async def execute(self, proposal: dict, *, bundle: bool) -> bytes:
        """Архив содержит только документы принятой версии и их действительные байты."""
        revision = int(proposal.get("revision") or 0)
        if revision < 1 or proposal.get("search", {}).get("status") in {
            "queued",
            "running",
        }:
            raise ReviewRequestError(
                409, "Дождитесь завершения версии EQ перед выгрузкой."
            )
        report = proposal.get("report") or {}
        rows = report.get("positions") or report.get("models") or []
        pdf = await self.documents.report_pdf(str(proposal["job_id"]), revision, rows)
        if not pdf.startswith(b"%PDF-"):
            raise ReviewRequestError(503, "Document Service не вернул PDF отчёта EQ.")
        if not bundle:
            return pdf
        stream = BytesIO()
        total = len(pdf)
        manifest = {
            "schema": "pdrd.eq-bundle.v1",
            "job_id": proposal["job_id"],
            "revision": revision,
            "report_sha256": sha256(pdf).hexdigest(),
            "documents": [],
            "positions": rows,
        }
        seen = set()
        with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
            archive.writestr("eq-report.pdf", pdf)
            for row in rows:
                document = row.get("document") or {}
                source = document.get("source_id")
                if not source or source in seen:
                    continue
                if not re.fullmatch(r"EQ-[A-Za-z0-9-]{1,76}", source):
                    raise ReviewRequestError(
                        503, "Некорректный идентификатор snapshot."
                    )
                seen.add(source)
                content, media_type = await self.documents.document(source)
                total += len(content)
                if total > self.max_bytes:
                    raise ReviewRequestError(
                        413, "Комплект EQ превышает допустимый размер выгрузки."
                    )
                if sha256(content).hexdigest() != document.get("sha256"):
                    raise ReviewRequestError(
                        503, "SHA-256 сохранённого документа EQ не совпадает."
                    )
                if media_type not in {"application/pdf", "text/html"}:
                    raise ReviewRequestError(503, "Недопустимый формат snapshot EQ.")
                path = f"documents/{source}.{'pdf' if media_type == 'application/pdf' else 'html'}"
                archive.writestr(path, content)
                manifest["documents"].append({**document, "file": path})
            archive.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
        return stream.getvalue()
