import pytest
from unittest.mock import patch, MagicMock

import httpx


def test_get_source_name():
    from app.crawler.vvhan import VvhanCrawler
    assert VvhanCrawler().get_source_name() == "vvhan"


def test_parse_hot_int():
    from app.crawler.vvhan import VvhanCrawler
    assert VvhanCrawler._parse_hot(12345) == 12345


def test_parse_hot_float():
    from app.crawler.vvhan import VvhanCrawler
    assert VvhanCrawler._parse_hot(123.7) == 123


def test_parse_hot_string_number():
    from app.crawler.vvhan import VvhanCrawler
    assert VvhanCrawler._parse_hot("12345") == 12345


def test_parse_hot_wan_string():
    from app.crawler.vvhan import VvhanCrawler
    assert VvhanCrawler._parse_hot("56.7万") == 567000


def test_parse_hot_with_suffix():
    from app.crawler.vvhan import VvhanCrawler
    assert VvhanCrawler._parse_hot("123万热度") == 1_230_000


def test_parse_hot_with_search_suffix():
    from app.crawler.vvhan import VvhanCrawler
    assert VvhanCrawler._parse_hot("45万热搜") == 450_000


def test_parse_hot_yi():
    from app.crawler.vvhan import VvhanCrawler
    assert VvhanCrawler._parse_hot("1.5亿") == 150_000_000


def test_parse_hot_invalid():
    from app.crawler.vvhan import VvhanCrawler
    assert VvhanCrawler._parse_hot("无数据") == 0
    assert VvhanCrawler._parse_hot(None) == 0
    assert VvhanCrawler._parse_hot("") == 0


def test_normalize_score_top_tier():
    from app.crawler.vvhan import VvhanCrawler
    assert VvhanCrawler._normalize_score(10_000_000) == 100


def test_crawl_returns_food_items():
    from app.crawler.vvhan import VvhanCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "title": "微博",
        "data": [
            {"title": "火锅新吃法火了", "hot": "500万", "pic": "http://img.com/1.jpg"},
            {"title": "科技新闻", "hot": "100万"},
            {"title": "奶茶价格战", "hot": 3_000_000, "pic": None},
        ],
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.vvhan.httpx.get", return_value=mock_response):
        crawler = VvhanCrawler()
        items = crawler.crawl()

    food_names = [i.food_name for i in items]
    assert "火锅" in food_names
    assert "奶茶" in food_names


def test_crawl_filters_non_food():
    from app.crawler.vvhan import VvhanCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "data": [
            {"title": "股票暴跌", "hot": 1_000_000},
            {"title": "天气预报", "hot": 500_000},
        ],
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.vvhan.httpx.get", return_value=mock_response):
        items = VvhanCrawler().crawl()

    assert items == []


def test_crawl_handles_api_failure():
    from app.crawler.vvhan import VvhanCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": False,
        "message": "rate limited",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.vvhan.httpx.get", return_value=mock_response):
        items = VvhanCrawler().crawl()

    assert items == []


def test_crawl_handles_platform_error():
    from app.crawler.vvhan import VvhanCrawler

    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "wbHot" in url:
            raise httpx.ConnectError("Network error")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": True,
            "data": [{"title": "烧烤节开幕", "hot": 2_000_000}],
        }
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    with patch("app.crawler.vvhan.httpx.get", side_effect=mock_get):
        items = VvhanCrawler().crawl()

    assert isinstance(items, list)


def test_crawl_deduplicates():
    from app.crawler.vvhan import VvhanCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "data": [
            {"title": "火锅新吃法", "hot": 5_000_000},
            {"title": "火锅底料推荐", "hot": 3_000_000},
        ],
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.vvhan.httpx.get", return_value=mock_response):
        items = VvhanCrawler().crawl()

    fire_pot = [i for i in items if i.food_name == "火锅"]
    assert len(fire_pot) == 1
    assert fire_pot[0].post_count == 5_000_000


def test_crawl_preserves_image_url():
    from app.crawler.vvhan import VvhanCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "data": [
            {"title": "蛋糕测评", "hot": 1_000_000, "pic": "http://img.com/cake.jpg"},
        ],
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.vvhan.httpx.get", return_value=mock_response):
        items = VvhanCrawler().crawl()

    assert len(items) >= 1
    assert items[0].image_url == "http://img.com/cake.jpg"
