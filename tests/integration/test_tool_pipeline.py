"""T4-6 验证：工具调用全链路集成测试"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import yaml

from sunday.tools.registry import ToolMeta, ToolRegistry


def _make_settings(tmp_path):
    from sunday.config import Settings
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "tools": {"default_timeout": 30, "max_output_chars": 4096},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        return Settings()


async def test_full_pipeline_safe_tool(tmp_path):
    """Guard + Registry + 普通工具全链路：输出经过 Guard 处理"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings)
    meta = ToolMeta(
        name="echo_tool",
        description="回显工具",
        input_schema={"type": "object", "properties": {}},
        is_dangerous=False,
        timeout=5,
    )
    registry.register(meta, AsyncMock(return_value="echo result"))
    result = await registry.execute("echo_tool", {}, "test_session")
    assert "echo result" in result


async def test_dangerous_tool_confirmed(tmp_path):
    """confirmation_handler 返回 True → 工具被执行"""
    settings = _make_settings(tmp_path)
    confirm = AsyncMock(return_value=True)
    registry = ToolRegistry(settings, confirmation_handler=confirm)
    fn = AsyncMock(return_value="executed")
    registry.register(
        ToolMeta(name="del_tool", description="删除", input_schema={},
                 is_dangerous=True, timeout=5),
        fn,
    )
    result = await registry.execute("del_tool", {}, "sess")
    fn.assert_called_once()
    assert "executed" in result


async def test_dangerous_tool_denied(tmp_path):
    """confirmation_handler 返回 False → 工具不执行"""
    settings = _make_settings(tmp_path)
    confirm = AsyncMock(return_value=False)
    registry = ToolRegistry(settings, confirmation_handler=confirm)
    fn = AsyncMock(return_value="should not run")
    registry.register(
        ToolMeta(name="del_tool", description="删除", input_schema={},
                 is_dangerous=True, timeout=5),
        fn,
    )
    result = await registry.execute("del_tool", {}, "sess")
    fn.assert_not_called()
    assert "should not run" not in result


async def test_guard_truncates_in_pipeline(tmp_path):
    """Guard 截断超长输出"""
    settings = _make_settings(tmp_path)
    registry = ToolRegistry(settings)
    meta = ToolMeta(
        name="big_tool",
        description="大输出",
        input_schema={},
        is_dangerous=False,
        timeout=5,
    )
    registry.register(meta, AsyncMock(return_value="x" * 10000))
    result = await registry.execute("big_tool", {}, "sess")
    # 不应超过 max_output_chars 太多（截断后带提示）
    assert len(result) < 10000
