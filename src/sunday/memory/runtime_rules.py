"""运行时规则解析 — 从 workspace/RUNTIME_RULES.md 读取关键词清单与阈值。

设计原则：
- "运行数据" 不是 "agent 配置"：关键词清单、敏感实体、阈值这类内容数据
  应当跟 sunday 实例的工作记忆一起演化，由用户/AI 共同维护，不写死在 yaml
- 解析失败必须静默回退到内置最小集，不能让框架因为运行规则缺失或异常而崩溃
- 纯函数模块，不依赖任何运行时状态，便于在测试与 ContextBuilder 中复用
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# 内置最小集 — 任何 sunday 实例都能 cold-start 用，无需先编辑 RUNTIME_RULES.md
_BUILTIN_REALTIME_KEYWORDS: tuple[str, ...] = (
    # 调研类
    "调研", "查询", "了解", "最新", "近期", "当前", "目前",
    # 公司状态
    "上市", "IPO", "融资", "估值", "市值", "股价",
    # 时间词
    "今天", "本周", "本月", "今年",
)


@dataclass
class RuntimeRules:
    """从 RUNTIME_RULES.md 解析出的运行规则集合。"""

    realtime_keywords: list[str] = field(default_factory=list)
    subject_min_output_chars: int = 200


def load_rules(workspace_dir: Path | str | None) -> RuntimeRules:
    """从 `<workspace>/RUNTIME_RULES.md` 加载规则；不存在或解析失败回退默认。"""
    if workspace_dir is None:
        return _builtin_rules()
    path = Path(workspace_dir) / "RUNTIME_RULES.md"
    if not path.exists():
        logger.debug("RUNTIME_RULES.md 不存在（%s），使用内置默认", path)
        return _builtin_rules()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("RUNTIME_RULES.md 读取失败（%s），回退默认：%s", path, e)
        return _builtin_rules()
    return parse_rules(text)


def parse_rules(md_text: str) -> RuntimeRules:
    """从 markdown 文本解析。任意节缺失时各自回退默认。"""
    keywords = _parse_keyword_section(md_text, "时间敏感关键词")
    if not keywords:
        keywords = list(_BUILTIN_REALTIME_KEYWORDS)
    threshold = _parse_int_section(md_text, "主题敏感最小输出长度", default=200)
    return RuntimeRules(
        realtime_keywords=keywords,
        subject_min_output_chars=threshold,
    )


def _parse_keyword_section(md_text: str, section_title: str) -> list[str]:
    """提取某 `## 节标题` 下所有 `### 子标题` 的列表项，扁平化去重。

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
        # 去掉 markdown 列表标记
        line = re.sub(r"^[-*+]\s+", "", line)
        for token in re.split(r"[,，;；\n]", line):
            token = token.strip()
            if token:
                items.append(token)
    # 去重保序
    seen: set[str] = set()
    deduped = []
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _parse_int_section(md_text: str, section_title: str, default: int) -> int:
    """提取某 `## 节标题` 下首个整数。失败回退 default。"""
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


def _builtin_rules() -> RuntimeRules:
    return RuntimeRules(
        realtime_keywords=list(_BUILTIN_REALTIME_KEYWORDS),
        subject_min_output_chars=200,
    )
