"""模型配置保留 SecretStr，并且只允许可控端点与有界预算。"""

import pytest
from pydantic import ValidationError

from enterprise_agent.config import Settings


def test_key_is_redacted_and_missing_key_does_not_disable_finance_api():
    settings = Settings(llm_api_key="not-a-real-key")
    assert "not-a-real-key" not in repr(settings)
    assert Settings(llm_api_key="").llm_api_key.get_secret_value() == ""


@pytest.mark.parametrize(
    "override",
    [
        {"llm_timeout_seconds": 61},
        {"llm_timeout_seconds": 0},
        {"llm_max_output_tokens": 100000},
        {"agent_confirmation_ttl_seconds": 0},
        {"agent_timezone": "unknown/timezone"},
    ],
)
def test_invalid_budgets_and_timezone_are_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**override)
