"""菜谱步骤补全（LLM 补写 + 真实补爬）的行为测试。

数据优先级：scraped > llm > 空；反向覆盖禁止。
"""
import json

import pytest

from app.models import Recipe
from app.schemas import RecommendedDish


def _add_recipe(
    db,
    name: str,
    url: str,
    steps_json: str | None = None,
    steps_source: str | None = None,
    ingredients_text: str | None = "鸡蛋 番茄",
):
    recipe = Recipe(
        name=name,
        source_url=url,
        ingredients_json=json.dumps(
            [{"name": "鸡蛋"}, {"name": "番茄"}], ensure_ascii=False
        ),
        ingredients_text=ingredients_text,
        steps_json=steps_json,
        steps_source=steps_source,
    )
    db.add(recipe)
    db.commit()
    return recipe


def _fake_dish() -> RecommendedDish:
    return RecommendedDish(
        name="番茄炒蛋",
        summary="家常快手",
        ingredients=["鸡蛋 2个", "番茄 1个"],
        steps=["打蛋", "炒番茄", "混合出锅"],
    )


DETAIL_HTML_WITH_STEPS = """
<html><body>
<div class="steps"><ol>
<li><p class="text">洗菜切块</p></li>
<li><p class="text">热锅翻炒</p></li>
<li><p class="text">调味出锅</p></li>
</ol></div>
</body></html>
"""

CAPTCHA_HTML = "<html>aliyun captcha verify</html>"

EMPTY_HTML = "<html><body>nothing here</body></html>"


# --- LLM 补写 ---


def test_llm_backfill_fills_null_steps_and_marks_source(db):
    from app.crawler.steps_backfill import backfill_steps_via_llm

    target = _add_recipe(db, "菜A", "https://x/1/")
    stats = backfill_steps_via_llm(
        db, generate=lambda name, ings: _fake_dish(), sleep_seconds=0
    )

    db.refresh(target)
    assert stats["updated"] == 1
    steps = json.loads(target.steps_json)
    assert [s["text"] for s in steps] == ["打蛋", "炒番茄", "混合出锅"]
    assert target.steps_source == "llm"


def test_llm_backfill_never_touches_existing_steps(db):
    from app.crawler.steps_backfill import backfill_steps_via_llm

    scraped = _add_recipe(
        db, "菜B", "https://x/2/",
        steps_json=json.dumps([{"text": "真实步骤"}], ensure_ascii=False),
        steps_source="scraped",
    )
    calls = []

    def generate(name, ings):
        calls.append(name)
        return _fake_dish()

    stats = backfill_steps_via_llm(db, generate=generate, sleep_seconds=0)

    db.refresh(scraped)
    assert calls == []
    assert stats["updated"] == 0
    assert json.loads(scraped.steps_json) == [{"text": "真实步骤"}]
    assert scraped.steps_source == "scraped"


def test_llm_backfill_is_resumable(db):
    from app.crawler.steps_backfill import backfill_steps_via_llm

    _add_recipe(db, "菜C", "https://x/3/")
    _add_recipe(db, "菜D", "https://x/4/")

    first = backfill_steps_via_llm(
        db, generate=lambda n, i: _fake_dish(), sleep_seconds=0
    )
    second = backfill_steps_via_llm(
        db, generate=lambda n, i: _fake_dish(), sleep_seconds=0
    )
    assert first["updated"] == 2
    assert second["processed"] == 0


def test_llm_backfill_circuit_breaks_after_consecutive_failures(db):
    from app.crawler.steps_backfill import backfill_steps_via_llm

    for i in range(8):
        _add_recipe(db, f"菜{i}", f"https://x/f{i}/")

    def generate(name, ings):
        raise RuntimeError("llm down")

    stats = backfill_steps_via_llm(
        db,
        generate=generate,
        sleep_seconds=0,
        max_consecutive_failures=5,
    )
    assert stats["circuit_broken"] is True
    assert stats["failed"] == 5
    assert stats["updated"] == 0


