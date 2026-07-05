import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crawler.ai_extractor import ExtractedFoodItem, extract_foods_from_titles
from app.crawler.base import BaseCrawler, FoodTrendItem
from app.crawler.baidu_suggest import BaiduSuggestCrawler
from app.crawler.dailyhot import DailyHotCrawler
from app.crawler.recipe_base import RecipeItem
from app.crawler.toutiao import ToutiaoCrawler
from app.crawler.xiachufang import XiachufangScraper
from app.database import SessionLocal
from app.models import AIDiscoveredFood, CrawlLog, FoodAlias, FoodTrend, FoodTrendSnapshot, Recipe
from app.schemas import CrawlResult

logger = logging.getLogger(__name__)

ALL_CRAWLERS: list[BaseCrawler] = [
    ToutiaoCrawler(),
    BaiduSuggestCrawler(),
    DailyHotCrawler(),
]

# 内置种子数据：热门食物列表（首次启动时导入）
SEED_FOODS: list[FoodTrendItem] = [
    FoodTrendItem("麻辣烫", heat_score=95, post_count=50000, category="小吃"),
    FoodTrendItem("螺蛳粉", heat_score=92, post_count=45000, category="小吃"),
    FoodTrendItem("火锅", heat_score=90, post_count=80000, category="正餐"),
    FoodTrendItem("烤肉", heat_score=88, post_count=60000, category="正餐"),
    FoodTrendItem("奶茶", heat_score=87, post_count=70000, category="饮品"),
    FoodTrendItem("炸鸡", heat_score=85, post_count=40000, category="小吃"),
    FoodTrendItem("寿司", heat_score=83, post_count=35000, category="日料"),
    FoodTrendItem("披萨", heat_score=80, post_count=30000, category="西餐"),
    FoodTrendItem("酸菜鱼", heat_score=82, post_count=38000, category="正餐"),
    FoodTrendItem("烧烤", heat_score=86, post_count=55000, category="小吃"),
    FoodTrendItem("煲仔饭", heat_score=78, post_count=25000, category="正餐"),
    FoodTrendItem("冒菜", heat_score=76, post_count=22000, category="小吃"),
    FoodTrendItem("拉面", heat_score=79, post_count=28000, category="正餐"),
    FoodTrendItem("咖啡", heat_score=84, post_count=65000, category="饮品"),
    FoodTrendItem("蛋糕", heat_score=81, post_count=42000, category="甜品"),
    FoodTrendItem("冰淇淋", heat_score=77, post_count=33000, category="甜品"),
    FoodTrendItem("饺子", heat_score=75, post_count=20000, category="正餐"),
    FoodTrendItem("汉堡", heat_score=74, post_count=18000, category="西餐"),
    FoodTrendItem("麻辣香锅", heat_score=89, post_count=48000, category="正餐"),
    FoodTrendItem("小龙虾", heat_score=91, post_count=52000, category="小吃"),
]


def _save_items(db: Session, source: str, items: list[FoodTrendItem]) -> int:
    """保存爬取结果到数据库，返回保存条数。"""
    count = 0
    for item in items:
        existing = db.execute(
            select(FoodTrend).where(
                FoodTrend.food_name == item.food_name,
                FoodTrend.source == source,
            )
        ).scalar_one_or_none()

        if existing:
            existing.heat_score = item.heat_score
            existing.post_count = item.post_count
            existing.category = item.category or existing.category
            existing.image_url = item.image_url or existing.image_url
            existing.updated_at = datetime.now(timezone.utc)
        else:
            db.add(
                FoodTrend(
                    food_name=item.food_name,
                    source=source,
                    heat_score=item.heat_score,
                    post_count=item.post_count,
                    category=item.category,
                    image_url=item.image_url,
                )
            )
        count += 1
    db.commit()
    return count


