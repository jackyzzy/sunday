"""Phase 1 临时实现：直接 LLM 调用，不走完整 Agent Loop"""
from __future__ import annotations

from sunday.config import Settings


class SimpleAgent:
    def __init__(self, settings: Settings, thinking_level: str = "medium",
                 model_override: str | None = None):
        self.settings = settings
        self.thinking_level = thinking_level
        self.model_override = model_override

    async def run(self, task: str) -> str:
        config = self.settings.sunday
        model_cfg = config.model

        # 解析 model_override（格式：provider/model-id）
        provider = model_cfg.provider
        model_id = model_cfg.id
        if self.model_override:
            parts = self.model_override.split("/", 1)
            if len(parts) == 2:
                provider, model_id = parts
            else:
                model_id = parts[0]

        api_key = self.settings.get_api_key(provider)

        if provider == "anthropic":
            return await self._run_anthropic(task, model_id, api_key, model_cfg)
        elif provider == "openai":
            return await self._run_openai(task, model_id, api_key, model_cfg)
        else:
            raise ValueError(f"Phase 1 暂不支持 provider: {provider}，请使用 anthropic 或 openai")

    async def _run_anthropic(self, task, model_id, api_key, cfg) -> str:
        import httpx

        system_prompt = self._build_system_prompt()
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": model_id,
            "max_tokens": cfg.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": task}],
        }

        # 扩展思考
        budget_map = {"off": 0, "minimal": 512, "low": 1024, "medium": 4096, "high": 8192}
        budget = budget_map.get(self.thinking_level, 4096)
        if budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        # 提取文本内容（跳过 thinking block）
        texts = [
            block["text"]
            for block in data.get("content", [])
            if block.get("type") == "text"
        ]
        return "\n".join(texts)

    async def _run_openai(self, task, model_id, api_key, cfg) -> str:
        import httpx

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model_id,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
            "messages": [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": task},
            ],
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        return data["choices"][0]["message"]["content"]

    def _build_system_prompt(self) -> str:
        from datetime import date
        from sunday.config import settings

        workspace = settings.sunday.agent.workspace_dir
        parts = []

        for fname in ["SOUL.md", "AGENTS.md"]:
            fpath = workspace / fname
            if fpath.exists():
                parts.append(fpath.read_text(encoding="utf-8"))

        parts.append(f"\n当前日期：{date.today().isoformat()}")
        return "\n\n---\n\n".join(p for p in parts if p.strip())
