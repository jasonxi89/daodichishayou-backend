import pytest


def test_get_trending_empty(client):
    resp = client.get("/api/trending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_get_trending_returns_items(client, sample_trends):
    # aggregate=false to check raw row count (火锅 appears twice from different sources)
    resp = client.get("/api/trending?aggregate=false")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 5


def test_get_trending_sorted_by_heat_score(client, sample_trends):
    resp = client.get("/api/trending?aggregate=false")
    items = resp.json()["items"]
    scores = [i["heat_score"] for i in items]
    assert scores == sorted(scores, reverse=True)


def test_get_trending_limit(client, sample_trends):
    # aggregate=false: 5 raw rows; limit=2 returns 2 items, total=5
    resp = client.get("/api/trending?limit=2&aggregate=false")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total"] == 5


def test_get_trending_offset(client, sample_trends):
    # aggregate=false: 5 raw rows; offset=3 returns last 2
    resp = client.get("/api/trending?offset=3&aggregate=false")
    data = resp.json()
    assert len(data["items"]) == 2


def test_get_trending_filter_source(client, sample_trends):
    # aggregate=false: toutiao has 2 raw rows (火锅 + 奶茶), source filter preserved
    resp = client.get("/api/trending?source=toutiao&aggregate=false")
    data = resp.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert item["source"] == "toutiao"


def test_get_trending_filter_category(client, sample_trends):
    # aggregate=false: 正餐 has 2 raw rows (火锅 from toutiao + baidu_suggest)
    resp = client.get("/api/trending?category=正餐&aggregate=false")
    data = resp.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert item["category"] == "正餐"


def test_get_trending_aggregate_filter_source_returns_grouped_items(client, sample_trends):
    # aggregate 默认开启: toutiao 有 火锅(90) 和 奶茶(87) 两组
    resp = client.get("/api/trending?source=toutiao")
    data = resp.json()
    assert data["total"] == 2
    names = [item["food_name"] for item in data["items"]]
    assert names == ["火锅", "奶茶"]


def test_get_trending_aggregate_filter_category_merges_sources(client, sample_trends):
    # aggregate 默认开启: 正餐 只有 火锅 一组 (toutiao 90 + baidu_suggest 88)
    resp = client.get("/api/trending?category=正餐")
    data = resp.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["food_name"] == "火锅"
    assert item["heat_score"] == 90
    assert set(item["sources"]) == {"toutiao", "baidu_suggest"}
    assert item["post_count"] == 80000 + 75000


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
    resp = client.get("/api/trending?limit=201")
    assert resp.status_code == 422  # validation error


def test_get_trending_offset_negative(client):
    resp = client.get("/api/trending?offset=-1")
    assert resp.status_code == 422


# --- Digest endpoint tests ---


def test_get_digest_empty(client):
    resp = client.get("/api/trending/digest")
    assert resp.status_code == 200
    assert resp.json() is None


def test_get_digest_returns_today(client, db):
    import json
    from datetime import date, datetime
    from app.models import FoodDigest
    db.add(FoodDigest(
        digest_date=datetime.combine(date.today(), datetime.min.time()),
        summary="今日火锅最火",
        top_foods=json.dumps(["火锅", "奶茶"]),
        recommendation="来份火锅",
    ))
    db.commit()

    resp = client.get("/api/trending/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == "今日火锅最火"
    assert data["top_foods"] == ["火锅", "奶茶"]
    assert data["recommendation"] == "来份火锅"


def test_get_digest_by_date(client, db):
    import json
    from datetime import datetime
    from app.models import FoodDigest
    target = datetime(2026, 3, 15)
    db.add(FoodDigest(
        digest_date=target,
        summary="三月中旬快报",
        top_foods=json.dumps(["螺蛳粉"]),
        recommendation="春天吃螺蛳粉",
    ))
    db.commit()

    resp = client.get("/api/trending/digest?date=2026-03-15")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == "三月中旬快报"


def test_get_digest_nonexistent_date(client):
    resp = client.get("/api/trending/digest?date=2020-01-01")
    assert resp.status_code == 200
    assert resp.json() is None


# --- History endpoint tests ---


def test_get_history_empty(client):
    resp = client.get("/api/trending/history/火锅")
    assert resp.status_code == 200
    data = resp.json()
    assert data["food_name"] == "火锅"
    assert data["history"] == []


def test_get_history_returns_snapshots(client, db):
    from datetime import date
    from app.models import FoodTrendSnapshot
    for i, d in enumerate([date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]):
        db.add(FoodTrendSnapshot(
            snapshot_date=d,
            food_name="火锅",
            heat_score=80 + i * 5,
            source="toutiao",
            category="正餐",
        ))
    db.commit()

    resp = client.get("/api/trending/history/火锅?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert data["food_name"] == "火锅"
    assert len(data["history"]) == 3
    # Should be sorted desc
    scores = [h["heat_score"] for h in data["history"]]
    assert scores == [90, 85, 80]


def test_get_history_respects_days_limit(client, db):
    from datetime import date
    from app.models import FoodTrendSnapshot
    for i in range(10):
        db.add(FoodTrendSnapshot(
            snapshot_date=date(2026, 3, 20 + i),
            food_name="奶茶",
            heat_score=70 + i,
            source="baidu_suggest",
        ))
    db.commit()

    resp = client.get("/api/trending/history/奶茶?days=3")
    data = resp.json()
    assert len(data["history"]) == 3


def test_get_history_nonexistent_food(client):
    resp = client.get("/api/trending/history/不存在的食物")
    assert resp.status_code == 200
    assert resp.json()["history"] == []


# ===== /api/trending/categories/annotated =====

from unittest.mock import MagicMock, patch  # noqa: E402

import openai  # noqa: E402


def make_openai_response(text: str):
    response = MagicMock()
    response.choices[0].message.content = text
    return response


def _seed_note(db, category: str, note: str):
    from app.models import CategoryNote

    db.add(CategoryNote(category=category, note=note))
    db.commit()


def test_annotated_categories_empty_db(client):
    resp = client.get("/api/trending/categories/annotated")
    assert resp.status_code == 200
    assert resp.json()["categories"] == []


def test_annotated_categories_all_cached_skips_llm(client, db, sample_trends, monkeypatch):
    monkeypatch.setattr("app.routers.trending.OPENROUTER_API_KEY", "test-key")
    for category, note in [("正餐", "好好吃饭"), ("饮品", "咕咚咕咚"), ("西餐", "刀叉伺候"), ("日料", "一口一个")]:
        _seed_note(db, category, note)

    with patch("app.routers.trending.OpenAI") as mock_openai:
        resp = client.get("/api/trending/categories/annotated")

    assert resp.status_code == 200
    data = {c["name"]: c["note"] for c in resp.json()["categories"]}
    assert data == {"正餐": "好好吃饭", "饮品": "咕咚咕咚", "西餐": "刀叉伺候", "日料": "一口一个"}
    mock_openai.assert_not_called()


def test_annotated_categories_generates_and_persists_missing(client, db, sample_trends, monkeypatch):
    from app.models import CategoryNote

    monkeypatch.setattr("app.routers.trending.OPENROUTER_API_KEY", "test-key")
    _seed_note(db, "正餐", "好好吃饭")

    llm_json = '{"饮品": "咕咚咕咚", "西餐": "刀叉伺候"}'  # 日料缺失 → note 应为 null
    with patch("app.routers.trending.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.return_value = make_openai_response(llm_json)

        resp = client.get("/api/trending/categories/annotated")

    assert resp.status_code == 200
    data = {c["name"]: c["note"] for c in resp.json()["categories"]}
    assert data == {"正餐": "好好吃饭", "饮品": "咕咚咕咚", "西餐": "刀叉伺候", "日料": None}
    saved = {row.category: row.note for row in db.query(CategoryNote).all()}
    assert saved == {"正餐": "好好吃饭", "饮品": "咕咚咕咚", "西餐": "刀叉伺候"}

    # 第二次请求：已入库的不再触发 LLM（只对仍缺失的日料再试一次）
    with patch("app.routers.trending.OpenAI") as mock_openai_2:
        mock_client_2 = MagicMock()
        mock_openai_2.return_value = mock_client_2
        mock_client_2.chat.completions.create.return_value = make_openai_response('{"日料": "一口一个"}')

        resp2 = client.get("/api/trending/categories/annotated")

    data2 = {c["name"]: c["note"] for c in resp2.json()["categories"]}
    assert data2["日料"] == "一口一个"


def test_annotated_categories_llm_failure_returns_null_notes(client, sample_trends, monkeypatch):
    monkeypatch.setattr("app.routers.trending.OPENROUTER_API_KEY", "test-key")

    with patch("app.routers.trending.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.side_effect = openai.OpenAIError("boom")

        resp = client.get("/api/trending/categories/annotated")

    assert resp.status_code == 200
    assert all(c["note"] is None for c in resp.json()["categories"])


def test_annotated_categories_without_api_key(client, sample_trends, monkeypatch):
    monkeypatch.setattr("app.routers.trending.OPENROUTER_API_KEY", "")

    with patch("app.routers.trending.OpenAI") as mock_openai:
        resp = client.get("/api/trending/categories/annotated")

    assert resp.status_code == 200
    assert all(c["note"] is None for c in resp.json()["categories"])
    mock_openai.assert_not_called()
