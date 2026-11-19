"""Business rules are deterministic; the model never decides if a total balances."""

import re
from datetime import date
from decimal import Decimal, localcontext

import pycountry

from docintel.models import FinancialDocument, TextResult, ValidationIssue, ValidationReport

_ZERO_DIGIT = {
    "BIF",
    "CLP",
    "DJF",
    "GNF",
    "ISK",
    "JPY",
    "KMF",
    "KRW",
    "PYG",
    "RWF",
    "UGX",
    "UYI",
    "VND",
    "VUV",
    "XAF",
    "XOF",
    "XPF",
}
_THREE_DIGIT = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}
_FOUR_DIGIT = {"CLF", "UYW"}
_MAX_AMOUNT = Decimal("1000000000000")


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _tolerance(currency: str | None) -> Decimal:
    digits = 0 if currency in _ZERO_DIGIT else 3 if currency in _THREE_DIGIT else 2
    if currency in _FOUR_DIGIT:
        digits = 4
    return Decimal(1).scaleb(-digits)


class ValidationTool:
    def run(self, document: FinancialDocument, source: TextResult) -> ValidationReport:
        issues: list[ValidationIssue] = []
        checks = [
            "document_type",
            "required_fields",
            "currency",
            "dates",
            "amount_ranges",
            "line_arithmetic",
            "subtotal",
            "grand_total",
            "evidence_quotes",
        ]

        def issue(code, field, message, severity="error", recheck=False):
            issues.append(
                ValidationIssue(
                    code=code,
                    field=field,
                    message=message,
                    severity=severity,
                    recheck=recheck,
                )
            )

        if document.document_type == "unknown":
            issue("unsupported_document", "document_type", "No invoice or receipt was identified.")
        required = ["vendor", "invoice_date", "currency", "total_amount"]
        if document.document_type == "invoice":
            required.append("invoice_number")
        for field in required:
            value = getattr(document, field)
            if value is None or (isinstance(value, str) and not value.strip()):
                issue("missing_field", field, f"Required field {field} is missing.", recheck=True)

        if document.currency and not pycountry.currencies.get(alpha_3=document.currency):
            issue(
                "invalid_currency",
                "currency",
                "Currency must be a recognized ISO 4217 code.",
                recheck=True,
            )
        parsed_dates = {}
        for field in ("invoice_date", "due_date"):
            value = getattr(document, field)
            if value is not None:
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError
                    parsed_dates[field] = date.fromisoformat(value)
                except ValueError:
                    issue(
                        "invalid_date",
                        field,
                        f"{field} must be a real YYYY-MM-DD date.",
                        recheck=True,
                    )
        if (
            "invoice_date" in parsed_dates
            and "due_date" in parsed_dates
            and parsed_dates["due_date"] < parsed_dates["invoice_date"]
        ):
            issue("date_order", "due_date", "Due date precedes the document date.")

        amounts = [
            (f, getattr(document, f))
            for f in ("subtotal", "tax_amount", "discount_amount", "total_amount")
        ]
        for index, item in enumerate(document.line_items):
            for field in ("quantity", "unit_price", "amount"):
                amounts.append((f"line_items.{index}.{field}", getattr(item, field)))
        unsafe = False
        for field, value in amounts:
            if value is not None:
                if abs(value) > _MAX_AMOUNT or value.as_tuple().exponent < -8:
                    issue("amount_range", field, "Value exceeds supported magnitude or precision.")
                    unsafe = True
                if value < 0:
                    issue(
                        "negative_value", field, "Negative values require manual review.", "warning"
                    )

        tolerance = _tolerance(document.currency)
        if not unsafe:
            with localcontext() as context:
                context.prec = 40
                for index, item in enumerate(document.line_items):
                    if all(v is not None for v in (item.quantity, item.unit_price, item.amount)):
                        expected = item.quantity * item.unit_price
                        if abs(expected - item.amount) > tolerance:
                            issue(
                                "line_total_mismatch",
                                f"line_items.{index}.amount",
                                f"Quantity × unit price is {expected}; "
                                f"printed amount is {item.amount}.",
                            )
                    else:
                        issue(
                            "incomplete_line",
                            f"line_items.{index}",
                            "A line item cannot be fully reconciled.",
                            "warning",
                        )
                if not document.line_items:
                    issue(
                        "missing_line_items",
                        "line_items",
                        "No line items were extracted.",
                        "warning",
                        recheck=True,
                    )
                if (
                    document.line_items
                    and document.subtotal is not None
                    and all(item.amount is not None for item in document.line_items)
                ):
                    item_sum = sum((item.amount for item in document.line_items), Decimal(0))
                    if abs(item_sum - document.subtotal) > tolerance:
                        issue(
                            "subtotal_mismatch",
                            "subtotal",
                            f"Line amounts sum to {item_sum}; subtotal is {document.subtotal}.",
                        )
                adjustments = (
                    document.subtotal,
                    document.tax_amount,
                    document.discount_amount,
                    document.total_amount,
                )
                if all(value is not None for value in adjustments):
                    expected = document.subtotal + document.tax_amount - document.discount_amount
                    if abs(expected - document.total_amount) > tolerance:
                        issue(
                            "total_mismatch",
                            "total_amount",
                            f"Subtotal + tax − discount is {expected}; "
                            f"total is {document.total_amount}.",
                        )
                else:
                    issue(
                        "incomplete_totals",
                        "total_amount",
                        "Cannot reconcile total: subtotal, tax, discount, or total is missing. "
                        "Missing adjustments are not assumed to be zero.",
                        "warning",
                    )

        page_text = {page.page: _normalize(page.text) for page in source.pages}
        supported_fields = set()
        for evidence in document.evidence:
            quote = _normalize(evidence.quote)
            if not quote or quote not in page_text.get(evidence.page, ""):
                issue(
                    "unverified_evidence",
                    evidence.field,
                    "The evidence quote was not found on the cited page.",
                    "warning",
                    recheck=True,
                )
            else:
                supported_fields.add(evidence.field)
        for field in required:
            if getattr(document, field) is not None and field not in supported_fields:
                issue(
                    "missing_evidence",
                    field,
                    "No verified source quote accompanies this field.",
                    "warning",
                    recheck=True,
                )
        if source.warnings:
            issue("source_warning", "pages", "Some source pages need review.", "warning")
        return ValidationReport(
            status="needs_review" if issues else "passed", issues=issues, checks_run=checks
        )
