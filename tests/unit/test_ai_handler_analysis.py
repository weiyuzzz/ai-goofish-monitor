import asyncio
from types import SimpleNamespace

import pytest

import src.ai_handler as ai_handler
import src.config as app_config
from src.services.token_usage_service import SOURCE_ANALYSIS


def _build_fake_client(responses_create_impl, chat_create_impl=None):
    responses = SimpleNamespace(create=responses_create_impl)
    chat = SimpleNamespace(
        completions=SimpleNamespace(create=chat_create_impl or responses_create_impl)
    )
    return SimpleNamespace(responses=responses, chat=chat)


def test_get_ai_analysis_stops_after_internal_retries_when_content_is_none(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    call_count = {"value": 0}

    async def fake_create(**_kwargs):
        call_count["value"] += 1
        return SimpleNamespace(output_text="")

    monkeypatch.setattr(ai_handler, "client", _build_fake_client(fake_create))
    monkeypatch.setattr(ai_handler, "MODEL_NAME", "fake-model")
    monkeypatch.setattr(ai_handler, "ENABLE_RESPONSE_FORMAT", True)
    monkeypatch.setattr(app_config, "ENABLE_RESPONSE_FORMAT", True)

    with pytest.raises(ValueError, match="AI响应内容为空"):
        asyncio.run(
            ai_handler.get_ai_analysis(
                {"商品信息": {"商品ID": "1", "商品标题": "测试商品"}},
                image_paths=[],
                prompt_text="请输出 JSON",
            )
        )

    assert call_count["value"] == 4


def test_get_ai_analysis_returns_parsed_json(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    call_count = {"value": 0}

    async def fake_create(**_kwargs):
        call_count["value"] += 1
        return SimpleNamespace(
            output_text=(
                '{"prompt_version":"v1","is_recommended":true,'
                '"reason":"ok","risk_tags":[],"criteria_analysis":{"seller_type":"个人"}}'
            )
        )

    monkeypatch.setattr(ai_handler, "client", _build_fake_client(fake_create))
    monkeypatch.setattr(ai_handler, "MODEL_NAME", "fake-model")
    monkeypatch.setattr(ai_handler, "ENABLE_RESPONSE_FORMAT", True)
    monkeypatch.setattr(app_config, "ENABLE_RESPONSE_FORMAT", True)

    result = asyncio.run(
        ai_handler.get_ai_analysis(
            {"商品信息": {"商品ID": "2", "商品标题": "测试商品2"}},
            image_paths=[],
            prompt_text="请输出 JSON",
        )
    )

    assert result["is_recommended"] is True
    assert call_count["value"] == 1


def test_get_ai_analysis_retries_without_structured_output_when_model_rejects_it(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    request_history = []

    async def fake_create(**kwargs):
        request_history.append(kwargs)
        if len(request_history) == 1:
            raise Exception(
                "Error code: 400 - {'error': {'code': 'InvalidParameter', "
                "'message': 'The parameter `response_format.type` specified in "
                "the request are not valid: `json_object` is not supported by "
                "this model.', 'param': 'response_format.type'}}"
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"prompt_version":"v1","is_recommended":true,'
                            '"reason":"ok","risk_tags":[],"criteria_analysis":{"seller_type":"个人"}}'
                        )
                    )
                )
            ]
        )

    monkeypatch.setattr(ai_handler, "client", _build_fake_client(fake_create))
    monkeypatch.setattr(ai_handler, "MODEL_NAME", "fake-model")
    monkeypatch.setattr(ai_handler, "ENABLE_RESPONSE_FORMAT", True)
    monkeypatch.setattr(app_config, "ENABLE_RESPONSE_FORMAT", True)

    result = asyncio.run(
        ai_handler.get_ai_analysis(
            {"商品信息": {"商品ID": "3", "商品标题": "测试商品3"}},
            image_paths=[],
            prompt_text="请输出 JSON",
        )
    )

    assert result["reason"] == "ok"
    assert request_history[0]["messages"][0]["role"] == "user"
    assert request_history[0]["response_format"]["type"] == "json_object"
    assert "response_format" not in request_history[1]
    assert ai_handler.ENABLE_RESPONSE_FORMAT is True


