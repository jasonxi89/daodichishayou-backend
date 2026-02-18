import pytest
from unittest.mock import patch, MagicMock


def test_normalize_score_top_tier():
    from app.crawler.toutiao import ToutiaoCrawler
    assert ToutiaoCrawler._normalize_score(10_000_000) == 100
    assert ToutiaoCrawler._normalize_score(20_000_000) == 100


def test_normalize_score_high_tier():
    from app.crawler.toutiao import ToutiaoCrawler
    score = ToutiaoCrawler._normalize_score(5_000_000)
    assert 90 <= score <= 100


def test_normalize_score_mid_high_tier():
    from app.crawler.toutiao import ToutiaoCrawler
    score = ToutiaoCrawler._normalize_score(1_000_000)
    assert 70 <= score < 90


def test_normalize_score_mid_tier():
    from app.crawler.toutiao import ToutiaoCrawler
    score = ToutiaoCrawler._normalize_score(100_000)
    assert 40 <= score < 70


def test_normalize_score_low_tier():
    from app.crawler.toutiao import ToutiaoCrawler
    score = ToutiaoCrawler._normalize_score(10_000)
    assert 1 <= score < 40


def test_normalize_score_zero():
    from app.crawler.toutiao import ToutiaoCrawler
    # _normalize_score uses max(1, ...) so minimum returned is 1
    score = ToutiaoCrawler._normalize_score(0)
    assert score >= 0 and score <= 1


def test_get_source_name():
    from app.crawler.toutiao import ToutiaoCrawler
    assert ToutiaoCrawler().get_source_name() == "toutiao"


def test_crawl_returns_food_items():
    from app.crawler.toutiao import ToutiaoCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"Title": "最近火锅很流行", "HotValue": 5_000_000, "Image": {"url": "http://img.com/1.jpg"}},
            {"Title": "科技新闻", "HotValue": 1_000_000},  # non-food, should be filtered
            {"Title": "奶茶限时活动", "HotValue": 3_000_000, "Image": None},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.toutiao.httpx.get", return_value=mock_response):
        crawler = ToutiaoCrawler()
        items = crawler.crawl()

    assert len(items) >= 1
    food_names = [i.food_name for i in items]
    assert "火锅" in food_names or "奶茶" in food_names


def test_crawl_filters_non_food():
    from app.crawler.toutiao import ToutiaoCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"Title": "今天股票大跌", "HotValue": 1_000_000},
            {"Title": "明星离婚", "HotValue": 2_000_000},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.toutiao.httpx.get", return_value=mock_response):
        items = ToutiaoCrawler().crawl()

    assert items == []


def test_crawl_handles_network_error():
    from app.crawler.toutiao import ToutiaoCrawler
    import httpx

    with patch("app.crawler.toutiao.httpx.get", side_effect=httpx.ConnectError("Network error")):
        items = ToutiaoCrawler().crawl()

    assert items == []
