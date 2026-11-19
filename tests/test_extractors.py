import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx2
import pytest
from openai import OpenAI

from docintel.errors import DocumentError
from docintel.extractors import DemoExtractionTool, OpenAIExtractionTool
from docintel.models import FinancialDocument, TextPage, TextResult


def test_openai_contract(settings, source, document):
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        status="completed", output_parsed=document
    )
    tool = OpenAIExtractionTool(settings, client=client)
    assert tool.run(source, []).vendor == "ABC Supplies Sdn Bhd"
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs["text_format"] is FinancialDocument
    assert kwargs["store"] is False
    assert kwargs["input"][0]["role"] == "user"
    assert "untrusted" in kwargs["instructions"]
    assert "[PAGE 1]" in kwargs["input"][0]["content"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("incomplete", "incomplete_llm_output"),
        ("completed", "llm_refusal"),
    ],
)
def test_incomplete_or_refused_response(settings, source, status, expected):
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(status=status, output_parsed=None)
    with pytest.raises(DocumentError) as exc:
        OpenAIExtractionTool(settings, client).run(source, [])
    assert exc.value.code == expected


def test_missing_key_fails_clearly(settings):
    with pytest.raises(DocumentError) as exc:
        OpenAIExtractionTool(settings)
    assert exc.value.code == "missing_api_key"


def test_demo_rejects_arbitrary_documents():
    source = TextResult(pages=[TextPage(page=1, method="pdf_text", text="An ordinary invoice")])
    with pytest.raises(DocumentError) as exc:
        DemoExtractionTool().run(source, [])
    assert exc.value.code == "demo_layout_only"


def test_real_sdk_schema_serialization_and_response_parsing(settings, source, document):
    captured = []

    def transport(request):
        captured.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-4.1-mini",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": document.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    with OpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(transport)),
    ) as client:
        result = OpenAIExtractionTool(settings, client).run(source, [])
    assert result == document
    schema = captured[0]["text"]["format"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == set(FinancialDocument.model_fields)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("timeout", "llm_timeout"),
        ("connection", "llm_unavailable"),
        ("quota", "llm_provider_error"),
    ],
)
def test_sdk_errors_are_safe_and_actionable(settings, source, failure, code):
    def transport(request):
        if failure == "timeout":
            raise httpx2.ReadTimeout("private-provider-debug")
        if failure == "connection":
            raise httpx2.ConnectError("private-provider-debug")
        return httpx2.Response(429, json={"error": {"message": "private-provider-debug"}})

    with OpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(transport)),
    ) as client:
        with pytest.raises(DocumentError) as exc:
            OpenAIExtractionTool(settings, client).run(source, [])
    assert exc.value.code == code
    assert "private-provider-debug" not in exc.value.message
