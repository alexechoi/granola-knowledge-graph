"""Tests for OpenAI-compatible structured extraction."""

from __future__ import annotations

import json

import httpx
import pytest

from granola_kg.llm_client import LlmConfig, StructuredLlmClient, StructuredLlmError
from tests.test_extraction_models import VALID_RESULT


def test_extracts_from_compatible_chat_completion() -> None:
    """The adapter should send an exact schema and validate assistant content."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        envelope = {"choices": [{"message": {"content": VALID_RESULT}}]}
        return httpx.Response(200, text=json.dumps(envelope), request=request)

    http_client = httpx.Client(
        base_url="https://provider.test/v1", transport=httpx.MockTransport(handler)
    )
    client = StructuredLlmClient(LlmConfig(model="local-model"), client=http_client)

    result = client.extract(title="Planning", evidence_json="[]", ontology_json="{}")

    assert result.entities[0].type_key == "meeting"
    assert len(requests) == 1
    request_text = requests[0].content.decode()
    assert '"type": "json_schema"' in request_text
    assert '"name": "granola_knowledge_graph_extraction"' in request_text
    assert '"additionalProperties": false' in request_text
    assert requests[0].url.path == "/v1/chat/completions"
    http_client.close()


def test_rejects_invalid_structured_content() -> None:
    """Malformed model JSON should surface as a domain error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='{"choices":[{"message":{"content":"{}"}}]}',
            request=request,
        )

    http_client = httpx.Client(
        base_url="https://provider.test/v1", transport=httpx.MockTransport(handler)
    )
    client = StructuredLlmClient(LlmConfig(model="local-model"), client=http_client)

    with pytest.raises(StructuredLlmError, match="extraction contract"):
        client.extract(title="Planning", evidence_json="[]", ontology_json="{}")
    http_client.close()


def test_rejects_empty_completion_choices() -> None:
    """A provider response without a usable choice should fail clearly."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='{"choices":[]}', request=request)

    http_client = httpx.Client(
        base_url="https://provider.test/v1", transport=httpx.MockTransport(handler)
    )
    client = StructuredLlmClient(LlmConfig(model="local-model"), client=http_client)

    with pytest.raises(StructuredLlmError, match="message content"):
        client.extract(title="Planning", evidence_json="[]", ontology_json="{}")
    http_client.close()


def test_reports_provider_status_without_response_content() -> None:
    """HTTP failures should expose status but not potentially sensitive response bodies."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="sensitive provider detail", request=request)

    http_client = httpx.Client(
        base_url="https://provider.test/v1", transport=httpx.MockTransport(handler)
    )
    client = StructuredLlmClient(LlmConfig(model="local-model"), client=http_client)

    with pytest.raises(StructuredLlmError, match="HTTP 400") as captured:
        client.extract(title="Planning", evidence_json="[]", ontology_json="{}")
    assert "sensitive" not in str(captured.value)
    http_client.close()
