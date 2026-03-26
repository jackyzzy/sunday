"""T6-5 验证：TaskRunner 单元测试"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


def _make_settings(tmp_path, tasks: dict | None = None):
    """构造包含指定 tasks 的临时 Settings。"""
    config = {
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
        "agent": {
            "workspace_dir": str(tmp_path / "workspace"),
            "sessions_dir": str(tmp_path / "sessions"),
        },
    }
    if tasks:
        config["tasks"] = tasks

    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump(config))

    import os

    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        from sunday.config import Settings

        return Settings()


def test_task_runner_loads_tasks_from_config(tmp_path):
    """TaskRunner 能从配置加载 tasks 节"""
    settings = _make_settings(tmp_path, tasks={
        "daily_brief": {"description": "每日简报", "steps": ["获取邮件", "生成摘要"]}
    })

    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
    from task_runner import TaskRunner

    runner = TaskRunner(settings=settings)
    tasks = runner.list_tasks()
    assert "daily_brief" in tasks


def test_task_runner_unknown_task_raises(tmp_path):
    """未知 task name 抛出 ValueError"""
    settings = _make_settings(tmp_path, tasks={})

    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
    from task_runner import TaskRunner

    runner = TaskRunner(settings=settings)
    with pytest.raises(ValueError, match="未知任务"):
        runner.get_task("nonexistent_task")


async def test_task_runner_runs_task(tmp_path):
    """run_task 调用注入的 agent_loop（mock）"""
    settings = _make_settings(tmp_path, tasks={
        "test_task": {"description": "测试任务", "steps": ["步骤一"]}
    })

    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
    from task_runner import TaskRunner

    runner = TaskRunner(settings=settings)

    mock_loop = MagicMock()
    mock_loop.run = AsyncMock(return_value="任务完成结果")

    result = await runner.run_task("test_task", agent_loop=mock_loop)
    assert result == "任务完成结果"
    mock_loop.run.assert_called_once()


def test_daily_brief_task_in_config():
    """configs/agent.yaml 包含 daily_brief 任务定义"""
    config_path = Path(__file__).parent.parent.parent / "configs" / "agent.yaml"
    assert config_path.exists()
    data = yaml.safe_load(config_path.read_text())
    assert "tasks" in data
    assert "daily_brief" in data["tasks"]
    task = data["tasks"]["daily_brief"]
    assert task.get("description")
    assert isinstance(task.get("steps", []), list)
    assert len(task.get("steps", [])) > 0


def test_task_runner_get_task_returns_config(tmp_path):
    """get_task 返回正确的任务配置"""
    settings = _make_settings(tmp_path, tasks={
        "my_task": {"description": "我的任务", "steps": ["第一步", "第二步"]}
    })

    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
    from task_runner import TaskRunner

    runner = TaskRunner(settings=settings)
    task = runner.get_task("my_task")
    assert task.description == "我的任务"
    assert task.steps == ["第一步", "第二步"]
