import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.crawler.ai_extractor import ExtractedFoodItem
from app.crawler.scheduler import _save_extracted_items
from app.database import Base
from app.models import FoodAlias, FoodTrend


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_save_extracted_writes_trend_fields(db):
    items = [ExtractedFoodItem(
        name="围炉煮茶",
        category="饮品",
        canonical_of="围炉煮茶",
        trend_type="seasonal",
        trend_context="入冬社交茶饮",
        source_title="入冬第一波围炉煮茶火了",
    )]
    _save_extracted_items(db, items)

    row = db.execute(
        select(FoodTrend).where(
            FoodTrend.food_name == "围炉煮茶",
            FoodTrend.source == "ai_extract",
        )
    ).scalar_one()
    assert row.canonical_name == "围炉煮茶"
    assert row.trend_type == "seasonal"
    assert row.trend_context == "入冬社交茶饮"


def test_save_extracted_writes_alias_when_canonical_differs(db):
    items = [ExtractedFoodItem(
        name="川式火锅",
        category="火锅",
        canonical_of="火锅",
        trend_type="seasonal",
        trend_context="入冬涮锅季",
    )]
    _save_extracted_items(db, items)

    alias = db.execute(
        select(FoodAlias).where(FoodAlias.alias_name == "川式火锅")
    ).scalar_one()
    assert alias.canonical_name == "火锅"
    assert alias.created_by == "ai"

    trend = db.execute(
        select(FoodTrend).where(FoodTrend.food_name == "川式火锅")
    ).scalar_one()
    assert trend.canonical_name == "火锅"


def test_save_extracted_no_duplicate_alias_when_canonical_equals_name(db):
    items = [ExtractedFoodItem(
        name="奶茶",
        category="饮品",
        canonical_of="奶茶",
        trend_type="evergreen",
        trend_context=None,
    )]
    _save_extracted_items(db, items)

    aliases = db.execute(
        select(FoodAlias).where(FoodAlias.alias_name == "奶茶")
    ).scalars().all()
    assert len(aliases) <= 1


def test_save_extracted_is_idempotent_on_existing_trend(db):
    items = [ExtractedFoodItem(
        name="围炉煮茶",
        category="饮品",
        canonical_of="围炉煮茶",
        trend_type="seasonal",
        trend_context="入冬社交茶饮",
    )]
    _save_extracted_items(db, items)
    _save_extracted_items(db, items)

    rows = db.execute(
        select(FoodTrend).where(
            FoodTrend.food_name == "围炉煮茶",
            FoodTrend.source == "ai_extract",
        )
    ).scalars().all()
    assert len(rows) == 1
