"""共享 LLM 调用客户端 — 消除 Planner/Executor/Verifier/MemoryManager 中的重复代码"""
from __future__ import annotations

import httpx


class LLMClient:
    """最小化 LLM 调用封装，支持 Anthropic 和 OpenAI 兼容接口。

    各组件按需传入参数，不持有状态。
    """

    @staticmethod
    async def call(
        model_cfg,
        api_key: str,
        messages: list[dict],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0,
        thinking_budget: int = 0,
        timeout: float = 120,
    ) -> dict:
        """统一 LLM 调用接口，返回规范化 dict。

        返回 dict 结构：
          - content: list[{"type": "text"/"tool_use", ...}]  (Anthropic 格式)
          - stop_reason / finish_reason: str
          - tool_calls: list[{"id", "name", "arguments"}] | None  (提取自 content)
        """
        if model_cfg.provider == "anthropic":
            return await LLMClient._call_anthropic(
                model_cfg, api_key, messages,
                system=system, tools=tools,
                max_tokens=max_tokens or model_cfg.max_tokens,
                temperature=temperature,
                thinking_budget=thinking_budget,
                timeout=timeout,
            )
        elif model_cfg.provider == "openai":
            return await LLMClient._call_openai(
                model_cfg, api_key, messages,
                system=system, tools=tools,
                max_tokens=max_tokens or model_cfg.max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
        else:
            raise ValueError(f"不支持的 provider: {model_cfg.provider}")

    @staticmethod
    async def call_text(
        model_cfg,
        api_key: str,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0,
        timeout: float = 60,
    ) -> str:
        """简化接口：单轮文本请求，直接返回字符串。"""
        messages = [{"role": "user", "content": prompt}]
        result = await LLMClient.call(
            model_cfg, api_key, messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        return LLMClient.extract_text(result)

    @staticmethod
    def extract_text(data: dict) -> str:
        """从规范化返回 dict 中提取文本内容。"""
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
        return ""

    @staticmethod
    def split_thinking(raw: str) -> tuple[str | None, str]:
        """剥离 thinking 标签，返回 (thinking, rest)。

        支持：
          - Anthropic extended thinking: <thinking>...</thinking>
          - DeepSeek / 通用 chain-of-thought: <think>...</think>
        """
        for open_tag, close_tag in [("<thinking>", "</thinking>"), ("<think>", "</think>")]:
            if open_tag in raw and close_tag in raw:
                start = raw.index(open_tag) + len(open_tag)
                end = raw.index(close_tag)
                thinking = raw[start:end].strip()
                rest = raw[raw.index(close_tag) + len(close_tag):].strip()
                return thinking, rest
        return None, raw

    @staticmethod
    def extract_tool_call(data: dict) -> tuple[str, str, str] | None:
        """从规范化返回 dict 中提取工具调用。返回 (name, arguments_str, id) 或 None。"""
        tool_calls = data.get("tool_calls")
        if not tool_calls:
            return None
        tc = tool_calls[0]
        return tc.get("name", ""), tc.get("arguments", "{}"), tc.get("id", "call_0")

    # ── 内部实现 ──────────────────────────────────────────────────────────

    @staticmethod
    async def _call_anthropic(
        model_cfg, api_key: str, messages: list[dict], *,
        system: str, tools: list[dict] | None, max_tokens: int,
        temperature: float, thinking_budget: int, timeout: float,
    ) -> dict:
        import json

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
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

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages", headers=headers, json=body
            )
            resp.raise_for_status()
            data = resp.json()

        # 提取工具调用到统一格式
        tool_use_blocks = [b for b in data.get("content", []) if b.get("type") == "tool_use"]
        if tool_use_blocks:
            tb = tool_use_blocks[0]
            data["tool_calls"] = [{
                "id": tb["id"],
                "name": tb["name"],
                "arguments": json.dumps(tb.get("input", {}), ensure_ascii=False),
            }]

        # 提取 thinking 块（供 Planner 使用）
        thinking_blocks = [b for b in data.get("content", []) if b.get("type") == "thinking"]
        if thinking_blocks:
            data["thinking"] = thinking_blocks[0].get("thinking", "")
        else:
            # 处理文本块中内嵌的 <think> 标签（部分 Anthropic 兼容模型）
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "")
                    thinking, rest = LLMClient.split_thinking(text)
                    if thinking is not None:
                        block["text"] = rest
                        data["thinking"] = thinking
                    break

        return data

    @staticmethod
    async def _call_openai(
        model_cfg, api_key: str, messages: list[dict], *,
        system: str, tools: list[dict] | None, max_tokens: int,
        temperature: float, timeout: float,
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        full_messages = ([{"role": "system", "content": system}] if system else []) + messages
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

        base = (model_cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base}/chat/completions", headers=headers, json=body
            )
            resp.raise_for_status()
            data = resp.json()

        # 规范化为统一格式
        choice = data["choices"][0]
        msg = choice["message"]
        raw_text = msg.get("content") or ""

        # 剥离 DeepSeek / 通用 chain-of-thought <think>...</think> 标签
        thinking: str | None = None
        for open_tag, close_tag in [("<thinking>", "</thinking>"), ("<think>", "</think>")]:
            if open_tag in raw_text and close_tag in raw_text:
                start = raw_text.index(open_tag) + len(open_tag)
                end = raw_text.index(close_tag)
                thinking = raw_text[start:end].strip()
                raw_text = raw_text[raw_text.index(close_tag) + len(close_tag):].strip()
                break

        result: dict = {
            "finish_reason": choice["finish_reason"],
            "content": [{"type": "text", "text": raw_text}],
        }
        if thinking:
            result["thinking"] = thinking
        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            result["tool_calls"] = [{
                "id": tc.get("id", "call_0"),
                "name": tc["function"]["name"],
                "arguments": tc["function"].get("arguments", "{}"),
            }]
        return result
