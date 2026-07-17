"""Environment-backed local runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from granola_kg.llm_client import LlmConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
GEMINI_LLM_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_PROMPT_VERSION = "v1"
DEFAULT_MAX_INPUT_TOKENS = 6500


class LlmProvider(StrEnum):
    """Supported OpenAI-compatible model endpoint families."""

    OPENAI = "openai"
    GEMINI = "gemini"
    CUSTOM = "custom"


@dataclass(frozen=True)
class RuntimeSettings:
    """Resolved paths, credentials, and model configuration."""

    database_path: Path
    granola_api_key: str | None
    llm_model: str | None
    llm_base_url: str
    llm_api_key: str | None
    llm_provider: LlmProvider
    prompt_version: str
    max_input_tokens: int

    def require_remote(self) -> tuple[str, LlmConfig]:
        """Return validated remote settings for sync or processing commands."""
        if self.granola_api_key is None:
            msg = "Set GRANOLA_API_KEY before syncing"
            raise ValueError(msg)
        if self.llm_model is None:
            msg = "Set GRANOLA_KG_LLM_MODEL before processing"
            raise ValueError(msg)
        if self.llm_provider is LlmProvider.GEMINI and self.llm_api_key is None:
            msg = "Set GEMINI_API_KEY before processing with Gemini"
            raise ValueError(msg)
        return self.granola_api_key, LlmConfig(
            model=self.llm_model,
            base_url=self.llm_base_url,
            api_key=self.llm_api_key,
            max_input_tokens=self.max_input_tokens,
        )


def load_settings(
    database_override: Path | None = None,
    environ: Mapping[str, str] = os.environ,
) -> RuntimeSettings:
    """Resolve settings without persisting secrets to local files."""
    database_path = database_override or _database_path(environ)
    provider = _llm_provider(environ)
    model, base_url, api_key = _llm_values(provider, environ)
    return RuntimeSettings(
        database_path=database_path.expanduser(),
        granola_api_key=environ.get("GRANOLA_API_KEY") or None,
        llm_model=model,
        llm_base_url=base_url,
        llm_api_key=api_key,
        llm_provider=provider,
        prompt_version=environ.get("GRANOLA_KG_PROMPT_VERSION", DEFAULT_PROMPT_VERSION),
        max_input_tokens=_positive_integer(
            environ.get("GRANOLA_KG_MAX_INPUT_TOKENS"), DEFAULT_MAX_INPUT_TOKENS
        ),
    )


def _database_path(environ: Mapping[str, str]) -> Path:
    configured = environ.get("GRANOLA_KG_DB")
    if configured:
        return Path(configured)
    data_root = environ.get("XDG_DATA_HOME")
    if data_root:
        return Path(data_root) / "granola-kg" / "graph.db"
    return Path.home() / ".local" / "share" / "granola-kg" / "graph.db"


def _llm_provider(environ: Mapping[str, str]) -> LlmProvider:
    configured = environ.get("GRANOLA_KG_LLM_PROVIDER")
    if configured:
        try:
            return LlmProvider(configured.casefold())
        except ValueError as error:
            choices = ", ".join(provider.value for provider in LlmProvider)
            msg = f"GRANOLA_KG_LLM_PROVIDER must be one of: {choices}"
            raise ValueError(msg) from error
    if environ.get("GEMINI_API_KEY") and not environ.get("GRANOLA_KG_LLM_BASE_URL"):
        return LlmProvider.GEMINI
    if environ.get("GRANOLA_KG_LLM_BASE_URL"):
        return LlmProvider.CUSTOM
    return LlmProvider.OPENAI


def _llm_values(
    provider: LlmProvider, environ: Mapping[str, str]
) -> tuple[str | None, str, str | None]:
    configured_model = environ.get("GRANOLA_KG_LLM_MODEL") or None
    configured_base_url = environ.get("GRANOLA_KG_LLM_BASE_URL") or None
    configured_key = environ.get("GRANOLA_KG_LLM_API_KEY") or None
    if provider is LlmProvider.GEMINI:
        return (
            configured_model or DEFAULT_GEMINI_MODEL,
            configured_base_url or GEMINI_LLM_BASE_URL,
            configured_key or environ.get("GEMINI_API_KEY") or None,
        )
    return (
        configured_model,
        configured_base_url or DEFAULT_LLM_BASE_URL,
        configured_key or environ.get("OPENAI_API_KEY") or None,
    )


def _positive_integer(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        msg = "GRANOLA_KG_MAX_INPUT_TOKENS must be a positive integer"
        raise ValueError(msg) from error
    if parsed < 1:
        msg = "GRANOLA_KG_MAX_INPUT_TOKENS must be a positive integer"
        raise ValueError(msg)
    return parsed
