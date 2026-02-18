import pytest
from unittest.mock import patch, MagicMock


def test_get_source_name():
    from app.crawler.baidu_suggest import BaiduSuggestCrawler
    assert BaiduSuggestCrawler().get_source_name() == "baidu_suggest"


def test_get_suggestions_parses_response():
    from app.crawler.baidu_suggest import BaiduSuggestCrawler

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "g": [
            {"q": "火锅推荐"},
            {"q": "奶茶哪家好"},
            {"other_key": "no_q_here"},  # should be filtered (no "q" key)
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.crawler.baidu_suggest.httpx.get", return_value=mock_response):
        crawler = BaiduSuggestCrawler()
        suggestions = crawler._get_suggestions("火锅")

    assert "火锅推荐" in suggestions
    assert "奶茶哪家好" in suggestions
    assert len(suggestions) == 2


def test_extract_foods_accumulates():
    from app.crawler.baidu_suggest import BaiduSuggestCrawler

    counts: dict = {}
    BaiduSuggestCrawler._extract_foods("喜欢吃火锅", counts)
    BaiduSuggestCrawler._extract_foods("再来一碗火锅", counts)
    BaiduSuggestCrawler._extract_foods("今天喝奶茶", counts)

    assert counts.get("火锅", 0) == 2
    assert counts.get("奶茶", 0) == 1


def test_build_items_normalizes_score():
    from app.crawler.baidu_suggest import BaiduSuggestCrawler

    counts = {"火锅": 10, "奶茶": 5, "蛋糕": 2}
    items = BaiduSuggestCrawler._build_items(counts)

    # Highest count should have score 100
    hottest = items[0]
    assert hottest.food_name == "火锅"
    assert hottest.heat_score == 100

    # Others should be proportional
    assert all(0 < i.heat_score <= 100 for i in items)


def test_build_items_sorted_by_score():
    from app.crawler.baidu_suggest import BaiduSuggestCrawler

    counts = {"火锅": 10, "奶茶": 5, "蛋糕": 1}
    items = BaiduSuggestCrawler._build_items(counts)

    scores = [i.heat_score for i in items]
    assert scores == sorted(scores, reverse=True)


def test_build_items_empty():
    from app.crawler.baidu_suggest import BaiduSuggestCrawler
    assert BaiduSuggestCrawler._build_items({}) == []


def test_crawl_handles_failed_keyword():
    from app.crawler.baidu_suggest import BaiduSuggestCrawler
    import httpx

    call_count = 0
    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Network error")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"g": [{"q": "火锅好吃"}]}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    with patch("app.crawler.baidu_suggest.httpx.get", side_effect=mock_get):
        crawler = BaiduSuggestCrawler()
        # Should not raise, just skip the failed keyword
        items = crawler.crawl()

    assert isinstance(items, list)


def test_build_items_assigns_category():
    from app.crawler.baidu_suggest import BaiduSuggestCrawler

    counts = {"火锅": 5}
    items = BaiduSuggestCrawler._build_items(counts)
    assert items[0].category == "正餐"


def test_extract_foods_no_match():
    from app.crawler.baidu_suggest import BaiduSuggestCrawler

    counts: dict = {}
    BaiduSuggestCrawler._extract_foods("今天天气很好", counts)
    assert counts == {}
