"""T4-1 验证：ToolRegistry 单元测试（mock httpx，无真实命令）"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import yaml

from sunday.tools.registry import ToolMeta, ToolRegistry


def _make_settings(tmp_path, allow_list=None, deny_list=None, timeout=30):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "tools": {
            "default_timeout": timeout,
            "max_output_chars": 4096,
            "allow_list": allow_list or [],
            "deny_list": deny_list or [],
        },
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        return Settings()


def _make_meta(name="test_tool", dangerous=False) -> ToolMeta:
    return ToolMeta(
        name=name,
        description="测试工具",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        is_dangerous=dangerous,
        timeout=5,
    )


# ── 注册与 schemas ────────────────────────────────────────────────────────────

async def test_register_and_get_schemas(tmp_path):
    """注册工具后 get_schemas() 包含该工具"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings.sunday)
    meta = _make_meta("my_tool")
    registry.register(meta, AsyncMock(return_value="ok"))
    schemas = registry.get_schemas()
    assert any(s["name"] == "my_tool" for s in schemas)


async def test_get_schemas_empty(tmp_path):
    """空 registry 返回空列表"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings.sunday)
    assert registry.get_schemas() == []


# ── allow/deny list ───────────────────────────────────────────────────────────

async def test_allow_list_filters(tmp_path):
    """allow_list 非空时只有列表内工具可执行"""
    settings = _make_settings(tmp_path, allow_list=["allowed_tool"])
    registry = ToolRegistry(settings.sunday)
    registry.register(_make_meta("allowed_tool"), AsyncMock(return_value="ok"))
    registry.register(_make_meta("blocked_tool"), AsyncMock(return_value="bad"))
    result = await registry.execute("blocked_tool", {}, "sess")
    assert "拒绝" in result or "denied" in result.lower() or "not allowed" in result.lower()


async def test_deny_list_blocks(tmp_path):
    """deny_list 内的工具被拒绝"""
    settings = _make_settings(tmp_path, deny_list=["bad_tool"])
    registry = ToolRegistry(settings.sunday)
    registry.register(_make_meta("bad_tool"), AsyncMock(return_value="secret"))
    result = await registry.execute("bad_tool", {}, "sess")
    assert "拒绝" in result or "denied" in result.lower()


# ── 正常执行 ──────────────────────────────────────────────────────────────────

async def test_execute_success(tmp_path):
    """正常执行返回工具结果"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings.sunday)
    fn = AsyncMock(return_value="tool output")
    registry.register(_make_meta("ok_tool"), fn)
    result = await registry.execute("ok_tool", {"x": "1"}, "sess")
    assert "tool output" in result
    fn.assert_called_once()


async def test_execute_unknown_tool(tmp_path):
    """未知工具返回错误字符串，不抛异常"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings.sunday)
    result = await registry.execute("no_such_tool", {}, "sess")
    assert "unknown" in result.lower() or "未知" in result or "not found" in result.lower()


async def test_execute_tool_exception(tmp_path):
    """工具内部抛异常返回格式化错误字符串，不抛出"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings.sunday)
    fn = AsyncMock(side_effect=RuntimeError("oops"))
    registry.register(_make_meta("broken_tool"), fn)
    result = await registry.execute("broken_tool", {}, "sess")
    assert "oops" in result or "error" in result.lower() or "错误" in result


async def test_execute_timeout(tmp_path):
    """工具超时返回超时错误字符串，不抛出"""
    import asyncio
    settings = _make_settings(tmp_path, timeout=1)
    registry = ToolRegistry(settings.sunday)

    async def slow_fn(**kwargs):
        await asyncio.sleep(10)
        return "never"

    meta = ToolMeta(
        name="slow_tool",
        description="慢工具",
        input_schema={},
        is_dangerous=False,
        timeout=1,
    )
    registry.register(meta, slow_fn)
    result = await registry.execute("slow_tool", {}, "sess")
    assert "timeout" in result.lower() or "超时" in result


# ── 危险工具确认 ───────────────────────────────────────────────────────────────

async def test_execute_dangerous_requires_confirm(tmp_path):
    """is_dangerous=True 时调用 confirmation_handler"""
    settings = _make_settings(tmp_path)
    confirm = AsyncMock(return_value=True)
    registry = ToolRegistry(settings.sunday, confirmation_handler=confirm)
    fn = AsyncMock(return_value="done")
    registry.register(_make_meta("danger_tool", dangerous=True), fn)
    result = await registry.execute("danger_tool", {}, "sess")
    confirm.assert_called_once()
    assert "done" in result


async def test_execute_dangerous_denied(tmp_path):
    """confirm 返回 False 时返回拒绝字符串，工具未被调用"""
    settings = _make_settings(tmp_path)
    confirm = AsyncMock(return_value=False)
    registry = ToolRegistry(settings.sunday, confirmation_handler=confirm)
    fn = AsyncMock(return_value="done")
    registry.register(_make_meta("danger_tool", dangerous=True), fn)
    result = await registry.execute("danger_tool", {}, "sess")
    fn.assert_not_called()
    assert "取消" in result or "cancelled" in result.lower() or "denied" in result.lower()
