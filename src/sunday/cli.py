import asyncio
import os
import signal
import subprocess
import sys
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
def run(task, thinking, model):
    """执行单次任务（非交互模式）"""
    asyncio.run(_run_task(task, thinking, model))


async def _run_task(task: str, thinking: str, model_override: str | None):
    """实际执行任务的异步函数（Phase 4：接入工具系统与技能）"""
    import uuid

    from sunday.agent.executor import Executor
    from sunday.agent.loop import AgentLoop
    from sunday.agent.models import AgentState, ThinkingLevel
    from sunday.agent.planner import Planner
    from sunday.agent.verifier import Verifier
    from sunday.config import settings
    from sunday.memory.context import ContextBuilder
    from sunday.memory.manager import MemoryManager
    from sunday.skills.loader import SkillLoader
    from sunday.tools.cli_tool import register_cli_tools
    from sunday.tools.registry import ToolRegistry

    # 如果指定了 model_override，临时调整 settings
    cfg_settings = settings
    if model_override:
        # 简单做法：直接用 SimpleAgent 兼容 model override
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
        cfg_settings.get_api_key(cfg_settings.sunday.model.provider, cfg_settings.sunday.model.api_key_env)
    except (ValueError, KeyError) as e:
        click.echo(f"配置错误：{e}", err=True)
        click.echo("请检查 .env 文件中的 API key 配置", err=True)
        raise SystemExit(1)

    # emit 回调：打印进度到 stdout
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
            goal = data.get("goal", "")
            steps = data.get("steps", [])
            click.echo(f"\n计划：{goal}")
            for s in steps:
                click.echo(f"  · {s['id']}: {s['intent']}")
            click.echo("")

    try:
        level = ThinkingLevel(thinking)
        state = AgentState(
            session_id=uuid.uuid4().hex[:12],
            task=task,
            thinking_level=level,
        )
        workspace_dir = cfg_settings.sunday.agent.workspace_dir

        # CLI 确认处理器：stdin 读取 y/n
        async def cli_confirm(tool_name: str, arguments: dict, session_id: str) -> bool:
            click.echo(f"\n⚠️  工具 '{tool_name}' 是不可逆操作，参数：{arguments}")
            answer = click.prompt("是否继续执行？[y/N]", default="N")
            return answer.strip().lower() in ("y", "yes")

        # 工具注册表
        registry = ToolRegistry(cfg_settings, confirmation_handler=cli_confirm)
        register_cli_tools(registry)

        # 技能加载器（注入 ContextBuilder 用于 L0 摘要）
        skill_loader = SkillLoader(
            project_skills_dir=workspace_dir.parent.parent / "skills",
            user_skills_dir=workspace_dir / "skills",
        )
        skill_loader.discover()

        context_builder = ContextBuilder(workspace_dir, skill_loader=skill_loader)
        memory_manager = MemoryManager(workspace_dir, cfg_settings)
        loop = AgentLoop(
            planner=Planner(cfg_settings),
            executor=Executor(cfg_settings, tool_registry=registry),
            verifier=Verifier(cfg_settings),
            emit=cli_emit,
            context_builder=context_builder,
            memory_manager=memory_manager,
        )
        result = await loop.run(state)
        click.echo("\n" + "─" * 50)
        click.echo(result)
    except Exception as e:
        click.echo(f"执行失败：{e}", err=True)
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
    from sunday.config import settings
    path = settings.sunday.agent.workspace_dir / f"{file}.md"
    if path.exists():
        click.echo(path.read_text(encoding="utf-8"))
    else:
        click.echo(f"文件不存在：{path}", err=True)


@memory.command("search")
@click.argument("keyword")
def memory_search(keyword):
    """搜索记忆内容"""
    from sunday.config import settings
    workspace = settings.sunday.agent.workspace_dir
    found = False
    for md_file in workspace.glob("*.md"):
        content = md_file.read_text(encoding="utf-8")
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
