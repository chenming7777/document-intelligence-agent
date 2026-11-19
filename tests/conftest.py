from pathlib import Path

import pytest

from docintel.config import Settings
from docintel.extractors import DemoExtractionTool
from docintel.reader import TextExtractionTool

SAMPLES = Path(__file__).resolve().parents[1] / "src" / "docintel" / "samples"


@pytest.fixture
def settings():
    return Settings(_env_file=None, provider="demo", gemini_api_key="")


@pytest.fixture
def invoice_bytes():
    return (SAMPLES / "invoice.pdf").read_bytes()


@pytest.fixture
def source(settings, invoice_bytes):
    return TextExtractionTool(settings).run(invoice_bytes, "invoice.pdf")


@pytest.fixture
def document(source):
    return DemoExtractionTool().run(source, [])
