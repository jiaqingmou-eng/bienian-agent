"""校验外部模型的契约、错误降级与参考样例；不会连接真实模型。"""

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError

from enterprise_agent.agent.model import CompatibleExtractor
from enterprise_agent.agent.schemas import DraftCandidate
from enterprise_agent.agent.workflow import validate_candidate
from enterprise_agent.config import Settings, get_settings

DATASET = Path(__file__).resolve().parents[1] / "evals" / "expense_cases.json"
CASES = json.loads(DATASET.read_text(encoding="utf-8"))


def test_chinese_eval_dataset_was_not_corrupted_by_shell_encoding():
    assert len(CASES) == 30
    assert all(re.search(r"[\u4e00-\u9fff]", case["message"]) for case in CASES)
    assert all("???" not in case["message"] for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_reference_cases_have_expected_validation(case):
    # 只证明参考提取后的确定性校验，不把 fake 输出当成自然语言模型质量。
    missing, payload = validate_candidate(
        DraftCandidate.model_validate(case["reference"]["candidate"])
    )
    if case["expected_status"] == "awaiting_confirmation":
        assert not missing and payload
        assert sum(item["amount_fen"] for item in payload["items"]) == case["expected_total_fen"]
    elif case["reference"]["intent"] == "draft":
        assert missing and payload is None


def configure(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "llm_api_key": SecretStr("fake-api-key-not-real"),
            "llm_base_url": "https://model.invalid/v1",
            "llm_model": "fake-model",
        }
    )
    monkeypatch.setattr("enterprise_agent.agent.model.get_settings", lambda: settings)
    return settings


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )


def test_compatible_model_sends_json_request_and_validates_output(monkeypatch):
    configure(monkeypatch)

    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fake-api-key-not-real"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert "fake-api-key-not-real" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(CASES[0]["reference"])},
                    }
                ]
            },
        )

    transport(monkeypatch, handler)
    result = CompatibleExtractor().extract({"anchor_date": "2026-11-23"})
    assert result.candidate.items[0].amount_yuan == "286.00"


@pytest.mark.parametrize(
    "status,body",
    [
        (401, {"error": "sensitive provider message"}),
        (429, {}),
        (500, {}),
        (200, {"choices": []}),
        (200, {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}),
        (
            200,
            {"choices": [{"finish_reason": "stop", "message": {"content": '{"tenant_id":"bad"}'}}]},
        ),
    ],
)
def test_invalid_provider_response_is_sanitized(monkeypatch, status, body):
    configure(monkeypatch)
    transport(monkeypatch, lambda _: httpx.Response(status, json=body))
    with pytest.raises(HTTPException) as error:
        CompatibleExtractor().extract({})
    assert error.value.status_code in {502, 503}
    assert "sensitive" not in str(error.value.detail)
    assert "fake-api-key" not in str(error.value.detail)


def test_missing_key_does_not_use_fake_or_external_model(monkeypatch):
    settings = configure(monkeypatch)
    settings.llm_api_key = SecretStr("")
    transport(monkeypatch, lambda _: pytest.fail("must not call provider without key"))
    with pytest.raises(HTTPException) as error:
        CompatibleExtractor().extract({})
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.invalid/v1",
        "https://key:secret@model.invalid",
        "https://model.invalid?api_key=secret",
        "file:///tmp/model",
    ],
)
def test_invalid_provider_endpoints_are_rejected(url):
    with pytest.raises(ValidationError):
        Settings(llm_base_url=url)
