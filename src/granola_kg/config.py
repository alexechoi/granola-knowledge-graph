"""Environment-backed local runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from granola_kg.llm_client import LlmConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_PROMPT_VERSION = "v1"


@dataclass(frozen=True)
class RuntimeSettings:
    """Resolved paths, credentials, and model configuration."""

    database_path: Path
    granola_api_key: str | None
    llm_model: str | None
    llm_base_url: str
    llm_api_key: str | None
    prompt_version: str

    def require_remote(self) -> tuple[str, LlmConfig]:
        """Return validated remote settings for sync or processing commands."""
        if self.granola_api_key is None:
            msg = "Set GRANOLA_API_KEY before syncing"
            raise ValueError(msg)
        if self.llm_model is None:
            msg = "Set GRANOLA_KG_LLM_MODEL before processing"
            raise ValueError(msg)
        return self.granola_api_key, LlmConfig(
            model=self.llm_model,
            base_url=self.llm_base_url,
            api_key=self.llm_api_key,
        )


def load_settings(
    database_override: Path | None = None,
    environ: Mapping[str, str] = os.environ,
) -> RuntimeSettings:
    """Resolve settings without persisting secrets to local files."""
    database_path = database_override or _database_path(environ)
    return RuntimeSettings(
        database_path=database_path.expanduser(),
        granola_api_key=environ.get("GRANOLA_API_KEY") or None,
        llm_model=environ.get("GRANOLA_KG_LLM_MODEL") or None,
        llm_base_url=environ.get("GRANOLA_KG_LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
        llm_api_key=environ.get("GRANOLA_KG_LLM_API_KEY") or environ.get("OPENAI_API_KEY") or None,
        prompt_version=environ.get("GRANOLA_KG_PROMPT_VERSION", DEFAULT_PROMPT_VERSION),
    )


def _database_path(environ: Mapping[str, str]) -> Path:
    configured = environ.get("GRANOLA_KG_DB")
    if configured:
        return Path(configured)
    data_root = environ.get("XDG_DATA_HOME")
    if data_root:
        return Path(data_root) / "granola-kg" / "graph.db"
    return Path.home() / ".local" / "share" / "granola-kg" / "graph.db"
