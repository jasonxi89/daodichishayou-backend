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


def test_match_food_in_text_prefers_longer():
    from app.crawler.food_keywords import match_food_in_text
    # "北京烤鸭" is longer than "烤鸭", should be preferred
    result = match_food_in_text("去吃北京烤鸭")
    assert result == "北京烤鸭"


def test_match_all_foods_in_text():
    from app.crawler.food_keywords import match_all_foods_in_text
    result = match_all_foods_in_text("又吃火锅又喝奶茶")
    assert "火锅" in result
    assert "奶茶" in result


def test_match_all_foods_in_text_empty():
    from app.crawler.food_keywords import match_all_foods_in_text
    result = match_all_foods_in_text("今天天气很好")
    assert result == []


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
    assert get_category("火锅") is not None
    assert get_category("奶茶") is not None
    assert get_category("蛋糕") is not None
    assert get_category("披萨") is not None
    assert get_category("寿司") is not None


def test_get_category_unknown():
    from app.crawler.food_keywords import get_category
    assert get_category("未知食物") is None


def test_food_names_is_set():
    from app.crawler.food_keywords import FOOD_NAMES
    assert isinstance(FOOD_NAMES, set)
    assert len(FOOD_NAMES) >= 500


def test_food_context_keywords_is_set():
    from app.crawler.food_keywords import FOOD_CONTEXT_KEYWORDS
    assert "美食" in FOOD_CONTEXT_KEYWORDS
    assert "餐厅" in FOOD_CONTEXT_KEYWORDS


def test_all_foods_have_category():
    from app.crawler.food_keywords import FOOD_NAMES, CATEGORY_MAP
    missing = [f for f in FOOD_NAMES if f not in CATEGORY_MAP]
    assert missing == [], f"Foods without category: {missing}"


def test_category_map_matches_food_names():
    from app.crawler.food_keywords import FOOD_NAMES, CATEGORY_MAP
    extra = [f for f in CATEGORY_MAP if f not in FOOD_NAMES]
    assert extra == [], f"Category entries not in FOOD_NAMES: {extra}"


def test_valid_categories():
    from app.crawler.food_keywords import CATEGORY_MAP
    expected = {
        "正餐", "小吃", "面食", "烧烤", "火锅", "西餐",
        "日料", "韩餐", "东南亚", "甜品", "饮品", "早餐",
        "轻食", "点心", "零食",
    }
    actual = set(CATEGORY_MAP.values())
    assert actual == expected, f"Missing categories: {expected - actual}, Extra: {actual - expected}"


def test_sorted_food_names_order():
    from app.crawler.food_keywords import _SORTED_FOOD_NAMES
    for i in range(len(_SORTED_FOOD_NAMES) - 1):
        assert len(_SORTED_FOOD_NAMES[i]) >= len(_SORTED_FOOD_NAMES[i + 1])
