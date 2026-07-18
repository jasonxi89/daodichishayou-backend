import asyncio
import time

import pytest

from app.schemas import (
    BulkGenerateFoodsRequest,
    GenerateFoodsRequest,
    IngredientRecommendRequest,
    RecommendedDish,
)


def _slow_result(events: list[str], result):
    events.append("llm-start")
    time.sleep(0.2)
    events.append("llm-end")
    return result


async def _probe_event_loop(events: list[str]) -> None:
    await asyncio.sleep(0.02)
    events.append("health")


@pytest.mark.asyncio
async def test_recommend_llm_does_not_block_event_loop(db, monkeypatch):
    from app.routers import recommend

    events: list[str] = []
    dish = RecommendedDish(
        name="番茄炒蛋",
        summary="家常菜",
        ingredients=["番茄", "鸡蛋"],
        steps=["炒熟"],
    )
    monkeypatch.setattr(recommend, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        recommend,
        "generate_dishes_via_llm",
        lambda *args, **kwargs: _slow_result(events, [dish]),
    )

    request = IngredientRecommendRequest(
        ingredients=["番茄"],
        preferences="绕过通用缓存",
        count=1,
    )
    response, _ = await asyncio.gather(
        recommend.recommend_by_ingredients(request, db),
        _probe_event_loop(events),
    )

    assert response.dishes == [dish]
    assert events.index("health") < events.index("llm-end")


@pytest.mark.asyncio
async def test_category_llm_does_not_block_event_loop(db, monkeypatch):
    from app.routers import recommend

    events: list[str] = []
    monkeypatch.setattr(recommend, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        recommend,
        "generate_foods_by_category_via_llm",
        lambda *args, **kwargs: _slow_result(events, ["火锅"]),
    )

    response, _ = await asyncio.gather(
        recommend.foods_by_category(
            GenerateFoodsRequest(category="并发测试分类", count=1), db
        ),
        _probe_event_loop(events),
    )

    assert response.foods == ["火锅"]
    assert events.index("health") < events.index("llm-end")


@pytest.mark.asyncio
async def test_bulk_category_llm_does_not_block_event_loop(db, monkeypatch):
    from app.routers import recommend

    events: list[str] = []
    monkeypatch.setattr(recommend, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        recommend,
        "generate_bulk_foods_by_category_via_llm",
        lambda *args, **kwargs: _slow_result(
            events, {"并发测试分类": ["火锅"]}
        ),
    )

    response, _ = await asyncio.gather(
        recommend.bulk_foods_by_category(
            BulkGenerateFoodsRequest(categories=["并发测试分类"], count=1), db
        ),
        _probe_event_loop(events),
    )

    assert response.results == {"并发测试分类": ["火锅"]}
    assert events.index("health") < events.index("llm-end")
