import asyncio
import json

import pytest
from fastapi.responses import StreamingResponse

from app.config import LLM_TIMEOUT_SECONDS
from app.services.llm import LLMError
from tests.fake_claude import FakeStream, install_fake_client


def fake_stream_text(texts, *, error=None, calls=None):
    """造一个能替换 llm.stream_text 的假流：按顺序产出 texts，然后（可选）抛错。"""

    async def stream_text(system_prompt, user_prompt, *, deadline_seconds=None):
        if calls is not None:
            calls.append((system_prompt, user_prompt, deadline_seconds))
        for text in texts:
            yield text
        if error:
            raise error

    return stream_text


def read_frames(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines()]


def test_steps_stream_forwards_chunks_and_appends_json(client, monkeypatch):
    from app.routers.recommend_progressive import STEPS_SYSTEM_PROMPT

    monkeypatch.setattr(
        "app.routers.recommend_progressive.ANTHROPIC_API_KEY", "test-key"
    )
    payload = {
        "name": "番茄炒蛋",
        "summary": "家常快手菜",
        "ingredients": ["番茄 2个", "鸡蛋 3个"],
        "steps": ["番茄切块", "鸡蛋炒熟"],
        "difficulty": "简单",
        "cook_time": "约10分钟",
        "extra_ingredients": None,
    }
    text = json.dumps(payload, ensure_ascii=False)
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.services.llm.stream_text",
        fake_stream_text([text[:20], text[20:]], calls=calls),
    )

    response = client.post(
        "/api/recommend/steps?stream=1",
        json={"dish_name": "番茄炒蛋", "ingredients": ["番茄", "鸡蛋"]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    frames = read_frames(response)
    assert "".join(
        frame["text"] for frame in frames if frame["type"] == "delta"
    ) == text
    assert frames[-1] == {"type": "complete", "dish": payload}
    [(system_prompt, user_prompt, deadline_seconds)] = calls
    assert system_prompt == STEPS_SYSTEM_PROMPT
    assert "番茄炒蛋" in user_prompt
    assert "番茄, 鸡蛋" in user_prompt


def test_steps_stream_applies_realtime_total_deadline(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.ANTHROPIC_API_KEY", "test-key"
    )
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.services.llm.stream_text", fake_stream_text(["{}"], calls=calls)
    )

    client.post(
        "/api/recommend/steps?stream=1",
        json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
    )

    assert [deadline for _, _, deadline in calls] == [LLM_TIMEOUT_SECONDS]


def test_steps_stream_accepts_fenced_json_reply(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.ANTHROPIC_API_KEY", "test-key"
    )
    dish = {
        "name": "番茄炒蛋",
        "summary": "家常快手菜",
        "ingredients": ["番茄 2个"],
        "steps": ["炒熟"],
    }
    fenced = f"```json\n{json.dumps(dish, ensure_ascii=False)}\n```"
    monkeypatch.setattr("app.services.llm.stream_text", fake_stream_text([fenced]))

    response = client.post(
        "/api/recommend/steps?stream=1",
        json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
    )

    last_frame = read_frames(response)[-1]
    assert last_frame["type"] == "complete"
    assert last_frame["dish"]["steps"] == ["炒熟"]


def test_steps_stream_emits_error_frame_on_midstream_failure(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.ANTHROPIC_API_KEY", "test-key"
    )
    monkeypatch.setattr(
        "app.services.llm.stream_text",
        fake_stream_text(['{"name":"半截'], error=LLMError("stream interrupted")),
    )

    response = client.post(
        "/api/recommend/steps?stream=1",
        json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
    )

    assert read_frames(response) == [
        {"type": "delta", "text": '{"name":"半截'},
        {"type": "error", "code": "provider_interrupted"},
    ]


def test_steps_stream_invalid_final_json_emits_error_frame(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.ANTHROPIC_API_KEY", "test-key"
    )
    monkeypatch.setattr(
        "app.services.llm.stream_text", fake_stream_text(["not-json"])
    )

    response = client.post(
        "/api/recommend/steps?stream=1",
        json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
    )

    assert read_frames(response) == [
        {"type": "delta", "text": "not-json"},
        {"type": "error", "code": "provider_interrupted"},
    ]


def test_steps_stream_closes_upstream_after_midstream_failure(client, monkeypatch):
    """上游出错时，真实的 llm.stream_text 也要把流和客户端都关掉。"""
    monkeypatch.setattr(
        "app.routers.recommend_progressive.ANTHROPIC_API_KEY", "test-key"
    )
    stream = FakeStream(['{"name":"半截'], error=RuntimeError("连接被重置"))
    provider_client = install_fake_client(monkeypatch, stream)

    response = client.post(
        "/api/recommend/steps?stream=1",
        json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
    )

    assert read_frames(response)[-1] == {
        "type": "error",
        "code": "provider_interrupted",
    }
    assert stream.closed is True
    assert provider_client.closed is True


@pytest.mark.asyncio
async def test_downstream_disconnect_closes_upstream_immediately(monkeypatch):
    from app.routers.recommend_progressive import _stream_steps_from_llm

    stream = FakeStream(['{"steps": ["第一步', '"]}'], stall_after=1)
    provider_client = install_fake_client(monkeypatch, stream)
    response = StreamingResponse(
        _stream_steps_from_llm("测试菜", ["番茄"]),
        media_type="application/x-ndjson",
    )
    first_body = asyncio.Event()
    receive_count = 0

    async def receive():
        nonlocal receive_count
        receive_count += 1
        if receive_count == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        await first_body.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            first_body.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/recommend/steps",
        "raw_path": b"/api/recommend/steps",
        "query_string": b"stream=1",
        "headers": [],
        "client": ("test", 1),
        "server": ("test", 80),
        "root_path": "",
    }
    await asyncio.wait_for(response(scope, receive, send), timeout=1)

    assert stream.closed is True
    assert provider_client.closed is True
    assert stream.delivered == 1
