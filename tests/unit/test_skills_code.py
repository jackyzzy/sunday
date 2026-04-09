"""skills/code/tools.py — run_python 单元测试"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import yaml


@pytest.fixture
def registry_with_report_dir(tmp_path):
    """返回已设置 _report_dir、已调用 register_cli_tools 的 ToolRegistry。"""
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

    report_dir = tmp_path / "reports" / "sess"
    report_dir.mkdir(parents=True)

    registry = ToolRegistry(settings.sunday)
    registry.set_report_dir(report_dir)
    register_cli_tools(registry)

    return registry, report_dir


async def test_run_python_has_report_dir_env(registry_with_report_dir):
    """registry 内的 run_python subprocess 能读取到 SUNDAY_REPORT_DIR"""
    registry, report_dir = registry_with_report_dir
    _, fn = registry._tools["run_python"]
    result = await fn(code="import os; print(os.environ.get('SUNDAY_REPORT_DIR', 'NOT_SET'))")
    assert str(report_dir) in result, f"Expected report_dir in result, got: {result}"


async def test_run_python_can_write_to_report_dir(registry_with_report_dir):
    """run_python 写入 $SUNDAY_REPORT_DIR 下的文件，文件出现在正确目录"""
    registry, report_dir = registry_with_report_dir
    _, fn = registry._tools["run_python"]
    code = (
        "import os, json\n"
        "d = os.environ.get('SUNDAY_REPORT_DIR', '')\n"
        "import pathlib; pathlib.Path(d).mkdir(parents=True, exist_ok=True)\n"
        "with open(os.path.join(d, 'result.json'), 'w') as f:\n"
        "    json.dump({'ok': True}, f)\n"
        "print('done')"
    )
    result = await fn(code=code)
    assert "done" in result
    assert (report_dir / "result.json").exists(), "result.json 应写入 report_dir"


async def test_run_python_module_level_still_works():
    """skills/code/tools.py 的模块级 run_python 函数仍可直接调用"""
    import importlib.util
    from pathlib import Path
    skill_file = Path(__file__).parents[2] / "skills" / "code" / "tools.py"
    spec = importlib.util.spec_from_file_location("skills.code.tools", skill_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = await mod.run_python(code="print('hello')")
    assert "hello" in result
