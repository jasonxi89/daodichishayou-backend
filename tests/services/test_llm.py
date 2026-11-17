"""app/services/llm.py 的测试：用假的 SDK 客户端，不连网络，不等真实时间。"""

import asyncio
import functools
import json
import logging
from contextlib import aclosing
from unittest.mock import AsyncMock

import anthropic
import httpx2
import pytest

from app.services import llm
from tests.fake_claude import FakeStream, install_fake_client, make_final_message

API_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
# conftest 的防护会在每个用例里换掉客户端工厂，所以在导入时先留一份真的
REAL_CREATE_CLIENT = llm._create_client


def make_status_error(error_class, status_code: int):
    response = httpx2.Response(
        status_code, request=API_REQUEST, headers={"request-id": "req_test_123"}
    )
    return error_class("boom", response=response, body=None)


@pytest.fixture
def install_client(monkeypatch):
    return functools.partial(install_fake_client, monkeypatch)


async def collect_texts(generator) -> list[str]:
    async with aclosing(generator) as texts:
        return [text async for text in texts]


# --- 客户端工厂 ---


async def test_client_factory_uses_configured_key_and_read_timeout(monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(llm, "LLM_TIMEOUT_SECONDS", 45)

    client = REAL_CREATE_CLIENT()
    try:
        assert client.api_key == "sk-ant-test"
        assert client.timeout == 45
    finally:
        await client.close()


# --- complete_text ---


def test_complete_text_returns_text_and_sends_expected_request(install_client):
    client = install_client(FakeStream(["菜", "谱"]))

    assert llm.complete_text("系统提示词", "用户提示词") == "菜谱"

    assert client.requests == [
        {
            "model": "claude-opus-5-5",
            "max_tokens": 128000,
            "system": "系统提示词",
            "messages": [{"role": "user", "content": "用户提示词"}],
            "output_config": {"effort": "low"},
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }
    ]
    assert client.manager.stream.closed is True
    assert client.closed is True


def test_complete_text_never_sends_parameters_opus_rejects(install_client):
    client = install_client()

    llm.complete_text("系统", "用户")

    request = client.requests[0]
    assert not {"thinking", "temperature", "top_p", "tool_choice"} & request.keys()
    assert all(message["role"] == "user" for message in request["messages"])


def test_complete_text_uses_given_model_without_server_side_fallback(
    install_client,
):
    client = install_client()

    llm.complete_text("系统", "用户", model="claude-haiku-5-5")

    request = client.requests[0]
    assert request["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in request
    assert "betas" not in request


def test_complete_text_uses_configured_effort(install_client, monkeypatch):
    monkeypatch.setattr(llm, "CLAUDE_EFFORT", "max")
    client = install_client()

    llm.complete_text("系统", "用户")

    assert client.requests[0]["output_config"] == {"effort": "max"}


def test_complete_text_logs_usage_line(install_client, caplog):
    install_client()

    with caplog.at_level(logging.INFO, logger="app.services.llm"):
        llm.complete_text("系统", "用户")

    assert "model=claude-opus-5-5" in caplog.text
    assert "stop_reason=end_turn" in caplog.text
    assert "input_tokens=12" in caplog.text
    assert "output_tokens=34" in caplog.text


@pytest.mark.parametrize(
    ("final_message", "expected_reason"),
    [
        (make_final_message("半句", stop_reason="refusal"), "拒答"),
        (make_final_message("半句", stop_reason="max_tokens"), "截断"),
        (make_final_message(None), "没有返回正文"),
        (make_final_message("  \n"), "没有返回正文"),
    ],
)
def test_complete_text_treats_unusable_reply_as_failure(
    install_client, caplog, final_message, expected_reason
):
    install_client(FakeStream(final_message=final_message))

    with caplog.at_level(logging.WARNING, logger="app.services.llm"):
        with pytest.raises(llm.LLMError, match=expected_reason):
            llm.complete_text("系统", "用户")

    assert expected_reason in caplog.text


@pytest.mark.parametrize(
    ("error", "expected_message", "expected_log"),
    [
        (
            make_status_error(anthropic.RateLimitError, 429),
            "限流",
            "rate limited request_id=req_test_123",
        ),
        (
            make_status_error(anthropic.APIStatusError, 500),
            "返回 500",
            "status=500 request_id=req_test_123",
        ),
        (
            anthropic.APIConnectionError(request=API_REQUEST),
            "连不上",
            "connection failed",
        ),
        (
            anthropic.APITimeoutError(request=API_REQUEST),
            "连不上",
            "connection failed",
        ),
        (anthropic.AnthropicError("缺少凭据"), "SDK 报错", "sdk error"),
    ],
)
def test_complete_text_wraps_sdk_errors_and_logs_each_kind(
    install_client, caplog, error, expected_message, expected_log
):
    client = install_client(FakeStream(error=error))

    with caplog.at_level(logging.WARNING, logger="app.services.llm"):
        with pytest.raises(llm.LLMError, match=expected_message) as raised:
            llm.complete_text("系统", "用户")

    assert raised.value.__cause__ is error
    assert expected_log in caplog.text
    assert client.closed is True


def test_complete_text_wraps_error_raised_while_opening_stream(install_client):
    error = make_status_error(anthropic.AuthenticationError, 401)
    client = install_client(enter_error=error)

    with pytest.raises(llm.LLMError, match="返回 401"):
        llm.complete_text("系统", "用户")

    assert client.closed is True


def test_complete_text_does_not_hide_unexpected_errors(install_client):
    install_client(FakeStream(error=KeyError("程序错误")))

    with pytest.raises(KeyError):
        llm.complete_text("系统", "用户")


# --- run_realtime 和总时限 ---


async def test_run_realtime_aborts_request_when_total_deadline_exceeded(
    install_client, monkeypatch, caplog
):
    stream = FakeStream(stall_final=True)
    client = install_client(stream)
    monkeypatch.setattr(llm, "LLM_TIMEOUT_SECONDS", 0)

    with caplog.at_level(logging.WARNING, logger="app.services.llm"):
        with pytest.raises(llm.LLMError, match="总时限"):
            await llm.run_realtime(llm.complete_text, "系统", "用户")

    assert stream.closed is True
    assert client.closed is True
    assert "exceeded total deadline" in caplog.text


async def test_total_deadline_applies_only_inside_run_realtime(monkeypatch):
    complete = AsyncMock(return_value="结果")
    monkeypatch.setattr(llm, "_complete", complete)
    monkeypatch.setattr(llm, "LLM_TIMEOUT_SECONDS", 45)

    assert await llm.run_realtime(llm.complete_text, "系统", "用户") == "结果"
    assert complete.await_args.args[1] == 45

    # 后台任务的调用方式：同样在线程里，但不经过 run_realtime
    assert await asyncio.to_thread(llm.complete_text, "系统", "用户") == "结果"
    assert complete.await_args.args[1] is None


async def test_run_realtime_clears_deadline_after_function_fails(monkeypatch):
    complete = AsyncMock(return_value="结果")
    monkeypatch.setattr(llm, "_complete", complete)

    def failing_function():
        raise ValueError("解析失败")

    with pytest.raises(ValueError):
        await llm.run_realtime(failing_function)

    await asyncio.to_thread(llm.complete_text, "系统", "用户")
    assert complete.await_args.args[1] is None


async def test_run_realtime_passes_arguments_and_keeps_event_loop_free():
    events: list[str] = []
    release = asyncio.Event()
    loop = asyncio.get_running_loop()

    def blocking_function(value, *, suffix):
        events.append("function-start")
        asyncio.run_coroutine_threadsafe(release.wait(), loop).result()
        return f"{value}{suffix}"

    async def probe_event_loop():
        events.append("loop-responsive")
        release.set()

    result, _ = await asyncio.gather(
        llm.run_realtime(blocking_function, "番茄", suffix="炒蛋"),
        probe_event_loop(),
    )

    assert result == "番茄炒蛋"
    assert "loop-responsive" in events


# --- stream_text ---


async def test_stream_text_yields_text_in_order_and_closes_upstream(install_client):
    client = install_client(FakeStream(['{"name":"', '番茄炒蛋"}']))

    texts = await collect_texts(llm.stream_text("系统", "用户"))

    assert texts == ['{"name":"', '番茄炒蛋"}']
    assert client.requests[0]["system"] == "系统"
    assert client.requests[0]["messages"] == [{"role": "user", "content": "用户"}]
    assert client.manager.stream.closed is True
    assert client.closed is True


@pytest.mark.parametrize(
    ("stop_reason", "expected_reason"),
    [("refusal", "拒答"), ("max_tokens", "截断")],
)
async def test_stream_text_fails_after_text_when_reply_is_unusable(
    install_client, stop_reason, expected_reason
):
    final_message = make_final_message("半句", stop_reason=stop_reason)
    install_client(FakeStream(["半句"], final_message=final_message))
    received: list[str] = []

    with pytest.raises(llm.LLMError, match=expected_reason):
        async with aclosing(llm.stream_text("系统", "用户")) as texts:
            async for text in texts:
                received.append(text)

    assert received == ["半句"]


async def test_stream_text_fails_when_reply_has_no_text(install_client):
    install_client(FakeStream([], final_message=make_final_message(None)))

    with pytest.raises(llm.LLMError, match="没有返回正文"):
        await collect_texts(llm.stream_text("系统", "用户"))


async def test_stream_text_wraps_midstream_api_error_and_closes_upstream(
    install_client,
):
    error = make_status_error(anthropic.APIStatusError, 529)
    stream = FakeStream(["半截"], error=error)
    client = install_client(stream)
    received: list[str] = []

    with pytest.raises(llm.LLMError, match="返回 529"):
        async with aclosing(llm.stream_text("系统", "用户")) as texts:
            async for text in texts:
                received.append(text)

    assert received == ["半截"]
    assert stream.closed is True
    assert client.closed is True


async def test_stream_text_aborts_when_deadline_exceeded_between_chunks(
    install_client,
):
    stream = FakeStream(["第一段", "第二段"], stall_after=1)
    client = install_client(stream)
    received: list[str] = []

    with pytest.raises(llm.LLMError, match="总时限"):
        async with aclosing(
            llm.stream_text("系统", "用户", deadline_seconds=0)
        ) as texts:
            async for text in texts:
                received.append(text)

    assert received == ["第一段"]
    assert stream.closed is True
    assert client.closed is True


async def test_stream_text_aborts_when_deadline_exceeded_while_opening(
    install_client,
):
    client = install_client(stall_on_enter=True)

    with pytest.raises(llm.LLMError, match="总时限"):
        await collect_texts(llm.stream_text("系统", "用户", deadline_seconds=0))

    assert client.closed is True


async def test_stream_text_closes_upstream_when_consumer_is_cancelled(
    install_client,
):
    stream = FakeStream(["第一段", "第二段"], stall_after=1)
    client = install_client(stream)
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
    assert stream.closed is True
    assert client.closed is True
    assert stream.delivered == 1


async def test_stream_text_closes_upstream_when_consumer_stops_early(
    install_client,
):
    stream = FakeStream(["第一段", "第二段"])
    client = install_client(stream)

    async with aclosing(llm.stream_text("系统", "用户")) as texts:
        async for _ in texts:
            break

    assert stream.closed is True
    assert client.closed is True


# --- parse_json_object ---


@pytest.mark.parametrize(
    "reply",
    [
        '{"foods": ["火锅"]}',
        '```json\n{"foods": ["火锅"]}\n```',
        '好的，结果如下：\n{"foods": ["火锅"]}\n希望符合要求。',
        '  \n{"foods": ["火锅"]}\n  ',
    ],
)
def test_parse_json_object_tolerates_fences_and_surrounding_text(reply):
    assert llm.parse_json_object(reply) == {"foods": ["火锅"]}


@pytest.mark.parametrize(
    "reply",
    ["", "没有任何 JSON", '["只有", "数组"]', "invalid json {{{", '{"缺右括号": 1'],
)
def test_parse_json_object_raises_decode_error_without_valid_object(reply):
    with pytest.raises(json.JSONDecodeError):
        llm.parse_json_object(reply)


def test_parse_json_object_logs_start_of_unparseable_reply(caplog):
    reply = "抱歉，我无法按要求输出。" + "很长的解释" * 100

    with caplog.at_level(logging.WARNING, logger="app.services.llm"):
        with pytest.raises(json.JSONDecodeError):
            llm.parse_json_object(reply)

    assert "抱歉，我无法按要求输出。" in caplog.text
    assert reply not in caplog.text
