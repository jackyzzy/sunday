"""AnthropicProvider — Anthropic Messages API 实现。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sunday.agent.providers.base import LLMRequest, LLMResponse, ToolCall
from sunday.agent.providers.utils import split_thinking

if TYPE_CHECKING:
    from sunday.config import ModelConfig

_DEFAULT_ENDPOINT = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    """Anthropic Messages API provider。

    支持：
    - Extended thinking（thinking_budget > 0 时写入 body）
    - tool_use / tool_result 消息协议
    - <thinking> 文本标签兼容（部分 Anthropic 兼容模型）
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
        thinking_budget: int,
    ) -> LLMRequest:
        endpoint = model_cfg.base_url or _DEFAULT_ENDPOINT
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body: dict = {
            "model": model_cfg.id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools
        if thinking_budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        return LLMRequest(url=endpoint, headers=headers, body=body)

    def parse_response(self, raw: dict) -> LLMResponse:
        """规范化 Anthropic 原始响应，不修改入参 dict。"""
        content_blocks = raw.get("content", [])

        # 提取工具调用
        tool_call: ToolCall | None = None
        tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
        if tool_use_blocks:
            tb = tool_use_blocks[0]
            tool_call = ToolCall(
                id=tb["id"],
                name=tb["name"],
                arguments=json.dumps(tb.get("input", {}), ensure_ascii=False),
            )

        # 提取 thinking（原生 thinking 块优先，再兼容文本内嵌标签）
        thinking: str | None = None
        text = ""
        thinking_blocks = [b for b in content_blocks if b.get("type") == "thinking"]
        if thinking_blocks:
            thinking = thinking_blocks[0].get("thinking", "")

        # 提取文本（兼容内嵌 <thinking> 标签）
        for block in content_blocks:
            if block.get("type") == "text":
                raw_text = block.get("text", "")
                if thinking is None:
                    t, raw_text = split_thinking(raw_text)
                    if t is not None:
                        thinking = t
                text = raw_text
                break

        return LLMResponse(
            text=text,
            thinking=thinking,
            tool_call=tool_call,
            finish_reason=raw.get("stop_reason", "end_turn"),
            raw_content=content_blocks,  # 构造 tool_result 消息时需要原始 content 数组
        )

    def build_tool_result_messages(
        self,
        response: LLMResponse,
        observation: str,
    ) -> list[dict]:
        assert response.tool_call is not None
        return [
            {
                "role": "assistant",
                "content": response.raw_content,  # 必须是原始 Anthropic content 数组
            },
            {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": response.tool_call.id,
                    "content": observation,
                }],
            },
        ]

    def supports_thinking(self) -> bool:
        return True
