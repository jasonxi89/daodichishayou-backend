import pytest


def test_xiaohongshu_source_name():
    from app.crawler.xiaohongshu import XiaohongshuCrawler
    assert XiaohongshuCrawler().get_source_name() == "xiaohongshu"


def test_xiaohongshu_crawl_returns_list():
    from app.crawler.xiaohongshu import XiaohongshuCrawler
    items = XiaohongshuCrawler().crawl()
    assert isinstance(items, list)


def test_xiaohongshu_deduplicate():
    from app.crawler.xiaohongshu import XiaohongshuCrawler
    from app.crawler.base import FoodTrendItem

    items = [
        FoodTrendItem("火锅", heat_score=80, post_count=1000),
        FoodTrendItem("火锅", heat_score=70, post_count=500),
        FoodTrendItem("奶茶", heat_score=60, post_count=200),
    ]
    deduped = XiaohongshuCrawler._deduplicate(items)
    names = [i.food_name for i in deduped]
    assert names.count("火锅") == 1
    hotpot = next(i for i in deduped if i.food_name == "火锅")
    assert hotpot.post_count == 1500  # accumulated
    assert hotpot.heat_score == 80  # max


def test_douyin_source_name():
    from app.crawler.douyin import DouyinCrawler
    assert DouyinCrawler().get_source_name() == "douyin"


def test_douyin_crawl_returns_list():
    from app.crawler.douyin import DouyinCrawler
    items = DouyinCrawler().crawl()
    assert isinstance(items, list)


def test_douyin_deduplicate():
    from app.crawler.douyin import DouyinCrawler
    from app.crawler.base import FoodTrendItem

    items = [
        FoodTrendItem("火锅", heat_score=80, post_count=1000),
        FoodTrendItem("火锅", heat_score=90, post_count=2000),
    ]
    deduped = DouyinCrawler._deduplicate(items)
    assert len(deduped) == 1
    assert deduped[0].heat_score == 90
    assert deduped[0].post_count == 3000
