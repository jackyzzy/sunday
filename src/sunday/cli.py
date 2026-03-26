import asyncio

import click

from sunday import __version__


@click.group(invoke_without_command=True)
@click.version_option(__version__)
@click.pass_context
def main(ctx):
    """Sunday — 你的个人边端 AI 智能体"""
    if ctx.invoked_subcommand is None:
        # 默认启动 TUI
        ctx.invoke(tui)


@main.command()
def tui():
    """启动交互式终端界面（默认模式）"""
    click.echo("[TODO] TUI 将在 Phase 5 实现")
    click.echo("提示：使用 'sunday run \"<任务>\"' 进行单次任务执行")


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
    """实际执行任务的异步函数"""
    from sunday.agent.simple import SimpleAgent  # Phase 1 临时实现
    from sunday.config import settings

    click.echo(f"🤔 任务：{task}")
    click.echo("─" * 50)

    try:
        agent = SimpleAgent(settings, thinking_level=thinking, model_override=model_override)
        result = await agent.run(task)
        click.echo(result)
    except ValueError as e:
        click.echo(f"配置错误：{e}", err=True)
        click.echo("请检查 .env 文件中的 API key 配置", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"执行失败：{e}", err=True)
        raise SystemExit(1)


@main.group()
def gateway():
    """管理 Gateway 守护进程"""
    pass


@gateway.command("start")
def gateway_start():
    """启动 Gateway 守护进程"""
    click.echo("[TODO] Gateway 将在 Phase 5 实现")


@gateway.command("stop")
def gateway_stop():
    """停止 Gateway 守护进程"""
    click.echo("[TODO] Gateway 将在 Phase 5 实现")


@gateway.command("status")
def gateway_status():
    """查看 Gateway 运行状态"""
    click.echo("[TODO] Gateway 将在 Phase 5 实现")


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
    click.echo("[TODO] 技能系统将在 Phase 4 实现")
