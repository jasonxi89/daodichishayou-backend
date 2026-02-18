import pytest
from sqlalchemy.exc import IntegrityError


def test_food_trend_create(db):
    from app.models import FoodTrend
    t = FoodTrend(food_name="麻辣烫", source="toutiao", heat_score=95, post_count=50000, category="小吃")
    db.add(t)
    db.commit()
    db.refresh(t)
    assert t.id is not None
    assert t.food_name == "麻辣烫"
    assert t.category == "小吃"


def test_food_trend_unique_constraint(db):
    from app.models import FoodTrend
    t1 = FoodTrend(food_name="火锅", source="toutiao", heat_score=90, post_count=80000)
    t2 = FoodTrend(food_name="火锅", source="toutiao", heat_score=85, post_count=75000)
    db.add(t1)
    db.commit()
    db.add(t2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_food_trend_nullable_fields(db):
    from app.models import FoodTrend
    t = FoodTrend(food_name="测试", source="manual")
    db.add(t)
    db.commit()
    db.refresh(t)
    assert t.category is None
    assert t.image_url is None
    assert t.heat_score == 0


def test_crawl_log_create(db):
    from app.models import CrawlLog
    log = CrawlLog(source="toutiao", status="success", items_count=10)
    db.add(log)
    db.commit()
    db.refresh(log)
    assert log.id is not None
    assert log.status == "success"
    assert log.error_message is None


def test_food_trend_timestamps(db):
    from app.models import FoodTrend
    t = FoodTrend(food_name="蛋糕", source="test")
    db.add(t)
    db.commit()
    db.refresh(t)
    assert t.created_at is not None
    assert t.updated_at is not None
