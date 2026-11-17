"""哪些大模型调用带总时限：面向小程序的实时接口带，后台任务不带。

做法是换掉 llm 的底层补全，记录每次调用拿到的总时限；调用点本身照常运行。
"""

import json
from unittest.mock import MagicMock

import pytest

from app.config import LLM_TIMEOUT_SECONDS
from app.models import Recipe
from app.services import llm

DISH = {
    "name": "番茄炒蛋",
    "summary": "家常快手菜",
    "ingredients": ["番茄 2个"],
    "steps": ["炒熟"],
}
DISHES_REPLY = json.dumps({"dishes": [DISH]}, ensure_ascii=False)
DISH_REPLY = json.dumps(DISH, ensure_ascii=False)


class RecordingCompletion:
    """假的底层补全：记录每次调用拿到的总时限，返回预设的回复。"""

    def __init__(self):
        self.reply = DISHES_REPLY
        self.deadlines: list[float | None] = []

    async def __call__(self, request: dict, deadline_seconds: float | None) -> str:
        self.deadlines.append(deadline_seconds)
        return self.reply


@pytest.fixture
def completion(monkeypatch) -> RecordingCompletion:
    recording = RecordingCompletion()
    monkeypatch.setattr(llm, "_complete", recording)
    for module in ("recommend", "recommend_progressive", "trending", "admin"):
        monkeypatch.setattr(f"app.routers.{module}.ANTHROPIC_API_KEY", "test-key")
    for module in ("ai_digest", "ai_extractor"):
        monkeypatch.setattr(f"app.crawler.{module}.ANTHROPIC_API_KEY", "test-key")
    return recording


REALTIME_REQUESTS = [
    ("POST", "/api/recommend", {"ingredients": ["冷门食材"]}, DISHES_REPLY),
    ("POST", "/api/recommend/quick", {"ingredients": ["冷门食材"]}, DISHES_REPLY),
    (
        "POST",
        "/api/recommend/steps",
        {"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
        DISH_REPLY,
    ),
    ("POST", "/api/foods-by-category", {"category": "川菜"}, '{"foods": ["火锅"]}'),
    (
        "POST",
        "/api/bulk-foods-by-category",
        {"categories": ["川菜"]},
        '{"川菜": ["火锅"]}',
    ),
    ("GET", "/api/trending/categories/annotated", None, '{"正餐": "好好吃饭"}'),
]


@pytest.mark.parametrize(
    ("method", "path", "body", "reply"),
    REALTIME_REQUESTS,
    ids=[path for _, path, _, _ in REALTIME_REQUESTS],
)
def test_realtime_endpoint_applies_total_deadline(
    client, sample_trends, completion, method, path, body, reply
):
    completion.reply = reply

    response = client.request(method, path, json=body)

    assert response.status_code == 200
    assert completion.deadlines == [LLM_TIMEOUT_SECONDS]


def test_realtime_fast_model_retry_gets_its_own_deadline(
    client, completion, monkeypatch
):
    monkeypatch.setattr("app.routers.recommend.CLAUDE_FAST_MODEL", "claude-haiku-5-5")
    completion.reply = "不是 JSON"

    response = client.post("/api/recommend", json={"ingredients": ["冷门食材"]})

    assert response.status_code == 502
    assert completion.deadlines == [LLM_TIMEOUT_SECONDS, LLM_TIMEOUT_SECONDS]


def test_pregeneration_has_no_total_deadline(db, completion):
    from app.crawler.pregen import run_pregeneration

    assert run_pregeneration(db, budget=1) == 1
    assert completion.deadlines == [None]


def test_daily_digest_has_no_total_deadline(db, sample_trends, completion):
    from app.crawler.ai_digest import generate_daily_digest

    completion.reply = '{"summary": "火锅最火", "top_foods": ["火锅"], "recommendation": "吃火锅"}'

    assert generate_daily_digest(db) is not None
    assert completion.deadlines == [None]


def test_title_extraction_has_no_total_deadline(completion, monkeypatch):
    from app.crawler.ai_extractor import extract_foods_from_titles

    uncached_session = MagicMock()
    uncached_session.execute.return_value.scalar_one_or_none.return_value = None
    monkeypatch.setattr(
        "app.crawler.ai_extractor.SessionLocal", lambda: uncached_session
    )
    completion.reply = '{"results": []}'

    assert extract_foods_from_titles(["酱香拿铁火了"]) == []
    assert completion.deadlines == [None]


def test_alias_merge_has_no_total_deadline(client, sample_trends, completion):
    completion.reply = '{"groups": []}'

    response = client.post("/api/admin/merge-aliases")

    assert response.status_code == 200
    assert completion.deadlines == [None]


def test_steps_backfill_has_no_total_deadline(db, completion):
    from app.crawler.steps_backfill import backfill_steps_via_llm

    db.add(Recipe(name="番茄炒蛋", source_url="https://example.com/backfill"))
    db.commit()
    completion.reply = DISH_REPLY

    stats = backfill_steps_via_llm(db, sleep_seconds=0)

    assert stats["updated"] == 1
    assert completion.deadlines == [None]
