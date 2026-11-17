import json
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.crawler.ai_extractor import (
    ExtractedFoodItem,
    _load_cached,
    _parse_response,
    extract_foods_from_titles,
)
from app.database import Base
from app.models import AITitleCache


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_parse_response_returns_canonical_and_trend():
    raw = json.dumps({
        "results": [
            {
                "title": "入冬第一顿火锅",
                "foods": [{
                    "name": "川式火锅",
                    "category": "火锅",
                    "canonical_of": "火锅",
                    "trend_type": "seasonal",
                    "trend_context": "入冬涮锅季",
                }],
            }
        ]
    }, ensure_ascii=False)
    items, mapping = _parse_response(raw, ["入冬第一顿火锅"])
    assert len(items) == 1
    assert items[0].name == "川式火锅"
    assert items[0].canonical_of == "火锅"
    assert items[0].trend_type == "seasonal"
    assert items[0].trend_context == "入冬涮锅季"


def test_parse_response_invalid_trend_type_falls_back_to_none():
    raw = json.dumps({
        "results": [{
            "title": "测试",
            "foods": [{
                "name": "测试食物",
                "category": "小吃",
                "canonical_of": "测试食物",
                "trend_type": "invalid_type",
                "trend_context": "xxx",
            }],
        }]
    }, ensure_ascii=False)
    items, _ = _parse_response(raw, ["测试"])
    assert items[0].trend_type is None


def test_parse_response_trims_context_to_15_chars():
    raw = json.dumps({
        "results": [{
            "title": "测试",
            "foods": [{
                "name": "测试食物",
                "category": "小吃",
                "canonical_of": "测试食物",
                "trend_type": "event",
                "trend_context": "这是一段非常非常非常长的归因说明一共超过十五个字",
            }],
        }]
    }, ensure_ascii=False)
    items, _ = _parse_response(raw, ["测试"])
    assert len(items[0].trend_context) <= 15


def test_load_cached_backward_compat_old_format(db):
    """旧缓存只有 {name, category}，新字段缺失也能正确解析。"""
    old_foods = [{"name": "火锅", "category": "火锅"}]
    db.add(AITitleCache(
        title_hash="a" * 64,
        title="旧缓存标题",
        extracted_foods=json.dumps(old_foods, ensure_ascii=False),
    ))
    db.commit()

    with patch("app.crawler.ai_extractor._hash_title", return_value="a" * 64):
        cached, uncached = _load_cached(db, ["旧缓存标题"])

    assert len(cached) == 1
    assert cached[0].name == "火锅"
    assert cached[0].canonical_of == "火锅"  # 默认等于 name
    assert cached[0].trend_type is None


def test_extract_foods_returns_empty_when_ai_disabled():
    with patch("app.crawler.ai_extractor.AI_EXTRACT_ENABLED", False):
        assert extract_foods_from_titles(["任意标题"]) == []


def test_extract_foods_returns_empty_when_no_api_key():
    with patch("app.crawler.ai_extractor.AI_EXTRACT_ENABLED", True), \
         patch("app.crawler.ai_extractor.ANTHROPIC_API_KEY", ""):
        assert extract_foods_from_titles(["任意标题"]) == []
