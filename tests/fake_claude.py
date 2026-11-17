"""测试用的假 Claude SDK 客户端：不连网络，可以模拟报错、卡住和被关闭。"""

import asyncio
from types import SimpleNamespace

from app.services import llm


def make_final_message(text: str | None = "正文", stop_reason: str = "end_turn"):
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(
        model="claude-opus-5-5",
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(input_tokens=12, output_tokens=34),
    )


class FakeStream:
    """模拟进入 client.beta.messages.stream(...) 之后拿到的流对象。"""

    def __init__(self, texts=("正文",), **options):
        self.texts = list(texts)
        self.final_message = options.get("final_message") or make_final_message(
            "".join(self.texts)
        )
        self.error = options.get("error")  # 文字发完后（或取最终消息时）抛出
        self.stall_after = options.get("stall_after")  # 发出这么多段后一直卡住
        self.stall_final = options.get("stall_final", False)  # 取最终消息时卡住
        self.delivered = 0
        self.closed = False

    @property
    def text_stream(self):
        return self._iterate_texts()

    async def _wait_forever_if_stalled(self):
        if self.stall_after is not None and self.delivered >= self.stall_after:
            await asyncio.Event().wait()

    async def _iterate_texts(self):
        for text in self.texts:
            await self._wait_forever_if_stalled()
            self.delivered += 1
            yield text
        await self._wait_forever_if_stalled()
        if self.error:
            raise self.error

    async def get_final_message(self):
        if self.stall_final:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return self.final_message

    async def close(self):
        self.closed = True


class FakeStreamManager:
    def __init__(self, stream: FakeStream, enter_error=None, stall_on_enter=False):
        self.stream = stream
        self.enter_error = enter_error
        self.stall_on_enter = stall_on_enter

    async def __aenter__(self):
        if self.stall_on_enter:
            await asyncio.Event().wait()
        if self.enter_error:
            raise self.enter_error
        return self.stream

    async def __aexit__(self, *exc_info):
        await self.stream.close()


class FakeClient:
    def __init__(self, manager: FakeStreamManager):
        self.manager = manager
        self.requests: list[dict] = []
        self.closed = False
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(stream=self._open_stream)
        )

    def _open_stream(self, **request):
        self.requests.append(request)
        return self.manager

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        self.closed = True


def install_fake_client(
    monkeypatch, stream: FakeStream | None = None, **manager_options
) -> FakeClient:
    """把 llm 的客户端工厂换成假客户端，返回它以便检查请求和关闭情况。"""
    client = FakeClient(FakeStreamManager(stream or FakeStream(), **manager_options))
    monkeypatch.setattr(llm, "_create_client", lambda: client)
    return client
