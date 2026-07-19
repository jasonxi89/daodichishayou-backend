import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
from fastapi.responses import StreamingResponse


def _chunk(content: str):
    return MagicMock(
        choices=[MagicMock(delta=MagicMock(content=content))]
    )


class AsyncChunkStream:
    def __init__(self, chunks, *, error=None, stall_after_first=False):
        self.chunks = list(chunks)
        self.error = error
        self.stall_after_first = stall_after_first
        self.index = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.stall_after_first and self.index >= 1:
            await asyncio.Event().wait()
        if self.index < len(self.chunks):
            chunk = self.chunks[self.index]
            self.index += 1
            return chunk
        if self.error:
            raise self.error
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


def _mock_async_client(stream):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=stream)
    client.close = AsyncMock()
    return client


def test_steps_stream_forwards_chunks_and_appends_json(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
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
    stream = AsyncChunkStream([_chunk(text[:20]), _chunk(text[20:])])
    provider_client = _mock_async_client(stream)

    with patch(
        "app.routers.recommend_progressive._async_client",
        return_value=provider_client,
    ):
        response = client.post(
            "/api/recommend/steps?stream=1",
            json={"dish_name": "番茄炒蛋", "ingredients": ["番茄", "鸡蛋"]},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    frames = [json.loads(line) for line in response.text.splitlines()]
    assert "".join(
        frame["text"] for frame in frames if frame["type"] == "delta"
    ) == text
    assert frames[-1] == {"type": "complete", "dish": payload}
    call = provider_client.chat.completions.create.call_args
    assert call.kwargs["stream"] is True
    assert stream.closed is True
    provider_client.close.assert_awaited_once()


def test_steps_stream_emits_error_frame_on_midstream_failure(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    stream = AsyncChunkStream(
        [_chunk('{"name":"半截')],
        error=openai.OpenAIError("stream interrupted"),
    )
    provider_client = _mock_async_client(stream)

    with patch(
        "app.routers.recommend_progressive._async_client",
        return_value=provider_client,
    ):
        response = client.post(
            "/api/recommend/steps?stream=1",
            json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
        )

    frames = [json.loads(line) for line in response.text.splitlines()]
    assert frames == [
        {"type": "delta", "text": '{"name":"半截'},
        {"type": "error", "code": "provider_interrupted"},
    ]
    assert stream.closed is True
    provider_client.close.assert_awaited_once()


def test_steps_stream_invalid_final_json_emits_error_frame(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    stream = AsyncChunkStream([_chunk("not-json")])
    provider_client = _mock_async_client(stream)
    with patch(
        "app.routers.recommend_progressive._async_client",
        return_value=provider_client,
    ):
        response = client.post(
            "/api/recommend/steps?stream=1",
            json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
        )

    frames = [json.loads(line) for line in response.text.splitlines()]
    assert frames == [
        {"type": "delta", "text": "not-json"},
        {"type": "error", "code": "provider_interrupted"},
    ]


@pytest.mark.asyncio
async def test_downstream_disconnect_closes_upstream_immediately(monkeypatch):
    from app.routers.recommend_progressive import _stream_steps_from_llm

    stream = AsyncChunkStream([_chunk('{"steps": ["第一步')], stall_after_first=True)
    provider_client = _mock_async_client(stream)
    monkeypatch.setattr(
        "app.routers.recommend_progressive._async_client",
        lambda: provider_client,
    )
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
    provider_client.close.assert_awaited_once()
    assert stream.index == 1
