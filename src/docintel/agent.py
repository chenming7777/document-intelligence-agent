import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import TypeVar

from docintel.config import Settings
from docintel.errors import DocumentError
from docintel.extractors import StructuredExtractionTool, make_extractor
from docintel.models import DocumentReport, FinancialDocument, TraceEvent, ValidationReport
from docintel.reader import TextExtractionTool
from docintel.validation import ValidationTool

logger = logging.getLogger(__name__)
T = TypeVar("T")


def summarize(document: FinancialDocument, validation: ValidationReport) -> str:
    vendor = document.vendor or "an unidentified vendor"
    amount = str(document.total_amount) if document.total_amount is not None else "unknown amount"
    currency = document.currency or "unspecified currency"
    identifier = f" {document.invoice_number}" if document.invoice_number else ""
    date_text = f", dated {document.invoice_date}" if document.invoice_date else ""
    summary = (
        f"{document.document_type.capitalize()}{identifier} from {vendor}{date_text}: "
        f"{currency} {amount}, with {len(document.line_items)} line item(s)."
    )
    if document.due_date:
        summary += f" Due {document.due_date}."
    if validation.status == "passed":
        summary += " All configured validation checks passed."
    else:
        errors = sum(i.severity == "error" for i in validation.issues)
        summary += (
            f" Review required: {errors} error(s) and {len(validation.issues) - errors} warning(s)."
        )
    return summary


class DocumentAgent:
    """An explicit, bounded workflow with tool calls and one conditional extraction recheck."""

    def __init__(
        self,
        settings: Settings,
        extractor: StructuredExtractionTool | None = None,
        reader: TextExtractionTool | None = None,
    ):
        self.settings = settings
        self.extractor = extractor or make_extractor(settings)
        self.reader = reader or TextExtractionTool(settings)
        self.validator = ValidationTool()

    def close(self) -> None:
        self.extractor.close()

    def run(self, content: bytes, filename: str, request_id: str | None = None) -> DocumentReport:
        request_id = request_id or str(uuid.uuid4())
        filename = PurePosixPath(filename.replace("\\", "/")).name[:200] or "document"
        trace = []

        def call(tool: str, attempt: int, operation: Callable[[], T]) -> T:
            started = time.perf_counter()
            outcome = "completed"
            try:
                return operation()
            except DocumentError as exc:
                outcome = exc.code
                raise
            except Exception:
                outcome = "internal_error"
                raise
            finally:
                duration = round((time.perf_counter() - started) * 1000)
                trace.append(
                    TraceEvent(tool=tool, attempt=attempt, duration_ms=duration, outcome=outcome)
                )
                # No raw text, file names, provider exceptions, or financial values in logs.
                logger.info(
                    "tool_run request_id=%s tool=%s attempt=%d duration_ms=%d outcome=%s",
                    request_id,
                    tool,
                    attempt,
                    duration,
                    outcome,
                )

        source = call("extract_text", 1, lambda: self.reader.run(content, filename))
        document = call("extract_structured", 1, lambda: self.extractor.run(source, []))
        validation = call("validate", 1, lambda: self.validator.run(document, source))
        notices = list(source.warnings)
        feedback = [issue for issue in validation.issues if issue.recheck]
        if feedback and self.settings.max_rechecks and self.extractor.provider != "demo":
            # Business discrepancies do not trigger a rewrite to make arithmetic pass.
            try:
                candidate = call(
                    "recheck_extraction", 2, lambda: self.extractor.run(source, feedback)
                )
                candidate_validation = call(
                    "validate", 2, lambda: self.validator.run(candidate, source)
                )

                def score(report):
                    return (sum(i.severity == "error" for i in report.issues), len(report.issues))

                if score(candidate_validation) < score(validation):
                    document, validation = candidate, candidate_validation
                    notices.append(
                        "A second extraction reduced validation issues and was selected."
                    )
                else:
                    notices.append(
                        "Recheck did not reduce validation issues; initial extraction retained."
                    )
            except DocumentError:
                notices.append("Extraction recheck failed; initial extraction retained for review.")
        summary = call("summarize", 1, lambda: summarize(document, validation))
        return DocumentReport(
            request_id=request_id,
            filename=filename,
            sha256=hashlib.sha256(content).hexdigest(),
            provider=self.extractor.provider,
            model=self.extractor.model,
            document=document,
            validation=validation,
            summary=summary,
            pages=source.pages,
            warnings=notices,
            trace=trace,
        )
