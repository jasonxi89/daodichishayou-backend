from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.crawler.ai_digest import generate_daily_digest
from app.database import Base
from app.models import FoodDigest, FoodTrend


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_digest_prompt_includes_trend_type_and_context(db):
    db.add(FoodTrend(
        food_name="围炉煮茶", source="toutiao",
        heat_score=95, post_count=1000, category="饮品",
        canonical_name="围炉煮茶",
        trend_type="seasonal", trend_context="入冬社交茶饮",
    ))
    db.add(FoodTrend(
        food_name="奶茶", source="dailyhot",
        heat_score=90, post_count=800, category="饮品",
        canonical_name="奶茶",
        trend_type="evergreen", trend_context=None,
    ))
    db.commit()

    with patch("app.crawler.ai_digest.DEEPSEEK_API_KEY", "fake-key"), \
         patch("app.crawler.ai_digest.Anthropic") as mock_anth:
        mock_client = MagicMock()
        mock_anth.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text='{"summary":"s","top_foods":["围炉煮茶"],"recommendation":"喝茶"}')]
        mock_client.messages.create.return_value = mock_resp

        generate_daily_digest(db)

        call_args = mock_client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "type:seasonal" in user_content
        assert "入冬社交茶饮" in user_content
        assert "type:evergreen" in user_content


def test_digest_system_prompt_explains_trend_types(db):
    db.add(FoodTrend(
        food_name="火锅", source="toutiao",
        heat_score=90, post_count=1000, category="火锅",
        canonical_name="火锅",
    ))
    db.commit()

    with patch("app.crawler.ai_digest.DEEPSEEK_API_KEY", "fake-key"), \
         patch("app.crawler.ai_digest.Anthropic") as mock_anth:
        mock_client = MagicMock()
        mock_anth.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text='{"summary":"s","top_foods":[],"recommendation":""}')]
        mock_client.messages.create.return_value = mock_resp

        generate_daily_digest(db)

        call_args = mock_client.messages.create.call_args
        system_prompt = call_args.kwargs["system"]
        assert "event" in system_prompt
        assert "seasonal" in system_prompt
        assert "evergreen" in system_prompt
