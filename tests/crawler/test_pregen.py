import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models import RecommendCache
from app.schemas import RecommendedDish


EXPECTED_PRESET_INGREDIENTS = [
    "番茄",
    "土豆",
    "白菜",
    "青椒",
    "黄瓜",
    "茄子",
    "西兰花",
    "胡萝卜",
    "菠菜",
    "洋葱",
    "蘑菇",
    "豆芽",
    "鸡胸肉",
    "猪肉",
    "牛肉",
    "排骨",
    "五花肉",
    "鸡翅",
    "鸡腿",
    "肉末",
    "虾",
    "鱼",
    "豆腐",
    "鸡蛋",
    "牛奶",
    "米饭",
    "面条",
    "馒头",
    "饺子皮",
    "面粉",
]


def _dish(name: str = "测试菜") -> RecommendedDish:
    return RecommendedDish(
        name=name,
        summary="测试推荐",
        ingredients=["番茄 1个"],
        steps=["处理食材", "炒熟"],
        difficulty="简单",
        cook_time="约10分钟",
    )


def test_preset_ingredients_match_frontend_pin():
    from app.crawler.pregen import PRESET_INGREDIENTS

    assert PRESET_INGREDIENTS == EXPECTED_PRESET_INGREDIENTS


def test_iter_preset_combos_contains_30_singles_and_435_pairs():
    from app.crawler.pregen import iter_preset_combos

    combos = list(iter_preset_combos())

    assert len(combos) == 465
    assert len({tuple(combo) for combo in combos}) == 465
    assert sum(len(combo) == 1 for combo in combos) == 30
    assert sum(len(combo) == 2 for combo in combos) == 435


def test_run_pregeneration_respects_attempt_budget(db):
    from app.crawler.pregen import run_pregeneration

    calls: list[tuple[str, ...]] = []

    def generate(ingredients, *args, **kwargs):
        calls.append(tuple(ingredients))
        return [_dish("-".join(ingredients))]

    with patch("app.crawler.pregen.generate_dishes_via_llm", generate):
        generated = run_pregeneration(db, budget=3)

    assert generated == 3
    assert len(calls) == 3
    assert db.query(RecommendCache).count() == 3


def test_run_pregeneration_skips_unexpired_cache(db):
    from app.crawler.pregen import run_pregeneration
    from app.routers.recommend import make_cache_key

    db.add(
        RecommendCache(
            cache_key=make_cache_key(["番茄"], 3),
            payload=json.dumps(
                {"dishes": [], "input_ingredients": ["番茄"]},
                ensure_ascii=False,
            ),
            model="existing-model",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db.commit()
    calls: list[list[str]] = []

    def generate(ingredients, *args, **kwargs):
        calls.append(ingredients)
        return [_dish()]

    with patch("app.crawler.pregen.generate_dishes_via_llm", generate):
        generated = run_pregeneration(db, budget=1)

    assert generated == 1
    assert calls == [["土豆"]]
    assert db.query(RecommendCache).count() == 2


def test_run_pregeneration_continues_after_failure_and_caps_attempts(db):
    from app.crawler.pregen import run_pregeneration

    calls = 0

    def generate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary model failure")
        return [_dish()]

    with patch("app.crawler.pregen.generate_dishes_via_llm", generate):
        generated = run_pregeneration(db, budget=2)

    assert calls == 2
    assert generated == 1
    assert db.query(RecommendCache).count() == 1


def test_run_pregeneration_stores_full_payload_with_staggered_ttl(db):
    from app.crawler.pregen import run_pregeneration

    before = datetime.now(timezone.utc).replace(tzinfo=None)
    with (
        patch(
            "app.crawler.pregen.generate_dishes_via_llm",
            return_value=[_dish("完整菜")],
        ),
        patch("app.crawler.pregen.random.uniform", return_value=12 * 3600),
    ):
        generated = run_pregeneration(db, budget=1)
    after = datetime.now(timezone.utc).replace(tzinfo=None)

    assert generated == 1
    cached = db.query(RecommendCache).one()
    payload = json.loads(cached.payload)
    assert payload["input_ingredients"] == ["番茄"]
    assert payload["dishes"][0]["name"] == "完整菜"
    assert payload["dishes"][0]["steps"] == ["处理食材", "炒熟"]
    assert before + timedelta(days=7, hours=12) <= cached.expires_at
    assert cached.expires_at <= after + timedelta(days=7, hours=12)


def test_pregeneration_job_has_overlap_guards(client):
    from app.main import scheduler

    job = scheduler.get_job("recommend_pregen")

    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.misfire_grace_time == 3600
    assert "hour='3'" in str(job.trigger)
    assert "minute='30'" in str(job.trigger)


def test_scheduled_pregeneration_swallows_job_errors():
    from app.crawler.scheduler import scheduled_pregeneration

    with (
        patch("app.crawler.scheduler.SessionLocal"),
        patch(
            "app.crawler.scheduler.run_pregeneration",
            side_effect=RuntimeError("job failed"),
        ),
    ):
        assert scheduled_pregeneration() == 0