def test_get_ai_analysis_falls_back_to_responses_when_chat_completions_api_is_missing(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    request_history = []

    async def fake_chat_create(**kwargs):
        request_history.append(("chat", kwargs))
        raise Exception("Error code: 404 - page not found")

    async def fake_responses_create(**kwargs):
        request_history.append(("responses", kwargs))
        if len([item for item in request_history if item[0] == "responses"]) == 1:
            raise Exception(
                "Error code: 400 - {'error': {'code': 'InvalidParameter', "
                "'message': 'The parameter `text.format.type` specified in "
                "the request are not valid: `json_object` is not supported by "
                "this model.', 'param': 'text.format.type'}}"
            )
        return SimpleNamespace(
            output_text=(
                '{"prompt_version":"v1","is_recommended":true,'
                '"reason":"ok","risk_tags":[],"criteria_analysis":{"seller_type":"个人"}}'
            )
        )

    monkeypatch.setattr(
        ai_handler,
        "client",
        _build_fake_client(fake_responses_create, fake_chat_create),
    )
    monkeypatch.setattr(ai_handler, "MODEL_NAME", "fake-model")
    monkeypatch.setattr(ai_handler, "ENABLE_RESPONSE_FORMAT", True)
    monkeypatch.setattr(app_config, "ENABLE_RESPONSE_FORMAT", True)

    result = asyncio.run(
        ai_handler.get_ai_analysis(
            {"商品信息": {"商品ID": "4", "商品标题": "测试商品4"}},
            image_paths=[],
            prompt_text="请输出 JSON",
        )
    )

    assert result["reason"] == "ok"
    assert request_history[0][0] == "chat"
    assert request_history[0][1]["messages"][0]["role"] == "user"
    assert request_history[1][0] == "responses"
    assert request_history[1][1]["text"]["format"]["type"] == "json_object"
    assert request_history[2][0] == "responses"
    assert "text" not in request_history[2][1]


def test_get_ai_analysis_retries_without_temperature_when_gateway_rejects_it(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    request_history = []

    async def fake_create(**kwargs):
        request_history.append(kwargs)
        if len(request_history) == 1:
            raise Exception("temperature is unsupported for this model")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"prompt_version":"v1","is_recommended":true,'
                            '"reason":"ok","risk_tags":[],"criteria_analysis":{"seller_type":"个人"}}'
                        )
                    )
                )
            ]
        )

    monkeypatch.setattr(ai_handler, "client", _build_fake_client(fake_create))
    monkeypatch.setattr(ai_handler, "MODEL_NAME", "fake-model")
    monkeypatch.setattr(ai_handler, "ENABLE_RESPONSE_FORMAT", True)
    monkeypatch.setattr(app_config, "ENABLE_RESPONSE_FORMAT", True)

    result = asyncio.run(
        ai_handler.get_ai_analysis(
            {"商品信息": {"商品ID": "4", "商品标题": "测试商品4"}},
            image_paths=[],
            prompt_text="请输出 JSON",
        )
    )

    assert result["reason"] == "ok"
    assert request_history[0]["temperature"] == 0.1
    assert "temperature" not in request_history[1]


def test_get_ai_analysis_uses_first_json_object_when_model_returns_multiple_objects(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)

    async def fake_create(**_kwargs):
        return SimpleNamespace(
            output_text="""```json
{"prompt_version":"v1","is_recommended":true,"reason":"first","risk_tags":[],"criteria_analysis":{"seller_type":"个人"}}
{"prompt_version":"v1","is_recommended":false,"reason":"second","risk_tags":[],"criteria_analysis":{"seller_type":"商家"}}
```"""
        )

    monkeypatch.setattr(ai_handler, "client", _build_fake_client(fake_create))
    monkeypatch.setattr(ai_handler, "MODEL_NAME", "fake-model")
    monkeypatch.setattr(ai_handler, "ENABLE_RESPONSE_FORMAT", True)
    monkeypatch.setattr(app_config, "ENABLE_RESPONSE_FORMAT", True)

    result = asyncio.run(
        ai_handler.get_ai_analysis(
            {"商品信息": {"商品ID": "5", "商品标题": "测试商品5"}},
            image_paths=[],
            prompt_text="请输出 JSON",
        )
    )

    assert result["is_recommended"] is True
    assert result["reason"] == "first"


def test_get_ai_analysis_records_token_usage_for_successful_call(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    recorded = []

    def fake_record(response, *, model=None, source=None, task_name=None):
        recorded.append(
            {
                "model": model,
                "source": source,
                "task_name": task_name,
                "total_tokens": response.usage.total_tokens,
            }
        )
        return True

    monkeypatch.setattr(ai_handler, "record_response_usage", fake_record)

    async def fake_create(**_kwargs):
        return SimpleNamespace(
            output_text=(
                '{"prompt_version":"v1","is_recommended":true,'
                '"reason":"ok","risk_tags":[],"criteria_analysis":{"seller_type":"个人"}}'
            ),
            usage=SimpleNamespace(
                prompt_tokens=800,
                completion_tokens=200,
                total_tokens=1000,
            ),
        )

    monkeypatch.setattr(ai_handler, "client", _build_fake_client(fake_create))
    monkeypatch.setattr(ai_handler, "MODEL_NAME", "fake-model")
    monkeypatch.setattr(ai_handler, "ENABLE_RESPONSE_FORMAT", True)
    monkeypatch.setattr(app_config, "ENABLE_RESPONSE_FORMAT", True)

    asyncio.run(
        ai_handler.get_ai_analysis(
            {"任务名称": "mac mini", "商品信息": {"商品ID": "6", "商品标题": "测试商品6"}},
            image_paths=[],
            prompt_text="请输出 JSON",
        )
    )

    assert recorded == [
        {
            "model": "fake-model",
            "source": SOURCE_ANALYSIS,
            "task_name": "mac mini",
            "total_tokens": 1000,
        }
    ]


def test_get_ai_analysis_records_tokens_for_every_retry_attempt(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    recorded = []

    def fake_record(response, *, model=None, source=None, task_name=None):
        recorded.append(response.usage.total_tokens)
        return True

    monkeypatch.setattr(ai_handler, "record_response_usage", fake_record)

    async def fake_create(**_kwargs):
        # 正文不是 JSON，会触发内部重试；每次请求消耗的 token 都必须记账。
        return SimpleNamespace(
            output_text="not-json",
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
            ),
        )

    monkeypatch.setattr(ai_handler, "client", _build_fake_client(fake_create))
    monkeypatch.setattr(ai_handler, "MODEL_NAME", "fake-model")
    monkeypatch.setattr(ai_handler, "ENABLE_RESPONSE_FORMAT", False)
    monkeypatch.setattr(app_config, "ENABLE_RESPONSE_FORMAT", False)

    with pytest.raises(Exception):
        asyncio.run(
            ai_handler.get_ai_analysis(
                {"商品信息": {"商品ID": "7", "商品标题": "测试商品7"}},
                image_paths=[],
                prompt_text="请输出 JSON",
            )
        )

    assert recorded == [150, 150, 150, 150]
