"""Provider-neutral structured extraction over OpenAI-compatible HTTP APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from granola_kg.extraction_models import ExtractionResult, extraction_instructions
from granola_kg.extraction_schema import extraction_json_schema


class CompletionEnvelope(BaseModel):
    """Minimal compatible subset of a chat-completions response."""

    model_config = ConfigDict(extra="ignore", strict=True)

    choices: list[CompletionChoice]


class CompletionMessage(BaseModel):
    """Assistant message containing JSON text."""

    model_config = ConfigDict(extra="ignore", strict=True)

    content: str | None


class CompletionChoice(BaseModel):
    """One completion choice."""

    model_config = ConfigDict(extra="ignore", strict=True)

    message: CompletionMessage


@dataclass(frozen=True)
class LlmConfig:
    """OpenAI-compatible endpoint configuration."""

    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    timeout_seconds: float = 120.0


class StructuredLlmError(RuntimeError):
    """Model transport or structured response failure."""


class StructuredLlmClient:
    """Extract graph JSON using a provider's chat-completions endpoint."""

    def __init__(self, config: LlmConfig, *, client: httpx.Client | None = None) -> None:
        """Configure an endpoint and optionally inject an HTTP transport."""
        if not config.model:
            msg = "LLM model cannot be empty"
            raise ValueError(msg)
        headers = {"Content-Type": "application/json"}
        if config.api_key is not None:
            headers["Authorization"] = f"Bearer {config.api_key}"
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=config.base_url,
            headers=headers,
            timeout=config.timeout_seconds,
        )
        self._model = config.model

    def close(self) -> None:
        """Release an internally owned HTTP client."""
        if self._owns_client:
            self._client.close()

    def extract(self, *, title: str, evidence_json: str, ontology_json: str) -> ExtractionResult:
        """Extract and validate ontology additions, entities, and relations."""
        user_content = _user_prompt(title, evidence_json, ontology_json)
        request_body: dict[str, object] = {
            "model": self._model,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "granola_knowledge_graph_extraction",
                    "strict": True,
                    "schema": extraction_json_schema(),
                },
            },
            "messages": [
                {"role": "system", "content": extraction_instructions()},
                {"role": "user", "content": user_content},
            ],
        }
        try:
            response = self._client.post(
                "/chat/completions",
                content=json.dumps(request_body),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            envelope = CompletionEnvelope.model_validate_json(response.text)
        except httpx.HTTPStatusError as error:
            msg = f"LLM completion request returned HTTP {error.response.status_code}"
            raise StructuredLlmError(msg) from error
        except (httpx.HTTPError, ValidationError) as error:
            msg = "LLM completion request failed or returned an invalid envelope"
            raise StructuredLlmError(msg) from error
        if not envelope.choices or envelope.choices[0].message.content is None:
            msg = "LLM completion did not contain message content"
            raise StructuredLlmError(msg)
        try:
            return ExtractionResult.model_validate_json(envelope.choices[0].message.content)
        except ValidationError as error:
            msg = "LLM completion did not match the extraction contract"
            raise StructuredLlmError(msg) from error


def _user_prompt(title: str, evidence_json: str, ontology_json: str) -> str:
    """Build a delimited prompt without parsing provider-independent JSON inputs."""
    return f"""Meeting title: {title}

Current ontology JSON:
<ontology>
{ontology_json}
</ontology>

Evidence JSON (each item has an evidence_id):
<evidence>
{evidence_json}
</evidence>

Return keys: ontology, entities, relations. The ontology value contains entity_types and
relation_types. Each entity contains local_id, type_key, name, identifiers, evidence_ids,
and properties. Each relation references entity local IDs and includes evidence_ids and
confidence.
"""
