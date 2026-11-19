from decimal import Decimal
from unittest.mock import Mock

from docintel.agent import DocumentAgent
from docintel.errors import DocumentError
from tests.conftest import SAMPLES


def provider(documents):
    extractor = Mock(provider="gemini", model="test-model")
    extractor.run.side_effect = documents
    return extractor


def test_end_to_end_invoice(settings, invoice_bytes):
    report = DocumentAgent(settings).run(invoice_bytes, "../../invoice.pdf")
    assert report.filename == "invoice.pdf"
    assert report.validation.status == "passed"
    assert report.document.total_amount == Decimal("3250.00")
    assert "3250.00" in report.summary
    assert len(report.sha256) == 64
    assert [event.tool for event in report.trace] == [
        "extract_text",
        "extract_structured",
        "validate",
        "summarize",
    ]


def test_bad_total_is_preserved_and_not_retried(settings):
    content = (SAMPLES / "inconsistent.pdf").read_bytes()
    report = DocumentAgent(settings).run(content, "a.pdf")
    assert report.document.total_amount == Decimal("3255.00")
    assert report.validation.status == "needs_review"
    assert "total_mismatch" in {issue.code for issue in report.validation.issues}
    assert "Review required" in report.summary


def test_one_recheck_can_improve_missing_field(settings, invoice_bytes, document):
    broken = document.model_copy(update={"vendor": None})
    extractor = provider([broken, document])
    report = DocumentAgent(settings, extractor).run(invoice_bytes, "a.pdf")
    assert extractor.run.call_count == 2
    assert report.document.vendor == document.vendor
    assert report.validation.status == "passed"
    assert extractor.run.call_args_list[1].args[1][0].code == "missing_field"


def test_recheck_is_bounded_and_original_survives_regression(settings, invoice_bytes, document):
    broken = document.model_copy(update={"vendor": None})
    worse = broken.model_copy(update={"currency": None})
    extractor = provider([broken, worse])
    report = DocumentAgent(settings, extractor).run(invoice_bytes, "a.pdf")
    assert extractor.run.call_count == 2
    assert report.document.currency == "MYR"
    assert report.validation.status == "needs_review"


def test_business_error_does_not_trigger_recheck(settings, invoice_bytes, document):
    document.total_amount = Decimal("4000")
    extractor = provider([document])
    report = DocumentAgent(settings, extractor).run(invoice_bytes, "a.pdf")
    assert extractor.run.call_count == 1
    assert report.document.total_amount == Decimal("4000")


def test_failed_recheck_retains_usable_report(settings, invoice_bytes, document):
    document.vendor = None
    extractor = provider([document, DocumentError("llm_timeout", "test", 504)])
    report = DocumentAgent(settings, extractor).run(invoice_bytes, "a.pdf")
    assert report.validation.status == "needs_review"
    assert any("recheck failed" in notice for notice in report.warnings)
    assert report.trace[3].outcome == "llm_timeout"


def test_logs_do_not_include_source_values(settings, invoice_bytes, caplog):
    with caplog.at_level("INFO"):
        DocumentAgent(settings).run(invoice_bytes, "private-vendor-invoice.pdf")
    assert "tool_run" in caplog.text
    assert "ABC Supplies" not in caplog.text
    assert "private-vendor" not in caplog.text
    assert "3250" not in caplog.text
