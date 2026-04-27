import asyncio
import os
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click

from sunday import __version__


def _gateway_pid_file() -> Path:
    """返回 PID 文件路径（~/.sunday/gateway.pid）。"""
    return Path.home() / ".sunday" / "gateway.pid"


def _gateway_log_file() -> Path:
    """返回 Gateway 日志文件路径（~/.sunday/logs/gateway.log）。"""
    return Path.home() / ".sunday" / "logs" / "gateway.log"


@click.group(invoke_without_command=True)
@click.version_option(__version__)
@click.pass_context
def main(ctx):
    """Sunday — 你的个人边端 AI 智能体"""
    if ctx.invoked_subcommand is None:
        # 默认启动 TUI
        ctx.invoke(tui)


@main.command()
@click.option("--port", default=7899, help="Gateway 端口")
def tui(port):
    """启动交互式终端界面（默认模式）"""
    from sunday.tui.app import SundayApp
    app = SundayApp(gateway_url=f"ws://localhost:{port}", auto_connect=True)
    app.run()


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
    """实际执行任务的异步函数（Phase 4：接入工具系统与技能）"""
    from sunday.agent.models import AgentState, ThinkingLevel
    from sunday.bootstrap import build_agent_loop
    from sunday.config import settings

    cfg_settings = settings

    # 如果指定了 model_override，临时调整 settings
    if model_override:
        try:
            from sunday.agent.simple import SimpleAgent
            click.echo(f"任务：{task}")
            click.echo("─" * 50)
            agent = SimpleAgent(settings, thinking_level=thinking, model_override=model_override)
            result = await agent.run(task)
            click.echo(result)
            return
        except ValueError as e:
            click.echo(f"配置错误：{e}", err=True)
            click.echo("请检查 .env 文件中的 API key 配置", err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(f"执行失败：{e}", err=True)
            raise SystemExit(1)

    click.echo(f"任务：{task}")
    click.echo("─" * 50)

    try:
        cfg_settings.sunday.model.get_api_key()
    except (ValueError, KeyError) as e:
        click.echo(f"配置错误：{e}", err=True)
        click.echo("请检查 .env 文件中的 API key 配置", err=True)
        raise SystemExit(1)

    # turn_buffer 与 Gateway 对齐，承载 plan/execution/output
    turn_buffer: dict = {}

    # emit 回调：打印进度到 stdout；同时填充 turn_buffer，让 turn 文件与 thread 更新可用
    async def cli_emit(session_id: str, event_type: str, data: dict) -> None:
        if event_type == "status":
            status = data.get("status", "")
            if status == "thinking":
                click.echo("[思考中...]")
            elif status.startswith("executing:"):
                click.echo(f"[执行 {status.split(':', 1)[1]}]")
            elif status == "replanning":
                click.echo("[重新规划中...]")
            elif status == "summarizing":
                click.echo("[生成摘要...]")
        elif event_type == "plan":
            turn_buffer["plan"] = dict(data)
            goal = data.get("goal", "")
            steps = data.get("steps", [])
            click.echo(f"\n计划：{goal}")
            for s in steps:
                click.echo(f"  [ ] {s['id']}: {s['intent']}")
            click.echo("")
        elif event_type == "step_result":
            turn_buffer.setdefault("execution", []).append(dict(data))
            step_id = data.get("step_id", "")
            status = data.get("status", "")
            verified = data.get("verified")
            icons = {"done": "[✓]", "failed": "[✗]", "skipped": "[↷]"}
            icon = icons.get(status)
            if icon:
                suffix = " (验证未通过)" if status == "done" and verified is False else ""
                click.echo(f"  {icon} {step_id}{suffix}")

    try:
        from sunday.bootstrap import build_memory_client
        from sunday.gateway.history import extract_conversation
        from sunday.gateway.protocol import EventType
        from sunday.memory.models import SessionEvent, TurnRecord
        from sunday.tools.cli_tool import format_report_footer

        cfg = cfg_settings.sunday
        level = ThinkingLevel(thinking)

        client = build_memory_client(cfg)
        try:
            if session_id:
                await client.sessions.ensure(session_id)
            else:
                session_meta = await client.sessions.create()
                session_id = session_meta.session_id
            turn_id = uuid.uuid4().hex[:8]
            turn_start_ts = datetime.now(timezone.utc).isoformat()

            recent_events = await client.sessions.load_events(session_id, max_events=50)
            history = extract_conversation(
                [_event_to_dict(e) for e in recent_events], max_turns=5,
            )
            session_thread = await client.sessions.get_thread(session_id)

            turn_buffer.update({
                "turn_id": turn_id,
                "turn_index": 0,
                "ts_start": turn_start_ts,
                "ts_end": "",
                "outcome": "",
                "user_input": task,
                "plan": None,
                "execution": [],
                "output": "",
            })

            state = AgentState(
                session_id=session_id,
                task=task,
                history=history,
                session_thread=session_thread,
                thinking_level=level,
                turn_id=turn_id,
            )

            async def cli_confirm(tool_name: str, arguments: dict, session_id: str) -> bool:
                if yes:
                    click.echo(f"[--yes] 自动确认工具 '{tool_name}'")
                    return True
                click.echo(f"\n⚠️  工具 '{tool_name}' 是不可逆操作，参数：{arguments}")
                import sys
                if not sys.stdin.isatty():
                    click.echo("非交互模式，自动拒绝不可逆操作。", err=True)
                    return False
                try:
                    answer = click.prompt("是否继续执行？[y/N]", default="N")
                    return answer.strip().lower() in ("y", "yes")
                except click.exceptions.Abort:
                    return False

            async def _append(et: EventType, data: dict) -> None:
                await client.sessions.append_event(
                    session_id, SessionEvent(
                        type=et.value, session_id=session_id,
                        turn_id=turn_id, data=data,
                    ),
                )

            await _append(EventType.TURN_START, {"content": task})
            await _append(EventType.SEND, {"content": task})

            loop = build_agent_loop(
                cfg, cli_emit, mode="cli",
                confirmation_handler=cli_confirm,
                memory_client=client,
            )
            result = ""
            outcome = "error"
            try:
                result = await loop.run(state) or ""
                outcome = "success"
            finally:
                turn_buffer["output"] = result
                turn_buffer["outcome"] = outcome
                turn_buffer["ts_end"] = datetime.now(timezone.utc).isoformat()
                await _append(EventType.DONE, {"content": result})
                await _append(EventType.TURN_END, {"outcome": outcome})
                await client.sessions.write_turn(session_id, TurnRecord(**turn_buffer))
                try:
                    from sunday.memory.session_thread import update_session_thread
                    await update_session_thread(session_id, turn_buffer, client, cfg)
                except Exception as thread_err:
                    click.echo(f"[警告] 会话主线更新失败：{thread_err}", err=True)

            click.echo("\n" + "─" * 50)
            click.echo(result)
            report_path = client.sessions.report_dir(session_id, turn_id)
            footer = format_report_footer(result, report_path)
            if footer:
                click.echo(footer)
            click.echo(f"\n会话 ID：{session_id}（复用：sunday run --session {session_id} ...）")
        finally:
            await client.aclose()
    except Exception as e:
        import httpx
        if isinstance(e, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
            click.echo(f"执行失败：网络连接失败（{type(e).__name__}），请检查网络或代理配置", err=True)
        else:
            click.echo(f"执行失败：{e or type(e).__name__}", err=True)
        raise SystemExit(1)


@main.group()
def gateway():
    """管理 Gateway 守护进程"""
    pass


@gateway.command("start")
@click.option("--port", default=7899, help="监听端口")
def gateway_start(port):
    """后台启动 Gateway 守护进程"""
    pid_file = _gateway_pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    # 检查是否已运行
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            click.echo(f"Gateway 已在运行（PID={pid}）")
            return
        except (ProcessLookupError, ValueError):
            pass

    proc = subprocess.Popen(
        [sys.executable, "-m", "sunday.gateway.__main__", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid))
    click.echo(f"Gateway 已启动（PID={proc.pid}，端口={port}）")


@gateway.command("stop")
def gateway_stop():
    """停止 Gateway 守护进程"""
    pid_file = _gateway_pid_file()
    if not pid_file.exists():
        click.echo("Gateway 未运行（PID 文件不存在）")
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        click.echo(f"已发送 SIGTERM（PID={pid}）")
    except (ProcessLookupError, ValueError):
        pid_file.unlink(missing_ok=True)
        click.echo("进程不存在，已清理 PID 文件")


@gateway.command("status")
def gateway_status():
    """查看 Gateway 运行状态"""
    pid_file = _gateway_pid_file()
    if not pid_file.exists():
        click.echo("Gateway 未运行")
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # signal 0 = 检查进程存在
        click.echo(f"Gateway 运行中（PID={pid}）")
    except (ProcessLookupError, ValueError):
        click.echo("Gateway 未运行（进程不存在）")
        pid_file.unlink(missing_ok=True)


@main.group()
def logs():
    """查看各组件日志"""
    pass


@logs.command("gateway")
@click.option("--lines", "-n", default=50, help="显示最后 N 行（默认 50）")
@click.option("--follow", "-f", is_flag=True, default=False, help="实时跟踪（类似 tail -f）")
def logs_gateway(lines: int, follow: bool) -> None:
    """查看 Gateway 日志"""
    log_file = _gateway_log_file()
    if not log_file.exists():
        click.echo(f"日志文件不存在：{log_file}\n提示：请先运行 sunday gateway start", err=True)
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


def _event_to_dict(event):
    """SessionEvent → 兼容 extract_conversation 的字典。"""
    return {
        "type": event.type,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "data": event.data,
        "ts": event.ts,
    }


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
