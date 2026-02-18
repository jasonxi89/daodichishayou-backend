import os

import pytest


def test_database_url_default():
    from app.config import DATABASE_URL
    assert "food_trends.db" in DATABASE_URL or "sqlite" in DATABASE_URL


def test_crawl_interval_default():
    from app.config import CRAWL_INTERVAL_HOURS
    assert CRAWL_INTERVAL_HOURS == 6


def test_claude_api_key_from_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_API_KEY", "test-key-123")
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    assert cfg.CLAUDE_API_KEY == "test-key-123"
