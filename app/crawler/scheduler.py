import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crawler.base import BaseCrawler, FoodTrendItem
from app.crawler.baidu_suggest import BaiduSuggestCrawler
from app.crawler.dailyhot import DailyHotCrawler
from app.crawler.toutiao import ToutiaoCrawler
from app.crawler.vvhan import VvhanCrawler
from app.database import SessionLocal
from app.models import CrawlLog, FoodTrend
from app.schemas import CrawlResult

logger = logging.getLogger(__name__)

ALL_CRAWLERS: list[BaseCrawler] = [
    ToutiaoCrawler(),
    BaiduSuggestCrawler(),
    DailyHotCrawler(),
    VvhanCrawler(),
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
    """执行所有爬虫并保存结果。"""
    results: list[CrawlResult] = []
    for crawler in ALL_CRAWLERS:
        source = crawler.get_source_name()
        try:
            items = crawler.crawl()
            saved = _save_items(db, source, items)
            db.add(
                CrawlLog(source=source, status="success", items_count=saved)
            )
            db.commit()
            results.append(
                CrawlResult(
                    source=source,
                    status="success",
                    items_count=saved,
                    message=f"抓取完成，保存{saved}条",
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
    return results


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


def scheduled_crawl() -> None:
    """定时任务入口：创建独立 session 并执行爬虫。"""
    db = SessionLocal()
    try:
        run_all_crawlers(db)
    finally:
        db.close()
