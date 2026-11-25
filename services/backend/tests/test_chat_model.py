"""模型传输契约测试：通过 MockTransport 验证真实适配器，不访问外部模型。"""

import json

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from enterprise_agent.agent.chat_model import CompatibleChatModel, response_format
from enterprise_agent.agent.chat_schemas import IntentDecision, PolicyGeneration
from enterprise_agent.config import get_settings


def configure(monkeypatch, handler):
    """配置虚构端点和凭据；禁止在测试日志输出真实配置。"""
    settings = get_settings().model_copy(
        update={
            "llm_api_key": SecretStr("fictional-chat-key"),
            "llm_base_url": "https://model.invalid/v1",
        }
    )
    monkeypatch.setattr("enterprise_agent.agent.chat_model.get_settings", lambda: settings)
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    return settings


def envelope(output, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(output)}}]}


def test_adapter_uses_separate_prompts_and_strict_json_contract(monkeypatch):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fictional-chat-key"
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        output = (
            {"intent": "policy", "query": "成都住宿", "as_of": "2026-11-25"}
            if len(bodies) == 1
            else {
                "status": "insufficient",
                "answer": "没有依据",
                "citations": [],
            }
        )
        return httpx.Response(200, json=envelope(output))

    configure(monkeypatch, handler)
    model = CompatibleChatModel()
    assert model.classify({"message": "成都住宿"}).intent == "policy"
    assert model.answer({"evidence": []}).status == "insufficient"
    assert bodies[0]["messages"][0]["content"] != bodies[1]["messages"][0]["content"]
    # 兼容端点的 JSON 模式不会自动约束字段；两次调用必须携带各自的结构契约。
    for body, field in zip(bodies, ["intent", "status"], strict=True):
        prompt = body["messages"][0]["content"]
        provided_schema = json.loads(prompt.split("不能返回空对象：\n", 1)[1])
        assert field in provided_schema["required"]
        assert provided_schema["additionalProperties"] is False
        transmitted = body["response_format"]["json_schema"]["schema"]
        assert field in transmitted["required"]
        assert transmitted["required"] == list(transmitted["properties"])
    assert "fictional-chat-key" not in str(bodies)


def test_strict_response_schema_covers_nested_citations_without_changing_api_models():
    """严格约束不能漏掉引用对象，同时不能把业务可选字段改成接口必填字段。"""
    original = PolicyGeneration.model_json_schema()
    contract = response_format(PolicyGeneration)["json_schema"]["schema"]
    assert "citations" in contract["required"]
    citation = contract["$defs"]["EvidenceQuote"]
    assert citation["required"] == ["citation_id", "quote"]
    assert citation["additionalProperties"] is False
    assert PolicyGeneration.model_json_schema() == original
    intent = response_format(IntentDecision)["json_schema"]["schema"]
    assert intent["required"] == ["intent", "query", "as_of"]
    assert {"type": "null"} in intent["properties"]["as_of"]["anyOf"]
    assert "default" not in intent["properties"]["as_of"]
    assert IntentDecision(intent="clarify").as_of is None


@pytest.mark.parametrize(
    "mode", ["http", "truncated", "extra", "invalid", "large", "timeout", "empty"]
)
def test_model_errors_are_sanitized(monkeypatch, mode):
    def handler(_request):
        if mode == "http":
            return httpx.Response(401, text="sensitive-provider-detail")
        if mode == "timeout":
            raise httpx.ReadTimeout("sensitive-provider-detail")
        if mode == "large":
            return httpx.Response(200, content=b"x" * 128_001)
        output = {"intent": "policy", "query": "住宿", "as_of": None}
        if mode == "extra":
            output["tenant_id"] = "forged"
        if mode == "invalid":
            output["intent"] = "arbitrary_tool"
        if mode == "empty":
            output = {}
        return httpx.Response(
            200, json=envelope(output, "length" if mode == "truncated" else "stop")
        )

    configure(monkeypatch, handler)
    with pytest.raises(HTTPException) as caught:
        CompatibleChatModel().classify({})
    assert caught.value.status_code in {502, 503}
    assert "sensitive-provider-detail" not in str(caught.value.detail)


def test_missing_key_never_calls_provider(monkeypatch):
    settings = configure(monkeypatch, lambda _: pytest.fail("unexpected external call"))
    settings.llm_api_key = SecretStr("")
    with pytest.raises(HTTPException) as caught:
        CompatibleChatModel().answer({})
    assert caught.value.status_code == 503
