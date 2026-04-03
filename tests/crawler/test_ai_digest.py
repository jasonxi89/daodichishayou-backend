import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models import FoodDigest, FoodTrend


@pytest.fixture
def db_with_trends(db):
    """Insert sample trends for digest generation."""
    trends = [
        FoodTrend(food_name="火锅", source="toutiao", heat_score=95, category="正餐"),
        FoodTrend(food_name="奶茶", source="toutiao", heat_score=90, category="饮品"),
        FoodTrend(food_name="螺蛳粉", source="baidu_suggest", heat_score=88, category="小吃"),
    ]
    for t in trends:
        db.add(t)
    db.commit()
    return db


def test_digest_skips_without_api_key(db_with_trends):
    from app.crawler.ai_digest import generate_daily_digest
    with patch("app.crawler.ai_digest.CLAUDE_API_KEY", ""):
        result = generate_daily_digest(db_with_trends)
    assert result is None


def test_digest_skips_empty_data(db):
    from app.crawler.ai_digest import generate_daily_digest
    with patch("app.crawler.ai_digest.CLAUDE_API_KEY", "test-key"):
        result = generate_daily_digest(db)
    assert result is None


def test_digest_creates_new(db_with_trends):
    from app.crawler.ai_digest import generate_daily_digest

    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=json.dumps({
        "summary": "今日火锅和奶茶最火",
        "top_foods": ["火锅", "奶茶", "螺蛳粉"],
        "recommendation": "天冷来份火锅",
    }))]

    with patch("app.crawler.ai_digest.CLAUDE_API_KEY", "test-key"), \
         patch("app.crawler.ai_digest.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = mock_resp
        result = generate_daily_digest(db_with_trends)

    assert result is not None
    assert result.summary == "今日火锅和奶茶最火"
    assert result.recommendation == "天冷来份火锅"
    assert json.loads(result.top_foods) == ["火锅", "奶茶", "螺蛳粉"]
    assert result.digest_date == datetime.combine(date.today(), datetime.min.time())


def test_digest_upserts_same_day(db_with_trends):
    from app.crawler.ai_digest import generate_daily_digest

    # Insert existing digest for today
    db_with_trends.add(FoodDigest(
        digest_date=datetime.combine(date.today(), datetime.min.time()),
        summary="旧摘要",
        top_foods=json.dumps(["旧食物"]),
        recommendation="旧推荐",
    ))
    db_with_trends.commit()

    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=json.dumps({
        "summary": "新摘要",
        "top_foods": ["火锅"],
        "recommendation": "新推荐",
    }))]

    with patch("app.crawler.ai_digest.CLAUDE_API_KEY", "test-key"), \
         patch("app.crawler.ai_digest.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = mock_resp
        result = generate_daily_digest(db_with_trends)

    assert result.summary == "新摘要"
    # Should be same row (upsert), not a new one
    all_digests = db_with_trends.query(FoodDigest).all()
    assert len(all_digests) == 1


def test_digest_handles_api_error(db_with_trends):
    from app.crawler.ai_digest import generate_daily_digest

    with patch("app.crawler.ai_digest.CLAUDE_API_KEY", "test-key"), \
         patch("app.crawler.ai_digest.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API down")
        result = generate_daily_digest(db_with_trends)

    assert result is None


def test_digest_handles_json_parse_error(db_with_trends):
    from app.crawler.ai_digest import generate_daily_digest

    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="not valid json")]

    with patch("app.crawler.ai_digest.CLAUDE_API_KEY", "test-key"), \
         patch("app.crawler.ai_digest.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = mock_resp
        result = generate_daily_digest(db_with_trends)

    assert result is None


def test_digest_handles_markdown_code_block(db_with_trends):
    from app.crawler.ai_digest import generate_daily_digest

    json_body = json.dumps({
        "summary": "markdown包裹",
        "top_foods": ["火锅"],
        "recommendation": "推荐",
    })
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=f"```json\n{json_body}\n```")]

    with patch("app.crawler.ai_digest.CLAUDE_API_KEY", "test-key"), \
         patch("app.crawler.ai_digest.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = mock_resp
        result = generate_daily_digest(db_with_trends)

    assert result is not None
    assert result.summary == "markdown包裹"
