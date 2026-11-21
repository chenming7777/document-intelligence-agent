import json
from unittest.mock import Mock

import pytest

from docintel.agent import DocumentAgent
from docintel.errors import DocumentError
from docintel.summaries import GeminiSummaryTool, SummaryResult
from docintel.validation import ValidationTool
from tests.test_extractors import model_response, sdk_client


def test_summary_uses_validated_values_and_issues(settings, document, source):
    document.vendor = None
    validation = ValidationTool().run(document, source)
    client = Mock()
    client.models.generate_content.return_value = model_response(
        json.dumps(
            {
                "summary": "This invoice requires review because the vendor is missing.",
                "validation_status": "needs_review",
            }
        )
    )
    tool = GeminiSummaryTool(settings, client)
    assert "requires review" in tool.run(document, validation)
    kwargs = client.models.generate_content.call_args.kwargs
    data = json.loads(kwargs["contents"][0].parts[0].text)
    assert data["document"]["total_amount"] == "3250.00"
    assert data["validation"]["status"] == "needs_review"
    assert any(i["field"] == "vendor" for i in data["validation"]["issues"])
    assert "evidence" not in data["document"]
    assert "notes" not in data["document"]
    assert "untrusted" in kwargs["config"].system_instruction
    assert kwargs["config"].response_json_schema == SummaryResult.model_json_schema(
        mode="serialization"
    )
    tool.close()
    client.close.assert_called_once()


@pytest.mark.parametrize(
    "payload",
    [
        {"summary": "All fine", "validation_status": "needs_review"},
        {"summary": "   ", "validation_status": "passed"},
        {"summary": "a" * 2401, "validation_status": "passed"},
        {"summary": "missing status"},
    ],
)
def test_invalid_summary_is_rejected(settings, document, source, payload):
    client = Mock()
    client.models.generate_content.return_value = model_response(json.dumps(payload))
    with pytest.raises(DocumentError) as exc:
        GeminiSummaryTool(settings, client).run(document, ValidationTool().run(document, source))
    assert exc.value.code == "invalid_llm_output"


def test_summary_timeout_does_not_silently_become_demo(settings, invoice_bytes):
    summarizer = Mock(method="gemini")
    summarizer.run.side_effect = DocumentError("llm_timeout", "Gemini timed out.", 504)
    with pytest.raises(DocumentError) as exc:
        DocumentAgent(settings, summarizer=summarizer).run(invoice_bytes, "invoice.pdf")
    assert exc.value.code == "llm_timeout"


def test_summary_follows_final_validation_after_recheck(settings, invoice_bytes, document):
    extractor = Mock(provider="gemini", model="test")
    extractor.run.side_effect = [document.model_copy(update={"vendor": None}), document]
    summary = Mock(method="gemini")
    summary.run.return_value = "An AI-generated report."
    report = DocumentAgent(settings, extractor=extractor, summarizer=summary).run(
        invoice_bytes, "invoice.pdf"
    )
    summarized_document, validation = summary.run.call_args.args
    assert summarized_document.vendor == document.vendor
    assert validation.status == "passed"
    assert report.summary_method == "gemini"
    assert report.trace[-2].tool == "validate"
    assert report.trace[-1].tool == "summarize"


def test_summary_sdk_round_trip(settings, document, source):
    import httpx

    def transport(request):
        payload = json.loads(request.content)
        assert "summary" in payload["generationConfig"]["responseJsonSchema"]["required"]
        text = json.dumps({"summary": "Invoice total MYR 3250.00.", "validation_status": "passed"})
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": text}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    with sdk_client(transport) as client:
        summary = GeminiSummaryTool(settings, client).run(
            document, ValidationTool().run(document, source)
        )
    assert "3250.00" in summary
