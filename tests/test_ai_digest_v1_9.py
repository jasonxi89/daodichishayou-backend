from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.crawler.ai_digest import generate_daily_digest
from app.database import Base
from app.models import FoodDigest, FoodTrend


def make_openai_response(text: str):
    return MagicMock(choices=[MagicMock(message=MagicMock(content=text))])


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

    with patch("app.crawler.ai_digest.OPENROUTER_API_KEY", "fake-key"), \
         patch("app.crawler.ai_digest.OpenAI") as mock_anth:
        mock_client = MagicMock()
        mock_anth.return_value = mock_client
        mock_client.chat.completions.create.return_value = make_openai_response(
            '{"summary":"s","top_foods":["围炉煮茶"],"recommendation":"喝茶"}'
        )

        generate_daily_digest(db)

        call_args = mock_client.chat.completions.create.call_args
        # User content is messages[1] (messages[0] is the system message)
        user_content = call_args.kwargs["messages"][1]["content"]
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

    with patch("app.crawler.ai_digest.OPENROUTER_API_KEY", "fake-key"), \
         patch("app.crawler.ai_digest.OpenAI") as mock_anth:
        mock_client = MagicMock()
        mock_anth.return_value = mock_client
        mock_client.chat.completions.create.return_value = make_openai_response(
            '{"summary":"s","top_foods":[],"recommendation":""}'
        )

        generate_daily_digest(db)

        call_args = mock_client.chat.completions.create.call_args
        # System prompt is messages[0]["content"]
        system_prompt = call_args.kwargs["messages"][0]["content"]
        assert "event" in system_prompt
        assert "seasonal" in system_prompt
        assert "evergreen" in system_prompt
