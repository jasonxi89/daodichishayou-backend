import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import openai
import pytest
from pydantic import ValidationError

from app.models import Recipe, RecommendCache
from app.schemas import RecommendedDish
from app.services.recommend_cache import make_cache_key


SAFE_DISH = RecommendedDish(
    name="清炒豆腐",
    summary="清淡家常菜",
    ingredients=["豆腐 1块"],
    steps=["切块", "炒熟"],
)


def _cache(db, ingredients, dish):
    db.add(
        RecommendCache(
            cache_key=make_cache_key(ingredients, 1),
            payload=json.dumps(
                {
                    "dishes": [dish.model_dump()],
                    "input_ingredients": ingredients,
                },
                ensure_ascii=False,
            ),
            model="test-model",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db.commit()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "   "),
        ("summary", ""),
        ("ingredients", []),
        ("ingredients", ["   "]),
        ("steps", []),
        ("steps", [""]),
    ],
)
def test_complete_dish_rejects_blank_or_empty_fields(field, value):
    payload = SAFE_DISH.model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        RecommendedDish.model_validate(payload)


def test_cache_keys_do_not_collide_on_delimiters_or_whitespace():
    assert make_cache_key(["a|b"], 1) != make_cache_key(["a", "b"], 1)
    assert make_cache_key(["ice cream"], 1) != make_cache_key(["icecream"], 1)
    assert make_cache_key(["番茄", "番茄"], 1) == make_cache_key(["番茄"], 1)


def test_recommend_rejects_effectively_blank_and_unbounded_ingredients(client):
    assert client.post(
        "/api/recommend", json={"ingredients": ["   "]}
    ).status_code == 422
    assert client.post(
        "/api/recommend", json={"ingredients": ["x"] * 21}
    ).status_code == 422


def test_steps_rejects_blank_and_wildcard_dish_names(client):
    for dish_name in ("", "   ", "%", "_"):
        response = client.post(
            "/api/recommend/steps",
            json={"dish_name": dish_name, "ingredients": ["番茄"]},
        )
        assert response.status_code == 422


def test_preferences_never_use_generic_offline_fallback(client, db, monkeypatch):
    unsafe = SAFE_DISH.model_copy(
        update={
            "name": "花生肉末豆腐",
            "ingredients": ["豆腐", "猪肉", "花生"],
        }
    )
    _cache(db, ["豆腐"], unsafe)
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "")

    response = client.post(
        "/api/recommend",
        json={
            "ingredients": ["豆腐"],
            "preferences": "纯素且花生过敏",
            "count": 1,
        },
    )

    assert response.status_code >= 500


def test_steps_cache_must_match_exact_ingredient_context(client, db, monkeypatch):
    chicken = SAFE_DISH.model_copy(
        update={"name": "咖喱", "ingredients": ["鸡肉"]}
    )
    shrimp = SAFE_DISH.model_copy(
        update={"name": "咖喱", "ingredients": ["虾"]}
    )
    _cache(db, ["鸡肉"], chicken)
    _cache(db, ["虾"], shrimp)
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )

    with patch(
        "app.routers.recommend_progressive.generate_steps_via_llm"
    ) as generate:
        response = client.post(
            "/api/recommend/steps",
            json={"dish_name": "咖喱", "ingredients": ["鸡肉"]},
        )

    assert response.status_code == 200
    assert response.json()["ingredients"] == ["鸡肉"]
    generate.assert_not_called()


def test_empty_llm_result_uses_offline_fallback_and_is_not_cached(
    client, db, monkeypatch
):
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "test-key")
    db.add(
        Recipe(
            name="番茄炒蛋",
            source_url="https://example.com/complete",
            ingredients_text="番茄 鸡蛋",
            ingredients_json='[{"name":"番茄"}]',
            steps_json='[{"text":"炒熟"}]',
        )
    )
    db.commit()

    with patch(
        "app.routers.recommend.generate_dishes_via_llm",
        return_value=[],
    ):
        response = client.post(
            "/api/recommend",
            json={"ingredients": ["番茄"], "preferences": "家常"},
        )

    assert response.status_code >= 500
    assert db.query(RecommendCache).count() == 0


