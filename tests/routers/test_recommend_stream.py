import json
from unittest.mock import MagicMock, patch

import openai


def _chunk(content: str):
    return MagicMock(
        choices=[MagicMock(delta=MagicMock(content=content))]
    )


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
    stream = [_chunk(text[:20]), _chunk(text[20:])]

    with patch(
        "app.routers.recommend_progressive._client"
    ) as client_factory:
        client_factory.return_value.chat.completions.create.return_value = stream
        response = client.post(
            "/api/recommend/steps?stream=1",
            json={"dish_name": "番茄炒蛋", "ingredients": ["番茄", "鸡蛋"]},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    streamed, marker_payload = response.text.split("\n@@JSON@@", 1)
    assert streamed == text
    assert json.loads(marker_payload) == payload
    call = client_factory.return_value.chat.completions.create.call_args
    assert call.kwargs["stream"] is True


def test_steps_stream_emits_error_marker_on_midstream_failure(
    client, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )

    def broken_stream():
        yield _chunk('{"name":"半截')
        raise openai.OpenAIError("stream interrupted")

    with patch(
        "app.routers.recommend_progressive._client"
    ) as client_factory:
        client_factory.return_value.chat.completions.create.return_value = (
            broken_stream()
        )
        response = client.post(
            "/api/recommend/steps?stream=1",
            json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
        )

    assert response.status_code == 200
    assert '{"name":"半截' in response.text
    assert response.text.endswith("\n@@ERR@@")
    assert "@@JSON@@" not in response.text


def test_steps_stream_invalid_final_json_emits_error_marker(
    client, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.recommend_progressive.OPENROUTER_API_KEY", "test-key"
    )
    with patch(
        "app.routers.recommend_progressive._client"
    ) as client_factory:
        client_factory.return_value.chat.completions.create.return_value = [
            _chunk("not-json")
        ]
        response = client.post(
            "/api/recommend/steps?stream=1",
            json={"dish_name": "番茄炒蛋", "ingredients": ["番茄"]},
        )

    assert response.status_code == 200
    assert response.text == "not-json\n@@ERR@@"
