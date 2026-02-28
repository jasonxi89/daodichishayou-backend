import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_ai_disabled():
    with patch("app.crawler.ai_extractor.AI_EXTRACT_ENABLED", False):
        yield


@pytest.fixture
def mock_ai_enabled():
    with patch("app.crawler.ai_extractor.AI_EXTRACT_ENABLED", True), \
         patch("app.crawler.ai_extractor.CLAUDE_API_KEY", "test-key"):
        yield


def test_extract_disabled(mock_ai_disabled):
    from app.crawler.ai_extractor import extract_foods_from_titles
    result = extract_foods_from_titles(["酱香拿铁大火"])
    assert result == []


def test_extract_no_api_key():
    from app.crawler.ai_extractor import extract_foods_from_titles
    with patch("app.crawler.ai_extractor.AI_EXTRACT_ENABLED", True), \
         patch("app.crawler.ai_extractor.CLAUDE_API_KEY", ""):
        result = extract_foods_from_titles(["酱香拿铁大火"])
        assert result == []


def test_extract_empty_titles(mock_ai_enabled):
    from app.crawler.ai_extractor import extract_foods_from_titles
    result = extract_foods_from_titles([])
    assert result == []


def test_parse_response_valid():
    from app.crawler.ai_extractor import _parse_response
    response = json.dumps({
        "results": [
            {
                "title": "酱香拿铁火了",
                "foods": [{"name": "酱香拿铁", "category": "饮品"}],
            },
            {
                "title": "今天天气好",
                "foods": [],
            },
        ]
    })
    items = _parse_response(response)
    assert len(items) == 1
    assert items[0].food_name == "酱香拿铁"
    assert items[0].category == "饮品"
    assert items[0].heat_score == 50


def test_parse_response_filters_short_names():
    from app.crawler.ai_extractor import _parse_response
    response = json.dumps({
        "results": [{"title": "t", "foods": [{"name": "鱼", "category": "正餐"}]}]
    })
    items = _parse_response(response)
    assert len(items) == 0


def test_parse_response_filters_long_names():
    from app.crawler.ai_extractor import _parse_response
    response = json.dumps({
        "results": [
            {"title": "t", "foods": [{"name": "超级无敌豪华版大号海鲜拼盘套餐", "category": "正餐"}]}
        ]
    })
    items = _parse_response(response)
    assert len(items) == 0


def test_parse_response_filters_existing_foods():
    from app.crawler.ai_extractor import _parse_response
    # "火锅" is in FOOD_NAMES, should be filtered out
    response = json.dumps({
        "results": [{"title": "t", "foods": [{"name": "火锅", "category": "正餐"}]}]
    })
    items = _parse_response(response)
    assert len(items) == 0


def test_parse_response_invalid_json():
    from app.crawler.ai_extractor import _parse_response
    items = _parse_response("this is not json")
    assert items == []


def test_parse_response_markdown_code_block():
    from app.crawler.ai_extractor import _parse_response
    response = '```json\n' + json.dumps({
        "results": [
            {"title": "t", "foods": [{"name": "酱香拿铁", "category": "饮品"}]}
        ]
    }) + '\n```'
    items = _parse_response(response)
    assert len(items) == 1
    assert items[0].food_name == "酱香拿铁"


def test_parse_response_invalid_category_defaults():
    from app.crawler.ai_extractor import _parse_response
    response = json.dumps({
        "results": [
            {"title": "t", "foods": [{"name": "新奇食物", "category": "不存在的分类"}]}
        ]
    })
    items = _parse_response(response)
    assert len(items) == 1
    assert items[0].category == "小吃"  # defaults to 小吃


def test_extract_deduplicates_titles(mock_ai_enabled):
    from app.crawler.ai_extractor import extract_foods_from_titles
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=json.dumps({
        "results": [
            {"title": "t1", "foods": [{"name": "酱香拿铁", "category": "饮品"}]},
            {"title": "t2", "foods": [{"name": "酱香拿铁", "category": "饮品"}]},
        ]
    }))]
    with patch("app.crawler.ai_extractor.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = mock_resp
        result = extract_foods_from_titles(["t1", "t2"])

    assert len(result) == 1
    assert result[0].food_name == "酱香拿铁"


def test_extract_api_error_handled(mock_ai_enabled):
    from app.crawler.ai_extractor import extract_foods_from_titles
    with patch("app.crawler.ai_extractor.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API error")
        result = extract_foods_from_titles(["酱香拿铁大火"])

    assert result == []
