"""Shared bounded Gemini JSON requests and safe provider error handling."""

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from docintel.config import Settings
from docintel.errors import DocumentError


def create_client(settings: Settings):
    if not settings.gemini_api_key.get_secret_value():
        raise DocumentError("missing_api_key", "Set GEMINI_API_KEY to enable live Gemini.", 503)
    return genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        enterprise=False,
        vertexai=False,
        http_options=types.HttpOptions(
            timeout=settings.llm_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
    )


def generate_json(client, model: str, instructions: str, content: str, schema: type[BaseModel]):
    try:
        response = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=[types.Part(text=content)])],
            config=types.GenerateContentConfig(
                system_instruction=instructions,
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(mode="serialization"),
                max_output_tokens=8000,
                temperature=0,
            ),
        )
    except httpx.TimeoutException as exc:
        raise DocumentError(
            "llm_timeout", "The Gemini provider timed out. Try again.", 504
        ) from exc
    except httpx.TransportError as exc:
        raise DocumentError("llm_unavailable", "Cannot reach the Gemini provider.", 503) from exc
    except errors.APIError as exc:
        raise DocumentError(
            "llm_provider_error", "Gemini rejected the request. Check model access and quota.", 502
        ) from exc
    if response.prompt_feedback and response.prompt_feedback.block_reason not in (
        None,
        "BLOCKED_REASON_UNSPECIFIED",
    ):
        raise DocumentError("llm_refusal", "Gemini blocked this request.", 422)
    if not response.candidates:
        raise DocumentError("llm_refusal", "Gemini did not return a result.", 422)
    finish_reason = response.candidates[0].finish_reason
    if finish_reason == "MAX_TOKENS":
        raise DocumentError("incomplete_llm_output", "The model response was incomplete.", 502)
    if finish_reason != "STOP" or not response.text:
        raise DocumentError("llm_refusal", "Gemini did not return a complete result.", 422)
    try:
        return schema.model_validate_json(response.text)
    except ValidationError as exc:
        raise DocumentError(
            "invalid_llm_output", "Gemini returned invalid structured data.", 502
        ) from exc
