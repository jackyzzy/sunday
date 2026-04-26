"""realtime_hints 信号聚合器单元测试 — 关键词匹配、实体抽取、prompt 格式化。"""
from __future__ import annotations

from pathlib import Path

from sunday.agent.realtime_hints import (
    RealtimeHints,
    classify,
    format_for_plan_prompt,
)
from sunday.memory.runtime_rules import RuntimeRules


def _rules(*kw: str) -> RuntimeRules:
    return RuntimeRules(realtime_keywords=list(kw), subject_min_output_chars=200)


# ── 任务关键词匹配 ──────────────────────────────────────────────────────────

def test_task_keyword_match_basic():
    hints = classify("帮我调研摩尔线程的近况", rules=_rules("调研", "近况", "今天"))
    assert "调研" in hints.task_keywords
    assert "近况" in hints.task_keywords
    assert "今天" not in hints.task_keywords


def test_task_keyword_case_insensitive():
    hints = classify("查一下 XYZ 的 IPO 进度", rules=_rules("IPO", "调研"))
    assert "IPO" in hints.task_keywords


def test_task_keyword_dedup_preserves_order():
    hints = classify("调研，再调研", rules=_rules("调研"))
    assert hints.task_keywords == ["调研"]


def test_task_no_match_returns_empty():
    hints = classify("帮我写一首关于春天的诗", rules=_rules("调研", "上市"))
    assert hints.task_keywords == []


def test_empty_task_returns_empty():
    hints = classify("", rules=_rules("调研"))
    assert hints.task_keywords == []


# ── claims 实体抽取 ──────────────────────────────────────────────────────────

def test_claim_entities_extract_chinese():
    hints = classify(
        "任务",
        claims=["摩尔线程是什么类型的公司？", "自变量公司目前在做什么？"],
        rules=_rules(),
    )
    assert "摩尔线程" in hints.claim_entities
    assert "自变量" in hints.claim_entities or "自变量公司" in hints.claim_entities


def test_claim_entities_extract_allcaps_english():
    hints = classify(
        "任务",
        claims=["What is the IPO status of NVIDIA?"],
        rules=_rules(),
    )
    assert any("NVIDIA" in e or "IPO" in e for e in hints.claim_entities)


def test_claim_entities_skip_stopwords():
    hints = classify(
        "任务",
        claims=["这个公司什么时候上市？"],
        rules=_rules(),
    )
    # 停用词如"什么"、"公司"不应进入实体列表
    assert "什么" not in hints.claim_entities
    assert "公司" not in hints.claim_entities


def test_claim_entities_dedup_and_capped():
    long_claims = [f"实体名{i}是某种东西？" for i in range(20)]
    hints = classify("任务", claims=long_claims, rules=_rules())
    assert len(hints.claim_entities) <= 8


def test_no_claims_returns_empty_entities():
    hints = classify("任务", claims=[], rules=_rules("调研"))
    assert hints.claim_entities == []


# ── prompt 格式化 ───────────────────────────────────────────────────────────

def test_format_empty_hints_returns_empty_string():
    assert format_for_plan_prompt(RealtimeHints()) == ""


def test_format_with_keywords_only():
    hints = RealtimeHints(task_keywords=["调研", "上市"])
    out = format_for_plan_prompt(hints)
    assert "实时数据信号" in out
    assert "调研" in out
    assert "上市" in out
    assert "时效敏感实体" not in out  # 没有 claim_entities 时不出现该行
    assert out.endswith("---\n\n")  # 分节符让 prompt 拼接干净


def test_format_with_claims_only():
    hints = RealtimeHints(claim_entities=["摩尔线程"])
    out = format_for_plan_prompt(hints)
    assert "时效敏感实体" in out
    assert "摩尔线程" in out


def test_format_combines_keywords_and_entities():
    hints = RealtimeHints(task_keywords=["调研"], claim_entities=["摩尔线程"])
    out = format_for_plan_prompt(hints)
    assert "调研" in out
    assert "摩尔线程" in out


# ── workspace 集成 ─────────────────────────────────────────────────────────

def test_classify_loads_rules_from_workspace(tmp_path: Path):
    (tmp_path / "RUNTIME_RULES.md").write_text(
        "## 时间敏感关键词\n调研, 上市\n", encoding="utf-8"
    )
    hints = classify("调研一下", workspace_dir=tmp_path)
    assert "调研" in hints.task_keywords