def run_all_crawlers(db: Session) -> list[CrawlResult]:
    """执行所有爬虫并保存结果，最后用 AI 提取未匹配标题中的食物。"""
    results: list[CrawlResult] = []
    all_unmatched: list[str] = []

    for crawler in ALL_CRAWLERS:
        source = crawler.get_source_name()
        try:
            items = crawler.crawl()
            if source == "baidu_suggest":
                _save_candidates(db, items)
                saved = len(items)
                message = f"百度候选写入 ai_discovered_foods: {saved} 条"
            else:
                saved = _save_items(db, source, items)
                message = f"抓取完成，保存{saved}条"
            all_unmatched.extend(crawler.unmatched_titles)
            db.add(
                CrawlLog(source=source, status="success", items_count=saved)
            )
            db.commit()
            results.append(
                CrawlResult(
                    source=source,
                    status="success",
                    items_count=saved,
                    message=message,
                )
            )
            logger.info("爬虫 %s 完成: %d 条", source, saved)
        except Exception as e:
            db.add(
                CrawlLog(
                    source=source,
                    status="failed",
                    items_count=0,
                    error_message=str(e)[:500],
                )
            )
            db.commit()
            results.append(
                CrawlResult(
                    source=source,
                    status="failed",
                    items_count=0,
                    message=f"抓取失败: {e}",
                )
            )
            logger.error("爬虫 %s 失败: %s", source, e, exc_info=True)

    # AI 智能提取：从未匹配标题中发现新食物
    try:
        if all_unmatched:
            ai_items = extract_foods_from_titles(all_unmatched)
            if ai_items:
                saved = _save_extracted_items(db, ai_items)
                _save_ai_discoveries_from_extracted(db, ai_items)
                db.add(
                    CrawlLog(source="ai_extract", status="success", items_count=saved)
                )
                db.commit()
                results.append(
                    CrawlResult(
                        source="ai_extract",
                        status="success",
                        items_count=saved,
                        message=f"AI提取完成，发现{saved}种新食物",
                    )
                )
                logger.info("AI 提取完成: %d 种新食物", saved)
            else:
                results.append(
                    CrawlResult(
                        source="ai_extract",
                        status="success",
                        items_count=0,
                        message="AI提取完成，未发现新食物",
                    )
                )
    except Exception as e:
        db.add(
            CrawlLog(
                source="ai_extract",
                status="failed",
                items_count=0,
                error_message=str(e)[:500],
            )
        )
        db.commit()
        results.append(
            CrawlResult(
                source="ai_extract",
                status="failed",
                items_count=0,
                message=f"AI提取失败: {e}",
            )
        )
        logger.error("AI 提取失败: %s", e, exc_info=True)

    # 候选词晋级：baidu_suggest 候选 + 其他源佐证 → 进主表
    try:
        _promote_candidates(db)
    except Exception:
        logger.error("候选词晋级失败", exc_info=True)

    # 保存今日热度快照
    _save_daily_snapshot(db)

    # 生成 AI 趋势快报
    try:
        from app.crawler.ai_digest import generate_daily_digest
        generate_daily_digest(db)
    except Exception:
        logger.error("AI 趋势快报生成失败", exc_info=True)

    return results


def _save_daily_snapshot(db: Session) -> None:
    """保存当前热度数据为今日快照（同一天同食物同来源只保留最新）。"""
    today = date.today()
    all_trends = db.execute(select(FoodTrend)).scalars().all()

    for trend in all_trends:
        existing = db.execute(
            select(FoodTrendSnapshot).where(
                FoodTrendSnapshot.snapshot_date == today,
                FoodTrendSnapshot.food_name == trend.food_name,
                FoodTrendSnapshot.source == trend.source,
            )
        ).scalar_one_or_none()

        if existing:
            existing.heat_score = trend.heat_score
            existing.category = trend.category
        else:
            db.add(FoodTrendSnapshot(
                snapshot_date=today,
                food_name=trend.food_name,
                heat_score=trend.heat_score,
                source=trend.source,
                category=trend.category,
            ))

    db.commit()
    logger.info("今日热度快照已保存: %s, %d 条", today, len(all_trends))


