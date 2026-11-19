import io
import shutil

import pytesseract
import pytest
from PIL import Image
from pypdf import PdfWriter

from docintel.errors import DocumentError
from docintel.reader import TextExtractionTool
from tests.conftest import SAMPLES


def test_embedded_pdf_text(settings, invoice_bytes):
    result = TextExtractionTool(settings).run(invoice_bytes, "INVOICE.PDF")
    assert result.pages[0].method == "pdf_text"
    assert "INV-10234" in result.pages[0].text


@pytest.mark.parametrize(
    ("content", "filename", "code"),
    [
        (b"", "a.pdf", "empty_file"),
        (b"text", "a.txt", "unsupported_file"),
        (b"not pdf", "a.pdf", "invalid_file"),
        (b"%PDF-broken", "a.pdf", "invalid_pdf"),
        (b"not image", "a.png", "invalid_image"),
    ],
)
def test_bad_uploads(settings, content, filename, code):
    with pytest.raises(DocumentError) as exc:
        TextExtractionTool(settings).run(content, filename)
    assert exc.value.code == code


def test_file_limit(settings):
    settings.max_upload_mb = 1
    with pytest.raises(DocumentError, match="upload limit"):
        TextExtractionTool(settings).run(b"x" * (1024 * 1024 + 1), "a.pdf")


def test_pdf_page_limit(settings):
    settings.max_pages = 1
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.add_blank_page(200, 200)
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(DocumentError) as exc:
        TextExtractionTool(settings).run(stream.getvalue(), "a.pdf")
    assert exc.value.code == "too_many_pages"


def test_encrypted_pdf(settings):
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("secret")
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(DocumentError) as exc:
        TextExtractionTool(settings).run(stream.getvalue(), "a.pdf")
    assert exc.value.code == "encrypted_pdf"


def test_image_and_scanned_pdf_use_ocr(settings, monkeypatch):
    monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "Some OCR text")
    reader = TextExtractionTool(settings)
    for filename in ("invoice.png", "scanned-invoice.pdf"):
        result = reader.run((SAMPLES / filename).read_bytes(), filename)
        assert result.pages[0].method == "ocr"
        assert result.pages[0].text == "Some OCR text"


def test_mismatched_image_type(settings):
    with pytest.raises(DocumentError) as exc:
        TextExtractionTool(settings).run((SAMPLES / "invoice.png").read_bytes(), "a.jpg")
    assert exc.value.code == "invalid_file"


def test_image_resolution_limit(settings):
    settings.max_image_pixels = 1000
    with pytest.raises(DocumentError) as exc:
        TextExtractionTool(settings).run((SAMPLES / "invoice.png").read_bytes(), "a.png")
    assert exc.value.code == "image_too_large"


def test_missing_ocr_is_actionable(settings, monkeypatch):
    def missing(*args, **kwargs):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_string", missing)
    with pytest.raises(DocumentError) as exc:
        TextExtractionTool(settings).run((SAMPLES / "invoice.png").read_bytes(), "a.png")
    assert exc.value.code == "ocr_unavailable"
    assert exc.value.status_code == 503


def test_ocr_timeout(settings, monkeypatch):
    def timeout(*args, **kwargs):
        raise RuntimeError("timeout")

    monkeypatch.setattr(pytesseract, "image_to_string", timeout)
    with pytest.raises(DocumentError) as exc:
        TextExtractionTool(settings)._ocr(Image.new("RGB", (10, 10)))
    assert exc.value.code == "ocr_timeout"


def test_ocr_engine_error_is_not_a_timeout(settings, monkeypatch):
    def failed(*args, **kwargs):
        raise pytesseract.TesseractError(1, "Missing language pack")

    monkeypatch.setattr(pytesseract, "image_to_string", failed)
    with pytest.raises(DocumentError) as exc:
        TextExtractionTool(settings)._ocr(Image.new("RGB", (10, 10)))
    assert exc.value.code == "ocr_failed"


def test_no_text_and_text_limit(settings, monkeypatch, invoice_bytes):
    monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "")
    with pytest.raises(DocumentError) as exc:
        TextExtractionTool(settings).run((SAMPLES / "invoice.png").read_bytes(), "a.png")
    assert exc.value.code == "no_text"
    settings.max_text_chars = 100
    with pytest.raises(DocumentError) as exc:
        TextExtractionTool(settings).run(invoice_bytes, "a.pdf")
    assert exc.value.code == "too_much_text"


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract executable is not installed")
@pytest.mark.parametrize("filename", ["invoice.png", "scanned-invoice.pdf"])
def test_real_ocr(settings, filename):
    result = TextExtractionTool(settings).run((SAMPLES / filename).read_bytes(), filename)
    assert "INV-10234" in result.pages[0].text
    assert "3250.00" in result.pages[0].text
