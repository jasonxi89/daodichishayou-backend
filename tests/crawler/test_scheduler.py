import pytest
from unittest.mock import MagicMock, patch


def test_seed_data_inserts_when_empty(db):
    from app.crawler.scheduler import seed_data, SEED_FOODS
    from app.models import FoodTrend
    from sqlalchemy import select
    from unittest.mock import patch

    # Patch SessionLocal to use our test db
    with patch("app.crawler.scheduler.SessionLocal", return_value=db):
        seed_data()

    count = db.execute(select(FoodTrend)).scalars().all()
    assert len(count) == len(SEED_FOODS)


def test_seed_data_idempotent(db):
    from app.crawler.scheduler import seed_data, SEED_FOODS
    from app.models import FoodTrend
    from sqlalchemy import select

    with patch("app.crawler.scheduler.SessionLocal", return_value=db):
        seed_data()
        seed_data()  # Second call should skip

    items = db.execute(select(FoodTrend)).scalars().all()
    assert len(items) == len(SEED_FOODS)


def test_save_items_inserts_new(db):
    from app.crawler.base import FoodTrendItem
    from app.crawler.scheduler import _save_items
    from app.models import FoodTrend
    from sqlalchemy import select

    items = [
        FoodTrendItem("新食物1", heat_score=80, post_count=1000, category="小吃"),
        FoodTrendItem("新食物2", heat_score=70, post_count=500),
    ]
    count = _save_items(db, "test_source", items)
    assert count == 2

    saved = db.execute(select(FoodTrend)).scalars().all()
    assert len(saved) == 2


def test_save_items_updates_existing(db):
    from app.crawler.base import FoodTrendItem
    from app.crawler.scheduler import _save_items
    from app.models import FoodTrend
    from sqlalchemy import select

    # Insert first
    _save_items(db, "toutiao", [FoodTrendItem("火锅", heat_score=80)])
    # Update
    _save_items(db, "toutiao", [FoodTrendItem("火锅", heat_score=95)])

    item = db.execute(select(FoodTrend).where(FoodTrend.food_name == "火锅")).scalar_one()
    assert item.heat_score == 95


def test_save_items_preserves_null_fields(db):
    from app.crawler.base import FoodTrendItem
    from app.crawler.scheduler import _save_items
    from app.models import FoodTrend
    from sqlalchemy import select

    # Insert with category
    _save_items(db, "toutiao", [FoodTrendItem("火锅", heat_score=80, category="正餐")])
    # Update without category (None) - should keep existing
    _save_items(db, "toutiao", [FoodTrendItem("火锅", heat_score=95, category=None)])

    item = db.execute(select(FoodTrend).where(FoodTrend.food_name == "火锅")).scalar_one()
    assert item.category == "正餐"  # preserved from first insert


def test_run_all_crawlers_exception_isolation(db):
    from app.crawler.scheduler import run_all_crawlers
    from app.crawler.base import BaseCrawler, FoodTrendItem

    class FailingCrawler(BaseCrawler):
        def get_source_name(self): return "failing"
        def crawl(self): raise RuntimeError("Network error")

    class SucceedingCrawler(BaseCrawler):
        def get_source_name(self): return "succeeding"
        def crawl(self): return [FoodTrendItem("火锅", heat_score=90)]

    with patch("app.crawler.scheduler.ALL_CRAWLERS", [FailingCrawler(), SucceedingCrawler()]):
        results = run_all_crawlers(db)

    assert len(results) == 2
    failing = next(r for r in results if r.source == "failing")
    succeeding = next(r for r in results if r.source == "succeeding")
    assert failing.status == "failed"
    assert succeeding.status == "success"


def test_run_all_crawlers_saves_crawl_log(db):
    from app.crawler.scheduler import run_all_crawlers
    from app.crawler.base import BaseCrawler, FoodTrendItem
    from app.models import CrawlLog
    from sqlalchemy import select

    class MockCrawler(BaseCrawler):
        def get_source_name(self): return "mock"
        def crawl(self): return [FoodTrendItem("测试", heat_score=50)]

    with patch("app.crawler.scheduler.ALL_CRAWLERS", [MockCrawler()]):
        run_all_crawlers(db)

    logs = db.execute(select(CrawlLog)).scalars().all()
    assert len(logs) == 1
    assert logs[0].source == "mock"
    assert logs[0].status == "success"


def test_run_all_crawlers_returns_results(db):
    from app.crawler.scheduler import run_all_crawlers
    from app.crawler.base import BaseCrawler, FoodTrendItem

    class MockCrawler(BaseCrawler):
        def get_source_name(self): return "mock"
        def crawl(self): return [FoodTrendItem("炸鸡", heat_score=85)]

    with patch("app.crawler.scheduler.ALL_CRAWLERS", [MockCrawler()]):
        results = run_all_crawlers(db)

    assert len(results) == 1
    assert results[0].status == "success"
    assert results[0].items_count == 1


def test_scheduled_crawl_uses_session():
    from app.crawler.scheduler import scheduled_crawl

    with patch("app.crawler.scheduler.SessionLocal") as mock_session_cls:
        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db
        with patch("app.crawler.scheduler.run_all_crawlers") as mock_run:
            mock_run.return_value = []
            scheduled_crawl()
            mock_run.assert_called_once_with(mock_db)
            mock_db.close.assert_called_once()


