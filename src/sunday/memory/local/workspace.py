"""LocalWorkspaceClient — 只读 L0 用户配置。

读取 SOUL/AGENTS/TOOLS/RUNTIME_RULES 四个 markdown 文件 + workspace/skills/ 列表。
RUNTIME_RULES.md 解析逻辑（关键词清单、阈值）也吸收进来，对外暴露
RuntimeRules 数据对象。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from sunday.memory.models import RuntimeRules

logger = logging.getLogger(__name__)


_FILE_NAMES: dict[str, str] = {
    "SOUL": "SOUL.md",
    "AGENTS": "AGENTS.md",
    "TOOLS": "TOOLS.md",
    "RUNTIME_RULES": "RUNTIME_RULES.md",
}

# 内置最小集 — 任何 sunday 实例 cold-start 都能用，无需先编辑 RUNTIME_RULES.md
_BUILTIN_REALTIME_KEYWORDS: tuple[str, ...] = (
    "调研", "查询", "了解", "最新", "近期", "当前", "目前",
    "上市", "IPO", "融资", "估值", "市值", "股价",
    "今天", "本周", "本月", "今年",
)
_BUILTIN_SUBJECT_MIN_OUTPUT_CHARS = 200


class LocalWorkspaceClient:
    """workspace 只读视图。"""

    def __init__(self, workspace_dir: Path, *, skills_dir: Path | None = None) -> None:
        self._dir = workspace_dir
        self._skills_dir = skills_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    async def read(self, name: str) -> str:
        filename = _FILE_NAMES.get(name)
        if filename is None:
            raise ValueError(f"未知 workspace 文件：{name}")
        path = self._dir / filename
        return path.read_text(encoding="utf-8") if path.exists() else ""

    async def read_runtime_rules(self) -> RuntimeRules:
        text = await self.read("RUNTIME_RULES")
        if not text:
            return _builtin_rules()
        return parse_runtime_rules(text)

    async def list_skills(self) -> list[str]:
        if self._skills_dir is None or not self._skills_dir.exists():
            return []
        return sorted(p.name for p in self._skills_dir.iterdir() if p.is_dir())


# ── RuntimeRules 解析器（从原 runtime_rules.py 整体迁入）───────────────────

def parse_runtime_rules(md_text: str) -> RuntimeRules:
    """从 markdown 文本解析。任意节缺失时各自回退默认。"""
    keywords = _parse_keyword_section(md_text, "时间敏感关键词")
    if not keywords:
        keywords = list(_BUILTIN_REALTIME_KEYWORDS)
    threshold = _parse_int_section(
        md_text, "主题敏感最小输出长度", default=_BUILTIN_SUBJECT_MIN_OUTPUT_CHARS,
    )
    return RuntimeRules(
        realtime_keywords=keywords,
        subject_min_output_chars=threshold,
    )


def _builtin_rules() -> RuntimeRules:
    return RuntimeRules(
        realtime_keywords=list(_BUILTIN_REALTIME_KEYWORDS),
        subject_min_output_chars=_BUILTIN_SUBJECT_MIN_OUTPUT_CHARS,
    )


def _parse_keyword_section(md_text: str, section_title: str) -> list[str]:
    """提取某 `## 节标题` 下所有列表项，扁平化去重保序。

    支持的列表写法：
        - 逗号分隔：`调研, 查询, 了解`
        - 项目符号：`- 调研` / `* 调研`
        - 换行分隔：每行一个
    """
    body = _slice_section(md_text, section_title)
    if not body:
        return []
    items: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        line = re.sub(r"^[-*+]\s+", "", line)
        for token in re.split(r"[,，;；\n]", line):
            token = token.strip()
            if token:
                items.append(token)
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _parse_int_section(md_text: str, section_title: str, default: int) -> int:
    body = _slice_section(md_text, section_title)
    if not body:
        return default
    match = re.search(r"\b(\d+)\b", body)
    if not match:
        return default
    try:
        return int(match.group(1))
    except ValueError:
        return default


def _slice_section(md_text: str, title_substring: str) -> str:
    """切出 `## <含 title_substring> ... 直到下一个 ## 或文末` 的内容。"""
    pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(md_text))
    for i, m in enumerate(matches):
        if title_substring in m.group(1):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
            return md_text[start:end]
    return ""
