"""用真实的 anthropic SDK 加假的 HTTP 传输层，验证 llm 模块和 SDK 的对接。

这里不替换 SDK 的任何对象：请求体由 SDK 序列化，响应按 SSE 字节流由 SDK 解析，
所以 SDK 升级后参数名或事件格式对不上，这些用例会先失败。
"""

import asyncio
import json
import logging
from contextlib import aclosing

import anthropic
import httpx2
import pytest

from app.services import llm


def opening_events(texts: list[str]) -> list[dict]:
    """一条流式回复的前半段：消息开始、一个空的思考块、一个 ping、文字块和各段文字。"""
    message = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 21, "output_tokens": 1},
    }
    thinking_block = {"type": "thinking", "thinking": "", "signature": ""}
    text_block = {"type": "text", "text": ""}
    events = [
        {"type": "message_start", "message": message},
        {"type": "content_block_start", "index": 0, "content_block": thinking_block},
        {"type": "content_block_stop", "index": 0},
        {"type": "ping"},
        {"type": "content_block_start", "index": 1, "content_block": text_block},
    ]
    for text in texts:
        delta = {"type": "text_delta", "text": text}
        events.append({"type": "content_block_delta", "index": 1, "delta": delta})
    return events


def closing_events(stop_reason: str) -> list[dict]:
    return [
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 34},
        },
        {"type": "message_stop"},
    ]


def encode_events(events: list[dict]) -> bytes:
    lines = [
        f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        for event in events
    ]
    return "".join(lines).encode("utf-8")


def build_stream_body(texts: list[str], stop_reason: str = "end_turn") -> bytes:
    return encode_events(opening_events(texts) + closing_events(stop_reason))


class StalledBody:
    """发出开头几段后就不再出字的响应体，并记录读取它的协程有没有被打断。"""

    def __init__(self, texts: list[str]):
        self.head = encode_events(opening_events(texts))
        self.read_started = False
        self.read_was_interrupted = False

    async def __aiter__(self):
        self.read_started = True
        try:
            yield self.head
            await asyncio.Event().wait()
        finally:
            self.read_was_interrupted = True


class FakeApi:
    """假的 api.anthropic.com：记录收到的请求，按预设回应。"""

    def __init__(self):
        self.status_code = 200
        self.body: bytes | StalledBody = build_stream_body(["正文"])
        self.requests: list[httpx2.Request] = []

    def respond_with_error(self, status_code: int, error_type: str) -> None:
        self.status_code = status_code
        error = {"type": error_type, "message": "模拟的接口报错"}
        self.body = json.dumps({"type": "error", "error": error}).encode("utf-8")

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        is_stream = self.status_code == 200
        content_type = "text/event-stream" if is_stream else "application/json"
        return httpx2.Response(
            self.status_code,
            headers={"content-type": content_type, "request-id": "req_fake_api"},
            content=self.body,
        )

    def create_client(self) -> anthropic.AsyncAnthropic:
        # max_retries=0：报错用例不需要 SDK 的退避重试，那会真的等待
        return anthropic.AsyncAnthropic(
            api_key="sk-ant-test",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle)),
        )


@pytest.fixture
def api(monkeypatch) -> FakeApi:
    fake_api = FakeApi()
    monkeypatch.setattr(llm, "_create_client", fake_api.create_client)
    return fake_api


def test_complete_text_sends_expected_wire_request(api):
    api.body = build_stream_body(['{"foods": ', '["火锅"]}'])

    assert llm.complete_text("系统提示词", "用户提示词") == '{"foods": ["火锅"]}'

    [request] = api.requests
    assert request.url.path == "/v1/messages"
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    assert json.loads(request.content) == {
        "model": "claude-opus-5-5",
        "max_tokens": 128000,
        "system": "系统提示词",
        "messages": [{"role": "user", "content": "用户提示词"}],
        "output_config": {"effort": "low"},
        "fallbacks": "default",
        "stream": True,
    }


