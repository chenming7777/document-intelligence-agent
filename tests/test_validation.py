from decimal import Decimal

import pytest
from pydantic import ValidationError

from docintel.models import Evidence, FinancialDocument
from docintel.validation import ValidationTool


def codes(document, source):
    return {i.code for i in ValidationTool().run(document, source).issues}


def test_valid_invoice(document, source):
    report = ValidationTool().run(document, source)
    assert report.status == "passed"
    assert report.issues == []


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("total_amount", Decimal("3255"), "total_mismatch"),
        ("subtotal", Decimal("2999"), "subtotal_mismatch"),
        ("vendor", None, "missing_field"),
        ("invoice_number", "", "missing_field"),
        ("currency", "ZZZ", "invalid_currency"),
        ("invoice_date", "2026-02-30", "invalid_date"),
        ("invoice_date", "01/02/2026", "invalid_date"),
        ("due_date", "2025-01-01", "date_order"),
        ("total_amount", Decimal("-1"), "negative_value"),
        ("total_amount", Decimal("1e99"), "amount_range"),
        ("tax_amount", None, "incomplete_totals"),
        ("line_items", [], "missing_line_items"),
        ("document_type", "unknown", "unsupported_document"),
    ],
)
def test_invalid_fields(document, source, field, value, expected):
    document = document.model_copy(update={field: value}, deep=True)
    assert expected in codes(document, source)


def test_line_arithmetic_and_incomplete_item(document, source):
    document.line_items[0].quantity = Decimal("3")
    assert "line_total_mismatch" in codes(document, source)
    document.line_items[0].quantity = None
    assert "incomplete_line" in codes(document, source)


def test_receipt_does_not_require_invoice_number_or_due_date(document, source):
    document.document_type = "receipt"
    document.invoice_number = None
    document.due_date = None
    assert ValidationTool().run(document, source).status == "passed"


def test_rounding_uses_currency_minor_unit(document, source):
    document.total_amount += Decimal("0.01")
    assert "total_mismatch" not in codes(document, source)
    document.total_amount += Decimal("0.001")
    assert "total_mismatch" in codes(document, source)
    document.currency = "KWD"
    document.total_amount = Decimal("3250.002")
    assert "total_mismatch" in codes(document, source)


def test_evidence_must_exist_on_cited_page(document, source):
    document.evidence = [Evidence(field="vendor", page=99, quote="fabricated vendor")]
    findings = codes(document, source)
    assert {"unverified_evidence", "missing_evidence"} <= findings


def test_nan_is_rejected_by_schema(document):
    values = document.model_dump()
    values["total_amount"] = "NaN"
    with pytest.raises(ValidationError):
        FinancialDocument.model_validate(values)


def test_decimal_serialization(document):
    assert document.model_dump(mode="json")["total_amount"] == "3250.00"


def test_source_warning_requires_review(document, source):
    source.warnings.append("Page 2 was blank")
    assert "source_warning" in codes(document, source)
