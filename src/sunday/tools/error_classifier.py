"""工具错误分类器 — 将错误字符串映射到 ErrorType + 建议。"""
from __future__ import annotations

import re

from sunday.tools.health_store import ErrorType

# 执行管道错误前缀正则（来自 registry.py）
_CLASSIFIABLE_PREFIX = re.compile(r"^\[(工具错误|工具超时)\]")
_REJECTED_PREFIX = re.compile(r"^\[工具拒绝\]")

# 分类规则：(ErrorType, 建议, [正则列表])
# 规则按优先级排列，先匹配先返回
_RULES: list[tuple[ErrorType, str, list[re.Pattern[str]]]] = [
    (
        ErrorType.AUTH_ERROR,
        "请检查 API Key 是否正确配置",
        [
            re.compile(r"401|unauthorized|invalid.?api.?key|authentication\s+failed", re.I),
            re.compile(r"auth_error|api.?key.*invalid|forbidden.*api|api key.*expired", re.I),
            re.compile(r"TAVILY_API_KEY.*invalid|invalid.*token|access.?denied", re.I),
        ],
    ),
    (
        ErrorType.RATE_LIMIT,
        "已触发速率限制，可稍等后重试，或使用替代工具",
        [
            re.compile(r"429|rate.?limit|too.?many.?request|quota.?exceed", re.I),
            re.compile(r"ratelimit|throttl|requests?\s+per\s+(minute|second|day|month)", re.I),
            re.compile(r"usage.?limit|daily.?limit|monthly.?quota|credits?.?exhausted", re.I),
        ],
    ),
    (
        ErrorType.NETWORK_ERROR,
        "网络连接异常，请检查网络或代理配置",
        [
            re.compile(r"connection.?error|network.?error|connection.?refused", re.I),
            re.compile(r"dns.*fail|name.?resolution|unreachable|connect.*timed.?out", re.I),
            re.compile(r"connecterror|networkerror|httpconnect|ssl.*error|certificate", re.I),
            re.compile(r"no\s+route\s+to\s+host|host.?not.?found|socket", re.I),
        ],
    ),
    (
        ErrorType.TIMEOUT,
        "工具执行超时，服务端响应较慢，可稍后重试",
        [
            re.compile(r"\[工具超时\]|readtimeout|writetimeout|超过.*未返回", re.I),
            re.compile(r"timed?\s*out(?!\s+connect)|request\s+timeout|gateway\s+timeout", re.I),
            re.compile(r"504|408", re.I),
        ],
    ),
    (
        ErrorType.NOT_FOUND,
        "未找到资源或端点，请确认服务配置",
        [
            re.compile(r"404|not\s+found|no\s+such|endpoint.*not.*exist", re.I),
        ],
    ),
]


def should_classify(result: str) -> bool:
    """判断工具结果是否需要分类（只分类错误/超时，不分类拒绝）。"""
    return bool(_CLASSIFIABLE_PREFIX.match(result))


def classify(error_text: str) -> tuple[ErrorType, str]:
    """对错误字符串分类，返回 (ErrorType, suggestion)。

    只处理 [工具错误] / [工具超时] 前缀；[工具拒绝] 属访问控制不触发健康记录。
    """
    if _REJECTED_PREFIX.match(error_text):
        return ErrorType.UNKNOWN, ""

    for error_type, suggestion, patterns in _RULES:
        for pattern in patterns:
            if pattern.search(error_text):
                return error_type, suggestion

    return ErrorType.UNKNOWN, "工具执行失败，具体原因未知"