def test_complete_text_omits_fallback_on_the_wire_for_other_models(api):
    llm.complete_text("系统", "用户", model="claude-haiku-5-5")

    [request] = api.requests
    body = json.loads(request.content)
    assert body["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in body
    assert "server-side-fallback" not in request.headers.get("anthropic-beta", "")


def test_complete_text_logs_usage_parsed_by_sdk(api, caplog):
    with caplog.at_level(logging.INFO, logger="app.services.llm"):
        llm.complete_text("系统", "用户")

    assert "model=claude-opus-5-5" in caplog.text
    assert "stop_reason=end_turn" in caplog.text
    assert "input_tokens=21" in caplog.text
    assert "output_tokens=34" in caplog.text


@pytest.mark.parametrize(
    ("stop_reason", "expected_reason"),
    [("refusal", "拒答"), ("max_tokens", "截断")],
)
def test_complete_text_rejects_unusable_stop_reason_from_sdk(
    api, stop_reason, expected_reason
):
    api.body = build_stream_body(["半句"], stop_reason=stop_reason)

    with pytest.raises(llm.LLMError, match=expected_reason):
        llm.complete_text("系统", "用户")


@pytest.mark.parametrize(
    ("status_code", "error_type", "expected_message", "expected_log"),
    [
        (429, "rate_limit_error", "限流", "rate limited request_id=req_fake_api"),
        (400, "invalid_request_error", "返回 400", "status=400 request_id=req_fake_api"),
        (401, "authentication_error", "返回 401", "status=401 request_id=req_fake_api"),
        (529, "overloaded_error", "返回 529", "status=529 request_id=req_fake_api"),
    ],
)
def test_complete_text_translates_real_sdk_status_errors(
    api, caplog, status_code, error_type, expected_message, expected_log
):
    api.respond_with_error(status_code, error_type)

    with caplog.at_level(logging.WARNING, logger="app.services.llm"):
        with pytest.raises(llm.LLMError, match=expected_message):
            llm.complete_text("系统", "用户")

    assert expected_log in caplog.text


async def test_stream_text_yields_deltas_parsed_by_sdk(api):
    api.body = build_stream_body(['{"name":"', '番茄炒蛋"}'])

    async with aclosing(llm.stream_text("系统", "用户", deadline_seconds=60)) as texts:
        received = [text async for text in texts]

    assert received == ['{"name":"', '番茄炒蛋"}']
    assert json.loads(api.requests[0].content)["stream"] is True


async def test_stream_text_translates_real_sdk_error_when_opening(api):
    api.respond_with_error(500, "api_error")

    with pytest.raises(llm.LLMError, match="返回 500"):
        async with aclosing(llm.stream_text("系统", "用户")) as texts:
            async for _ in texts:
                pass


async def test_stream_text_cancellation_reaches_the_transport_read(api):
    body = StalledBody(["第一段"])
    api.body = body
    first_text_seen = asyncio.Event()

    async def consume():
        async with aclosing(llm.stream_text("系统", "用户")) as texts:
            async for _ in texts:
                first_text_seen.set()

    task = asyncio.create_task(consume())
    await first_text_seen.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert body.read_was_interrupted is True


async def test_stream_text_deadline_aborts_real_sdk_stream(api):
    body = StalledBody(["第一段"])
    api.body = body

    with pytest.raises(llm.LLMError, match="总时限"):
        async with aclosing(
            llm.stream_text("系统", "用户", deadline_seconds=0)
        ) as texts:
            async for _ in texts:
                pass

    # SDK 发请求前有没有让出事件循环因版本而异，所以时限可能在读响应之前就到；
    # 只要已经开始读，就必须被打断
    assert body.read_was_interrupted is body.read_started


async def test_realtime_deadline_aborts_real_sdk_completion(api, monkeypatch):
    api.body = StalledBody(["第一段"])
    monkeypatch.setattr(llm, "LLM_TIMEOUT_SECONDS", 0)

    with pytest.raises(llm.LLMError, match="总时限"):
        await llm.run_realtime(llm.complete_text, "系统", "用户")


# --- 整条链路：接口或后台任务 -> 调用点 -> llm -> 真实 SDK -> 假传输层 ---

FULL_DISH = {
    "name": "番茄炒蛋",
    "summary": "家常快手菜",
    "ingredients": ["番茄 2个"],
    "steps": ["炒熟"],
}


def test_realtime_endpoint_reaches_claude_through_real_sdk(api, client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.ANTHROPIC_API_KEY", "test-key"
    )
    reply = json.dumps({"dishes": [FULL_DISH]}, ensure_ascii=False)
    api.body = build_stream_body([reply])

    response = client.post("/api/recommend/quick", json={"ingredients": ["冷门食材"]})

    assert response.status_code == 200
    assert response.json()["dishes"][0]["name"] == "番茄炒蛋"
    sent = json.loads(api.requests[0].content)
    assert "冷门食材" in sent["messages"][0]["content"]


def test_steps_stream_endpoint_reaches_claude_through_real_sdk(
    api, client, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.ANTHROPIC_API_KEY", "test-key"
    )
    reply = json.dumps(FULL_DISH, ensure_ascii=False)
    api.body = build_stream_body([reply[:10], reply[10:]])

    response = client.post(
        "/api/recommend/steps?stream=1",
        json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
    )

    frames = [json.loads(line) for line in response.text.splitlines()]
    assert [frame["type"] for frame in frames] == ["delta", "delta", "complete"]
    assert frames[-1]["dish"]["steps"] == ["炒熟"]


def test_sync_endpoint_reaches_claude_from_worker_thread(
    api, client, sample_trends, monkeypatch
):
    monkeypatch.setattr("app.routers.admin.ANTHROPIC_API_KEY", "test-key")
    api.body = build_stream_body([json.dumps({"groups": []})])

    response = client.post("/api/admin/merge-aliases")

    assert response.status_code == 200
    assert len(api.requests) == 1


def test_background_job_reaches_claude_through_real_sdk(api, db):
    from app.crawler.pregen import run_pregeneration

    reply = json.dumps({"dishes": [FULL_DISH]}, ensure_ascii=False)
    api.body = build_stream_body([reply])

    assert run_pregeneration(db, budget=1) == 1
    assert len(api.requests) == 1
