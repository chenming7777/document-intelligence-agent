import json
from unittest.mock import Mock

import httpx
import pytest
from google import genai
from google.genai import types
from pydantic import SecretStr

from docintel.errors import DocumentError
from docintel.extractors import DemoExtractionTool, GeminiExtractionTool, make_extractor
from docintel.models import FinancialDocument, TextPage, TextResult


def model_response(text, finish_reason="STOP"):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                finish_reason=finish_reason,
            )
        ]
    )


def sdk_client(transport):
    return genai.Client(
        api_key="test-key",
        http_options=types.HttpOptions(
            client_args={"transport": httpx.MockTransport(transport)},
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def test_gemini_contract(settings, source, document):
    client = Mock()
    client.models.generate_content.return_value = model_response(document.model_dump_json())
    tool = GeminiExtractionTool(settings, client=client)
    assert tool.run(source, []).vendor == "ABC Supplies Sdn Bhd"
    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == settings.gemini_model
    assert kwargs["config"].response_mime_type == "application/json"
    assert kwargs["config"].response_json_schema == FinancialDocument.model_json_schema(
        mode="serialization"
    )
    assert kwargs["contents"][0].role == "user"
    assert "untrusted" in kwargs["config"].system_instruction
    assert "[PAGE 1]" in kwargs["contents"][0].parts[0].text
    tool.close()
    client.close.assert_called_once()


@pytest.mark.parametrize(
    ("finish_reason", "text", "expected"),
    [
        ("MAX_TOKENS", '{"vendor":', "incomplete_llm_output"),
        ("SAFETY", "", "llm_refusal"),
        ("STOP", "", "llm_refusal"),
        ("STOP", "not json", "invalid_llm_output"),
        ("STOP", '{"vendor":"missing other fields"}', "invalid_llm_output"),
    ],
)
def test_incomplete_refused_or_invalid_response(settings, source, finish_reason, text, expected):
    client = Mock()
    client.models.generate_content.return_value = model_response(text, finish_reason)
    with pytest.raises(DocumentError) as exc:
        GeminiExtractionTool(settings, client).run(source, [])
    assert exc.value.code == expected


@pytest.mark.parametrize("blocked", [False, True])
def test_empty_candidates_and_prompt_block(settings, source, blocked):
    client = Mock()
    response = types.GenerateContentResponse(candidates=[])
    if blocked:
        response.prompt_feedback = types.GenerateContentResponsePromptFeedback(
            block_reason="SAFETY"
        )
    client.models.generate_content.return_value = response
    with pytest.raises(DocumentError) as exc:
        GeminiExtractionTool(settings, client).run(source, [])
    assert exc.value.code == "llm_refusal"


def test_missing_key_fails_clearly(settings):
    with pytest.raises(DocumentError) as exc:
        GeminiExtractionTool(settings)
    assert exc.value.code == "missing_api_key"
    assert "GEMINI_API_KEY" in exc.value.message


def test_demo_rejects_arbitrary_documents():
    source = TextResult(pages=[TextPage(page=1, method="pdf_text", text="An ordinary invoice")])
    with pytest.raises(DocumentError) as exc:
        DemoExtractionTool().run(source, [])
    assert exc.value.code == "demo_layout_only"
    assert "GEMINI_API_KEY" in exc.value.message


def test_factory_uses_gemini_key_and_bounded_http_options(settings, monkeypatch):
    settings.provider = "gemini"
    settings.gemini_api_key = SecretStr("test-key")
    constructor = Mock()
    monkeypatch.setattr(genai, "Client", constructor)
    extractor = make_extractor(settings)
    assert extractor.provider == "gemini"
    kwargs = constructor.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["enterprise"] is False
    assert kwargs["vertexai"] is False
    assert kwargs["http_options"].timeout == settings.llm_timeout_seconds * 1000
    assert kwargs["http_options"].retry_options.attempts == 3


def test_real_sdk_schema_serialization_and_response_parsing(settings, source, document):
    captured = []

    def transport(request):
        captured.append(json.loads(request.content))
        assert request.headers["x-goog-api-key"] == "test-key"
        assert ":generateContent" in request.url.path
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": document.model_dump_json()}],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "modelVersion": settings.gemini_model,
            },
        )

    with sdk_client(transport) as client:
        result = GeminiExtractionTool(settings, client).run(source, [])
    assert result == document
    config = captured[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    schema = config["responseJsonSchema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(FinancialDocument.model_fields)
    assert schema["properties"]["total_amount"]["anyOf"][0]["type"] == "string"
    assert "untrusted" in captured[0]["systemInstruction"]["parts"][0]["text"]


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("timeout", "llm_timeout"),
        ("connection", "llm_unavailable"),
        ("quota", "llm_provider_error"),
        ("auth", "llm_provider_error"),
    ],
)
def test_sdk_errors_are_safe_and_actionable(settings, source, failure, code):
    def transport(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private-provider-debug")
        if failure == "connection":
            raise httpx.ConnectError("private-provider-debug")
        status = 429 if failure == "quota" else 403
        return httpx.Response(
            status, json={"error": {"code": status, "message": "private-provider-debug"}}
        )

    with sdk_client(transport) as client:
        with pytest.raises(DocumentError) as exc:
            GeminiExtractionTool(settings, client).run(source, [])
    assert exc.value.code == code
    assert "private-provider-debug" not in exc.value.message
