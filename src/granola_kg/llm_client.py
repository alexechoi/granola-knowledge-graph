"""Provider-neutral structured extraction over OpenAI-compatible HTTP APIs."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from granola_kg.extraction_models import ExtractionResult, extraction_instructions
from granola_kg.extraction_schema import extraction_json_schema


class CompletionEnvelope(BaseModel):
    """Minimal compatible subset of a chat-completions response."""

    model_config = ConfigDict(extra="ignore", strict=True)

    choices: list[CompletionChoice]
    usage: CompletionUsage | None = None


class CompletionTokenDetails(BaseModel):
    """Optional provider token breakdown."""

    model_config = ConfigDict(extra="ignore", strict=True)

    cached_tokens: int = 0
    reasoning_tokens: int = 0


class CompletionUsage(BaseModel):
    """OpenAI-compatible token usage fields."""

    model_config = ConfigDict(extra="ignore", strict=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_tokens_details: CompletionTokenDetails | None = None
    completion_tokens_details: CompletionTokenDetails | None = None


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
    max_input_tokens: int = 6500


@dataclass(frozen=True)
class TokenUsage:
    """Provider-reported usage retained with an extraction run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class ExtractionResponse:
    """Validated graph extraction and its provider telemetry."""

    extraction: ExtractionResult
    usage: TokenUsage


class StructuredLlmError(RuntimeError):
    """Model transport or structured response failure."""


class StructuredLlmClient:
    """Extract graph JSON using a provider's chat-completions endpoint."""

    def __init__(self, config: LlmConfig, *, client: httpx.Client | None = None) -> None:
        """Configure an endpoint and optionally inject an HTTP transport."""
        if not config.model:
            msg = "LLM model cannot be empty"
            raise ValueError(msg)
        if config.max_input_tokens < 1:
            msg = "LLM input token budget must be positive"
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
        self._max_input_tokens = config.max_input_tokens

    def close(self) -> None:
        """Release an internally owned HTTP client."""
        if self._owns_client:
            self._client.close()

    def extract(self, *, title: str, evidence_json: str, ontology_json: str) -> ExtractionResponse:
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
        estimated_tokens = math.ceil(len(json.dumps(request_body, separators=(",", ":"))) / 4)
        if estimated_tokens > self._max_input_tokens:
            msg = (
                f"LLM input exceeds configured budget: {estimated_tokens} estimated tokens "
                f"> {self._max_input_tokens}"
            )
            raise StructuredLlmError(msg)
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
            extraction = ExtractionResult.model_validate_json(envelope.choices[0].message.content)
        except ValidationError as error:
            msg = "LLM completion did not match the extraction contract"
            raise StructuredLlmError(msg) from error
        return ExtractionResponse(extraction, _token_usage(envelope.usage))


def _token_usage(usage: CompletionUsage | None) -> TokenUsage:
    if usage is None:
        return TokenUsage()
    prompt_details = usage.prompt_tokens_details or CompletionTokenDetails()
    completion_details = usage.completion_tokens_details or CompletionTokenDetails()
    return TokenUsage(
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cached_input_tokens=prompt_details.cached_tokens,
        reasoning_tokens=completion_details.reasoning_tokens,
    )


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
