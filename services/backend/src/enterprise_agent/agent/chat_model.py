"""沿用 httpx 和现有 OpenAI 兼容 JSON 协议；每轮至多分类、作答两次有界调用。"""

import json
import logging
from typing import Protocol, TypeVar

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from enterprise_agent.agent.chat_prompts import ANSWER_PROMPT, INTENT_PROMPT
from enterprise_agent.agent.chat_schemas import IntentDecision, PolicyGeneration
from enterprise_agent.config import get_settings

Output = TypeVar("Output", bound=BaseModel)
logger = logging.getLogger(__name__)


def response_format(schema: type[BaseModel]) -> dict:
    """生成端点可实际约束字段的严格结构，保持 HTTP 业务协议与模型输出协议分离。

    json_object 只表示合法 JSON；当前兼容端点曾反复返回空对象而结束生成。
    json_schema 明确提供字段与枚举，不能依靠在提示词中粘贴 schema 替代传输参数。
    严格模式要求每个对象的属性全部必填；可空字段仍允许 null，省略默认值仅作用于
    本次模型请求，不修改 Pydantic 的默认值、公共 OpenAPI 或数据库结构。
    """
    contract = schema.model_json_schema()

    def require_properties(node: object) -> None:
        """递归覆盖 $defs、引用条目及可空分支，避免只约束最外层对象。"""
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["required"] = list(node.get("properties", {}))
                node["additionalProperties"] = False
            for child in node.values():
                require_properties(child)
        elif isinstance(node, list):
            for child in node:
                require_properties(child)

    require_properties(contract)
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "strict": True, "schema": contract},
    }


class ChatModel(Protocol):
    """依赖注入便于测试分类和作答，不在业务代码内回退到假模型。"""

    def classify(self, context: dict) -> IntentDecision: ...

    def answer(self, context: dict) -> PolicyGeneration: ...


class CompatibleChatModel:
    """独立适配器避免改动已验收的 Extractor；共享现有部署配置及凭据。"""

    def _generate(self, prompt: str, context: dict, schema: type[Output]) -> Output:
        settings = get_settings()
        key = settings.llm_api_key.get_secret_value()
        if not key:
            raise HTTPException(503, "尚未配置模型，无法识别对话或回答制度问题")
        # 提示词解释业务语义，response_format 约束输出形状；返回后仍进行本地校验。
        structured_prompt = (
            f"{prompt}\n必须返回一个符合以下 JSON Schema 的对象，不能返回空对象：\n"
            f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
        )
        try:
            # 不跟随重定向、不读环境代理、不隐式重试；问题和证据不写日志。
            with httpx.Client(timeout=settings.llm_timeout_seconds, trust_env=False) as client:
                with client.stream(
                    "POST",
                    f"{settings.llm_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": settings.llm_model,
                        "messages": [
                            {"role": "system", "content": structured_prompt},
                            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                        ],
                        "response_format": response_format(schema),
                        "max_tokens": settings.llm_max_output_tokens,
                    },
                ) as response:
                    if response.status_code != 200:
                        # 仅记录阶段及状态，不记录提供商响应正文、用户输入或凭据。
                        logger.warning(
                            "Chat provider rejected schema=%s status=%s",
                            schema.__name__,
                            response.status_code,
                        )
                        raise HTTPException(503, "对话模型暂不可用，请稍后重试")
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 128_000:
                            raise HTTPException(502, "模型输出超过上限，已停止处理")
            choice = json.loads(data)["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("incomplete model response")
            return schema.model_validate_json(choice["message"]["content"])
        except httpx.HTTPError as exc:
            raise HTTPException(503, "对话模型连接失败或超时，请稍后重试") from exc
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            # 不输出 ValidationError 本身，它可能包含模型原文；保留分类/作答阶段供定位。
            logger.warning(
                "Chat output invalid schema=%s cause=%s", schema.__name__, type(exc).__name__
            )
            raise HTTPException(502, "模型未返回有效的对话结构，请重新表述或稍后重试") from exc

    def classify(self, context: dict) -> IntentDecision:
        """只识别固定意图，不让模型返回可执行的函数或 SQL。"""
        return self._generate(INTENT_PROMPT, context, IntentDecision)

    def answer(self, context: dict) -> PolicyGeneration:
        """只接收经过权限筛选和字段裁剪的证据，不接收 ORM 对象。"""
        return self._generate(ANSWER_PROMPT, context, PolicyGeneration)


def get_chat_model() -> ChatModel:
    """FastAPI 注入入口；创建对象本身不会发起外部请求。"""
    return CompatibleChatModel()
