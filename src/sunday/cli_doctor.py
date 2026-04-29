"""sunday doctor —— 环境健康检查。

跑业务前确认：
  1. agent.yaml 当前 provider 对应的 KEY 在环境变量中可读
  2. ~/.sunday/{workspace, memory, sessions, logs}/ 均存在且可写
  3. SOUL.md 非空（空 → warning）
  4. LLM ping（最低 token 调用，验证 base_url 可达）
  5. 项目模板 vs 用户模板内容 diff 提示（不强制覆盖，避免破坏用户自定义）

每个检查项独立 PASS / WARN / FAIL，最终汇总退出码：
- 全 PASS → 0
- 有 WARN 但无 FAIL → 0（仅打印提示）
- 有 FAIL → 1
"""
from __future__ import annotations

import asyncio
import difflib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import click


class CheckLevel(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    level: CheckLevel
    message: str


# ── 检查项 ────────────────────────────────────────────────────────────────────


def _check_api_key(cfg) -> CheckResult:
    """agent.yaml 当前 provider 对应的 KEY 是否可读。"""
    api_key_env = cfg.model.api_key_env
    if api_key_env is None:
        return CheckResult("API KEY", CheckLevel.PASS, "本地模型，无需 API KEY")
    try:
        cfg.model.get_api_key()
        return CheckResult("API KEY", CheckLevel.PASS, f"{api_key_env} 已配置")
    except (ValueError, KeyError):
        return CheckResult(
            "API KEY", CheckLevel.FAIL,
            f"{api_key_env} 未配置或为空。请编辑 .env 或运行 sunday init。",
        )


def _check_runtime_dirs(cfg) -> CheckResult:
    """~/.sunday/{workspace, memory, sessions, logs}/ 均存在且可写。"""
    paths = {
        "workspace": cfg.agent.workspace_dir,
        "memory": cfg.agent.memory_dir,
        "sessions": cfg.agent.sessions_dir,
        "logs": cfg.agent.log_dir,
    }
    missing = [name for name, p in paths.items() if not p.exists()]
    if missing:
        return CheckResult(
            "运行时目录", CheckLevel.FAIL,
            f"缺失：{', '.join(missing)}。请运行 sunday init 完成首次部署。",
        )
    not_writable = [name for name, p in paths.items() if not _is_writable(p)]
    if not_writable:
        return CheckResult(
            "运行时目录", CheckLevel.FAIL,
            f"无写权限：{', '.join(not_writable)}",
        )
    return CheckResult("运行时目录", CheckLevel.PASS, "所有目录存在且可写")


def _is_writable(path: Path) -> bool:
    """简易写权限检查：尝试创建子目录。"""
    try:
        probe = path / ".doctor_probe"
        probe.mkdir(exist_ok=True)
        probe.rmdir()
        return True
    except OSError:
        return False


def _check_soul_not_empty(cfg) -> CheckResult:
    soul_path = cfg.agent.workspace_dir / "SOUL.md"
    if not soul_path.exists():
        return CheckResult(
            "SOUL.md", CheckLevel.FAIL,
            f"{soul_path} 不存在。请运行 sunday init。",
        )
    content = soul_path.read_text(encoding="utf-8").strip()
    if not content:
        return CheckResult(
            "SOUL.md", CheckLevel.WARN,
            f"{soul_path} 为空。Agent 将以无身份运行。请编辑该文件填入身份说明。",
        )
    return CheckResult("SOUL.md", CheckLevel.PASS, f"非空（{len(content)} 字符）")


def _check_llm_ping(cfg, timeout: float = 10.0) -> CheckResult:
    """调一次最小 token 的 LLM 请求，验证 base_url 可达 + KEY 有效。"""
    try:
        from sunday.agent.llm_client import LLMClient
    except ImportError as e:
        return CheckResult("LLM ping", CheckLevel.FAIL, f"加载 LLMClient 失败：{e}")

    async def _ping():
        result = await LLMClient.call(
            cfg.model,
            messages=[{"role": "user", "content": "hi"}],
            system="只回复一个字。",
            max_tokens=8,
            thinking_budget=0,
            timeout=timeout,
        )
        return result.text

    try:
        text = asyncio.run(_ping())
        return CheckResult(
            "LLM ping", CheckLevel.PASS,
            f"{cfg.model.provider}/{cfg.model.id} 可达（响应：{text[:30]}）",
        )
    except Exception as e:
        return CheckResult(
            "LLM ping", CheckLevel.FAIL,
            f"{cfg.model.provider}/{cfg.model.id} 不可达：{type(e).__name__}：{e}",
        )


_DIFF_CONTEXT_LINES = 1
_DIFF_MAX_LINES_PER_FILE = 12  # 每个文件最多打印多少行 diff，避免 doctor 输出爆炸


def _check_template_diff(cfg) -> CheckResult:
    """项目模板 vs 用户模板：行级 unified diff 提示，不强制覆盖。"""
    from sunday.bootstrap import project_template_dir

    template_dir = project_template_dir(cfg)
    if not template_dir.is_dir():
        return CheckResult(
            "模板 diff", CheckLevel.WARN,
            f"项目模板目录不存在：{template_dir}（开发安装才有；生产环境正常）",
        )

    user_workspace = cfg.agent.workspace_dir
    user_memory = cfg.agent.memory_dir
    files_to_check = [
        ("SOUL.md", user_workspace),
        ("AGENTS.md", user_workspace),
        ("TOOLS.md", user_workspace),
        ("RUNTIME_RULES.md", user_workspace),
        ("MEMORY.md", user_memory),
        ("USER.md", user_memory),
    ]

    findings: list[str] = []
    for fname, user_dir in files_to_check:
        src = template_dir / fname
        dest = user_dir / fname
        if not src.exists():
            continue
        if not dest.exists():
            findings.append(f"  ! {fname}：用户版缺失（建议运行 sunday init 补齐）")
            continue
        diff_block = _file_unified_diff(src, dest, fname)
        if diff_block:
            findings.append(diff_block)

    if not findings:
        return CheckResult("模板 diff", CheckLevel.PASS, "用户模板与项目模板一致")
    return CheckResult(
        "模板 diff", CheckLevel.WARN,
        "用户模板与项目模板存在差异（保留用户自定义；如需对齐请手动 diff）：\n"
        + "\n".join(findings),
    )


def _file_unified_diff(template_file: Path, user_file: Path, fname: str) -> str:
    """生成单文件的精简 unified diff 块；无差异时返回空串。

    输出形如：
        ~ SOUL.md（项目模板 19 行 / 用户版 1 行）
            -项目独有的指令...
            +用户自定义...
            ...（已截断，余 N 行）
    """
    template_text = template_file.read_text(encoding="utf-8")
    user_text = user_file.read_text(encoding="utf-8")
    if template_text == user_text:
        return ""

    template_lines = template_text.splitlines()
    user_lines = user_text.splitlines()
    diff_iter = difflib.unified_diff(
        template_lines, user_lines,
        fromfile=f"template/{fname}",
        tofile=f"user/{fname}",
        lineterm="",
        n=_DIFF_CONTEXT_LINES,
    )
    diff_lines = list(diff_iter)[2:]  # 跳过 +++ / --- 文件头（自带行已在 header）
    truncated = ""
    if len(diff_lines) > _DIFF_MAX_LINES_PER_FILE:
        omitted = len(diff_lines) - _DIFF_MAX_LINES_PER_FILE
        diff_lines = diff_lines[:_DIFF_MAX_LINES_PER_FILE]
        truncated = f"\n    ...（已截断，余 {omitted} 行）"

    header = (
        f"  ~ {fname}（项目模板 {len(template_lines)} 行 / 用户版 {len(user_lines)} 行）"
    )
    body = "\n".join(f"    {line}" for line in diff_lines)
    return f"{header}\n{body}{truncated}"


# ── 主流程 ────────────────────────────────────────────────────────────────────


def run_doctor(skip_llm_ping: bool = False) -> int:
    """跑全部检查，按级别打印，返回退出码。"""
    from sunday.config import settings

    cfg = settings.sunday

    click.echo("Sunday 环境健康检查\n" + "=" * 40)

    checks = [
        _check_api_key(cfg),
        _check_runtime_dirs(cfg),
        _check_soul_not_empty(cfg),
        _check_template_diff(cfg),
    ]
    if not skip_llm_ping:
        checks.append(_check_llm_ping(cfg))

    icons = {
        CheckLevel.PASS: click.style("[✓]", fg="green"),
        CheckLevel.WARN: click.style("[!]", fg="yellow"),
        CheckLevel.FAIL: click.style("[✗]", fg="red"),
    }

    for result in checks:
        click.echo(f"{icons[result.level]} {result.name}：{result.message}")

    click.echo("=" * 40)
    fails = [c for c in checks if c.level == CheckLevel.FAIL]
    warns = [c for c in checks if c.level == CheckLevel.WARN]
    if fails:
        click.echo(click.style(f"{len(fails)} 项失败、{len(warns)} 项警告。", fg="red"))
        return 1
    if warns:
        click.echo(click.style(f"全部检查通过（{len(warns)} 项警告）。", fg="yellow"))
    else:
        click.echo(click.style("全部检查通过。", fg="green"))
    return 0
