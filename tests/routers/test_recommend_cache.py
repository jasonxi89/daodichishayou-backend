import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError


CACHED_RESPONSE = {
    "dishes": [
        {
            "name": "番茄炒蛋",
            "summary": "家常快手菜",
            "ingredients": ["番茄 2个", "鸡蛋 3个"],
            "steps": ["番茄切块", "鸡蛋炒熟"],
            "difficulty": "简单",
            "cook_time": "约10分钟",
            "extra_ingredients": None,
        }
    ],
    "input_ingredients": ["番茄", "鸡蛋"],
}


def _insert_cache(db, *, expires_at: datetime, count: int = 1):
    from app.models import RecommendCache
    from app.routers.recommend import make_cache_key

    row = RecommendCache(
        cache_key=make_cache_key(["番茄", "鸡蛋"], count),
        payload=json.dumps(CACHED_RESPONSE, ensure_ascii=False),
        model="cached-model",
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    return row


def _mock_openai_response():
    content = json.dumps(CACHED_RESPONSE, ensure_ascii=False)
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


def test_recommend_cache_model_enforces_unique_key(db):
    from app.models import RecommendCache

    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db.add(
        RecommendCache(
            cache_key="番茄#c1",
            payload="{}",
            model="model-a",
            expires_at=expires_at,
        )
    )
    db.commit()

    db.add(
        RecommendCache(
            cache_key="番茄#c1",
            payload="{}",
            model="model-b",
            expires_at=expires_at,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_make_cache_key_normalizes_ingredients_and_includes_count():
    from app.routers.recommend import make_cache_key

    assert make_cache_key(["番茄", "鸡蛋"], 3) == make_cache_key(
        ["鸡蛋", " 番 茄 "], 3
    )
    assert make_cache_key(["番茄", "鸡蛋"], 3) != make_cache_key(
        ["番茄", "鸡蛋"], 5
    )
    assert make_cache_key([" TOMATO ", "Egg"], 3) == "egg|tomato#c3"


def test_recommend_cache_hit_skips_local_and_llm(client, db, monkeypatch):
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "test-key")
    _insert_cache(
        db,
        count=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    with (
        patch("app.routers.recommend._search_local_recipes") as local_search,
        patch("app.routers.recommend.OpenAI") as openai_client,
    ):
        response = client.post(
            "/api/recommend",
            json={"ingredients": ["鸡蛋", " 番茄 "], "count": 1},
        )

    assert response.status_code == 200
    assert response.json()["dishes"] == CACHED_RESPONSE["dishes"]
    assert response.json()["input_ingredients"] == ["鸡蛋", " 番茄 "]
    local_search.assert_not_called()
    openai_client.assert_not_called()


def test_recommend_expired_cache_is_refreshed(client, db, monkeypatch):
    from app.models import RecommendCache
    from app.routers.recommend import make_cache_key

    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "test-key")
    expired = _insert_cache(
        db,
        count=1,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    old_expiry = expired.expires_at

    with patch("app.routers.recommend.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = (
            _mock_openai_response()
        )
        response = client.post(
            "/api/recommend",
            json={"ingredients": ["番茄", "鸡蛋"], "count": 1},
        )

    assert response.status_code == 200
    mock_openai.return_value.chat.completions.create.assert_called_once()
    refreshed = (
        db.query(RecommendCache)
        .filter(
            RecommendCache.cache_key
            == make_cache_key(["番茄", "鸡蛋"], 1)
        )
        .one()
    )
    assert refreshed.id == expired.id
    assert refreshed.expires_at > old_expiry
    assert json.loads(refreshed.payload) == CACHED_RESPONSE


def test_recommend_cache_miss_stores_full_response(client, db, monkeypatch):
    from app.models import RecommendCache
    from app.routers.recommend import make_cache_key

    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "test-key")
    with patch("app.routers.recommend.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = (
            _mock_openai_response()
        )
        response = client.post(
            "/api/recommend",
            json={"ingredients": ["番茄", "鸡蛋"], "count": 1},
        )

    assert response.status_code == 200
    cached = (
        db.query(RecommendCache)
        .filter(
            RecommendCache.cache_key
            == make_cache_key(["番茄", "鸡蛋"], 1)
        )
        .one()
    )
    assert cached.model
    assert cached.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)
    assert json.loads(cached.payload) == response.json()


@pytest.mark.parametrize(
    "request_overrides",
    [
        {"preferences": "清淡"},
        {"exclude_dishes": ["番茄炒蛋"]},
        {"allow_extra": True},
    ],
)
def test_recommend_cache_is_bypassed_for_custom_requests(
    client, db, monkeypatch, request_overrides
):
    monkeypatch.setattr("app.routers.recommend.OPENROUTER_API_KEY", "test-key")
    _insert_cache(
        db,
        count=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    with patch("app.routers.recommend.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = (
            _mock_openai_response()
        )
        response = client.post(
            "/api/recommend",
            json={
                "ingredients": ["番茄", "鸡蛋"],
                "count": 1,
                **request_overrides,
            },
        )

    assert response.status_code == 200
    mock_openai.return_value.chat.completions.create.assert_called_once()
