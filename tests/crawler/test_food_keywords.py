import pytest


def test_match_food_in_text_found():
    from app.crawler.food_keywords import match_food_in_text
    assert match_food_in_text("今天想吃火锅") == "火锅"


def test_match_food_in_text_not_found():
    from app.crawler.food_keywords import match_food_in_text
    assert match_food_in_text("今天天气很好") is None


def test_match_food_in_text_returns_first_match():
    from app.crawler.food_keywords import match_food_in_text
    # Should return some food name when multiple present
    result = match_food_in_text("又吃火锅又喝奶茶")
    assert result in ("火锅", "奶茶")


def test_is_food_related_by_food_name():
    from app.crawler.food_keywords import is_food_related
    assert is_food_related("周末去吃火锅") is True


def test_is_food_related_by_context_keyword():
    from app.crawler.food_keywords import is_food_related
    assert is_food_related("这家餐厅真的很好") is True


def test_is_food_related_false():
    from app.crawler.food_keywords import is_food_related
    assert is_food_related("今天股票涨了") is False


def test_get_category_known():
    from app.crawler.food_keywords import get_category
    assert get_category("火锅") == "正餐"
    assert get_category("奶茶") == "饮品"
    assert get_category("蛋糕") == "甜品"
    assert get_category("披萨") == "西餐"
    assert get_category("寿司") == "日料"


def test_get_category_unknown():
    from app.crawler.food_keywords import get_category
    assert get_category("未知食物") is None


def test_food_names_is_set():
    from app.crawler.food_keywords import FOOD_NAMES
    assert isinstance(FOOD_NAMES, set)
    assert len(FOOD_NAMES) > 0


def test_food_context_keywords_is_set():
    from app.crawler.food_keywords import FOOD_CONTEXT_KEYWORDS
    assert "美食" in FOOD_CONTEXT_KEYWORDS
    assert "餐厅" in FOOD_CONTEXT_KEYWORDS
