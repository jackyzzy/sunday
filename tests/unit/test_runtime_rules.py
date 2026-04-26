"""runtime_rules 解析器单元测试 — 静默回退、节切割、列表写法兼容。"""
from __future__ import annotations

from pathlib import Path

from sunday.memory.runtime_rules import (
    RuntimeRules,
    load_rules,
    parse_rules,
)


# ── 静默回退 ────────────────────────────────────────────────────────────────

def test_load_rules_missing_file_returns_builtin(tmp_path: Path):
    """workspace 下没有 RUNTIME_RULES.md 时，返回内置默认，不抛错。"""
    rules = load_rules(tmp_path)
    assert isinstance(rules, RuntimeRules)
    assert rules.realtime_keywords  # 非空
    assert rules.subject_min_output_chars == 200


def test_load_rules_none_workspace_returns_builtin():
    rules = load_rules(None)
    assert rules.realtime_keywords  # 非空
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


# ── 集成：从真实文件读 ──────────────────────────────────────────────────────

def test_load_rules_from_workspace_file(tmp_path: Path):
    (tmp_path / "RUNTIME_RULES.md").write_text(
        "## 时间敏感关键词\n调研, 上市\n\n"
        "## 主题敏感最小输出长度\n\n300\n",
        encoding="utf-8",
    )
    rules = load_rules(tmp_path)
    assert rules.realtime_keywords == ["调研", "上市"]
    assert rules.subject_min_output_chars == 300
