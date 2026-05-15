"""TUI 输出渲染层 —— 把 Service 推送的事件渲染到终端 stdout。

设计原则：
- 用 rich.console.Console 直接 print，不依赖任何全屏 UI 框架
- 关键事件（PLAN / STEP_RESULT / TOOL_START / SUB_STEP_RESULT / DONE / ERROR）
  作为持久行打印，保留在终端 scrollback 中
- 临时状态（thinking / executing 子状态）只更新 StatusSpinner，不写 scrollback
- 所有 Rich markup 字符串与原 ChatLog 完全一致，方便对照

依赖：rich.console.Console（由 cli.py 注入），rich.live.Live（spinner）
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import Console


class StatusSpinner:
    """运行时 spinner：在屏幕底部显示当前阶段，新输出自动把它推上去再重绘。

    使用 rich.live.Live(transient=True) —— 完成时自动清除占用的行，
    不污染 scrollback。Live 内部 vt 协议会和 patch_stdout 协作正确。
    """

    def __init__(self, console: "Console") -> None:
        self._console = console
        self._live: Live | None = None
        self._text: str = ""

    def update(self, text: str) -> None:
        """更新 spinner 文本；首次调用会启动 Live。"""
        self._text = text
        if self._live is None:
            spinner = Spinner("dots", text=Text(text, style="cyan"))
            self._live = Live(
                spinner,
                console=self._console,
                refresh_per_second=10,
                transient=True,
            )
            self._live.start()
        else:
            self._live.update(Spinner("dots", text=Text(text, style="cyan")))

    def stop(self) -> None:
        """清除 spinner（在终端原地清掉，不留痕迹）。"""
        if self._live is not None:
            self._live.stop()
            self._live = None
            self._text = ""

    @property
    def is_running(self) -> bool:
        return self._live is not None


# ── 启动 banner ─────────────────────────────────────────────────────────────

def render_banner(console: "Console", *, session_id: str, model_id: str,
                  thinking_level: str, service_url: str) -> None:
    """启动时打印一次的信息横幅。"""
    console.print()
    console.print("[bold green]Sunday[/bold green] [dim]— 本地优先 AI 智能体[/dim]")
    console.print(
        f"  [dim]session:[/dim] {session_id[:8]}  "
        f"[dim]│ model:[/dim] {model_id}  "
        f"[dim]│ think:[/dim] {thinking_level}"
    )
    console.print(f"  [dim]service:[/dim] {service_url}")
    console.print(
        "  [dim]Enter 发送 │ Ctrl+Enter 换行 │ ↑↓ 历史 │ /help 命令列表 │ Ctrl+D 退出[/dim]"
    )
    console.print()


# ── 用户输入与最终输出 ──────────────────────────────────────────────────────

def render_user_message(console: "Console", content: str) -> None:
    console.print(f"[bold cyan][用户][/bold cyan] {content}")


def render_assistant_message(console: "Console", content: str) -> None:
    console.print(f"[bold green][Sunday][/bold green] {content}")


def render_system_message(console: "Console", content: str) -> None:
    console.print(f"[dim]{content}[/dim]")


def render_error_message(console: "Console", content: str) -> None:
    console.print(f"[bold red][错误][/bold red] {content}")


# ── 计划与执行进度 ─────────────────────────────────────────────────────────

def render_plan(console: "Console", goal: str, steps: list[dict]) -> None:
    console.print(f"[bold yellow][规划][/bold yellow] {goal}")
    for s in steps:
        console.print(
            f"  [dim][ ][/dim] {s.get('id', '?')}: {s.get('intent', '')}"
        )


def render_step_result(console: "Console", step_id: str, status: str,
                       verified: bool | None = None) -> None:
    icons = {
        "done": "[green][✓][/green]",
        "failed": "[red][✗][/red]",
        "skipped": "[dim][↷][/dim]",
    }
    icon = icons.get(status)
    if not icon:
        return
    suffix = " [yellow](验证未通过)[/yellow]" if status == "done" and verified is False else ""
    console.print(f"  {icon} {step_id}{suffix}")


def render_sub_step_result(console: "Console", sub_step_id: str,
                            verified: bool | None) -> None:
    icon = "[green]✓[/green]" if verified else "[red]✗[/red]"
    console.print(f"    {icon} {sub_step_id}")


def render_tool_activity(console: "Console", tool_name: str,
                          args_preview: str = "") -> None:
    suffix = f" {args_preview!r}" if args_preview else ""
    console.print(f"  [dim]→ {tool_name}{suffix}[/dim]")


def render_replan(console: "Console", step_id: str, reason: str = "") -> None:
    msg = f"[bold magenta][重规划][/bold magenta] {step_id}"
    if reason:
        msg += f"：[dim]{reason[:80]}[/dim]"
    console.print(msg)


def render_confirm_request(console: "Console", tool: str, message: str) -> None:
    console.print(f"[bold red]⚠ 确认请求[/bold red] 工具：{tool}")
    console.print(f"  {message}")
    console.print("  请回复 [bold]y[/bold] 确认或 [bold]n[/bold] 取消")


# ── /info 命令的状态展示 ──────────────────────────────────────────────────

def render_info(console: "Console", *, session_id: str, model_id: str,
                model_override: str | None, thinking_level: str,
                service_url: str, trust_mode: bool) -> None:
    console.print("[bold]当前会话信息[/bold]")
    console.print(f"  session_id    : {session_id}")
    console.print(f"  model         : {model_id}")
    if model_override:
        console.print(f"  model_override: {model_override}")
    console.print(f"  thinking      : {thinking_level}")
    console.print(f"  service_url   : {service_url}")
    console.print(f"  trust_mode    : {'启用' if trust_mode else '关闭'}")


# ── /history 渲染（纯函数，便于单测）──────────────────────────────────────

def format_history_payload(payload: dict) -> str:
    """把 /history 的 SLASH_RESULT payload 渲染成多行文本。"""
    session_id = payload.get("session_id", "")
    thread = payload.get("session_thread") or {}
    turns = payload.get("turns") or []

    lines: list[str] = [f"会话 ID：{session_id}"]
    if thread:
        summary = thread.get("summary", "")
        entities = thread.get("key_entities") or []
        if summary:
            lines.append(f"主线：{summary}")
        if entities:
            lines.append(f"关键实体：{', '.join(entities)}")
    if not turns:
        lines.append("")
        lines.append("（当前会话还没有已完成的 turn）")
        return "\n".join(lines)

    lines.append(f"共 {len(turns)} 轮：")
    for t in turns:
        idx = t.get("turn_index", "?")
        ts = (t.get("ts_start") or "")[:19]
        outcome = t.get("outcome", "")
        header = f"─ Turn {idx}  [{ts}]"
        if outcome and outcome != "success":
            header += f"  ({outcome})"
        lines.append("")
        lines.append(header)
        if t.get("user_input"):
            lines.append(f"  用户：{t['user_input']}")
        if t.get("plan_goal"):
            lines.append(f"  目标：{t['plan_goal']}")
        if t.get("output"):
            lines.append(f"  回答：{t['output']}")
    return "\n".join(lines)
