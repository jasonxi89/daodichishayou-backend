from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_db
from app.main import app
from app.models import FoodAlias, FoodDigest, FoodTrend

from tests.conftest import TestingSessionLocal, engine as test_engine
from app.database import Base


@pytest.fixture
def client():
    Base.metadata.create_all(bind=test_engine)
    with Session(test_engine) as s:
        s.query(FoodTrend).delete()
        s.query(FoodDigest).delete()
        s.query(FoodAlias).delete()
        s.commit()

    session = TestingSessionLocal()

    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    session.close()

    with Session(test_engine) as s:
        s.query(FoodTrend).delete()
        s.query(FoodDigest).delete()
        s.query(FoodAlias).delete()
        s.commit()


def test_trending_aggregate_default_dedupes_canonical_name(client):
    with Session(test_engine) as s:
        s.add(FoodTrend(
            food_name="烧烤", source="baidu_suggest",
            heat_score=100, post_count=10,
            canonical_name="烧烤", category="烧烤",
        ))
        s.add(FoodTrend(
            food_name="烧烤", source="dailyhot",
            heat_score=95, post_count=7_000_000,
            canonical_name="烧烤", category="烧烤",
        ))
        s.commit()

    resp = client.get("/api/trending?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    names = [i["food_name"] for i in data["items"]]
    assert names.count("烧烤") == 1


def test_trending_aggregate_items_contain_aliases_and_sources(client):
    with Session(test_engine) as s:
        s.add(FoodTrend(
            food_name="川式火锅", source="toutiao",
            heat_score=90, post_count=500,
            canonical_name="火锅", category="火锅",
            trend_type="seasonal", trend_context="入冬涮锅季",
        ))
        s.add(FoodTrend(
            food_name="重庆火锅", source="dailyhot",
            heat_score=88, post_count=400,
            canonical_name="火锅", category="火锅",
        ))
        s.commit()

    resp = client.get("/api/trending?limit=5")
    data = resp.json()
    items = [i for i in data["items"] if i.get("canonical_name") == "火锅"]
    assert len(items) == 1
    assert set(items[0]["aliases"]) == {"川式火锅", "重庆火锅"}
    assert set(items[0]["sources"]) == {"toutiao", "dailyhot"}
    assert items[0]["trend_type"] in ("seasonal", None)


def test_trending_aggregate_false_returns_raw_rows(client):
    with Session(test_engine) as s:
        s.add(FoodTrend(
            food_name="烧烤", source="baidu_suggest",
            heat_score=100, post_count=10, canonical_name="烧烤",
        ))
        s.add(FoodTrend(
            food_name="烧烤", source="dailyhot",
            heat_score=95, post_count=7_000_000, canonical_name="烧烤",
        ))
        s.commit()

    resp = client.get("/api/trending?limit=5&aggregate=false")
    data = resp.json()
    names = [i["food_name"] for i in data["items"]]
    assert names.count("烧烤") == 2


def test_trending_total_reflects_canonical_count_when_aggregate(client):
    with Session(test_engine) as s:
        s.add(FoodTrend(food_name="烧烤", source="a", heat_score=100, post_count=0, canonical_name="烧烤"))
        s.add(FoodTrend(food_name="烧烤", source="b", heat_score=95, post_count=0, canonical_name="烧烤"))
        s.add(FoodTrend(food_name="奶茶", source="a", heat_score=90, post_count=0, canonical_name="奶茶"))
        s.commit()

    resp = client.get("/api/trending?limit=10")
    data = resp.json()
    assert data["total"] == 2


def test_digest_fallback_to_latest_when_no_date_param(client):
    with Session(test_engine) as s:
        s.add(FoodDigest(
            digest_date=datetime(2026, 4, 23, 0, 0),
            summary="昨日快报",
            top_foods='["火锅"]',
            recommendation="吃火锅",
        ))
        s.add(FoodDigest(
            digest_date=datetime(2026, 4, 24, 0, 0),
            summary="今日快报",
            top_foods='["奶茶"]',
            recommendation="喝奶茶",
        ))
        s.commit()

    resp = client.get("/api/trending/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data is not None
    assert data["summary"] == "今日快报"


def test_digest_exact_date_still_works(client):
    with Session(test_engine) as s:
        s.add(FoodDigest(
            digest_date=datetime(2026, 4, 23, 0, 0),
            summary="昨日快报",
            top_foods='["火锅"]',
        ))
        s.commit()

    resp = client.get("/api/trending/digest?date=2026-04-23")
    data = resp.json()
    assert data["summary"] == "昨日快报"


def test_digest_returns_null_when_table_empty(client):
    resp = client.get("/api/trending/digest")
    assert resp.status_code == 200
    assert resp.json() is None
