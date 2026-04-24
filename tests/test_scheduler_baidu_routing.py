import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.crawler.base import FoodTrendItem
from app.crawler.scheduler import _save_candidates, _promote_candidates
from app.database import Base
from app.models import AIDiscoveredFood, FoodTrend


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_save_candidates_writes_to_discovered_foods_not_trends(db):
    items = [
        FoodTrendItem(food_name="火锅", heat_score=100, post_count=10, category="火锅"),
        FoodTrendItem(food_name="奶茶", heat_score=95, post_count=10, category="饮品"),
    ]
    _save_candidates(db, items)

    discovered = db.execute(select(AIDiscoveredFood)).scalars().all()
    trends = db.execute(
        select(FoodTrend).where(FoodTrend.source == "baidu_suggest")
    ).scalars().all()

    assert len(discovered) == 2
    assert {d.food_name for d in discovered} == {"火锅", "奶茶"}
    assert all(d.promoted_to_trends is False for d in discovered)
    assert len(trends) == 0


def test_save_candidates_idempotent_increments_discovery_count(db):
    items = [FoodTrendItem(food_name="火锅", heat_score=100, post_count=10, category="火锅")]
    _save_candidates(db, items)
    _save_candidates(db, items)

    row = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == "火锅")
    ).scalar_one()
    assert row.discovery_count == 2


def test_promote_candidates_only_promotes_when_other_sources_have_it(db):
    db.add(FoodTrend(
        food_name="火锅", source="toutiao",
        heat_score=90, post_count=1000,
        canonical_name="火锅",
    ))
    db.add(AIDiscoveredFood(food_name="火锅", category="火锅"))
    db.add(AIDiscoveredFood(food_name="奶茶", category="饮品"))
    db.commit()

    _promote_candidates(db)

    promoted = db.execute(
        select(FoodTrend).where(
            FoodTrend.source == "baidu_suggest",
            FoodTrend.food_name == "火锅",
        )
    ).scalar_one_or_none()
    assert promoted is not None
    assert promoted.heat_score == int(90 * 0.8)
    hotpot = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == "火锅")
    ).scalar_one()
    assert hotpot.promoted_to_trends is True
    milk_tea = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == "奶茶")
    ).scalar_one()
    assert milk_tea.promoted_to_trends is False


def test_promote_candidates_idempotent_skips_already_promoted(db):
    db.add(FoodTrend(
        food_name="火锅", source="toutiao",
        heat_score=90, post_count=1000, canonical_name="火锅",
    ))
    db.add(AIDiscoveredFood(food_name="火锅", category="火锅", promoted_to_trends=True))
    db.commit()

    _promote_candidates(db)

    promoted = db.execute(
        select(FoodTrend).where(
            FoodTrend.source == "baidu_suggest",
            FoodTrend.food_name == "火锅",
        )
    ).scalar_one_or_none()
    assert promoted is None
