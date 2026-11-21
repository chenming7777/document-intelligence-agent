import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from typing import TypeVar

from docintel.config import Settings
from docintel.errors import DocumentError
from docintel.extractors import StructuredExtractionTool, make_extractor
from docintel.models import DocumentReport, TraceEvent
from docintel.reader import TextExtractionTool
from docintel.storage import safe_filename
from docintel.summaries import SummaryTool, make_summarizer
from docintel.validation import ValidationTool

logger = logging.getLogger(__name__)
T = TypeVar("T")


class DocumentAgent:
    """An explicit, bounded workflow with tool calls and one conditional extraction recheck."""

    def __init__(
        self,
        settings: Settings,
        extractor: StructuredExtractionTool | None = None,
        reader: TextExtractionTool | None = None,
        summarizer: SummaryTool | None = None,
    ):
        self.settings = settings
        self.extractor = extractor or make_extractor(settings)
        try:
            self.summarizer = summarizer or make_summarizer(settings)
        except Exception:
            self.extractor.close()
            raise
        self.reader = reader or TextExtractionTool(settings)
        self.validator = ValidationTool()

    def close(self) -> None:
        try:
            self.extractor.close()
        finally:
            self.summarizer.close()

    def run(
        self,
        content: bytes,
        filename: str,
        request_id: str | None = None,
        document_id: str | None = None,
    ) -> DocumentReport:
        request_id = request_id or str(uuid.uuid4())
        filename = safe_filename(filename)
        document_id = document_id or "doc_" + uuid.uuid4().hex
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
                    "tool_run document_id=%s request_id=%s tool=%s "
                    "attempt=%d duration_ms=%d outcome=%s",
                    document_id,
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
        summary = call("summarize", 1, lambda: self.summarizer.run(document, validation))
        return DocumentReport(
            document_id=document_id,
            request_id=request_id,
            filename=filename,
            sha256=hashlib.sha256(content).hexdigest(),
            provider=self.extractor.provider,
            model=self.extractor.model,
            document=document,
            validation=validation,
            summary=summary,
            summary_method=self.summarizer.method,
            pages=source.pages,
            warnings=notices,
            trace=trace,
        )
