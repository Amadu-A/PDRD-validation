# services/knowledge-service/src/pdrd_knowledge_service/application/normative_preview.py

"""Связь Word-оригинала и сохранённого PDF без повторной векторизации."""

import hashlib
import json

from pdrd_knowledge_service.application.normative_document_formats import (
    preview_storage_key,
)
from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorageNotFoundError,
)


def preview_provenance_key(storage_key):
    """Метаданные лежат рядом с preview и удаляются вместе с каталогом документа."""
    return preview_storage_key(storage_key) + ".source.json"


async def save_preview(storage, document, content):
    """Атомарная замена файлов не оставляет окно между delete и save preview."""
    await storage.replace_derived(
        storage_key=preview_storage_key(document.storage_key), content=content
    )
    await storage.replace_derived(
        storage_key=preview_provenance_key(document.storage_key),
        content=json.dumps(
            {
                "source_sha256": document.sha256,
                "pdf_sha256": hashlib.sha256(content).hexdigest(),
            },
            sort_keys=True,
        ).encode("utf-8"),
    )


async def verified_preview(storage, converter, document, original):
    """Старый preview без доказанной связи пересоздаётся штатным LibreOffice converter."""
    try:
        provenance = json.loads(
            await storage.read(storage_key=preview_provenance_key(document.storage_key))
        )
        content = await storage.read(
            storage_key=preview_storage_key(document.storage_key)
        )
        if (
            provenance.get("source_sha256") == document.sha256
            and provenance.get("pdf_sha256") == hashlib.sha256(content).hexdigest()
        ):
            return content
    except (NormativeDocumentStorageNotFoundError, ValueError, UnicodeDecodeError):
        pass
    if converter is None:
        raise ValueError(
            "preview_source_unverified: для DOC/DOCX нужен штатный converter."
        )
    content = await converter.convert_to_pdf(
        content=original, original_name=document.original_name
    )
    await save_preview(storage, document, content)
    return content
