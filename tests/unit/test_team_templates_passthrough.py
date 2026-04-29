"""S2-D 验证：Team / SimpleNode 透传 TemplateLoader（任务类型模板）。

注：此处的 templates 是 TemplateLoader（任务类型模板，由 configs/templates/*.yaml
加载），不是 prompt 模板（plan.md / team_plan.md / executor_*.md）—— 两者无关。
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import yaml

from sunday.agent.simple import SimpleNode
from sunday.agent.team import Team
from sunday.templates.loader import TemplateLoader


def _make_settings(tmp_path: Path):
    from sunday.config import Settings

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("# Sunday\n", encoding="utf-8")
    (tmp_path / "agent.yaml").write_text(yaml.dump({
        "agent": {
            "workspace_dir": str(workspace),
            "memory_dir": str(tmp_path / "memory"),
            "sessions_dir": str(tmp_path / "sessions"),
            "log_dir": str(tmp_path / "logs"),
        },
        "model": {"provider": "anthropic", "id": "claude-test"},
    }))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


class _StubRegistry:
    """ToolRegistry 占位（Team / SimpleNode 不调，仅类型对齐）。"""

    def get_schemas(self):
        return []


def test_team_receives_templates(tmp_path: Path):
    """Team.__init__(templates=...) 把 TemplateLoader 透传给内部 Planner。"""
    settings = _make_settings(tmp_path)
    builtin = tmp_path / "builtin_templates"
    user = tmp_path / "user_templates"
    builtin.mkdir()
    user.mkdir()
    loader = TemplateLoader(builtin_dir=builtin, user_dir=user)
    loader.discover()

    team = Team(settings.sunday, tool_registry=_StubRegistry(), templates=loader)
    assert team.planner._templates is loader


def test_team_works_without_templates(tmp_path: Path):
    """templates=None（默认）时 Team 仍可正常构造（向后兼容）。"""
    settings = _make_settings(tmp_path)
    team = Team(settings.sunday, tool_registry=_StubRegistry())
    assert team.planner._templates is None


def test_simple_node_accepts_templates(tmp_path: Path):
    """SimpleNode 也接收 templates 参数（接口对齐 Team），即使当前不做子规划也保留。"""
    settings = _make_settings(tmp_path)
    builtin = tmp_path / "builtin_templates"
    user = tmp_path / "user_templates"
    builtin.mkdir()
    user.mkdir()
    loader = TemplateLoader(builtin_dir=builtin, user_dir=user)

    # 不应抛异常
    node = SimpleNode(settings.sunday, tool_registry=_StubRegistry(), templates=loader)
    # SimpleNode 当前没有 sub-planner；验证构造通过即可
    assert node.executor is not None
    assert node.verifier is not None
