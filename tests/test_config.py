import os

import pytest


def test_database_url_default():
    from app.config import DATABASE_URL
    assert "food_trends.db" in DATABASE_URL or "sqlite" in DATABASE_URL


def test_crawl_interval_default():
    from app.config import CRAWL_INTERVAL_HOURS
    assert CRAWL_INTERVAL_HOURS == 6


def test_openrouter_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123")
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    assert cfg.OPENROUTER_API_KEY == "test-key-123"


def test_llm_timeout_defaults_to_60_seconds():
    from app.config import LLM_TIMEOUT_SECONDS
    assert LLM_TIMEOUT_SECONDS == 60


def test_llm_timeout_overridable_from_env(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "120")
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    assert cfg.LLM_TIMEOUT_SECONDS == 120


def test_pregeneration_defaults():
    from app.config import PREGEN_DAILY_BUDGET, PREGEN_ENABLED

    assert PREGEN_ENABLED is True
    assert PREGEN_DAILY_BUDGET == 120


def test_fast_model_is_disabled_by_default():
    from app.config import OPENROUTER_FAST_MODEL

    assert OPENROUTER_FAST_MODEL == ""
