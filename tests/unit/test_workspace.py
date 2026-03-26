"""T1-3 验证：工作区文件静态检查（只读，无 mock）"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent.parent
WORKSPACE = ROOT / "workspace"
CONFIGS = ROOT / "configs"

WORKSPACE_FILES = ["SOUL.md", "AGENTS.md", "MEMORY.md", "USER.md", "TOOLS.md"]


# ── workspace/*.md 文件 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("filename", WORKSPACE_FILES)
def test_workspace_file_exists(filename):
    """workspace/ 下 5 个 .md 文件均存在"""
    assert (WORKSPACE / filename).exists(), f"缺少文件：workspace/{filename}"


@pytest.mark.parametrize("filename", WORKSPACE_FILES)
def test_workspace_file_nonempty(filename):
    """workspace/.md 文件均非空"""
    content = (WORKSPACE / filename).read_text(encoding="utf-8")
    assert content.strip(), f"文件为空：workspace/{filename}"


@pytest.mark.parametrize("filename", WORKSPACE_FILES)
def test_workspace_file_has_title(filename):
    """workspace/.md 文件首行为 # 标题"""
    lines = (WORKSPACE / filename).read_text(encoding="utf-8").splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    assert non_empty, f"文件无内容：workspace/{filename}"
    assert non_empty[0].startswith("#"), (
        f"workspace/{filename} 首个非空行应为 # 标题，实际：{non_empty[0]!r}"
    )


def test_soul_md_has_sections():
    """SOUL.md 包含必要的 ## 级章节"""
    content = (WORKSPACE / "SOUL.md").read_text(encoding="utf-8")
    sections = [ln for ln in content.splitlines() if ln.startswith("## ")]
    assert len(sections) >= 2, (
        f"SOUL.md 应至少包含 2 个 ## 章节，实际：{len(sections)} 个"
    )


def test_workspace_memory_in_gitignore():
    """workspace/memory/ 受 .gitignore 保护"""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "workspace/memory/" in gitignore, (
        ".gitignore 应包含 workspace/memory/ 以防止每日日志被提交"
    )


# ── configs/ 文件 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("filename", ["system.md", "verifier.md"])
def test_configs_prompts_exist(filename):
    """configs/prompts/*.md 文件存在"""
    path = CONFIGS / "prompts" / filename
    assert path.exists(), f"缺少文件：configs/prompts/{filename}"


def test_mcp_servers_yaml_parseable():
    """configs/mcp_servers.yaml 可被 yaml.safe_load 解析"""
    path = CONFIGS / "mcp_servers.yaml"
    assert path.exists(), "缺少文件：configs/mcp_servers.yaml"
    content = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    # 允许 None（空文件）或 dict/list
    assert parsed is None or isinstance(parsed, (dict, list)), (
        f"mcp_servers.yaml 格式错误，解析结果类型：{type(parsed)}"
    )


def test_agent_yaml_parseable():
    """configs/agent.yaml 可被 yaml.safe_load 解析"""
    path = CONFIGS / "agent.yaml"
    assert path.exists(), "缺少文件：configs/agent.yaml"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), "agent.yaml 应解析为字典"
