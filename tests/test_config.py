import importlib

import pytest


@pytest.fixture
def reload_config(monkeypatch):
    """按当前环境变量重新加载 app.config；用例结束后恢复环境再加载一次，避免污染别的用例。"""
    import app.config as config

    yield lambda: importlib.reload(config)
    monkeypatch.undo()
    importlib.reload(config)


def test_database_url_default():
    from app.config import DATABASE_URL
    assert "food_trends.db" in DATABASE_URL or "sqlite" in DATABASE_URL


def test_crawl_interval_default():
    from app.config import CRAWL_INTERVAL_HOURS
    assert CRAWL_INTERVAL_HOURS == 6


def test_anthropic_api_key_from_env(monkeypatch, reload_config):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    assert reload_config().ANTHROPIC_API_KEY == "test-key-123"


def test_anthropic_api_key_defaults_to_empty():
    from app.config import ANTHROPIC_API_KEY

    assert ANTHROPIC_API_KEY == ""


def test_claude_model_defaults_to_opus_5_5():
    from app.config import CLAUDE_MODEL

    assert CLAUDE_MODEL == "claude-opus-5-5"


def test_claude_model_overridable_from_env(monkeypatch, reload_config):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-5-5")
    assert reload_config().CLAUDE_MODEL == "claude-sonnet-5-5"


def test_claude_effort_defaults_to_low():
    from app.config import CLAUDE_EFFORT

    assert CLAUDE_EFFORT == "low"


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_claude_effort_accepts_every_allowed_level(monkeypatch, effort):
    from app.config import read_claude_effort

    monkeypatch.setenv("CLAUDE_EFFORT", effort)
    assert read_claude_effort() == effort


@pytest.mark.parametrize("effort", ["", "Low", "extreme", "max "])
def test_invalid_claude_effort_fails_fast_with_clear_message(monkeypatch, effort):
    from app.config import read_claude_effort

    monkeypatch.setenv("CLAUDE_EFFORT", effort)
    with pytest.raises(RuntimeError, match="CLAUDE_EFFORT=.*无效.*low, medium, high, xhigh, max"):
        read_claude_effort()


def test_llm_timeout_defaults_to_60_seconds():
    from app.config import LLM_TIMEOUT_SECONDS
    assert LLM_TIMEOUT_SECONDS == 60


def test_llm_timeout_overridable_from_env(monkeypatch, reload_config):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "120")
    assert reload_config().LLM_TIMEOUT_SECONDS == 120


def test_pregeneration_defaults():
    from app.config import PREGEN_DAILY_BUDGET, PREGEN_ENABLED

    assert PREGEN_ENABLED is True
    assert PREGEN_DAILY_BUDGET == 120


def test_fast_model_is_disabled_by_default():
    from app.config import CLAUDE_FAST_MODEL

    assert CLAUDE_FAST_MODEL == ""


def test_recipe_scraping_is_disabled_by_default():
    from app.config import RECIPE_SCRAPE_ENABLED

    assert RECIPE_SCRAPE_ENABLED is False