def test_llm_backfill_dry_run_writes_nothing(db):
    from app.crawler.steps_backfill import backfill_steps_via_llm

    target = _add_recipe(db, "菜E", "https://x/5/")
    stats = backfill_steps_via_llm(
        db, generate=lambda n, i: _fake_dish(), dry_run=True, sleep_seconds=0
    )
    db.refresh(target)
    assert stats["processed"] == 1
    assert stats["updated"] == 0
    assert target.steps_json is None


# --- 真实补爬 ---


def test_scrape_backfill_upgrades_llm_but_skips_scraped(db):
    from app.crawler.steps_backfill import backfill_steps_via_scrape

    empty = _add_recipe(
        db, "菜F", "https://www.xiachufang.com/recipe/1/"
    )
    llm_filled = _add_recipe(
        db, "菜G", "https://www.xiachufang.com/recipe/2/",
        steps_json=json.dumps([{"text": "AI步骤"}], ensure_ascii=False),
        steps_source="llm",
    )
    scraped = _add_recipe(
        db, "菜H", "https://www.xiachufang.com/recipe/3/",
        steps_json=json.dumps([{"text": "真实步骤"}], ensure_ascii=False),
        steps_source="scraped",
    )
    fetched = []

    def fetch(url):
        fetched.append(url)
        return DETAIL_HTML_WITH_STEPS

    stats = backfill_steps_via_scrape(db, fetch=fetch, sleep_seconds=0)

    db.refresh(empty)
    db.refresh(llm_filled)
    db.refresh(scraped)
    assert stats["updated"] == 2
    assert empty.steps_source == "scraped"
    assert llm_filled.steps_source == "scraped"
    assert [s["text"] for s in json.loads(llm_filled.steps_json)] == [
        "洗菜切块", "热锅翻炒", "调味出锅",
    ]
    assert json.loads(scraped.steps_json) == [{"text": "真实步骤"}]
    assert scraped.source_url not in fetched


def test_scrape_backfill_stops_immediately_on_captcha(db):
    from app.crawler.steps_backfill import backfill_steps_via_scrape

    a = _add_recipe(db, "菜I", "https://www.xiachufang.com/recipe/4/")
    _add_recipe(db, "菜J", "https://www.xiachufang.com/recipe/5/")

    calls = []

    def fetch(url):
        calls.append(url)
        return CAPTCHA_HTML

    stats = backfill_steps_via_scrape(db, fetch=fetch, sleep_seconds=0)

    db.refresh(a)
    assert stats["captcha"] is True
    assert stats["updated"] == 0
    assert len(calls) == 1, "触发 CAPTCHA 后必须立即停，不再请求后续行"
    assert a.steps_json is None


def test_scrape_backfill_counts_pages_without_steps_as_failures(db):
    from app.crawler.steps_backfill import backfill_steps_via_scrape

    for i in range(7):
        _add_recipe(
            db, f"菜K{i}", f"https://www.xiachufang.com/recipe/k{i}/"
        )

    stats = backfill_steps_via_scrape(
        db,
        fetch=lambda url: EMPTY_HTML,
        sleep_seconds=0,
        max_consecutive_failures=5,
    )
    assert stats["circuit_broken"] is True
    assert stats["failed"] == 5
    assert stats["updated"] == 0


def test_scrape_backfill_respects_limit(db):
    from app.crawler.steps_backfill import backfill_steps_via_scrape

    for i in range(4):
        _add_recipe(
            db, f"菜L{i}", f"https://www.xiachufang.com/recipe/l{i}/"
        )

    stats = backfill_steps_via_scrape(
        db, fetch=lambda url: DETAIL_HTML_WITH_STEPS,
        limit=2, sleep_seconds=0,
    )
    assert stats["updated"] == 2
    assert stats["processed"] == 2


# --- 覆盖率补齐：默认依赖、limit、dry-run、空步骤、sleep 分支 ---


