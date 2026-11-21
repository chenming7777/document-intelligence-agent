"""Bounded local text extraction; scanned pages take an OCR branch."""

import io
import math
import threading
import warnings
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader

from docintel.config import Settings
from docintel.errors import DocumentError
from docintel.models import TextPage, TextResult

# PDFium is not thread safe. Only PDF rendering touches this lock; OCR runs outside it.
_PDFIUM_LOCK = threading.Lock()
_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


def validate_upload(content: bytes, filename: str, settings: Settings) -> str:
    """Cheap checks before accepting an upload; full decoding happens in the worker."""
    if not content:
        raise DocumentError("empty_file", "The uploaded file is empty.")
    if len(content) > settings.max_upload_bytes:
        raise DocumentError("file_too_large", "The document exceeds the upload limit.", 413)
    extension = Path(filename).suffix.lower()
    signatures = {
        ".pdf": b"%PDF-",
        ".png": b"\x89PNG\r\n\x1a\n",
        ".jpg": b"\xff\xd8",
        ".jpeg": b"\xff\xd8",
    }
    if extension not in _EXTENSIONS:
        raise DocumentError("unsupported_file", "Upload a PDF, PNG, or JPEG document.", 415)
    if not content.startswith(signatures[extension]):
        raise DocumentError("invalid_file", "The file content does not match its extension.")
    return extension


class TextExtractionTool:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

    def run(self, content: bytes, filename: str) -> TextResult:
        if not content:
            raise DocumentError("empty_file", "The uploaded file is empty.")
        if len(content) > self.settings.max_upload_bytes:
            raise DocumentError("file_too_large", "The document exceeds the upload limit.", 413)
        extension = Path(filename).suffix.lower()
        if extension not in _EXTENSIONS:
            raise DocumentError("unsupported_file", "Upload a PDF, PNG, or JPEG document.", 415)
        if extension == ".pdf":
            if not content.startswith(b"%PDF-"):
                raise DocumentError("invalid_file", "The file is not a valid PDF.")
            result = self._pdf(content)
        else:
            result = self._image(content, extension)
        if not any(p.text.strip() for p in result.pages):
            raise DocumentError("no_text", "No readable text was found. Try a clearer scan.")
        if sum(len(p.text) for p in result.pages) > self.settings.max_text_chars:
            raise DocumentError("too_much_text", "The document exceeds the text limit.", 413)
        return result

    def _ocr(self, image: Image.Image) -> str:
        try:
            return pytesseract.image_to_string(
                image,
                lang=self.settings.ocr_language,
                config="--psm 3",
                timeout=self.settings.ocr_timeout_seconds,
            ).strip()
        except pytesseract.TesseractNotFoundError as exc:
            raise DocumentError(
                "ocr_unavailable", "Install Tesseract OCR or use the Docker image for scans.", 503
            ) from exc
        except pytesseract.TesseractError as exc:
            raise DocumentError(
                "ocr_failed", "OCR failed. Check the configured language pack."
            ) from exc
        except RuntimeError as exc:
            raise DocumentError(
                "ocr_timeout", "OCR timed out. Try fewer or smaller pages.", 504
            ) from exc

    def _image(self, content: bytes, extension: str) -> TextResult:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as source:
                    allowed = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}
                    if source.format != allowed[extension]:
                        raise DocumentError("invalid_file", "Image content and extension differ.")
                    if source.width * source.height > self.settings.max_image_pixels:
                        raise DocumentError(
                            "image_too_large", "Image resolution exceeds the limit."
                        )
                    if getattr(source, "n_frames", 1) > 1:
                        raise DocumentError("animated_image", "Upload a single-frame image.")
                    image = ImageOps.exif_transpose(source).convert("RGB")
                    try:
                        text = self._ocr(image)
                    finally:
                        image.close()
        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise DocumentError(
                "invalid_image", "The image is damaged or exceeds safe limits."
            ) from exc
        return TextResult(pages=[TextPage(page=1, method="ocr", text=text)])

    def _render_page(self, content: bytes, page_index: int) -> Image.Image:
        with _PDFIUM_LOCK, pdfium.PdfDocument(content) as pdf:
            page = pdf[page_index]
            try:
                width, height = page.get_size()
                if width <= 0 or height <= 0:
                    raise DocumentError("invalid_pdf", "The PDF has an invalid page size.")
                scale = min(3.0, math.sqrt(self.settings.max_image_pixels / (width * height)))
                bitmap = page.render(scale=scale)
                try:
                    return bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
            finally:
                page.close()

    def _pdf(self, content: bytes) -> TextResult:
        pages = []
        notices = []
        try:
            pdf = PdfReader(io.BytesIO(content))
            if pdf.is_encrypted:
                raise DocumentError("encrypted_pdf", "Upload a PDF without password protection.")
            if len(pdf.pages) > self.settings.max_pages:
                raise DocumentError("too_many_pages", "The PDF exceeds the page limit.", 413)
            characters = 0
            for index, page in enumerate(pdf.pages):
                text = (page.extract_text() or "").strip()
                method = "pdf_text"
                # Sparse text layers are common on scans. OCR the complete page in that case.
                if sum(c.isalnum() for c in text) < 40:
                    image = self._render_page(content, index)
                    try:
                        text = self._ocr(image)
                    finally:
                        image.close()
                    method = "ocr"
                if not text:
                    notices.append(f"Page {index + 1} has no readable text.")
                characters += len(text)
                if characters > self.settings.max_text_chars:
                    raise DocumentError("too_much_text", "The PDF exceeds the text limit.", 413)
                pages.append(TextPage(page=index + 1, method=method, text=text))
        except DocumentError:
            raise
        except Exception as exc:
            raise DocumentError("invalid_pdf", "The PDF is damaged or could not be read.") from exc
        return TextResult(pages=pages, warnings=notices)
