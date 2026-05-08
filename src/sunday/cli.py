import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

import click

from sunday import __version__


def _service_pid_file() -> Path:
    """返回 PID 文件路径（~/.sunday/service.pid）。"""
    return Path.home() / ".sunday" / "service.pid"


def _service_log_file() -> Path:
    """返回 Service 日志文件路径（~/.sunday/logs/service.log）。"""
    return Path.home() / ".sunday" / "logs" / "service.log"


@click.group(invoke_without_command=True)
@click.version_option(__version__)
@click.pass_context
def main(ctx):
    """Sunday — 你的个人边端 AI 智能体"""
    if ctx.invoked_subcommand is None:
        # 默认启动 TUI
        ctx.invoke(tui)


@main.command()
@click.option(
    "--no-key-prompt", is_flag=True, default=False,
    help="跳过 API KEY 互动输入，让用户后续手动编辑 .env",
)
def init(no_key_prompt):
    """首次部署：选 provider + 写 .env + seed L0/L1 模板。

    完全幂等 —— 重复运行只为切换 provider，已配置的 KEY 不被覆盖。
    """
    from sunday.cli_init import run_init
    run_init(no_key_prompt=no_key_prompt)


@main.command()
@click.option(
    "--skip-llm-ping", is_flag=True, default=False,
    help="跳过 LLM 可达性检查（离线 / 测试场景）",
)
def doctor(skip_llm_ping):
    """环境健康检查：API KEY / 运行时目录 / SOUL.md / 模板 diff / LLM 可达性。"""
    from sunday.cli_doctor import run_doctor
    raise SystemExit(run_doctor(skip_llm_ping=skip_llm_ping))


@main.command()
@click.option("--port", default=7899, help="Service 端口")
def tui(port):
    """启动交互式终端界面（默认模式）"""
    from sunday.config import settings
    from sunday.tui.app import SundayApp
    app = SundayApp(service_url=f"ws://localhost:{port}", auto_connect=True)
    # mouse=False 让终端模拟器接管鼠标 → 拖拽=终端原生选区高亮、右键=终端原生菜单/粘贴
    app.run(mouse=settings.sunday.tui.enable_mouse)


@main.command()
@click.argument("task")
@click.option("--thinking", "-t", default="medium",
              type=click.Choice(["off", "minimal", "low", "medium", "high"]),
              help="思考深度")
@click.option("--model", "-m", default=None, help="临时指定模型（格式：provider/model-id）")
@click.option("--yes", "-y", is_flag=True, default=False, help="自动确认所有危险操作（跳过交互提示）")
@click.option("--session", "session_id", default=None,
              help="复用已有会话 ID（多轮延续）；不指定则新建")
def run(task, thinking, model, yes, session_id):
    """执行单次任务（非交互模式）"""
    asyncio.run(_run_task(task, thinking, model, yes, session_id))


async def _run_task(task: str, thinking: str, model_override: str | None,
                    yes: bool = False, session_id: str | None = None):
    """通过 Service 客户端执行单次任务（方向 1：CLI 是 Service 客户端）。

    流程：检测 service 在跑 → 不在则 spawn → WS 连接 → 提交任务 → 流式打印 → 等 DONE。
    会话 ID 由 service 端管理；CLI 只传值。
    """
    from sunday.service.client import (
        ServiceClient,
        is_service_running,
        new_session_id,
        spawn_service_if_needed,
    )
    from sunday.service.protocol import EventType

    port = 7899
    embed = os.environ.get("SUNDAY_EMBED") == "1"

    # 1. 确保 service 在跑（embedded 模式下由调用方负责，这里跳过 spawn）
    if not embed:
        if not is_service_running(port):
            click.echo("Service 未运行，正在自动启动...")
            if not spawn_service_if_needed(port=port):
                click.echo("Service 启动失败，请运行 sunday doctor 排查。", err=True)
                raise SystemExit(1)

    click.echo(f"任务：{task}")
    click.echo("─" * 50)

    sid = session_id or new_session_id()
    final_output = ""

    try:
        async with ServiceClient(port=port) as client:
            async for msg in client.submit_task(
                session_id=sid,
                task=task,
                thinking_level=thinking,
                model_override=model_override,
                auto_confirm=yes,
            ):
                _print_service_event(msg, client, sid, yes)
                if msg.type == EventType.DONE:
                    final_output = msg.data.get("content", "") or ""
                elif msg.type == EventType.ERROR:
                    err = msg.data.get("message") or msg.data.get("error", "未知错误")
                    click.echo(f"执行失败：{err}", err=True)
                    raise SystemExit(1)
    except (ConnectionRefusedError, OSError) as e:
        click.echo(f"无法连接 Service（{e}）。请运行 sunday doctor 排查。", err=True)
        raise SystemExit(1)

    click.echo("\n" + "─" * 50)
    click.echo(final_output)
    click.echo(f"\n会话 ID：{sid}（复用：sunday run --session {sid} ...）")