def _save_extracted_items(db: Session, items: list[ExtractedFoodItem]) -> int:
    """把 AI 提取的 ExtractedFoodItem 写入 food_trends (source='ai_extract')。

    同时处理：
    - 若 canonical_of != name → 插入 food_aliases (created_by='ai')
    - food_trends.canonical_name 写入 canonical_of
    - trend_type/trend_context 写入对应列
    """
    count = 0
    for item in items:
        if item.canonical_of and item.canonical_of != item.name:
            existing_alias = db.execute(
                select(FoodAlias).where(FoodAlias.alias_name == item.name)
            ).scalar_one_or_none()
            if not existing_alias:
                db.add(FoodAlias(
                    alias_name=item.name,
                    canonical_name=item.canonical_of,
                    created_by="ai",
                ))

        existing = db.execute(
            select(FoodTrend).where(
                FoodTrend.food_name == item.name,
                FoodTrend.source == "ai_extract",
            )
        ).scalar_one_or_none()

        canonical = item.canonical_of or item.name

        if existing:
            existing.category = item.category or existing.category
            existing.canonical_name = canonical
            existing.trend_type = item.trend_type or existing.trend_type
            existing.trend_context = item.trend_context or existing.trend_context
            existing.updated_at = datetime.now(timezone.utc)
        else:
            db.add(FoodTrend(
                food_name=item.name,
                source="ai_extract",
                heat_score=50,
                post_count=0,
                category=item.category,
                canonical_name=canonical,
                trend_type=item.trend_type,
                trend_context=item.trend_context,
            ))
        count += 1

    db.commit()
    return count


def _save_ai_discoveries_from_extracted(
    db: Session, items: list[ExtractedFoodItem]
) -> None:
    """记录 AI 发现的新食物到 ai_discovered_foods 表。"""
    for item in items:
        existing = db.execute(
            select(AIDiscoveredFood).where(
                AIDiscoveredFood.food_name == item.name
            )
        ).scalar_one_or_none()
        if existing:
            existing.discovery_count += 1
        else:
            db.add(AIDiscoveredFood(
                food_name=item.name,
                category=item.category,
            ))
    db.commit()


def _save_candidates(db: Session, items: list[FoodTrendItem]) -> None:
    """把候选源（如 baidu_suggest）的 items 写入 ai_discovered_foods，不入主表。"""
    for item in items:
        existing = db.execute(
            select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == item.food_name)
        ).scalar_one_or_none()
        if existing:
            existing.discovery_count += 1
        else:
            db.add(AIDiscoveredFood(
                food_name=item.food_name,
                category=item.category,
            ))
    db.commit()


def _promote_candidates(db: Session) -> None:
    """把有其他源佐证的候选词晋级到 food_trends（source='baidu_suggest'）。"""
    pending = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.promoted_to_trends.is_(False))
    ).scalars().all()

    for candidate in pending:
        other_src_max = db.execute(
            select(FoodTrend.heat_score).where(
                FoodTrend.food_name == candidate.food_name,
                FoodTrend.source != "baidu_suggest",
            ).order_by(FoodTrend.heat_score.desc()).limit(1)
        ).scalar_one_or_none()

        if other_src_max is None:
            continue

        existing_bs = db.execute(
            select(FoodTrend).where(
                FoodTrend.food_name == candidate.food_name,
                FoodTrend.source == "baidu_suggest",
            )
        ).scalar_one_or_none()
        new_score = int(other_src_max * 0.8)
        if existing_bs:
            existing_bs.heat_score = new_score
            existing_bs.updated_at = datetime.now(timezone.utc)
        else:
            db.add(FoodTrend(
                food_name=candidate.food_name,
                source="baidu_suggest",
                heat_score=new_score,
                post_count=candidate.discovery_count,
                category=candidate.category,
                canonical_name=candidate.food_name,
            ))
        candidate.promoted_to_trends = True

    db.commit()


