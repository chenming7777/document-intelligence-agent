from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LineItem(StrictModel):
    description: str
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


class Evidence(StrictModel):
    field: str
    page: int
    quote: str


class FinancialDocument(StrictModel):
    # Nullable, required properties work with strict JSON Schema output. Unknown is not zero.
    document_type: Literal["invoice", "receipt", "unknown"]
    vendor: str | None
    invoice_number: str | None
    invoice_date: str | None
    due_date: str | None
    currency: str | None
    subtotal: Decimal | None
    tax_amount: Decimal | None
    discount_amount: Decimal | None
    total_amount: Decimal | None
    line_items: list[LineItem]
    evidence: list[Evidence]
    notes: list[str]


class TextPage(StrictModel):
    page: int
    method: Literal["pdf_text", "ocr"]
    text: str


class TextResult(StrictModel):
    pages: list[TextPage]
    warnings: list[str] = Field(default_factory=list)

    def prompt_text(self) -> str:
        return "\n\n".join(f"[PAGE {p.page}]\n{p.text}" for p in self.pages)


class ValidationIssue(StrictModel):
    code: str
    severity: Literal["error", "warning"]
    field: str
    message: str
    recheck: bool = False


class ValidationReport(StrictModel):
    status: Literal["passed", "needs_review"]
    issues: list[ValidationIssue]
    checks_run: list[str]


class TraceEvent(StrictModel):
    tool: str
    attempt: int
    duration_ms: int
    outcome: str


class DocumentReport(StrictModel):
    request_id: str
    filename: str
    sha256: str
    provider: str
    model: str | None
    document: FinancialDocument
    validation: ValidationReport
    summary: str
    pages: list[TextPage]
    warnings: list[str]
    trace: list[TraceEvent]
