import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import openai

from app.models import Recipe, RecommendCache
from app.schemas import QuickRecommendedDish, RecommendedDish
from app.services.recommend_cache import make_cache_key


FULL_DISH = RecommendedDish(
    name="番茄炒蛋",
    summary="家常快手菜",
    ingredients=["番茄 2个", "鸡蛋 3个"],
    steps=["番茄切块", "鸡蛋炒熟"],
    difficulty="简单",
    cook_time="约10分钟",
)
QUICK_DISH = QuickRecommendedDish(
    name=FULL_DISH.name,
    summary=FULL_DISH.summary,
    difficulty=FULL_DISH.difficulty,
    cook_time=FULL_DISH.cook_time,
)


def _insert_full_cache(db):
    payload = {
        "dishes": [FULL_DISH.model_dump()],
        "input_ingredients": ["番茄", "鸡蛋"],
    }
    db.add(
        RecommendCache(
            cache_key=make_cache_key(["番茄", "鸡蛋"], 1),
            payload=json.dumps(payload, ensure_ascii=False),
            model="cached-model",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db.commit()


def _completion(content: str):
    return MagicMock(
        choices=[MagicMock(message=MagicMock(content=content))]
    )


def test_generate_quick_dishes_parses_fence_and_prompt_options():
    from app.routers import recommend_progressive

    content = json.dumps(
        {"dishes": [QUICK_DISH.model_dump()]}, ensure_ascii=False
    )
    with patch.object(recommend_progressive, "_client") as client_factory:
        create = client_factory.return_value.chat.completions.create
        create.return_value = _completion(f"```json\n{content}\n```")
        dishes = recommend_progressive.generate_quick_dishes_via_llm(
            ["番茄"],
            1,
            "清淡",
            True,
            ["番茄炒蛋"],
        )

    assert dishes == [QUICK_DISH]
    prompt = create.call_args.kwargs["messages"][1]["content"]
    assert "清淡" in prompt
    assert "额外购买" in prompt
    assert "番茄炒蛋" in prompt


def test_generate_steps_parses_complete_dish():
    from app.routers import recommend_progressive

    content = json.dumps(FULL_DISH.model_dump(), ensure_ascii=False)
    with patch.object(recommend_progressive, "_client") as client_factory:
        create = client_factory.return_value.chat.completions.create
        create.return_value = _completion(content)
        dish = recommend_progressive.generate_steps_via_llm(
            "番茄炒蛋", ["番茄", "鸡蛋"]
        )

    assert dish == FULL_DISH
    prompt = create.call_args.kwargs["messages"][1]["content"]
    assert "番茄炒蛋" in prompt
    assert "番茄, 鸡蛋" in prompt


def test_quick_returns_summary_only(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    with patch(
        "app.routers.recommend_progressive.generate_quick_dishes_via_llm",
        return_value=[QUICK_DISH],
    ):
        response = client.post(
            "/api/recommend/quick",
            json={"ingredients": ["番茄", "鸡蛋"], "count": 1},
        )

    assert response.status_code == 200
    assert response.json() == {
        "dishes": [QUICK_DISH.model_dump()],
        "input_ingredients": ["番茄", "鸡蛋"],
    }
    assert "steps" not in response.json()["dishes"][0]
    assert "ingredients" not in response.json()["dishes"][0]


def test_quick_cache_hit_crops_full_payload(client, db, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    _insert_full_cache(db)

    with patch(
        "app.routers.recommend_progressive.generate_quick_dishes_via_llm"
    ) as generate:
        response = client.post(
            "/api/recommend/quick",
            json={"ingredients": ["鸡蛋", "番茄"], "count": 1},
        )

    assert response.status_code == 200
    assert response.json()["dishes"] == [QUICK_DISH.model_dump()]
    assert response.json()["input_ingredients"] == ["鸡蛋", "番茄"]
    generate.assert_not_called()


def test_quick_llm_result_does_not_pollute_full_cache(
    client, db, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    with patch(
        "app.routers.recommend_progressive.generate_quick_dishes_via_llm",
        return_value=[QUICK_DISH],
    ):
        response = client.post(
            "/api/recommend/quick",
            json={"ingredients": ["番茄"], "count": 1},
        )

    assert response.status_code == 200
    assert db.query(RecommendCache).count() == 0


def test_quick_llm_error_returns_502(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    with patch(
        "app.routers.recommend_progressive.generate_quick_dishes_via_llm",
        side_effect=openai.OpenAIError("down"),
    ):
        response = client.post(
            "/api/recommend/quick",
            json={"ingredients": ["番茄"]},
        )

    assert response.status_code == 502


def test_quick_empty_ingredients_returns_422(client):
    response = client.post(
        "/api/recommend/quick", json={"ingredients": [], "count": 1}
    )
    assert response.status_code == 422


def test_quick_missing_ingredients_returns_422(client):
    response = client.post("/api/recommend/quick", json={"count": 1})
    assert response.status_code == 422


def test_quick_without_key_or_cache_returns_500(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", ""
    )
    response = client.post(
        "/api/recommend/quick", json={"ingredients": ["番茄"]}
    )
    assert response.status_code == 500


def test_steps_uses_full_cache_before_llm(client, db, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    _insert_full_cache(db)

    with patch(
        "app.routers.recommend_progressive.generate_steps_via_llm",
        return_value=FULL_DISH,
    ) as generate:
        response = client.post(
            "/api/recommend/steps",
            json={"dish_name": "番茄炒蛋", "ingredients": ["番茄", "鸡蛋"]},
        )

    assert response.status_code == 200
    assert response.json() == FULL_DISH.model_dump()
    generate.assert_not_called()


def test_steps_does_not_reuse_context_free_local_recipe(client, db, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    db.add(
        Recipe(
            name="番茄炒蛋",
            source_url="https://example.com/local-recipe",
            ingredients_text="番茄 鸡蛋",
            ingredients_json=json.dumps(
                [{"name": "番茄", "amount": "2个"}],
                ensure_ascii=False,
            ),
            steps_json=json.dumps(
                [{"text": "切番茄"}, {"text": "炒熟"}],
                ensure_ascii=False,
            ),
            rating=9.0,
        )
    )
    db.commit()

    with patch(
        "app.routers.recommend_progressive.generate_steps_via_llm",
        return_value=FULL_DISH,
    ) as generate:
        response = client.post(
            "/api/recommend/steps",
            json={"dish_name": "番茄炒蛋", "ingredients": ["番茄", "鸡蛋"]},
        )

    assert response.status_code == 200
    assert response.json() == FULL_DISH.model_dump()
    generate.assert_called_once()


def test_steps_ignores_invalid_cache_payload(client, db, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    db.add(
        RecommendCache(
            cache_key="invalid#c1",
            payload='{"name":"番茄炒蛋", invalid}',
            model="broken",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db.commit()
    with patch(
        "app.routers.recommend_progressive.generate_steps_via_llm",
        return_value=FULL_DISH,
    ) as generate:
        response = client.post(
            "/api/recommend/steps",
            json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
        )

    assert response.status_code == 200
    generate.assert_called_once()


def test_steps_llm_success(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    with patch(
        "app.routers.recommend_progressive.generate_steps_via_llm",
        return_value=FULL_DISH,
    ):
        response = client.post(
            "/api/recommend/steps",
            json={"dish_name": "番茄炒蛋", "ingredients": ["番茄", "鸡蛋"]},
        )

    assert response.status_code == 200
    assert response.json() == FULL_DISH.model_dump()


def test_steps_llm_error_returns_502(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    with patch(
        "app.routers.recommend_progressive.generate_steps_via_llm",
        side_effect=openai.OpenAIError("down"),
    ):
        response = client.post(
            "/api/recommend/steps",
            json={"dish_name": "未知菜", "ingredients": ["番茄"]},
        )

    assert response.status_code == 502


def test_steps_without_key_or_fallback_returns_500(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", ""
    )
    response = client.post(
        "/api/recommend/steps",
        json={"dish_name": "未知菜", "ingredients": ["番茄"]},
    )
    assert response.status_code == 500


def test_steps_missing_dish_name_returns_422(client):
    response = client.post(
        "/api/recommend/steps",
        json={"ingredients": ["番茄"]},
    )
    assert response.status_code == 422
