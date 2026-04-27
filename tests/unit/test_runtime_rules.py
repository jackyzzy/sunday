"""runtime_rules 解析器单元测试 — 静默回退、节切割、列表写法兼容。

解析器自 Phase 4 起整合进 LocalWorkspaceClient（src/sunday/memory/local/workspace.py），
本套测试直接覆盖纯函数 parse_runtime_rules / _builtin_rules。
"""
from __future__ import annotations

import pytest

from sunday.memory.local import LocalMemoryClient
from sunday.memory.local.workspace import _builtin_rules, parse_runtime_rules
from sunday.memory.models import RuntimeRules

# 兼容旧调用名：测试体保留下文使用 parse_rules 名字
parse_rules = parse_runtime_rules


# ── 静默回退 ────────────────────────────────────────────────────────────────

def test_builtin_rules_returns_nonempty():
    rules = _builtin_rules()
    assert isinstance(rules, RuntimeRules)
    assert rules.realtime_keywords
    assert rules.subject_min_output_chars == 200


def test_parse_empty_md_returns_builtin_keywords():
    rules = parse_rules("")
    assert rules.realtime_keywords  # 内置兜底
    assert rules.subject_min_output_chars == 200


def test_parse_missing_keyword_section_returns_builtin():
    rules = parse_rules("## 其他章节\n内容\n")
    assert rules.realtime_keywords


# ── 关键词节解析 ────────────────────────────────────────────────────────────

def test_parse_comma_separated_keywords():
    md = (
        "## 时间敏感关键词（realtime_hints）\n\n"
        "### 通用类\n"
        "调研, 查询, 了解\n\n"
        "### 时间词\n"
        "今天, 本周\n"
    )
    rules = parse_rules(md)
    assert "调研" in rules.realtime_keywords
    assert "查询" in rules.realtime_keywords
    assert "今天" in rules.realtime_keywords
    assert "本周" in rules.realtime_keywords


def test_parse_bullet_list_keywords():
    md = (
        "## 时间敏感关键词\n\n"
        "- 上市\n"
        "- IPO\n"
        "- 融资\n"
    )
    rules = parse_rules(md)
    assert rules.realtime_keywords[:3] == ["上市", "IPO", "融资"]


def test_parse_keywords_dedup_preserves_order():
    md = "## 时间敏感关键词\n调研, 查询, 调研, 上市, 查询\n"
    rules = parse_rules(md)
    assert rules.realtime_keywords == ["调研", "查询", "上市"]


def test_parse_keywords_skips_blockquotes_and_blank():
    md = (
        "## 时间敏感关键词\n\n"
        "> 这是说明，不参与解析\n\n"
        "调研, 查询\n"
    )
    rules = parse_rules(md)
    assert rules.realtime_keywords == ["调研", "查询"]


def test_parse_keywords_chinese_and_english_punctuation():
    md = "## 时间敏感关键词\n调研，查询；上市,IPO\n"
    rules = parse_rules(md)
    assert "调研" in rules.realtime_keywords
    assert "查询" in rules.realtime_keywords
    assert "上市" in rules.realtime_keywords
    assert "IPO" in rules.realtime_keywords


# ── 阈值节解析 ──────────────────────────────────────────────────────────────

def test_parse_threshold_section():
    md = "## 主题敏感最小输出长度（subject_consistency.min_output_chars）\n\n500\n"
    rules = parse_rules(md)
    assert rules.subject_min_output_chars == 500


def test_parse_threshold_falls_back_to_default_when_missing():
    md = "## 时间敏感关键词\n调研\n"
    rules = parse_rules(md)
    assert rules.subject_min_output_chars == 200


def test_parse_threshold_falls_back_when_no_int():
    md = "## 主题敏感最小输出长度\n\n说明文字，没有数字\n"
    rules = parse_rules(md)
    assert rules.subject_min_output_chars == 200


# ── 集成：从真实文件读（走 LocalMemoryClient.workspace）──────────────────

@pytest.mark.asyncio
async def test_workspace_runtime_rules_from_disk(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "RUNTIME_RULES.md").write_text(
        "## 时间敏感关键词\n调研, 上市\n\n"
        "## 主题敏感最小输出长度\n\n300\n",
        encoding="utf-8",
    )
    client = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=tmp_path / "m",
        log_dir=tmp_path / "l",
        workspace_dir=workspace,
        run_janitor=False,
    )
    try:
        rules = await client.workspace.read_runtime_rules()
    finally:
        await client.aclose()
    assert rules.realtime_keywords == ["调研", "上市"]
    assert rules.subject_min_output_chars == 300


@pytest.mark.asyncio
async def test_workspace_runtime_rules_falls_back_when_missing(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = LocalMemoryClient(
        sessions_dir=tmp_path / "s",
        memory_dir=tmp_path / "m",
        log_dir=tmp_path / "l",
        workspace_dir=workspace,
        run_janitor=False,
    )
    try:
        rules = await client.workspace.read_runtime_rules()
    finally:
        await client.aclose()
    assert rules.realtime_keywords  # 内置非空
    assert rules.subject_min_output_chars == 200
