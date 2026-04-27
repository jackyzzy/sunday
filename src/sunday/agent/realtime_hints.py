"""实时数据需求识别 — 信号聚合器。

向 Planner 提供"本次任务可能需要实时数据"的提示信号，让 plan LLM 更稳地为
每个 step 标注 `requires_realtime_data`。

三个独立信号（按是否产生 LLM 调用区分）：

    Signal A：任务/intent 关键词匹配（纯规则、零 LLM 调用、可解释）
    Signal B：think 阶段已识别的不确定断言（LLM 调用，由 Planner 提前完成并复用）
    Signal C：plan LLM 自身的 intent 判断（在 plan.md 里要求）

本模块负责合成 A + B 为一段可读 markdown，注入到 plan prompt 里供 Signal C 使用。

设计原则：
- 纯函数 + 数据类，不持有运行时状态
- 关键词清单从 RUNTIME_RULES.md 读（运行数据），不写死在配置里
- 实体抽取保守宽松（中文连续词 + ALLCAPS 英文），精度由 plan LLM 兜底
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sunday.memory.local.workspace import _builtin_rules
from sunday.memory.models import RuntimeRules


@dataclass
class RealtimeHints:
    """聚合后的实时性提示信号。"""

    task_keywords: list[str] = field(default_factory=list)
    """task 文本命中的关键词（去重保序）"""

    intent_keywords: list[str] = field(default_factory=list)
    """所有候选 step intent 命中的关键词（当前未启用 — 留作未来 plan 已生成后回流）"""

    claim_entities: list[str] = field(default_factory=list)
    """从 think 阶段 claims 中提取的命名实体（可能为时间敏感主体）"""

    @property
    def has_signal(self) -> bool:
        return bool(self.task_keywords or self.intent_keywords or self.claim_entities)


# ── 公开 API ──────────────────────────────────────────────────────────────

def classify(
    task: str,
    claims: list[str] | None = None,
    rules: RuntimeRules | None = None,
) -> RealtimeHints:
    """从 task 与 think 阶段 claims 聚合实时性提示。

    `rules` 给定时优先使用；否则回退内置默认（实际生产路径中由 Planner 提前
    通过 MemoryClient.workspace.read_runtime_rules() 注入）。
    """
    effective_rules = rules if rules is not None else _builtin_rules()
    keywords = effective_rules.realtime_keywords

    task_kw = _match_keywords(task or "", keywords)
    claim_entities = _extract_entities(claims or [])

    return RealtimeHints(
        task_keywords=task_kw,
        intent_keywords=[],
        claim_entities=claim_entities,
    )


def format_for_plan_prompt(hints: RealtimeHints) -> str:
    """生成一段注入 plan.md 前的 markdown 文本；无信号时返回空串。"""
    if not hints.has_signal:
        return ""
    lines = ["# 实时数据信号（供规划参考）"]
    if hints.task_keywords:
        lines.append(f"- 任务文本命中关键词：{', '.join(hints.task_keywords)}")
    if hints.claim_entities:
        lines.append(f"- 时效敏感实体（来自 think 阶段）：{', '.join(hints.claim_entities)}")
    lines.append(
        "\n判断 step.requires_realtime_data 时，凡涉及上述关键词或实体的步骤"
        "都应设为 true（联网获取最新信息）；纯整合/写作步骤可设 false。"
    )
    return "\n".join(lines) + "\n\n---\n\n"


# ── 内部实现 ──────────────────────────────────────────────────────────────

def _match_keywords(text: str, keywords: list[str]) -> list[str]:
    """子串匹配：任意 keyword 是 text 的子串即命中（不区分大小写）。"""
    if not text or not keywords:
        return []
    text_lower = text.lower()
    hits: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        if not kw:
            continue
        if kw.lower() in text_lower and kw not in seen:
            hits.append(kw)
            seen.add(kw)
    return hits


# 实体抽取规则（保守、易解释）：
#   - 中文：先按"停用助词 + 标点 + 空白"切分，再保留 2~6 字的中文块
#   - 英文：ALLCAPS 缩写（IPO、SaaS）或首字母大写的连续词（NVIDIA、Anthropic）
#
# 把停用助词当作切分符（不当作可独立提取的词），可避免"摩尔线程是什"这种
# 跨越主谓的贪婪误抓；剩下的纯名词性短语作为候选实体。
_CN_MULTI_PARTICLES = (
    "如何", "怎么", "怎样", "哪些", "什么", "是否",
    "应该", "需要", "包括", "其他", "这个", "那个",
    "目前", "现在", "已经", "今天", "本周", "本月", "今年",
)
_CN_SINGLE_PARTICLES = "是在了的得对吗呢吧啊么把被给将让从向到和与及或而但以为"
_CN_SPLIT_RE = re.compile(
    rf"(?:{'|'.join(_CN_MULTI_PARTICLES)}|[{_CN_SINGLE_PARTICLES}]|[^一-龥A-Za-z0-9])+"
)
_EN_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]+)*\b")
_STOPWORD_CHUNKS = frozenset({
    "公司", "行业", "市场", "产品", "团队", "用户", "现在", "目前",
    "近况", "状态", "情况", "进展", "进度", "方面", "时候",
})


def _extract_entities(claims: list[str]) -> list[str]:
    """从 claims 列表中粗抽实体；返回去重保序列表，最多 8 个。"""
    seen: set[str] = set()
    out: list[str] = []

    def _add(token: str) -> bool:
        """append 若已满 8 条返回 True 表示停止。"""
        if not token or token in seen or token in _STOPWORD_CHUNKS:
            return False
        seen.add(token)
        out.append(token)
        return len(out) >= 8

    for claim in claims:
        if not isinstance(claim, str):
            continue
        # 中文：切分后取 2~6 字的纯中文短语
        for chunk in _CN_SPLIT_RE.split(claim):
            if not chunk:
                continue
            if 2 <= len(chunk) <= 6 and re.fullmatch(r"[一-龥]+", chunk):
                if _add(chunk):
                    return out
        # 英文：ALLCAPS / PascalCase
        for m in _EN_ENTITY_RE.findall(claim):
            if len(m) < 2:
                continue
            if _add(m):
                return out
    return out
