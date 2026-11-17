"""Claude API 封装：全项目的大模型调用都从这里走。

调用点只负责拼提示词和解析业务字段；客户端、请求参数、结束原因检查、日志和时限都在这里。
对外只有四样：complete_text（同步补全）、run_realtime（给实时接口加总时限）、
stream_text（异步流式）、parse_json_object（从回复里取 JSON），失败统一抛 LLMError。
需要 Python 3.11+（asyncio.timeout）。
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
from typing import Any

import anthropic

from app.config import (
    ANTHROPIC_API_KEY,
    CLAUDE_EFFORT,
    CLAUDE_MODEL,
    LLM_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

# 思考也算在输出里；这么大的上限只有流式请求才允许，所以所有调用都走流式
MAX_OUTPUT_TOKENS = 128000
SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# 官方拒答兜底只对这些模型有效；其余模型（如 Haiku 5.5）文档要求不要带这个参数
MODELS_WITH_SERVER_SIDE_FALLBACK = (
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
)
UNPARSEABLE_REPLY_LOG_CHARS = 200

# 实时接口单次补全的总时限（秒）。由 run_realtime 设置并随 asyncio.to_thread 带进线程；
# 没设置（后台任务）就只有相邻数据之间的读超时
_total_deadline_seconds: ContextVar[float | None] = ContextVar(
    "llm_total_deadline_seconds", default=None
)


class LLMError(Exception):
    """这一次调用没拿到可用的正文：接口报错、连不上、超时、拒答、被截断或空回复。"""


def _create_client() -> anthropic.AsyncAnthropic:
    # timeout 在流式请求里是相邻数据之间的读超时，不是整次请求的时限
    return anthropic.AsyncAnthropic(
        api_key=ANTHROPIC_API_KEY, timeout=LLM_TIMEOUT_SECONDS
    )


def _build_request(system_prompt: str, user_prompt: str, model: str | None) -> dict:
    chosen_model = model or CLAUDE_MODEL
    request: dict[str, Any] = {
        "model": chosen_model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "output_config": {"effort": CLAUDE_EFFORT},
    }
    if chosen_model in MODELS_WITH_SERVER_SIDE_FALLBACK:
        # Claude 因安全策略拒答时，服务端在同一个请求里换模型接着答
        request["betas"] = [SERVER_SIDE_FALLBACK_BETA]
        request["fallbacks"] = "default"
    return request


@contextmanager
def _translated_errors(deadline_seconds: float | None):
    """按类别记日志，再统一包成 LLMError，调用点据此走各自的兜底。"""
    try:
        yield
    except TimeoutError as error:
        logger.warning(
            "claude call exceeded total deadline of %s seconds, aborted",
            deadline_seconds,
        )
        raise LLMError(
            f"大模型调用超过总时限 {deadline_seconds} 秒，已中止"
        ) from error
    except anthropic.RateLimitError as error:
        logger.warning("claude rate limited request_id=%s: %s", error.request_id, error)
        raise LLMError("Claude 接口限流") from error
    except anthropic.APIStatusError as error:
        logger.error(
            "claude api error status=%s request_id=%s: %s",
            error.status_code,
            error.request_id,
            error,
        )
        raise LLMError(f"Claude 接口返回 {error.status_code}") from error
    except anthropic.APIConnectionError as error:
        logger.error("claude api connection failed: %s", error)
        raise LLMError("连不上 Claude 接口") from error
    except anthropic.AnthropicError as error:
        logger.error("claude sdk error: %s", error)
        raise LLMError(f"Claude SDK 报错：{error}") from error


def _require_text(final_message: Any, started_at: float) -> str:
    """记一行用量日志；拒答、截断、空正文都算失败。"""
    usage = final_message.usage
    logger.info(
        "claude done model=%s stop_reason=%s input_tokens=%s output_tokens=%s seconds=%.1f",
        final_message.model,
        final_message.stop_reason,
        usage.input_tokens,
        usage.output_tokens,
        time.monotonic() - started_at,
    )
    text = "".join(
        block.text for block in final_message.content if block.type == "text"
    )
    if final_message.stop_reason == "refusal":
        reason = "Claude 拒答，服务端兜底也没有给出回答"
    elif final_message.stop_reason == "max_tokens":
        reason = f"输出超过 max_tokens={MAX_OUTPUT_TOKENS} 被截断"
    elif not text.strip():
        reason = "Claude 没有返回正文"
    else:
        return text
    logger.warning("claude reply unusable: %s", reason)
    raise LLMError(reason)


async def _complete(request: dict, deadline_seconds: float | None) -> str:
    started_at = time.monotonic()
    with _translated_errors(deadline_seconds):
        async with (
            asyncio.timeout(deadline_seconds),
            _create_client() as client,
            client.beta.messages.stream(**request) as stream,
        ):
            final_message = await stream.get_final_message()
    return _require_text(final_message, started_at)


def complete_text(
    system_prompt: str, user_prompt: str, *, model: str | None = None
) -> str:
    """同步发一次补全，返回正文；失败抛 LLMError。

    只能在没有事件循环的线程里调用：后台任务直接调，接口处理函数经 run_realtime 调。
    """
    request = _build_request(system_prompt, user_prompt, model)
    return asyncio.run(_complete(request, _total_deadline_seconds.get()))


async def run_realtime(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """给面向用户的接口用：在线程里跑一个会调用大模型的同步函数，并加上总时限。

    函数里每次补全超过 LLM_TIMEOUT_SECONDS 就中止请求、抛 LLMError，接口随后走各自的兜底。
    后台任务不经过这里，所以只受读超时约束。
    """
    token = _total_deadline_seconds.set(LLM_TIMEOUT_SECONDS)
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    finally:
        _total_deadline_seconds.reset(token)


async def _before(deadline_at: float | None, awaitable: Awaitable[Any]) -> Any:
    """等一个结果，但不超过总时限。每次等待单独计时，时限的作用域就不会跨过 yield。"""
    async with asyncio.timeout_at(deadline_at):
        return await awaitable


async def stream_text(
    system_prompt: str,
    user_prompt: str,
    *,
    deadline_seconds: float | None = None,
) -> AsyncGenerator[str, None]:
    """异步流式补全，逐段产出正文；失败抛 LLMError。

    deadline_seconds 是从发起请求到收完的总时限，不传就只有读超时。
    正文发完后才知道结束原因，所以拒答、截断、空正文是在最后抛出的。
    调用方要用 contextlib.aclosing 包住它，客户端断开时才能立刻关掉上游连接。
    """
    request = _build_request(system_prompt, user_prompt, None)
    started_at = time.monotonic()
    deadline_at = None
    if deadline_seconds is not None:
        deadline_at = asyncio.get_running_loop().time() + deadline_seconds
    with _translated_errors(deadline_seconds):
        async with AsyncExitStack() as stack:
            client = await stack.enter_async_context(_create_client())
            opening = stack.enter_async_context(client.beta.messages.stream(**request))
            stream = await _before(deadline_at, opening)
            texts = aiter(stream.text_stream)
            while (text := await _before(deadline_at, anext(texts, None))) is not None:
                yield text
            final_message = await _before(deadline_at, stream.get_final_message())
    _require_text(final_message, started_at)


def parse_json_object(raw_text: str) -> dict:
    """从模型回复里取出 JSON 对象。

    截取第一个 { 到最后一个 }，所以代码围栏和前后的说明文字都不碍事。
    找不到对象或格式不对时记下回复开头并抛 json.JSONDecodeError，由调用点决定怎么兜底。
    """
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    try:
        if start == -1 or end <= start:
            raise json.JSONDecodeError("回复里没有 JSON 对象", raw_text, 0)
        return json.loads(raw_text[start:end + 1])
    except json.JSONDecodeError:
        logger.warning(
            "claude reply is not a JSON object, first %d chars: %r",
            UNPARSEABLE_REPLY_LOG_CHARS,
            raw_text[:UNPARSEABLE_REPLY_LOG_CHARS],
        )
        raise
