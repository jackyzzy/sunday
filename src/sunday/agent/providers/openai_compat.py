"""OpenAICompatProvider — OpenAI Chat Completions API 兼容实现。

适用于所有 OpenAI 兼容接口的模型：DeepSeek、Qwen、通义、本地 Ollama 等。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sunday.agent.providers.base import LLMRequest, LLMResponse, ToolCall
from sunday.agent.providers.utils import split_thinking

if TYPE_CHECKING:
    from sunday.config import ModelConfig

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatProvider:
    """OpenAI Chat Completions API 兼容 provider。

    支持：
    - 自定义 base_url（DeepSeek、Qwen 等第三方服务）
    - <think>...</think> 标签剥离（DeepSeek-R1 等推理模型）
    - OpenAI 工具调用协议（role: tool + tool_call_id）
    - content 用空串而非 None（DeepSeek 拒绝 null content）
    """

    def build_request(
        self,
        model_cfg: "ModelConfig",
        api_key: str,
        messages: list[dict],
        *,
        system: str,
        tools: list[dict] | None,
        max_tokens: int,
        temperature: float,
        thinking_budget: int,  # OpenAI 兼容接口不支持，忽略
    ) -> LLMRequest:
        base = (model_cfg.base_url or _DEFAULT_BASE_URL).rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        full_messages = (
            [{"role": "system", "content": system}] if system else []
        ) + messages
        body: dict = {
            "model": model_cfg.id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": full_messages,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t.get("input_schema", {}),
                    },
                }
                for t in tools
            ]
        return LLMRequest(url=f"{base}/chat/completions", headers=headers, body=body)

    def parse_response(self, raw: dict) -> LLMResponse:
        """规范化 OpenAI 原始响应，不修改入参 dict。"""
        choice = raw["choices"][0]
        msg = choice["message"]
        raw_text = msg.get("content") or ""

        # 剥离 DeepSeek / 通用 chain-of-thought 标签
        thinking, text = split_thinking(raw_text)

        tool_call: ToolCall | None = None
        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            tool_call = ToolCall(
                id=tc.get("id", "call_0"),
                name=tc["function"]["name"],
                arguments=tc["function"].get("arguments", "{}"),
            )

        return LLMResponse(
            text=text,
            thinking=thinking,
            tool_call=tool_call,
            finish_reason=choice["finish_reason"],
            raw_content=None,  # OpenAI 的 build_tool_result_messages 不需要原始 content
        )

    def build_tool_result_messages(
        self,
        response: LLMResponse,
        observation: str,
    ) -> list[dict]:
        assert response.tool_call is not None
        tc = response.tool_call
        return [
            {
                "role": "assistant",
                # content 用 "" 而非 None：部分 provider（DeepSeek）拒绝 null content
                "content": "",
                "tool_calls": [{
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }],
            },
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": observation,
            },
        ]

    def supports_thinking(self) -> bool:
        return False
