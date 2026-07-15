"""Tests for environment-only runtime settings."""

from pathlib import Path

import pytest

from granola_kg.config import DEFAULT_LLM_BASE_URL, load_settings


def test_uses_xdg_local_data_path(tmp_path: Path) -> None:
    """The default database should live in the user's local data directory."""
    data_home = tmp_path / "local-data"
    settings = load_settings(environ={"XDG_DATA_HOME": str(data_home)})

    assert settings.database_path == data_home / "granola-kg" / "graph.db"
    assert settings.llm_base_url == DEFAULT_LLM_BASE_URL
    assert settings.granola_api_key is None


def test_database_override_wins_over_environment(tmp_path: Path) -> None:
    """CLI database selection should override persistent environment settings."""
    override = tmp_path / "explicit.db"

    settings = load_settings(override, {"GRANOLA_KG_DB": str(tmp_path / "ignored.db")})

    assert settings.database_path == override


def test_builds_provider_neutral_remote_configuration() -> None:
    """Hosted or local OpenAI-compatible endpoints should use the same settings."""
    settings = load_settings(
        environ={
            "GRANOLA_API_KEY": "grn_test",
            "GRANOLA_KG_LLM_MODEL": "local-model",
            "GRANOLA_KG_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "GRANOLA_KG_LLM_API_KEY": "local-secret",
        }
    )

    granola_key, llm = settings.require_remote()

    assert granola_key == "grn_test"
    assert llm.model == "local-model"
    assert llm.base_url == "http://127.0.0.1:11434/v1"
    assert llm.api_key == "local-secret"


def test_requires_model_after_granola_key() -> None:
    """Processing should report a missing model without constructing adapters."""
    settings = load_settings(environ={"GRANOLA_API_KEY": "grn_test"})

    with pytest.raises(ValueError, match="GRANOLA_KG_LLM_MODEL"):
        settings.require_remote()
