"""菜谱步骤补全：LLM 补写 + 下厨房真实补爬。

数据优先级（2026-07-18 决策）：scraped（真实抓取）> llm（AI 补写）> 空。
- LLM 补写只处理 ``steps_json IS NULL`` 的行，落 ``steps_source='llm'``
- 真实补爬处理 ``steps_json IS NULL`` 或 ``steps_source='llm'`` 的行，
  落 ``steps_source='scraped'``；已是 scraped 的行绝不重抓
- 两者均逐行 commit（可断点续跑）、连续失败熔断；补爬遇 CAPTCHA 立即停
"""
import json
import logging
import time
from collections.abc import Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Recipe

logger = logging.getLogger(__name__)

STEPS_SOURCE_SCRAPED = "scraped"
STEPS_SOURCE_LLM = "llm"

DEFAULT_MAX_CONSECUTIVE_FAILURES = 5
# 实测下厨房 10s 间隔第二个请求就触发 CAPTCHA，真实补爬必须用长间隔
DEFAULT_SCRAPE_SLEEP_SECONDS = 30.0
DEFAULT_LLM_SLEEP_SECONDS = 0.5


def _recipe_ingredient_names(recipe: Recipe) -> list[str]:
    if recipe.ingredients_json:
        try:
            parsed = json.loads(recipe.ingredients_json)
            names = [
                item["name"] for item in parsed
                if isinstance(item, dict) and item.get("name")
            ]
            if names:
                return names
        except (json.JSONDecodeError, TypeError):
            pass
    if recipe.ingredients_text:
        return recipe.ingredients_text.split()
    return []


def _new_stats() -> dict:
    return {
        "processed": 0,
        "updated": 0,
        "failed": 0,
        "circuit_broken": False,
        "captcha": False,
    }


def backfill_steps_via_llm(
    db: Session,
    *,
    generate: Callable | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    sleep_seconds: float = DEFAULT_LLM_SLEEP_SECONDS,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
) -> dict:
    """用 LLM 按菜名+配料补写步骤，只填空行，绝不覆盖已有步骤。"""
    if generate is None:
        from app.routers.recommend_progressive import generate_steps_via_llm

        def generate(name: str, ingredients: list[str]):
            return generate_steps_via_llm(name, ingredients)

    stmt = (
        select(Recipe)
        .where(
            Recipe.steps_json.is_(None),
            Recipe.name.isnot(None),
        )
        .order_by(Recipe.made_count.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    targets = db.execute(stmt).scalars().all()

    stats = _new_stats()
    consecutive = 0
    total = len(targets)
    for recipe in targets:
        stats["processed"] += 1
        if dry_run:
            logger.info("[dry-run] would backfill: %s", recipe.name)
            continue
        try:
            dish = generate(recipe.name, _recipe_ingredient_names(recipe))
            steps = [{"text": s} for s in dish.steps if s]
            if not steps:
                raise ValueError("LLM returned empty steps")
            recipe.steps_json = json.dumps(steps, ensure_ascii=False)
            recipe.steps_source = STEPS_SOURCE_LLM
            db.commit()
            stats["updated"] += 1
            consecutive = 0
            logger.info(
                "[%d/%d] llm backfilled: %s",
                stats["processed"], total, recipe.name,
            )
        except Exception as e:
            db.rollback()
            stats["failed"] += 1
            consecutive += 1
            logger.warning("llm backfill failed for %s: %s", recipe.name, e)
            if consecutive >= max_consecutive_failures:
                stats["circuit_broken"] = True
                logger.error(
                    "连续 %d 次失败，熔断停止", max_consecutive_failures
                )
                break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return stats


def _default_fetch(url: str) -> str:
    from app.crawler.xiachufang import XiachufangScraper

    scraper = _default_fetch._scraper = getattr(
        _default_fetch, "_scraper", None
    ) or XiachufangScraper()
    resp = scraper._client.get(url)
    resp.raise_for_status()
    return resp.text


def backfill_steps_via_scrape(
    db: Session,
    *,
    fetch: Callable[[str], str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    sleep_seconds: float = DEFAULT_SCRAPE_SLEEP_SECONDS,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
) -> dict:
    """重抓详情页补真实步骤，可覆盖 llm 补写，绝不动已有 scraped。"""
    from app.crawler.recipe_base import RecipeItem
    from app.crawler.xiachufang import _is_captcha_page, _parse_detail_page

    fetch = fetch or _default_fetch

    stmt = (
        select(Recipe)
        .where(
            or_(
                Recipe.steps_json.is_(None),
                Recipe.steps_source == STEPS_SOURCE_LLM,
            ),
            Recipe.source_url.like("%xiachufang.com%"),
        )
        .order_by(Recipe.made_count.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    targets = db.execute(stmt).scalars().all()

    stats = _new_stats()
    consecutive = 0
    total = len(targets)
    for recipe in targets:
        stats["processed"] += 1
        if dry_run:
            logger.info("[dry-run] would scrape: %s", recipe.source_url)
            continue
        try:
            html = fetch(recipe.source_url)
            if _is_captcha_page(html):
                stats["captcha"] = True
                logger.error("CAPTCHA 触发，立即停止补爬（已处理 %d/%d）",
                             stats["processed"], total)
                break
            item = RecipeItem(name=recipe.name, source_url=recipe.source_url)
            _parse_detail_page(html, item)
            if not item.steps:
                raise ValueError("page has no parseable steps")
            recipe.steps_json = json.dumps(item.steps, ensure_ascii=False)
            recipe.steps_source = STEPS_SOURCE_SCRAPED
            db.commit()
            stats["updated"] += 1
            consecutive = 0
            logger.info(
                "[%d/%d] scraped steps: %s",
                stats["processed"], total, recipe.name,
            )
        except Exception as e:
            db.rollback()
            stats["failed"] += 1
            consecutive += 1
            logger.warning(
                "scrape backfill failed for %s: %s", recipe.source_url, e
            )
            if consecutive >= max_consecutive_failures:
                stats["circuit_broken"] = True
                logger.error(
                    "连续 %d 次失败，熔断停止", max_consecutive_failures
                )
                break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return stats
