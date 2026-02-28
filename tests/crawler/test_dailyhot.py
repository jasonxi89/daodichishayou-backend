import pytest
from unittest.mock import patch, MagicMock

import httpx


def test_get_source_name():
    from app.crawler.dailyhot import DailyHotCrawler
    assert DailyHotCrawler().get_source_name() == "dailyhot"


def test_normalize_score_top_tier():
    from app.crawler.dailyhot import DailyHotCrawler
    assert DailyHotCrawler._normalize_score(10_000_000) == 100
    assert DailyHotCrawler._normalize_score(20_000_000) == 100


def test_normalize_score_high_tier():
    from app.crawler.dailyhot import DailyHotCrawler
    score = DailyHotCrawler._normalize_score(5_000_000)
    assert 90 <= score <= 100


def test_normalize_score_low_tier():
    from app.crawler.dailyhot import DailyHotCrawler
    score = DailyHotCrawler._normalize_score(10_000)
    assert 1 <= score < 40


def test_normalize_score_zero():
    from app.crawler.dailyhot import DailyHotCrawler
    score = DailyHotCrawler._normalize_score(0)
    assert score >= 0 and score <= 1


def test_parse_hot_number():
    from app.crawler.dailyhot import DailyHotCrawler
    assert DailyHotCrawler._parse_hot("12345") == 12345


def test_parse_hot_wan():
    from app.crawler.dailyhot import DailyHotCrawler
    assert DailyHotCrawler._parse_hot("56.7万") == 567000


def test_parse_hot_yi():
    from app.crawler.dailyhot import DailyHotCrawler
    assert DailyHotCrawler._parse_hot("1.2亿") == 120_000_000


def test_parse_hot_empty():
    from app.crawler.dailyhot import DailyHotCrawler
    assert DailyHotCrawler._parse_hot("") == 0
    assert DailyHotCrawler._parse_hot(None) == 0


def test_crawl_returns_food_items():
    from app.crawler.dailyhot import DailyHotCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"title": "火锅新吃法火了", "hot": 5_000_000},
            {"title": "科技新闻", "hot": 1_000_000},
            {"title": "奶茶价格战开始", "hot": 3_000_000},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.dailyhot.httpx.get", return_value=mock_response):
        crawler = DailyHotCrawler()
        items = crawler.crawl()

    food_names = [i.food_name for i in items]
    assert "火锅" in food_names
    assert "奶茶" in food_names


def test_crawl_filters_non_food():
    from app.crawler.dailyhot import DailyHotCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"title": "今天股票大跌", "hot": 1_000_000},
            {"title": "明星离婚", "hot": 2_000_000},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.dailyhot.httpx.get", return_value=mock_response):
        items = DailyHotCrawler().crawl()

    assert items == []


def test_crawl_handles_platform_error():
    from app.crawler.dailyhot import DailyHotCrawler

    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "weibo" in url:
            raise httpx.ConnectError("Network error")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"title": "烧烤节开幕", "hot": 2_000_000}]
        }
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    with patch("app.crawler.dailyhot.httpx.get", side_effect=mock_get):
        items = DailyHotCrawler().crawl()

    # Should still get items from other platforms despite weibo failing
    assert isinstance(items, list)


def test_crawl_deduplicates():
    from app.crawler.dailyhot import DailyHotCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"title": "火锅新吃法", "hot": 5_000_000},
            {"title": "火锅底料推荐", "hot": 3_000_000},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.dailyhot.httpx.get", return_value=mock_response):
        items = DailyHotCrawler().crawl()

    # "火锅" should appear only once (deduplicated)
    fire_pot_items = [i for i in items if i.food_name == "火锅"]
    assert len(fire_pot_items) == 1
    # Should keep the higher score
    assert fire_pot_items[0].post_count == 5_000_000


def test_crawl_handles_string_hot():
    from app.crawler.dailyhot import DailyHotCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"title": "螺蛳粉又火了", "hot": "123万"},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.dailyhot.httpx.get", return_value=mock_response):
        items = DailyHotCrawler().crawl()

    assert len(items) >= 1
    assert items[0].food_name == "螺蛳粉"
    assert items[0].post_count == 1_230_000
