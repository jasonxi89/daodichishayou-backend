import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AIDiscoveredFood, FoodAlias, FoodTrend


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_food_alias_insert_and_read(db):
    alias = FoodAlias(
        alias_name="川式火锅",
        canonical_name="火锅",
        created_by="ai",
        confidence=0.95,
    )
    db.add(alias)
    db.commit()

    row = db.execute(
        select(FoodAlias).where(FoodAlias.alias_name == "川式火锅")
    ).scalar_one()
    assert row.canonical_name == "火锅"
    assert row.created_by == "ai"
    assert row.confidence == 0.95


def test_food_alias_unique_constraint(db):
    db.add(FoodAlias(alias_name="川式火锅", canonical_name="火锅", created_by="ai"))
    db.commit()
    db.add(FoodAlias(alias_name="川式火锅", canonical_name="火锅", created_by="manual"))
    with pytest.raises(Exception):
        db.commit()


def test_food_trend_new_columns_default_null(db):
    t = FoodTrend(food_name="测试食物", source="manual", heat_score=50, post_count=0)
    db.add(t)
    db.commit()
    row = db.execute(
        select(FoodTrend).where(FoodTrend.food_name == "测试食物")
    ).scalar_one()
    assert row.canonical_name is None
    assert row.trend_type is None
    assert row.trend_context is None


def test_food_trend_new_columns_writable(db):
    t = FoodTrend(
        food_name="围炉煮茶",
        source="toutiao",
        heat_score=90,
        post_count=1000,
        canonical_name="围炉煮茶",
        trend_type="seasonal",
        trend_context="入冬社交茶饮",
    )
    db.add(t)
    db.commit()
    row = db.execute(select(FoodTrend).where(FoodTrend.food_name == "围炉煮茶")).scalar_one()
    assert row.canonical_name == "围炉煮茶"
    assert row.trend_type == "seasonal"
    assert row.trend_context == "入冬社交茶饮"


def test_ai_discovered_food_promoted_default_false(db):
    d = AIDiscoveredFood(food_name="新食物X", category="小吃")
    db.add(d)
    db.commit()
    row = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == "新食物X")
    ).scalar_one()
    assert row.promoted_to_trends is False