def _print_service_event(msg, client, session_id: str, auto_yes: bool) -> None:
    """把 service 推送的事件打印到 stdout（CLI 风格）。

    与原 cli_emit 行为对齐：status / plan / step_result 输出可读进度。
    """
    from sunday.service.protocol import EventType

    et = msg.type
    data = msg.data

    if et == EventType.STATUS:
        status = data.get("status", "")
        if status == "thinking":
            click.echo("[思考中...]")
        elif status.startswith("executing:"):
            click.echo(f"[执行 {status.split(':', 1)[1]}]")
        elif status == "replanning":
            click.echo("[重新规划中...]")
        elif status == "summarizing":
            click.echo("[生成摘要...]")
    elif et == EventType.PLAN:
        goal = data.get("goal", "")
        steps = data.get("steps", [])
        click.echo(f"\n计划：{goal}")
        for s in steps:
            click.echo(f"  [ ] {s.get('id', '')}: {s.get('intent', '')}")
        click.echo("")
    elif et == EventType.STEP_RESULT:
        step_id = data.get("step_id", "")
        status = data.get("status", "")
        verified = data.get("verified")
        icons = {"done": "[✓]", "failed": "[✗]", "skipped": "[↷]"}
        icon = icons.get(status)
        if icon:
            suffix = " (验证未通过)" if status == "done" and verified is False else ""
            click.echo(f"  {icon} {step_id}{suffix}")
    elif et == EventType.CONFIRM_REQUEST:
        tool_name = data.get("tool_name", "")
        arguments = data.get("arguments", {})
        if auto_yes:
            click.echo(f"[--yes] 自动确认工具 '{tool_name}'")
            return  # auto_confirm=True 时 client 已自动回复，这里仅打印
        click.echo(f"\n⚠️  工具 '{tool_name}' 是不可逆操作，参数：{arguments}")
        if not sys.stdin.isatty():
            click.echo("非交互模式，自动拒绝不可逆操作。", err=True)
            asyncio.create_task(client.send_confirm(session_id, False))
            return
        try:
            answer = click.prompt("是否继续执行？[y/N]", default="N")
            confirmed = answer.strip().lower() in ("y", "yes")
        except click.exceptions.Abort:
            confirmed = False
        asyncio.create_task(client.send_confirm(session_id, confirmed))


@main.group()
def service():
    """管理 Sunday Service 守护进程"""
    pass


@service.command("start")
@click.option("--port", default=7899, help="监听端口")
def service_start(port):
    """后台启动 Service 守护进程"""
    pid_file = _service_pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    # 检查是否已运行
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            click.echo(f"Service 已在运行（PID={pid}）")
            return
        except (ProcessLookupError, ValueError):
            pass

    proc = subprocess.Popen(
        [sys.executable, "-m", "sunday.service", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid))
    click.echo(f"Service 已启动（PID={proc.pid}，端口={port}）")


