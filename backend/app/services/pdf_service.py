from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError


def extract_pages(file_path: Path) -> list[dict]:
    try:
        with file_path.open("rb") as file:
            reader = PdfReader(file)

            if reader.is_encrypted:
                raise ValueError("Password-protected PDFs are not supported yet.")

            pages = []

            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""

                pages.append({
                    "page": page_number,
                    "text": text.strip()
                })

            return pages

    except PyPdfError as error:
        raise ValueError("PDF could not be processed.") from error