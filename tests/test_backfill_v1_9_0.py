import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.database import Base
from app.migrations.backfill_v1_9_0 import migrate_v1_9_0
from app.models import FoodAlias, FoodTrend


def _make_engine():
    return create_engine("sqlite:///:memory:")


def test_backfill_adds_canonical_name_column_if_missing():
    engine = _make_engine()
    # 模拟 v1.8.1 schema：先建一个没有新列的 food_trends
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE food_trends ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "food_name VARCHAR(100) NOT NULL,"
            "source VARCHAR(50) NOT NULL,"
            "heat_score INTEGER DEFAULT 0,"
            "post_count INTEGER DEFAULT 0,"
            "category VARCHAR(50),"
            "image_url VARCHAR(500),"
            "updated_at DATETIME,"
            "created_at DATETIME"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE ai_discovered_foods ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "food_name VARCHAR(100) UNIQUE,"
            "category VARCHAR(50),"
            "source_title VARCHAR(500),"
            "discovery_count INTEGER DEFAULT 1,"
            "created_at DATETIME"
            ")"
        ))
        # food_aliases table must exist too (migrate_v1_9_0 requires it).
        # Normally Base.metadata.create_all() creates it on startup before migration.
        conn.execute(text(
            "CREATE TABLE food_aliases ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "alias_name VARCHAR(100) UNIQUE,"
            "canonical_name VARCHAR(100),"
            "created_by VARCHAR(20),"
            "confidence FLOAT,"
            "created_at DATETIME"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO food_trends (food_name, source, heat_score, post_count) "
            "VALUES ('火锅', 'manual', 90, 100)"
        ))
        conn.commit()

    migrate_v1_9_0(engine)

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("food_trends")}
    assert "canonical_name" in cols
    assert "trend_type" in cols
    assert "trend_context" in cols
    cols_disc = {c["name"] for c in insp.get_columns("ai_discovered_foods")}
    assert "promoted_to_trends" in cols_disc


def test_backfill_sets_canonical_name_to_food_name():
    engine = _make_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(FoodTrend(food_name="火锅", source="manual", heat_score=90, post_count=100))
        s.add(FoodTrend(food_name="奶茶", source="manual", heat_score=85, post_count=50))
        s.commit()

    migrate_v1_9_0(engine)

    with Session(engine) as s:
        rows = s.execute(select(FoodTrend)).scalars().all()
        for r in rows:
            assert r.canonical_name == r.food_name


def test_backfill_is_idempotent():
    engine = _make_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(FoodTrend(food_name="火锅", source="manual", heat_score=90, post_count=100))
        s.commit()

    migrate_v1_9_0(engine)
    migrate_v1_9_0(engine)  # 二次调用不应报错

    with Session(engine) as s:
        rows = s.execute(select(FoodAlias)).scalars().all()
        names = [r.alias_name for r in rows]
        assert names.count("火锅") == 1  # 不重复插入


def test_backfill_seeds_food_aliases_self_reference():
    engine = _make_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(FoodTrend(food_name="火锅", source="manual", heat_score=90, post_count=100))
        s.add(FoodTrend(food_name="火锅", source="toutiao", heat_score=95, post_count=1000))
        s.add(FoodTrend(food_name="奶茶", source="manual", heat_score=85, post_count=50))
        s.commit()

    migrate_v1_9_0(engine)

    with Session(engine) as s:
        rows = s.execute(select(FoodAlias)).scalars().all()
        alias_map = {r.alias_name: r.canonical_name for r in rows}
        assert alias_map == {"火锅": "火锅", "奶茶": "奶茶"}
