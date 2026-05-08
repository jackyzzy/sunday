"""LLMProvider Protocol 及相关数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


class LLMAPIError(RuntimeError):
    """LLM API 调用失败，包含服务商返回的原始错误消息。"""

if TYPE_CHECKING:
    from sunday.config import ModelConfig


# ── 请求/响应数据模型 ─────────────────────────────────────────────────────────

@dataclass
class LLMRequest:
    """构造好的 HTTP 请求，由 build_request() 返回。

    显式命名三个字段，避免 tuple[str,dict,dict] 的位置依赖。
    """
    url: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class ToolCall:
    """单次工具调用。"""
    id: str
    name: str
    arguments: str  # JSON 字符串


@dataclass
class LLMResponse:
    """所有 provider 统一的规范化响应。

    text:         模型输出的文本（已剥离 thinking 标签）
    thinking:     思考过程文本，可能为 None
    tool_call:    工具调用（当前仅处理第一个），可能为 None
    finish_reason: 结束原因（stop / end_turn / tool_calls 等）
    raw_content:  provider 原始 content 字段，供 build_tool_result_messages 使用
                  （Anthropic 需要原始 content 数组来构造 tool_result 消息）
    """
    text: str
    thinking: str | None
    tool_call: ToolCall | None
    finish_reason: str
    raw_content: Any = field(default=None, repr=False)


# ── Provider Protocol ─────────────────────────────────────────────────────────

@runtime_checkable
class LLMProvider(Protocol):
    """LLM provider 抽象接口。

    每个 provider 负责三件事：
    1. 构造 HTTP 请求（build_request → LLMRequest）
    2. 解析原始响应为统一格式（parse_response → LLMResponse）
    3. 构造 ReAct 循环中 tool result 所需的消息列表（build_tool_result_messages）

    TODO: 流式响应支持（stream_request / yield_chunks）尚未纳入接口，
          后续加入时需在此处扩展 Protocol，以及更新所有实现类。
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
        """构造请求，返回 LLMRequest。"""
        ...

    def parse_response(self, raw: dict) -> LLMResponse:
        """将 provider 原始响应规范化为 LLMResponse，不修改入参。"""
        ...

    def build_tool_result_messages(
        self,
        response: LLMResponse,
        observation: str,
    ) -> list[dict]:
        """返回追加到对话历史的消息列表（assistant + tool_result）。

        Anthropic 格式：2 条消息（assistant content array + user tool_result）
        OpenAI 格式：2 条消息（assistant tool_calls + role:tool）
        """
        ...

    def supports_thinking(self) -> bool:
        """声明此 provider 是否原生支持 thinking_budget（写入请求体）。"""
        ...
