from unittest.mock import patch

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

    with patch("app.crawler.ai_digest.ANTHROPIC_API_KEY", "fake-key"), \
         patch("app.services.llm.complete_text") as mock_complete:
        mock_complete.return_value = (
            '{"summary":"s","top_foods":["围炉煮茶"],"recommendation":"喝茶"}'
        )

        generate_daily_digest(db)

        call_args = mock_complete.call_args
        user_content = call_args.args[1]
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

    with patch("app.crawler.ai_digest.ANTHROPIC_API_KEY", "fake-key"), \
         patch("app.services.llm.complete_text") as mock_complete:
        mock_complete.return_value = (
            '{"summary":"s","top_foods":[],"recommendation":""}'
        )

        generate_daily_digest(db)

        call_args = mock_complete.call_args
        system_prompt = call_args.args[0]
        assert "event" in system_prompt
        assert "seasonal" in system_prompt
        assert "evergreen" in system_prompt
