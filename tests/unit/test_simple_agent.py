"""T1-4 验证：SimpleAgent 单元测试（全程 mock httpx，无真实 API 调用）"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sunday.agent.simple import SimpleAgent


def _make_settings(tmp_path, provider="anthropic", model_id="claude-test"):
    """构造指向 tmp_path 的 Settings，注入假 API key。"""
    import yaml

    from sunday.config import Settings

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "SOUL.md").write_text("# Sunday\n\n身份说明。\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# 操作规则\n\n默认规则。\n", encoding="utf-8")

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(
        yaml.dump({
            "agent": {"workspace_dir": str(workspace)},
            "model": {"provider": provider, "id": model_id},
        })
    )

    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "OPENAI_API_KEY": "sk-openai-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        return Settings()


def _anthropic_response(text: str, extra_blocks: list | None = None) -> dict:
    """构造 Anthropic API 响应 JSON。"""
    content = extra_blocks or []
    content.append({"type": "text", "text": text})
    return {"content": content, "id": "msg_test", "model": "claude-test", "stop_reason": "end_turn"}


def _openai_response(text: str) -> dict:
    """构造 OpenAI API 响应 JSON。"""
    return {
        "choices": [{"message": {"content": text, "role": "assistant"}, "finish_reason": "stop"}],
        "id": "chatcmpl-test",
    }


def _mock_httpx_response(data: dict, status_code: int = 200):
    """构造 mock httpx Response 对象。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = data
    mock_resp.status_code = status_code
    if status_code >= 400:
        from httpx import HTTPStatusError
        mock_resp.raise_for_status.side_effect = HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=MagicMock(),
        )
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


# ── Anthropic 调用 ────────────────────────────────────────────────────────────

async def test_anthropic_success(tmp_path):
    """Anthropic 成功调用返回文本内容"""
    settings = _make_settings(tmp_path)
    agent = SimpleAgent(settings, thinking_level="off")

    mock_resp = _mock_httpx_response(_anthropic_response("你好！"))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await agent.run("你好")

    assert result == "你好！"


async def test_thinking_block_filtered(tmp_path):
    """thinking block 被过滤，只返回 text block 内容"""
    settings = _make_settings(tmp_path)
    agent = SimpleAgent(settings, thinking_level="medium")

    thinking_block = {"type": "thinking", "thinking": "内部思考过程..."}
    mock_resp = _mock_httpx_response(
        _anthropic_response("最终回复", extra_blocks=[thinking_block])
    )
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await agent.run("测试")

    assert result == "最终回复"
    assert "内部思考过程" not in result


async def test_openai_success(tmp_path):
    """OpenAI 成功调用返回文本内容"""
    settings = _make_settings(tmp_path, provider="openai", model_id="gpt-4o")
    agent = SimpleAgent(settings, thinking_level="off")

    mock_resp = _mock_httpx_response(_openai_response("OpenAI 回复"))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with (
        patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "sk-ant-fake",
            "OPENAI_API_KEY": "sk-openai-fake",
        }),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await agent.run("你好")

    assert result == "OpenAI 回复"


# ── model_override 解析 ───────────────────────────────────────────────────────

async def test_model_override_with_provider(tmp_path):
    """model_override 格式 provider/model-id 正确解析"""
    settings = _make_settings(tmp_path)  # 默认 anthropic
    agent = SimpleAgent(settings, thinking_level="off", model_override="openai/gpt-4o")

    mock_resp = _mock_httpx_response(_openai_response("override ok"))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with (
        patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "sk-ant-fake",
            "OPENAI_API_KEY": "sk-openai-fake",
        }),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await agent.run("test")

    # 调用了 OpenAI endpoint
    call_args = mock_client.post.call_args
    assert "openai" in str(call_args)


async def test_model_override_id_only(tmp_path):
    """model_override 仅 model-id（无 provider）时使用默认 provider"""
    settings = _make_settings(tmp_path)  # 默认 anthropic
    agent = SimpleAgent(settings, thinking_level="off", model_override="claude-sonnet-4-6")

    mock_resp = _mock_httpx_response(_anthropic_response("id only ok"))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await agent.run("test")

    # 调用了 Anthropic endpoint
    call_args = mock_client.post.call_args
    assert "anthropic" in str(call_args)


# ── thinking_budget 映射 ──────────────────────────────────────────────────────

async def test_thinking_budget_off(tmp_path):
    """thinking_level=off 时请求 body 不含 thinking 字段"""
    settings = _make_settings(tmp_path)
    agent = SimpleAgent(settings, thinking_level="off")

    mock_resp = _mock_httpx_response(_anthropic_response("ok"))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await agent.run("test")

    # 检查 POST body 不含 thinking
    call_kwargs = mock_client.post.call_args.kwargs
    body = call_kwargs.get("json", {})
    assert "thinking" not in body, "thinking_level=off 时不应有 thinking 字段"


async def test_thinking_budget_high(tmp_path):
    """thinking_level=high 时 budget_tokens=8192"""
    settings = _make_settings(tmp_path)
    agent = SimpleAgent(settings, thinking_level="high")

    mock_resp = _mock_httpx_response(_anthropic_response("ok"))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await agent.run("test")

    call_kwargs = mock_client.post.call_args.kwargs
    body = call_kwargs.get("json", {})
    assert "thinking" in body, "thinking_level=high 时应有 thinking 字段"
    assert body["thinking"]["budget_tokens"] == 8192


# ── 错误处理 ──────────────────────────────────────────────────────────────────

async def test_unknown_provider_raises(tmp_path):
    """未知 provider 应抛出 ValueError"""
    settings = _make_settings(tmp_path)
    agent = SimpleAgent(settings, thinking_level="off", model_override="cohere/command")

    with pytest.raises(ValueError, match="provider"):
        await agent.run("test")


async def test_http_error_propagates(tmp_path):
    """HTTP 错误应正确上抛"""
    import httpx

    settings = _make_settings(tmp_path)
    agent = SimpleAgent(settings, thinking_level="off")

    error_resp = MagicMock()
    error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="401 Unauthorized",
        request=MagicMock(),
        response=MagicMock(),
    )
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=error_resp)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await agent.run("test")


# ── 系统提示 ──────────────────────────────────────────────────────────────────

async def test_system_prompt_includes_soul(tmp_path):
    """系统提示应包含 SOUL.md 内容"""
    settings = _make_settings(tmp_path)
    agent = SimpleAgent(settings, thinking_level="off")

    mock_resp = _mock_httpx_response(_anthropic_response("ok"))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    # _build_system_prompt 内部 `from sunday.config import settings`，patch 模块级单例
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        patch("sunday.config.settings", settings),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await agent.run("test")

    call_kwargs = mock_client.post.call_args.kwargs
    body = call_kwargs.get("json", {})
    system_prompt = body.get("system", "")
    assert "Sunday" in system_prompt, "系统提示应包含 SOUL.md 中的 Sunday 身份信息"
