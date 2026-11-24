"""OpenAI 兼容 JSON 提取适配器；没有配置时显式失败，不自动用假模型。"""

import json
import logging
from typing import Protocol

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from enterprise_agent.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from enterprise_agent.agent.schemas import Extraction
from enterprise_agent.config import get_settings

logger = logging.getLogger(__name__)


class Extractor(Protocol):
    def extract(self, context: dict) -> Extraction: ...


class CompatibleExtractor:
    def extract(self, context: dict) -> Extraction:
        settings = get_settings()
        key = settings.llm_api_key.get_secret_value()
        if not key:
            raise HTTPException(503, "请在后端 .env 配置 LLM_API_KEY 后重试；也可使用表单创建草稿")
        try:
            # 禁止重定向、隐式重试和环境代理转发凭据；端点只来自部署配置。
            with httpx.Client(timeout=settings.llm_timeout_seconds, trust_env=False) as client:
                with client.stream(
                    "POST",
                    f"{settings.llm_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": settings.llm_model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": settings.llm_max_output_tokens,
                    },
                ) as response:
                    if response.status_code != 200:
                        logger.warning(
                            "Model request failed status=%s prompt=%s",
                            response.status_code,
                            PROMPT_VERSION,
                        )
                        raise HTTPException(503, "模型暂不可用，请稍后重试或使用表单；尚未创建草稿")
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 128_000:
                            raise HTTPException(502, "模型返回过大，已停止处理；尚未创建草稿")
            envelope = json.loads(data)
            choice = envelope["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("incomplete output")
            result = Extraction.model_validate_json(choice["message"]["content"])
            logger.info(
                "Model extraction complete prompt=%s model=%s", PROMPT_VERSION, settings.llm_model
            )
            return result
        except httpx.HTTPError as exc:
            raise HTTPException(503, "模型连接失败或超时，请重试或使用表单；尚未创建草稿") from exc
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise HTTPException(502, "模型未返回有效的报销信息，请换一种表述或使用表单") from exc


def get_extractor() -> Extractor:
    return CompatibleExtractor()