def test_seed_data_skips_if_data_exists(db):
    from app.crawler.scheduler import seed_data, _save_items
    from app.crawler.base import FoodTrendItem
    from app.models import FoodTrend
    from sqlalchemy import select

    # Pre-populate
    _save_items(db, "manual", [FoodTrendItem("已有数据", heat_score=50)])

    with patch("app.crawler.scheduler.SessionLocal", return_value=db):
        seed_data()

    # Should only have 1 item, not 1 + SEED_FOODS
    items = db.execute(select(FoodTrend)).scalars().all()
    assert len(items) == 1


def test_save_items_empty_list(db):
    from app.crawler.scheduler import _save_items
    count = _save_items(db, "test", [])
    assert count == 0


def test_run_all_crawlers_failed_log(db):
    from app.crawler.scheduler import run_all_crawlers
    from app.crawler.base import BaseCrawler
    from app.models import CrawlLog
    from sqlalchemy import select

    class BrokenCrawler(BaseCrawler):
        def get_source_name(self): return "broken"
        def crawl(self): raise ValueError("something broke")

    with patch("app.crawler.scheduler.ALL_CRAWLERS", [BrokenCrawler()]):
        results = run_all_crawlers(db)

    logs = db.execute(select(CrawlLog)).scalars().all()
    assert logs[0].status == "failed"
    assert "something broke" in logs[0].error_message


def test_run_all_crawlers_collects_unmatched(db):
    """Crawlers with unmatched titles trigger AI extraction."""
    from app.crawler.scheduler import run_all_crawlers
    from app.crawler.base import BaseCrawler, FoodTrendItem

    class UnmatchedCrawler(BaseCrawler):
        def get_source_name(self): return "unmatched_test"
        def crawl(self):
            self.unmatched_titles = ["酱香拿铁火了", "天气预报"]
            return [FoodTrendItem("火锅", heat_score=90)]

    ai_items = [FoodTrendItem("酱香拿铁", heat_score=50, category="饮品")]
    with patch("app.crawler.scheduler.ALL_CRAWLERS", [UnmatchedCrawler()]), \
         patch("app.crawler.scheduler.extract_foods_from_titles", return_value=ai_items):
        results = run_all_crawlers(db)

    sources = {r.source for r in results}
    assert "unmatched_test" in sources
    assert "ai_extract" in sources
    ai_result = next(r for r in results if r.source == "ai_extract")
    assert ai_result.status == "success"
    assert ai_result.items_count == 1


def test_run_all_crawlers_ai_extract_failure_isolated(db):
    """AI extraction failure should not affect crawler results."""
    from app.crawler.scheduler import run_all_crawlers
    from app.crawler.base import BaseCrawler, FoodTrendItem

    class UnmatchedCrawler(BaseCrawler):
        def get_source_name(self): return "ok_crawler"
        def crawl(self):
            self.unmatched_titles = ["something"]
            return [FoodTrendItem("火锅", heat_score=90)]

    with patch("app.crawler.scheduler.ALL_CRAWLERS", [UnmatchedCrawler()]), \
         patch("app.crawler.scheduler.extract_foods_from_titles", side_effect=RuntimeError("AI broken")):
        results = run_all_crawlers(db)

    ok = next(r for r in results if r.source == "ok_crawler")
    assert ok.status == "success"
    ai = next(r for r in results if r.source == "ai_extract")
    assert ai.status == "failed"


def test_run_all_crawlers_no_ai_when_no_unmatched(db):
    """No AI extraction when all titles matched."""
    from app.crawler.scheduler import run_all_crawlers
    from app.crawler.base import BaseCrawler, FoodTrendItem

    class MatchedCrawler(BaseCrawler):
        def get_source_name(self): return "matched"
        def crawl(self): return [FoodTrendItem("火锅", heat_score=90)]

    with patch("app.crawler.scheduler.ALL_CRAWLERS", [MatchedCrawler()]), \
         patch("app.crawler.scheduler.extract_foods_from_titles") as mock_ai:
        results = run_all_crawlers(db)

    mock_ai.assert_not_called()
    assert all(r.source != "ai_extract" for r in results)


def test_save_ai_discoveries(db):
    """AI discovered foods are saved to ai_discovered_foods table."""
    from app.crawler.scheduler import _save_ai_discoveries
    from app.crawler.base import FoodTrendItem
    from app.models import AIDiscoveredFood
    from sqlalchemy import select

    items = [
        FoodTrendItem("酱香拿铁", heat_score=50, category="饮品"),
        FoodTrendItem("脏脏包", heat_score=50, category="甜品"),
    ]
    _save_ai_discoveries(db, items)

    discoveries = db.execute(select(AIDiscoveredFood)).scalars().all()
    assert len(discoveries) == 2
    names = {d.food_name for d in discoveries}
    assert "酱香拿铁" in names
    assert "脏脏包" in names


def test_save_ai_discoveries_increments_count(db):
    """Repeated discovery increments count."""
    from app.crawler.scheduler import _save_ai_discoveries
    from app.crawler.base import FoodTrendItem
    from app.models import AIDiscoveredFood
    from sqlalchemy import select

    items = [FoodTrendItem("酱香拿铁", heat_score=50, category="饮品")]
    _save_ai_discoveries(db, items)
    _save_ai_discoveries(db, items)

    disc = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == "酱香拿铁")
    ).scalar_one()
    assert disc.discovery_count == 2
