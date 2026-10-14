# services/document-service/src/pdrd_document_service/infrastructure/equipment_document.py

"""Детерминированное извлечение текста PDF и неактивного HTML."""

from contextlib import suppress
from html.parser import HTMLParser

import fitz

from pdrd_document_service.domain.equipment_document import (
    EquipmentDocumentPage,
    EquipmentDocumentText,
)


class _TextOnlyHtmlParser(HTMLParser):
    """Собирает видимый текст без выполнения или сохранения active content."""

    def __init__(self) -> None:
        """Инициализирует накопитель текста и глубину скрытых элементов."""
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Пропускает script, style, iframe и вложенное содержимое."""
        del attrs
        if tag in {"script", "style", "iframe", "noscript", "svg"}:
            self.hidden_depth += 1
        elif not self.hidden_depth and tag in {
            "p",
            "div",
            "br",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
            "section",
        }:
            self.parts.append("\n")
        elif not self.hidden_depth and tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        """Завершает игнорирование активного элемента."""
        if tag in {"script", "style", "iframe", "noscript", "svg"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)

    def handle_data(self, data: str) -> None:
        """Добавляет только текст видимой части документа."""
        if not self.hidden_depth and data.strip():
            self.parts.append(data.strip() + " ")


class PyMuPdfEquipmentDocumentReader:
    """Использует существующий PDF runtime Document Service для EQ."""

    def render_page(self, content: bytes, page_number: int, max_pages: int) -> bytes:
        """Рендерит только выбранную страницу PDF с лимитом пикселей."""
        try:
            document = fitz.open(stream=content, filetype="pdf")
        except Exception as error:
            raise ValueError("Не удалось открыть PDF производителя.") from error
        try:
            if page_number < 1 or page_number > min(len(document), max_pages):
                raise ValueError("Страница документации вне разрешённого диапазона.")
            page = document[page_number - 1]
            scale = min(
                2.0,
                (4_000_000 / max(page.rect.width * page.rect.height, 1)) ** 0.5,
            )
            image = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale), alpha=False
            ).tobytes("png")
            if len(image) > 20_000_000:
                raise ValueError("Растр страницы превышает лимит VLM.")
            return image
        finally:
            document.close()

    def _page_text(self, page: fitz.Page) -> str:
        """Сохраняет колонки геометрических PDF таблиц без визуальной модели."""
        content = page.get_text("text")[:20_000]
        tables: list[str] = []
        with suppress(Exception):
            for table in page.find_tables().tables[:8]:
                for row in table.extract()[:80]:
                    if 1 < len(row) <= 16:
                        cells = [
                            " ".join(str(cell or "").split())[:500] for cell in row
                        ]
                        tables.append(" | ".join(cells))
        return (content + "\n" + "\n".join(tables))[:20_000]

    def extract(
        self,
        content: bytes,
        media_type: str,
        max_pages: int,
    ) -> EquipmentDocumentText:
        """Возвращает страницы PDF либо очищенный текст технического HTML."""
        if media_type == "text/html":
            parser = _TextOnlyHtmlParser()
            parser.feed(content.decode("utf-8", errors="replace"))
            lines = (
                " ".join(line.split()) for line in "".join(parser.parts).splitlines()
            )
            text = "\n".join(line for line in lines if line)[:200_000]
            return EquipmentDocumentText(
                "text/html",
                1,
                (EquipmentDocumentPage(1, text),),
            )
        try:
            document = fitz.open(stream=content, filetype="pdf")
        except Exception as error:
            raise ValueError("Не удалось открыть PDF производителя.") from error
        try:
            total = len(document)
            pages = tuple(
                EquipmentDocumentPage(
                    index + 1,
                    self._page_text(document[index]),
                )
                for index in range(min(total, max_pages))
            )
            return EquipmentDocumentText("application/pdf", total, pages)
        finally:
            document.close()