@service.command("stop")
def service_stop():
    """停止 Service 守护进程"""
    pid_file = _service_pid_file()
    if not pid_file.exists():
        click.echo("Service 未运行（PID 文件不存在）")
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        click.echo(f"已发送 SIGTERM（PID={pid}）")
    except (ProcessLookupError, ValueError):
        pid_file.unlink(missing_ok=True)
        click.echo("进程不存在，已清理 PID 文件")


@service.command("status")
def service_status():
    """查看 Service 运行状态"""
    pid_file = _service_pid_file()
    if not pid_file.exists():
        click.echo("Service 未运行")
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # signal 0 = 检查进程存在
        click.echo(f"Service 运行中（PID={pid}）")
    except (ProcessLookupError, ValueError):
        click.echo("Service 未运行（进程不存在）")
        pid_file.unlink(missing_ok=True)


@main.group()
def logs():
    """查看各组件日志"""
    pass


@logs.command("service")
@click.option("--lines", "-n", default=50, help="显示最后 N 行（默认 50）")
@click.option("--follow", "-f", is_flag=True, default=False, help="实时跟踪（类似 tail -f）")
def logs_service(lines: int, follow: bool) -> None:
    """查看 Service 日志"""
    log_file = _service_log_file()
    if not log_file.exists():
        click.echo(f"日志文件不存在：{log_file}\n提示：请先运行 sunday service start", err=True)
        raise SystemExit(1)

    if follow:
        import time
        with log_file.open(encoding="utf-8") as f:
            f.seek(0, 2)
            click.echo(f"跟踪 {log_file}（Ctrl+C 退出）")
            try:
                while True:
                    line = f.readline()
                    if line:
                        click.echo(line, nl=False)
                    else:
                        time.sleep(0.2)
            except KeyboardInterrupt:
                pass
        return

    all_lines = log_file.read_text(encoding="utf-8").splitlines()
    for line in all_lines[-lines:]:
        click.echo(line)


@main.group()
def memory():
    """管理记忆文件"""
    pass


@memory.command("show")
@click.argument("file", default="MEMORY",
                type=click.Choice(["SOUL", "MEMORY", "USER", "TOOLS", "AGENTS"]))
def memory_show(file):
    """查看记忆文件"""
    from sunday.bootstrap import build_memory_client
    from sunday.config import settings

    async def _show() -> None:
        client = build_memory_client(settings.sunday, run_janitor=False)
        try:
            if file in {"SOUL", "AGENTS", "TOOLS"}:
                content = await client.workspace.read(file)
            else:
                content = await client.knowledge.read_layer(file)
            click.echo(content if content else f"（{file} 内容为空或文件不存在）")
        finally:
            await client.aclose()

    asyncio.run(_show())


@memory.command("search")
@click.argument("keyword")
def memory_search(keyword):
    """搜索记忆内容（扫描 workspace + memory 下的 .md 文件）"""
    from sunday.config import settings

    workspace = settings.sunday.agent.workspace_dir
    memory_dir = settings.sunday.agent.memory_dir
    found = False
    for md_file in list(workspace.glob("*.md")) + list(memory_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = [line for line in content.splitlines() if keyword.lower() in line.lower()]
        if lines:
            click.echo(f"\n📄 {md_file.name}:")
            for ln in lines:
                click.echo(f"  {ln}")
            found = True
    if not found:
        click.echo(f"未找到包含 '{keyword}' 的记忆")


@main.group()
def skills():
    """管理技能包"""
    pass


@skills.command("list")
def skills_list():
    """列出所有可用技能"""
    from pathlib import Path

    from sunday.config import settings
    from sunday.skills.loader import SkillLoader

    workspace_dir = settings.sunday.agent.workspace_dir
    loader = SkillLoader(
        project_skills_dir=Path(__file__).parent.parent.parent / "skills",
        user_skills_dir=workspace_dir / "skills",
    )
    found = loader.discover()
    if not found:
        click.echo("未发现任何技能")
        return
    click.echo(f"发现 {len(found)} 个技能：")
    for skill in found:
        click.echo(f"  · {skill.name}: {skill.description}")