def test_legacy_endpoint_never_returns_incomplete_local_steps(
    client, db, monkeypatch
):
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "")
    db.add(
        Recipe(
            name="番茄拌饭",
            source_url="https://example.com/incomplete-legacy",
            ingredients_text="番茄 米饭",
            ingredients_json='[{"name":"番茄"}]',
            steps_json=None,
        )
    )
    db.commit()

    response = client.post(
        "/api/recommend",
        json={"ingredients": ["番茄"], "count": 1},
    )

    assert response.status_code >= 500


def test_fallback_ignores_invalid_cache_and_handles_malformed_local_json(db):
    from app.services.recommend_fallback import get_fallback_recommendation

    db.add(
        RecommendCache(
            cache_key="broken",
            payload="not-json",
            model="broken",
            expires_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        Recipe(
            name="简易番茄",
            source_url="https://example.com/malformed",
            ingredients_text="番茄",
            ingredients_json="not-json",
            steps_json="not-json",
        )
    )
    db.commit()

    response = get_fallback_recommendation(db, ["番茄"], 1)

    assert response is None


def test_local_search_skips_invalid_higher_rated_rows(db):
    from app.services.recipe_search import search_local_recipes
    from app.services.recommend_fallback import get_fallback_recommendation

    invalid_rows = [
        ("missing", None, None),
        ("malformed", "not-json", '[{"text":"炒熟"}]'),
        ("non-list", "{}", "{}"),
        ("empty", "[]", "[]"),
        ("missing-name", '[{"amount":"2个"}]', '[{"text":"炒熟"}]'),
        ("blank-name", '[{"name":"","amount":"2个"}]', '[{"text":"炒熟"}]'),
        ("missing-step", '[{"name":"番茄"}]', '[{"image":"x"}]'),
    ]
    invalid_rows.extend(
        (
            f"batch-{index}",
            '[{"amount":"2个"}]',
            '[{"text":"炒熟"}]',
        )
        for index in range(55)
    )
    for index, (suffix, ingredients_json, steps_json) in enumerate(invalid_rows):
        db.add(
            Recipe(
                name=f"坏番茄-{suffix}",
                source_url=f"https://example.com/invalid-{suffix}",
                ingredients_text="番茄",
                ingredients_json=ingredients_json,
                steps_json=steps_json,
                rating=100 - index,
            )
        )
    db.add(
        Recipe(
            name="安全番茄",
            source_url="https://example.com/valid-lower-rated",
            ingredients_text="番茄",
            ingredients_json='[{"name":"番茄","amount":"2个"}]',
            steps_json='[{"text":"炒熟"}]',
            rating=1,
        )
    )
    db.commit()

    assert [dish.name for dish in search_local_recipes(db, ["番茄"], 1)] == [
        "安全番茄"
    ]
    fallback = get_fallback_recommendation(db, ["番茄"], 1)
    assert fallback is not None
    assert [dish.name for dish in fallback.dishes] == ["安全番茄"]


def test_fallback_rejects_empty_input_and_honors_exclusions(db):
    from app.services.recommend_fallback import get_fallback_recommendation

    assert get_fallback_recommendation(db, [], 1) is None
    db.add(
        Recipe(
            name="番茄炒蛋",
            source_url="https://example.com/excluded",
            ingredients_text="番茄",
        )
    )
    db.commit()
    assert get_fallback_recommendation(
        db,
        ["番茄"],
        1,
        ["番茄炒蛋"],
    ) is None


def test_primary_and_fast_failure_do_not_bypass_preferences(
    client, db, monkeypatch
):
    _cache(db, ["豆腐"], SAFE_DISH)
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_FAST_MODEL", "fast")
    with patch(
        "app.routers.recommend.generate_dishes_via_llm",
        side_effect=openai.OpenAIError("down"),
    ):
        response = client.post(
            "/api/recommend",
            json={"ingredients": ["豆腐"], "preferences": "严格纯素"},
        )

    assert response.status_code == 502
