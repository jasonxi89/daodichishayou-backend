import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import openai

from app.models import Recipe, RecommendCache
from app.schemas import QuickRecommendedDish, RecommendedDish
from app.services.recommend_cache import make_cache_key


FULL_DISHES = [
    RecommendedDish(
        name="番茄炒蛋",
        summary="家常快手菜",
        ingredients=["番茄 2个", "鸡蛋 3个"],
        steps=["切块", "炒熟"],
    ),
    RecommendedDish(
        name="番茄蛋汤",
        summary="清爽热汤",
        ingredients=["番茄 1个", "鸡蛋 2个"],
        steps=["煮汤"],
    ),
]


def _insert_old_cache(db):
    db.add(
        RecommendCache(
            cache_key=make_cache_key(["番茄", "鸡蛋"], 2),
            payload=json.dumps(
                {
                    "dishes": [dish.model_dump() for dish in FULL_DISHES],
                    "input_ingredients": ["番茄", "鸡蛋"],
                },
                ensure_ascii=False,
            ),
            model="old-model",
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
            expires_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
    )
    db.commit()


def test_recommend_without_key_uses_old_cache(client, db, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "")
    _insert_old_cache(db)

    response = client.post(
        "/api/recommend",
        json={"ingredients": ["番茄"], "count": 2},
    )

    assert response.status_code == 200
    assert [dish["name"] for dish in response.json()["dishes"]] == [
        "番茄炒蛋",
        "番茄蛋汤",
    ]
    assert response.json()["input_ingredients"] == ["番茄"]


def test_recommend_old_cache_filters_excluded_dishes(
    client, db, monkeypatch
):
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "")
    _insert_old_cache(db)

    response = client.post(
        "/api/recommend",
        json={
            "ingredients": ["番茄"],
            "count": 2,
            "exclude_dishes": ["番茄炒蛋"],
        },
    )

    assert response.status_code == 200
    assert [dish["name"] for dish in response.json()["dishes"]] == [
        "番茄蛋汤"
    ]


def test_legacy_recommend_without_key_rejects_incomplete_local_recipe(
    client, db, monkeypatch
):
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "")
    db.add(
        Recipe(
            name="番茄拌饭",
            source_url="https://example.com/incomplete",
            ingredients_text="番茄 米饭",
            ingredients_json=json.dumps(
                [{"name": "番茄", "amount": "1个"}],
                ensure_ascii=False,
            ),
            steps_json=None,
            rating=8.8,
        )
    )
    db.commit()

    response = client.post(
        "/api/recommend",
        json={"ingredients": ["番茄"], "count": 1},
    )

    assert response.status_code == 500


def test_recommend_primary_failure_retries_configured_fast_model(
    client, monkeypatch
):
    from app.routers import recommend

    monkeypatch.setattr(recommend, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(recommend, "OPENROUTER_FAST_MODEL", "fast-model")
    calls: list[str | None] = []

    def generate(*args, model=None, **kwargs):
        calls.append(model)
        if model is None:
            raise openai.OpenAIError("primary down")
        return [FULL_DISHES[0]]

    with patch.object(recommend, "generate_dishes_via_llm", generate):
        response = client.post(
            "/api/recommend",
            json={
                "ingredients": ["番茄"],
                "preferences": "绕过通用缓存",
                "count": 1,
            },
        )

    assert response.status_code == 200
    assert calls == [None, "fast-model"]
    assert response.json()["dishes"][0]["name"] == "番茄炒蛋"


def test_quick_without_key_uses_old_cache(client, db, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", ""
    )
    _insert_old_cache(db)

    response = client.post(
        "/api/recommend/quick",
        json={"ingredients": ["番茄"], "count": 2},
    )

    assert response.status_code == 200
    assert response.json()["dishes"] == [
        QuickRecommendedDish(
            name=dish.name,
            summary=dish.summary,
            difficulty=dish.difficulty,
            cook_time=dish.cook_time,
        ).model_dump()
        for dish in FULL_DISHES
    ]


def test_quick_primary_failure_retries_fast_model(client, monkeypatch):
    from app.routers import recommend_progressive

    monkeypatch.setattr(
        recommend_progressive, "OPENROUTER_API_KEY", "test-key"
    )
    monkeypatch.setattr(
        recommend_progressive, "OPENROUTER_FAST_MODEL", "fast-model"
    )
    calls: list[str | None] = []

    def generate(*args, model=None, **kwargs):
        calls.append(model)
        if model is None:
            raise openai.OpenAIError("primary down")
        return [
            QuickRecommendedDish(
                name="番茄炒蛋",
                summary="家常快手菜",
            )
        ]

    with patch.object(
        recommend_progressive,
        "generate_quick_dishes_via_llm",
        generate,
    ):
        response = client.post(
            "/api/recommend/quick",
            json={"ingredients": ["番茄"], "count": 1},
        )

    assert response.status_code == 200
    assert calls == [None, "fast-model"]


def test_recommend_without_key_and_without_fallback_returns_5xx(
    client, monkeypatch
):
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "")

    response = client.post(
        "/api/recommend",
        json={"ingredients": ["不存在的测试食材"]},
    )

    assert response.status_code >= 500
