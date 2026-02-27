import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


def make_claude_response(dishes_json: str):
    """Create a mock Claude API response."""
    mock_message = MagicMock()
    mock_message.content = [MagicMock()]
    mock_message.content[0].text = dishes_json
    return mock_message


VALID_DISHES_JSON = json.dumps({
    "dishes": [
        {
            "name": "番茄炒蛋",
            "summary": "家常经典菜",
            "ingredients": ["番茄2个", "鸡蛋3个", "盐适量"],
            "steps": ["番茄切块", "鸡蛋打散", "炒熟"],
            "difficulty": "简单",
            "cook_time": "约10分钟",
        }
    ]
})


def test_recommend_no_api_key(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "")
    resp = client.post("/api/recommend", json={"ingredients": ["番茄"]})
    assert resp.status_code == 500
    assert "CLAUDE_API_KEY" in resp.json()["detail"]


def test_recommend_empty_ingredients(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    resp = client.post("/api/recommend", json={"ingredients": []})
    assert resp.status_code == 400
    assert "ingredient" in resp.json()["detail"].lower()


def test_recommend_success(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_DISHES_JSON)

        resp = client.post("/api/recommend", json={"ingredients": ["番茄", "鸡蛋"]})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["dishes"]) == 1
        assert data["dishes"][0]["name"] == "番茄炒蛋"
        assert data["input_ingredients"] == ["番茄", "鸡蛋"]


def test_recommend_count_clamped_to_max_5(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_DISHES_JSON)

        resp = client.post("/api/recommend", json={"ingredients": ["番茄"], "count": 10})
        assert resp.status_code == 200
        # Check that the prompt used count=5 (clamped)
        call_args = mock_client.messages.create.call_args
        messages_content = call_args[1]["messages"][0]["content"]
        assert "5" in messages_content


def test_recommend_count_clamped_to_min_1(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_DISHES_JSON)

        resp = client.post("/api/recommend", json={"ingredients": ["番茄"], "count": 0})
        assert resp.status_code == 200


