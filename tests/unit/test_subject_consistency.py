"""主题一致性检查器单元测试（mock httpx，无真实 API）。"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from sunday.agent.subject_consistency import (
    LLMSubjectChecker,
    SubjectCheckResult,
    _AlwaysConsistentChecker,
    build_subject_checker,
)


def _make_settings(tmp_path, quality: dict | None = None):
    from sunday.config import Settings
    payload = {
        "model": {"provider": "anthropic", "id": "claude-test", "max_tokens": 4096},
    }
    if quality is not None:
        payload["quality"] = quality
    (tmp_path / "agent.yaml").write_text(yaml.dump(payload))
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-fake",
        "SUNDAY_CONFIGS_DIR": str(tmp_path),
    }):
        s = Settings()
        _ = s.sunday
        return s


def _mock_client(text: str):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }
    mock_resp.raise_for_status.return_value = None
    mock_resp.is_success = True
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


# ── LLMSubjectChecker ─────────────────────────────────────────────────────────

async def test_llm_checker_consistent(tmp_path):
    """LLM 返回 consistent=true 时结果正确解析。"""
    settings = _make_settings(tmp_path)
    checker = LLMSubjectChecker(settings.sunday.model)

    resp = json.dumps({"consistent": True, "reason": "主题匹配"})
    mock_cl = _mock_client(resp)
    with patch("sunday.agent.llm_client._get_http_client", return_value=mock_cl):
        r = await checker.check("关于自变量公司的分析...", ["自变量", "具身智能"])
    assert isinstance(r, SubjectCheckResult)
    assert r.consistent is True


async def test_llm_checker_inconsistent(tmp_path):
    """LLM 返回 consistent=false 时翻译为 SubjectCheckResult(consistent=False)。"""
    settings = _make_settings(tmp_path)
    checker = LLMSubjectChecker(settings.sunday.model)

    resp = json.dumps({"consistent": False, "reason": "输出转向云厂商大赛，脱离自变量主题"})
    mock_cl = _mock_client(resp)
    with patch("sunday.agent.llm_client._get_http_client", return_value=mock_cl):
        r = await checker.check("# 云厂商开发者竞赛分析..." * 10, ["自变量"])
    assert r.consistent is False
    assert "云厂商" in r.reason or "自变量" in r.reason


async def test_llm_checker_empty_subjects_returns_consistent(tmp_path):
    """主题列表为空时无需调用 LLM，直接返回 consistent=True。"""
    settings = _make_settings(tmp_path)
    checker = LLMSubjectChecker(settings.sunday.model)

    r = await checker.check("任何内容", [])
    assert r.consistent is True


async def test_llm_checker_empty_output_returns_consistent(tmp_path):
    """输出为空时直接返回 consistent=True。"""
    settings = _make_settings(tmp_path)
    checker = LLMSubjectChecker(settings.sunday.model)

    r = await checker.check("", ["自变量"])
    assert r.consistent is True


async def test_llm_checker_invalid_json_falls_back_to_consistent(tmp_path):
    """LLM 返回非 JSON 时兜底为 consistent=True（避免误伤）。"""
    settings = _make_settings(tmp_path)
    checker = LLMSubjectChecker(settings.sunday.model)

    mock_cl = _mock_client("not json at all")
    with patch("sunday.agent.llm_client._get_http_client", return_value=mock_cl):
        r = await checker.check("一段长文本" * 30, ["自变量"])
    assert r.consistent is True  # 兜底


# ── build_subject_checker 工厂 ──────────────────────────────────────────────

def test_factory_disabled_returns_noop(tmp_path):
    """quality.subject_consistency.enabled=false 时返回 _AlwaysConsistentChecker。"""
    settings = _make_settings(tmp_path, quality={
        "subject_consistency": {"enabled": False, "checker": "llm"},
    })
    checker = build_subject_checker(settings.sunday)
    assert isinstance(checker, _AlwaysConsistentChecker)


def test_factory_llm_returns_llm_checker(tmp_path):
    """checker=llm 返回 LLMSubjectChecker。"""
    settings = _make_settings(tmp_path, quality={
        "subject_consistency": {"enabled": True, "checker": "llm"},
    })
    checker = build_subject_checker(settings.sunday)
    assert isinstance(checker, LLMSubjectChecker)


def test_factory_unknown_checker_falls_back_to_llm(tmp_path):
    """未实现的 checker 类型回退到 LLMSubjectChecker。"""
    settings = _make_settings(tmp_path, quality={
        "subject_consistency": {"enabled": True, "checker": "small_model"},
    })
    checker = build_subject_checker(settings.sunday)
    assert isinstance(checker, LLMSubjectChecker)


async def test_always_consistent_checker_never_flags():
    """_AlwaysConsistentChecker 永远返回 consistent=True。"""
    checker = _AlwaysConsistentChecker()
    r = await checker.check("任何内容", ["任何主题"])
    assert r.consistent is True
