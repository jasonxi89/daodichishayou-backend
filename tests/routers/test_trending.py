import pytest


def test_get_trending_empty(client):
    resp = client.get("/api/trending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_get_trending_returns_items(client, sample_trends):
    resp = client.get("/api/trending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 5


def test_get_trending_sorted_by_heat_score(client, sample_trends):
    resp = client.get("/api/trending")
    items = resp.json()["items"]
    scores = [i["heat_score"] for i in items]
    assert scores == sorted(scores, reverse=True)


def test_get_trending_limit(client, sample_trends):
    resp = client.get("/api/trending?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total"] == 5


def test_get_trending_offset(client, sample_trends):
    resp = client.get("/api/trending?offset=3")
    data = resp.json()
    assert len(data["items"]) == 2


def test_get_trending_filter_source(client, sample_trends):
    resp = client.get("/api/trending?source=toutiao")
    data = resp.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert item["source"] == "toutiao"


def test_get_trending_filter_category(client, sample_trends):
    resp = client.get("/api/trending?category=正餐")
    data = resp.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert item["category"] == "正餐"


def test_get_trending_filter_nonexistent(client, sample_trends):
    resp = client.get("/api/trending?source=nonexistent")
    data = resp.json()
    assert data["total"] == 0


def test_get_categories(client, sample_trends):
    resp = client.get("/api/trending/categories")
    assert resp.status_code == 200
    categories = resp.json()
    assert isinstance(categories, list)
    assert "正餐" in categories
    assert "饮品" in categories


def test_get_categories_empty(client):
    resp = client.get("/api/trending/categories")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_sources(client, sample_trends):
    resp = client.get("/api/trending/sources")
    assert resp.status_code == 200
    sources = resp.json()
    assert "toutiao" in sources
    assert "baidu_suggest" in sources
    assert "manual" in sources


def test_import_creates_new(client):
    payload = [{"food_name": "新菜品", "source": "test", "heat_score": 50, "post_count": 1000, "category": "测试"}]
    resp = client.post("/api/trending/import", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["food_name"] == "新菜品"
    assert data[0]["id"] is not None


def test_import_upsert_existing(client):
    # First insert
    payload = [{"food_name": "火锅", "source": "toutiao", "heat_score": 80}]
    resp1 = client.post("/api/trending/import", json=payload)
    id1 = resp1.json()[0]["id"]

    # Upsert with higher score
    payload2 = [{"food_name": "火锅", "source": "toutiao", "heat_score": 99}]
    resp2 = client.post("/api/trending/import", json=payload2)
    assert resp2.status_code == 200
    data = resp2.json()
    assert data[0]["id"] == id1
    assert data[0]["heat_score"] == 99


def test_import_mixed_insert_and_update(client):
    # Pre-insert one
    client.post("/api/trending/import", json=[{"food_name": "火锅", "source": "toutiao", "heat_score": 80}])
    # Now batch with one existing + one new
    payload = [
        {"food_name": "火锅", "source": "toutiao", "heat_score": 95},
        {"food_name": "奶茶", "source": "toutiao", "heat_score": 87},
    ]
    resp = client.post("/api/trending/import", json=payload)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_import_preserves_category(client):
    payload = [{"food_name": "蛋糕", "source": "manual", "heat_score": 70, "category": "甜品"}]
    resp = client.post("/api/trending/import", json=payload)
    assert resp.json()[0]["category"] == "甜品"


def test_get_trending_limit_max(client):
    resp = client.get("/api/trending?limit=101")
    assert resp.status_code == 422  # validation error


def test_get_trending_offset_negative(client):
    resp = client.get("/api/trending?offset=-1")
    assert resp.status_code == 422