def seed_data() -> None:
    """首次启动时导入种子数据（如果数据库为空）。"""
    db = SessionLocal()
    try:
        count = db.execute(select(FoodTrend.id).limit(1)).scalar()
        if count is not None:
            logger.info("数据库已有数据，跳过种子导入")
            return
        _save_items(db, "manual", SEED_FOODS)
        logger.info("种子数据导入完成: %d 条", len(SEED_FOODS))
    finally:
        db.close()


def _save_recipes(db: Session, items: list[RecipeItem]) -> int:
    """保存菜谱到数据库，按 source_url 去重，返回保存条数。"""
    count = 0
    for item in items:
        existing = db.execute(
            select(Recipe).where(Recipe.source_url == item.source_url)
        ).scalar_one_or_none()

        ingredients_json = (
            json.dumps(item.ingredients, ensure_ascii=False)
            if item.ingredients
            else None
        )
        steps_json = (
            json.dumps(item.steps, ensure_ascii=False)
            if item.steps
            else None
        )

        if existing:
            existing.name = item.name
            existing.rating = item.rating
            existing.made_count = item.made_count
            existing.image_url = item.image_url or existing.image_url
            existing.author = item.author or existing.author
            existing.ingredients_json = ingredients_json or existing.ingredients_json
            existing.ingredients_text = item.ingredients_text or existing.ingredients_text
            existing.steps_json = steps_json or existing.steps_json
            existing.category = item.category or existing.category
            existing.list_source = item.list_source or existing.list_source
            existing.updated_at = datetime.now(timezone.utc)
        else:
            db.add(
                Recipe(
                    name=item.name,
                    source_url=item.source_url,
                    rating=item.rating,
                    made_count=item.made_count,
                    image_url=item.image_url,
                    author=item.author,
                    ingredients_json=ingredients_json,
                    ingredients_text=item.ingredients_text,
                    steps_json=steps_json,
                    category=item.category,
                    list_source=item.list_source,
                )
            )
        count += 1
    db.commit()
    return count


def run_recipe_scrapers(db: Session) -> list[CrawlResult]:
    """执行菜谱爬虫并保存结果。"""
    results: list[CrawlResult] = []

    # Collect existing URLs to skip
    existing_urls = {
        row[0]
        for row in db.execute(select(Recipe.source_url)).all()
    }

    scraper = XiachufangScraper()
    source = scraper.get_source_name()
    try:
        items = scraper.scrape(existing_urls=existing_urls)
        saved = _save_recipes(db, items)
        db.add(CrawlLog(source=source, status="success", items_count=saved))
        db.commit()
        results.append(
            CrawlResult(
                source=source,
                status="success",
                items_count=saved,
                message=f"菜谱爬取完成，保存{saved}条",
            )
        )
        logger.info("菜谱爬虫 %s 完成: %d 条", source, saved)
    except Exception as e:
        db.add(
            CrawlLog(
                source=source,
                status="failed",
                items_count=0,
                error_message=str(e)[:500],
            )
        )
        db.commit()
        results.append(
            CrawlResult(
                source=source,
                status="failed",
                items_count=0,
                message=f"菜谱爬取失败: {e}",
            )
        )
        logger.error("菜谱爬虫 %s 失败: %s", source, e, exc_info=True)

    return results


def scheduled_recipe_scrape() -> None:
    """菜谱定时爬取入口。"""
    db = SessionLocal()
    try:
        run_recipe_scrapers(db)
    finally:
        db.close()


def scheduled_crawl() -> None:
    """定时任务入口：创建独立 session 并执行爬虫。"""
    db = SessionLocal()
    try:
        run_all_crawlers(db)
    finally:
        db.close()
