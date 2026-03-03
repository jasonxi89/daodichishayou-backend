import json
from unittest.mock import patch, MagicMock

import pytest

from app.models import Recipe


def _add_recipes(db):
    """Helper to add sample recipes for testing."""
    recipes = [
        Recipe(
            name="番茄炒蛋",
            source_url="https://example.com/r/1",
            rating=8.9,
            made_count=12000,
            ingredients_text="番茄 鸡蛋",
            ingredients_json='[{"name":"番茄"},{"name":"鸡蛋"}]',
            steps_json='[{"text":"切番茄"},{"text":"炒蛋"}]',
            category="honor",
            list_source="xiachufang",
        ),
        Recipe(
            name="红烧肉",
            source_url="https://example.com/r/2",
            rating=9.2,
            made_count=8000,
            ingredients_text="五花肉 冰糖 酱油",
            category="honor",
        ),
        Recipe(
            name="鸡蛋羹",
            source_url="https://example.com/r/3",
            rating=8.5,
            made_count=5000,
            ingredients_text="鸡蛋 水",
            category="rising",
        ),
        Recipe(
            name="西红柿蛋汤",
            source_url="https://example.com/r/4",
            rating=8.0,
            made_count=3000,
            ingredients_text="番茄 鸡蛋 葱",
            category="honor",
        ),
    ]
    for r in recipes:
        db.add(r)
    db.commit()


def test_search_recipes_by_ingredients(client, db):
    _add_recipes(db)
    resp = client.get("/api/recipes/search?ingredients=鸡蛋,番茄")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    names = [item["name"] for item in data["items"]]
    assert "番茄炒蛋" in names
    assert "西红柿蛋汤" in names


def test_search_recipes_no_match(client, db):
    _add_recipes(db)
    resp = client.get("/api/recipes/search?ingredients=鱼翅")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


def test_search_recipes_single_ingredient(client, db):
    _add_recipes(db)
    resp = client.get("/api/recipes/search?ingredients=鸡蛋")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2  # 番茄炒蛋, 鸡蛋羹, 西红柿蛋汤


def test_search_recipes_empty_ingredients(client, db):
    _add_recipes(db)
    resp = client.get("/api/recipes/search?ingredients=")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


def test_search_recipes_limit(client, db):
    _add_recipes(db)
    resp = client.get("/api/recipes/search?ingredients=鸡蛋&limit=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 1


def test_search_recipes_no_source_url(client, db):
    """Ensure source_url is not exposed in API response."""
    _add_recipes(db)
    resp = client.get("/api/recipes/search?ingredients=鸡蛋")
    data = resp.json()
    for item in data["items"]:
        assert "source_url" not in item


def test_list_recipes(client, db):
    _add_recipes(db)
    resp = client.get("/api/recipes?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert len(data["items"]) == 4


def test_list_recipes_with_category(client, db):
    _add_recipes(db)
    resp = client.get("/api/recipes?category=rising")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "鸡蛋羹"


def test_list_recipes_with_min_rating(client, db):
    _add_recipes(db)
    resp = client.get("/api/recipes?min_rating=9.0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "红烧肉"


def test_list_recipes_pagination(client, db):
    _add_recipes(db)
    resp = client.get("/api/recipes?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert len(data["items"]) == 2

    resp2 = client.get("/api/recipes?limit=2&offset=2")
    data2 = resp2.json()
    assert len(data2["items"]) == 2


def test_list_recipes_sorted_by_rating(client, db):
    _add_recipes(db)
    resp = client.get("/api/recipes?limit=10")
    data = resp.json()
    ratings = [item["rating"] for item in data["items"]]
    assert ratings == sorted(ratings, reverse=True)


def test_trigger_scrape(client, db):
    with patch("app.routers.recipe.run_recipe_scrapers") as mock_scrape:
        from app.schemas import CrawlResult

        mock_scrape.return_value = [
            CrawlResult(
                source="xiachufang",
                status="success",
                items_count=10,
                message="done",
            )
        ]
        resp = client.post("/api/recipes/scrape")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["source"] == "xiachufang"
    assert data[0]["status"] == "success"


def test_list_recipes_empty(client, db):
    resp = client.get("/api/recipes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
