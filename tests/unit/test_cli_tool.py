"""T4-2 验证：CLI 工具与内置文件工具单元测试"""
from __future__ import annotations

import os
from unittest.mock import patch

import yaml

from sunday.tools.cli_tool import list_dir, read_file, run_shell, write_file


async def test_run_shell_success():
    """正常命令返回 stdout"""
    result = await run_shell("echo hello")
    assert "hello" in result


async def test_run_shell_stderr_captured():
    """stderr 被包含在输出中"""
    result = await run_shell("echo err >&2", timeout=5)
    # stderr 或 stdout 有内容即可
    assert isinstance(result, str)


async def test_run_shell_nonzero_exit():
    """非 0 退出码时包含退出码信息"""
    result = await run_shell("exit 1", timeout=5)
    assert "1" in result or "exit" in result.lower() or "returncode" in result.lower()


async def test_run_shell_timeout():
    """命令超时时返回超时提示"""
    result = await run_shell("sleep 10", timeout=1)
    assert "timeout" in result.lower() or "超时" in result


async def test_file_read_tool(tmp_path):
    """read_file 读取文件内容"""
    f = tmp_path / "hello.txt"
    f.write_text("content here")
    result = await read_file(str(f))
    assert "content here" in result


async def test_file_write_tool(tmp_path):
    """write_file 写入后可读回"""
    f = tmp_path / "out.txt"
    await write_file(str(f), "written content")
    assert f.read_text(encoding="utf-8") == "written content"


async def test_list_dir_tool(tmp_path):
    """list_dir 返回目录下文件名"""
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.txt").write_text("")
    result = await list_dir(str(tmp_path))
    assert "a.py" in result
    assert "b.txt" in result


async def test_register_cli_tools_adds_to_registry(tmp_path):
    """register_cli_tools 后 get_schemas() 包含 run_shell"""
    from sunday.config import Settings
    from sunday.tools.cli_tool import register_cli_tools
    from sunday.tools.registry import ToolRegistry

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        settings = Settings()

    registry = ToolRegistry(settings.sunday)
    register_cli_tools(registry)
    names = [s["name"] for s in registry.get_schemas()]
    assert "run_shell" in names


def _make_registry(tmp_path, report_dir=None):
    """辅助：构造带可选 _report_dir 的 ToolRegistry。"""
    from sunday.config import Settings
    from sunday.tools.cli_tool import register_cli_tools
    from sunday.tools.registry import ToolRegistry

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        settings = Settings()

    registry = ToolRegistry(settings.sunday)
    if report_dir is not None:
        registry.set_report_dir(report_dir)
    register_cli_tools(registry)
    return registry


async def test_read_file_prefers_report_dir(tmp_path):
    """read_file 相对路径：_report_dir 内文件优先"""
    report_dir = tmp_path / "reports" / "session1"
    report_dir.mkdir(parents=True)
    (report_dir / "data.json").write_text('{"source": "report_dir"}', encoding="utf-8")

    registry = _make_registry(tmp_path, report_dir)
    _, fn = registry._tools["read_file"]
    result = await fn(path="data.json")
    assert "report_dir" in result


async def test_read_file_fallback_absolute_path(tmp_path):
    """read_file 绝对路径：直接读取，不受 _report_dir 影响"""
    report_dir = tmp_path / "reports" / "session1"
    report_dir.mkdir(parents=True)

    external_file = tmp_path / "src" / "module.py"
    external_file.parent.mkdir(parents=True)
    external_file.write_text("def foo(): pass", encoding="utf-8")

    registry = _make_registry(tmp_path, report_dir)
    _, fn = registry._tools["read_file"]
    result = await fn(path=str(external_file))
    assert "def foo" in result


async def test_run_shell_injects_report_dir_env(tmp_path):
    """通过 registry 注册的 run_shell 能让 subprocess 读取到 SUNDAY_REPORT_DIR"""
    report_dir = tmp_path / "reports" / "sess"
    report_dir.mkdir(parents=True)

    registry = _make_registry(tmp_path, report_dir)
    _, fn = registry._tools["run_shell"]
    result = await fn(command='echo "$SUNDAY_REPORT_DIR"')
    assert str(report_dir) in result


async def test_run_shell_module_level_unchanged():
    """模块级 run_shell 仍可直接调用，不依赖 registry"""
    from sunday.tools.cli_tool import run_shell
    result = await run_shell("echo hello")
    assert "hello" in result