def test_recommend_strips_markdown_fence(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        fenced = f"```json\n{VALID_DISHES_JSON}\n```"
        mock_client.messages.create.return_value = make_claude_response(fenced)

        resp = client.post("/api/recommend", json={"ingredients": ["番茄"]})
        assert resp.status_code == 200
        assert resp.json()["dishes"][0]["name"] == "番茄炒蛋"


def test_recommend_json_parse_fail_returns_502(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response("invalid json {{{")

        resp = client.post("/api/recommend", json={"ingredients": ["番茄"]})
        assert resp.status_code == 502


def test_recommend_api_error_returns_502(client, monkeypatch):
    import anthropic as anthropic_module
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = anthropic_module.APIError(
            message="API error", request=MagicMock(), body=None
        )

        resp = client.post("/api/recommend", json={"ingredients": ["番茄"]})
        assert resp.status_code == 502


def test_recommend_with_preferences(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_DISHES_JSON)

        resp = client.post(
            "/api/recommend", json={"ingredients": ["猪肉"], "preferences": "家常"}
        )
        assert resp.status_code == 200
        call_args = mock_client.messages.create.call_args
        messages_content = call_args[1]["messages"][0]["content"]
        assert "家常" in messages_content


def test_recommend_optional_dish_fields(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        # No difficulty or cook_time
        minimal_json = json.dumps({
            "dishes": [{"name": "炒饭", "summary": "快手菜", "ingredients": ["米饭"], "steps": ["炒"]}]
        })
        mock_client.messages.create.return_value = make_claude_response(minimal_json)

        resp = client.post("/api/recommend", json={"ingredients": ["米饭"]})
        assert resp.status_code == 200
        dish = resp.json()["dishes"][0]
        assert dish["difficulty"] is None
        assert dish["cook_time"] is None


def test_recommend_empty_dishes_list(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(json.dumps({"dishes": []}))

        resp = client.post("/api/recommend", json={"ingredients": ["未知食材"]})
        assert resp.status_code == 200
        assert resp.json()["dishes"] == []


def test_recommend_allow_extra_uses_extra_prompt(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_DISHES_JSON)

        resp = client.post(
            "/api/recommend",
            json={"ingredients": ["番茄"], "allow_extra": True},
        )
        assert resp.status_code == 200
        call_args = mock_client.messages.create.call_args
        system_prompt = call_args[1]["system"]
        from app.routers.recommend import SYSTEM_PROMPT_EXTRA
        assert system_prompt == SYSTEM_PROMPT_EXTRA


def test_recommend_default_uses_standard_prompt(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_DISHES_JSON)

        resp = client.post(
            "/api/recommend",
            json={"ingredients": ["番茄"]},
        )
        assert resp.status_code == 200
        call_args = mock_client.messages.create.call_args
        system_prompt = call_args[1]["system"]
        from app.routers.recommend import SYSTEM_PROMPT
        assert system_prompt == SYSTEM_PROMPT


def test_recommend_extra_ingredients_parsed(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        extra_json = json.dumps({
            "dishes": [
                {
                    "name": "番茄牛腩",
                    "summary": "经典炖菜",
                    "ingredients": ["番茄2个", "牛腩500g", "盐适量"],
                    "steps": ["牛腩焯水", "番茄切块", "炖煮1小时"],
                    "difficulty": "中等",
                    "cook_time": "约70分钟",
                    "extra_ingredients": ["牛腩"],
                }
            ]
        })
        mock_client.messages.create.return_value = make_claude_response(extra_json)

        resp = client.post(
            "/api/recommend",
            json={"ingredients": ["番茄"], "allow_extra": True},
        )
        assert resp.status_code == 200
        dish = resp.json()["dishes"][0]
        assert dish["extra_ingredients"] == ["牛腩"]


def test_recommend_exclude_dishes_in_prompt(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_DISHES_JSON)

        resp = client.post(
            "/api/recommend",
            json={
                "ingredients": ["番茄", "鸡蛋"],
                "exclude_dishes": ["番茄炒蛋", "蛋花汤"],
            },
        )
        assert resp.status_code == 200
        call_args = mock_client.messages.create.call_args
        user_content = call_args[1]["messages"][0]["content"]
        assert "番茄炒蛋" in user_content
        assert "蛋花汤" in user_content


# --- foods-by-category tests ---

VALID_FOODS_JSON = json.dumps({"foods": ["火锅", "串串香", "麻婆豆腐"]})


def test_foods_by_category_success(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_FOODS_JSON)

        resp = client.post("/api/foods-by-category", json={"category": "川菜"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["foods"] == ["火锅", "串串香", "麻婆豆腐"]
        assert data["category"] == "川菜"


def test_foods_by_category_no_api_key(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "")
    resp = client.post("/api/foods-by-category", json={"category": "川菜"})
    assert resp.status_code == 500
    assert "CLAUDE_API_KEY" in resp.json()["detail"]


def test_foods_by_category_json_parse_fail(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response("not valid json")

        resp = client.post("/api/foods-by-category", json={"category": "川菜"})
        assert resp.status_code == 502


def test_foods_by_category_api_error(client, monkeypatch):
    import anthropic as anthropic_module
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = anthropic_module.APIError(
            message="API error", request=MagicMock(), body=None
        )

        resp = client.post("/api/foods-by-category", json={"category": "川菜"})
        assert resp.status_code == 502


def test_foods_by_category_strips_markdown_fence(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        fenced = f"```json\n{VALID_FOODS_JSON}\n```"
        mock_client.messages.create.return_value = make_claude_response(fenced)

        resp = client.post("/api/foods-by-category", json={"category": "川菜"})
        assert resp.status_code == 200
        assert resp.json()["foods"] == ["火锅", "串串香", "麻婆豆腐"]


def test_foods_by_category_count_clamped(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_FOODS_JSON)

        # count=100 should be clamped to 50
        resp = client.post("/api/foods-by-category", json={"category": "川菜", "count": 100})
        assert resp.status_code == 200
        call_args = mock_client.messages.create.call_args
        user_content = call_args[1]["messages"][0]["content"]
        assert "50" in user_content

        # count=0 should be clamped to 1 (use different category to avoid cache hit)
        resp = client.post("/api/foods-by-category", json={"category": "粤菜", "count": 0})
        assert resp.status_code == 200
        call_args = mock_client.messages.create.call_args
        user_content = call_args[1]["messages"][0]["content"]
        assert "1" in user_content


# --- foods-by-category cache tests ---


def test_foods_by_category_cache_hit(client, db, monkeypatch):
    """When a valid (non-expired) cache exists, Claude API should NOT be called."""
    from app.models import FoodsCategoryCache

    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")

    # Pre-insert a cache row with future expires_at
    cache_entry = FoodsCategoryCache(
        category="川菜",
        foods=json.dumps(["回锅肉", "宫保鸡丁"], ensure_ascii=False),
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    db.add(cache_entry)
    db.commit()

    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client

        resp = client.post("/api/foods-by-category", json={"category": "川菜"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["foods"] == ["回锅肉", "宫保鸡丁"]
        assert data["category"] == "川菜"
        # Claude API should NOT have been called
        mock_client.messages.create.assert_not_called()


def test_foods_by_category_cache_miss(client, db, monkeypatch):
    """When no cache exists, Claude API should be called and result stored in cache."""
    from app.models import FoodsCategoryCache

    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")

    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_FOODS_JSON)

        resp = client.post("/api/foods-by-category", json={"category": "川菜"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["foods"] == ["火锅", "串串香", "麻婆豆腐"]
        # Claude API should have been called
        mock_client.messages.create.assert_called_once()

    # Verify cache was stored in DB
    cached = db.query(FoodsCategoryCache).filter(FoodsCategoryCache.category == "川菜").first()
    assert cached is not None
    assert json.loads(cached.foods) == ["火锅", "串串香", "麻婆豆腐"]
    # SQLite returns naive datetimes; compare naive-to-naive
    assert cached.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)


def test_foods_by_category_cache_expired(client, db, monkeypatch):
    """When cache is expired, Claude API should be called (expired cache ignored)."""
    from app.models import FoodsCategoryCache

    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")

    # Insert an expired cache row
    cache_entry = FoodsCategoryCache(
        category="川菜",
        foods=json.dumps(["old_food"], ensure_ascii=False),
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(cache_entry)
    db.commit()

    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_FOODS_JSON)

        resp = client.post("/api/foods-by-category", json={"category": "川菜"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["foods"] == ["火锅", "串串香", "麻婆豆腐"]
        # Claude API should have been called (expired cache ignored)
        mock_client.messages.create.assert_called_once()


# --- bulk-foods-by-category tests ---

VALID_BULK_FOODS_JSON = json.dumps({
    "家常下饭": ["红烧肉", "番茄炒蛋", "宫保鸡丁"],
    "火锅烫涮": ["四川火锅", "潮汕牛肉锅", "酸汤火锅"],
})


def test_bulk_all_cached(client, db, monkeypatch):
    """When all requested categories are cached, Claude API should NOT be called."""
    from app.models import FoodsCategoryCache

    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")

    for cat, foods in [("家常下饭", ["红烧肉", "番茄炒蛋"]), ("火锅烫涮", ["四川火锅", "酸汤火锅"])]:
        db.add(FoodsCategoryCache(
            category=cat,
            foods=json.dumps(foods, ensure_ascii=False),
            expires_at=datetime.now(timezone.utc) + timedelta(days=3),
        ))
    db.commit()

    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client

        resp = client.post("/api/bulk-foods-by-category", json={"categories": ["家常下饭", "火锅烫涮"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"]["家常下饭"] == ["红烧肉", "番茄炒蛋"]
        assert data["results"]["火锅烫涮"] == ["四川火锅", "酸汤火锅"]
        mock_client.messages.create.assert_not_called()


def test_bulk_none_cached(client, monkeypatch):
    """When no categories are cached, Claude API should be called once."""
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(VALID_BULK_FOODS_JSON)

        resp = client.post("/api/bulk-foods-by-category", json={"categories": ["家常下饭", "火锅烫涮"]})
        assert resp.status_code == 200
        data = resp.json()
        assert "家常下饭" in data["results"]
        assert "火锅烫涮" in data["results"]
        mock_client.messages.create.assert_called_once()


def test_bulk_partial_cache(client, db, monkeypatch):
    """When some categories are cached and some are not, only uncached are sent to Claude."""
    from app.models import FoodsCategoryCache

    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")

    # Cache only 家常下饭
    db.add(FoodsCategoryCache(
        category="家常下饭",
        foods=json.dumps(["回锅肉"], ensure_ascii=False),
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    ))
    db.commit()

    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        # Claude returns only the uncached category
        uncached_json = json.dumps({"火锅烫涮": ["四川火锅", "潮汕牛肉锅"]})
        mock_client.messages.create.return_value = make_claude_response(uncached_json)

        resp = client.post("/api/bulk-foods-by-category", json={"categories": ["家常下饭", "火锅烫涮"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"]["家常下饭"] == ["回锅肉"]
        assert data["results"]["火锅烫涮"] == ["四川火锅", "潮汕牛肉锅"]
        mock_client.messages.create.assert_called_once()
        # Verify the prompt only contains uncached category
        call_args = mock_client.messages.create.call_args
        user_content = call_args[1]["messages"][0]["content"]
        assert "火锅烫涮" in user_content
        assert "家常下饭" not in user_content


def test_bulk_no_api_key(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "")
    resp = client.post("/api/bulk-foods-by-category", json={"categories": ["家常下饭"]})
    assert resp.status_code == 500
    assert "CLAUDE_API_KEY" in resp.json()["detail"]


def test_bulk_claude_error(client, monkeypatch):
    import anthropic as anthropic_module
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = anthropic_module.APIError(
            message="API error", request=MagicMock(), body=None
        )
        resp = client.post("/api/bulk-foods-by-category", json={"categories": ["家常下饭"]})
        assert resp.status_code == 502


def test_bulk_json_parse_error(client, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    with patch("app.routers.recommend.anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response("not valid json {{{")
        resp = client.post("/api/bulk-foods-by-category", json={"categories": ["家常下饭"]})
        assert resp.status_code == 502


def test_bulk_empty_categories(client, monkeypatch):
    """Empty categories list should return empty results without calling Claude."""
    monkeypatch.setattr("app.routers.recommend.CLAUDE_API_KEY", "test-key")
    resp = client.post("/api/bulk-foods-by-category", json={"categories": []})
    assert resp.status_code == 200
    assert resp.json()["results"] == {}