def test_ingredient_names_fall_back_to_text_when_json_invalid(db):
    from app.crawler.steps_backfill import _recipe_ingredient_names

    recipe = Recipe(
        name="X", source_url="https://x/badjson/",
        ingredients_json="not-json", ingredients_text="A B C",
    )
    assert _recipe_ingredient_names(recipe) == ["A", "B", "C"]

    empty = Recipe(name="Y", source_url="https://x/empty/")
    assert _recipe_ingredient_names(empty) == []


def test_llm_backfill_default_generate_calls_progressive(db, monkeypatch):
    from app.crawler import steps_backfill

    target = _add_recipe(db, "M1", "https://x/m1/")
    monkeypatch.setattr(
        "app.routers.recommend_progressive.generate_steps_via_llm",
        lambda name, ings: _fake_dish(),
    )
    stats = steps_backfill.backfill_steps_via_llm(db, sleep_seconds=0)
    db.refresh(target)
    assert stats["updated"] == 1
    assert target.steps_source == "llm"


def test_llm_backfill_respects_limit(db):
    from app.crawler.steps_backfill import backfill_steps_via_llm

    for i in range(3):
        _add_recipe(db, f"N{i}", f"https://x/n{i}/")
    stats = backfill_steps_via_llm(
        db, generate=lambda n, i: _fake_dish(), limit=2, sleep_seconds=0
    )
    assert stats["processed"] == 2


def test_llm_backfill_empty_steps_counts_as_failure(db):
    from types import SimpleNamespace

    from app.crawler.steps_backfill import backfill_steps_via_llm

    target = _add_recipe(db, "O1", "https://x/o1/")
    stats = backfill_steps_via_llm(
        db,
        generate=lambda n, i: SimpleNamespace(steps=[]),
        sleep_seconds=0,
    )
    db.refresh(target)
    assert stats["failed"] == 1
    assert target.steps_json is None


def test_llm_backfill_sleeps_between_rows(db, monkeypatch):
    from app.crawler import steps_backfill

    slept = []
    monkeypatch.setattr(
        steps_backfill.time, "sleep", lambda s: slept.append(s)
    )
    _add_recipe(db, "P1", "https://x/p1/")
    steps_backfill.backfill_steps_via_llm(
        db, generate=lambda n, i: _fake_dish(), sleep_seconds=0.5
    )
    assert slept == [0.5]


def test_scrape_backfill_dry_run_writes_nothing(db):
    from app.crawler.steps_backfill import backfill_steps_via_scrape

    target = _add_recipe(
        db, "Q1", "https://www.xiachufang.com/recipe/q1/"
    )
    stats = backfill_steps_via_scrape(
        db, fetch=lambda url: DETAIL_HTML_WITH_STEPS,
        dry_run=True, sleep_seconds=0,
    )
    db.refresh(target)
    assert stats["processed"] == 1
    assert stats["updated"] == 0
    assert target.steps_json is None


def test_scrape_backfill_sleeps_between_rows(db, monkeypatch):
    from app.crawler import steps_backfill

    slept = []
    monkeypatch.setattr(
        steps_backfill.time, "sleep", lambda s: slept.append(s)
    )
    _add_recipe(db, "R1", "https://www.xiachufang.com/recipe/r1/")
    steps_backfill.backfill_steps_via_scrape(
        db, fetch=lambda url: DETAIL_HTML_WITH_STEPS, sleep_seconds=30
    )
    assert slept == [30]


def test_default_fetch_uses_scraper_client(monkeypatch):
    from types import SimpleNamespace

    from app.crawler import steps_backfill

    class DummyScraper:
        def __init__(self):
            self._client = SimpleNamespace(
                get=lambda url: SimpleNamespace(
                    text="<html>ok</html>",
                    raise_for_status=lambda: None,
                )
            )

    monkeypatch.setattr(
        "app.crawler.xiachufang.XiachufangScraper", DummyScraper
    )
    steps_backfill._default_fetch._scraper = None
    try:
        html = steps_backfill._default_fetch("https://x/1/")
    finally:
        steps_backfill._default_fetch._scraper = None
    assert html == "<html>ok</html>"
