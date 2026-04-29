"""sunday init —— 首次部署的唯一入口。

执行顺序：
1. 预扫描 .env，列出已就位（非空、非占位符）的 KEY
2. 互动选 provider（已配置的标 ✓）→ 改写 configs/agent.yaml 的 model 节
3. KEY 处理：已就位则跳过；否则 getpass 互动（--no-key-prompt 跳过）
4. 写出 / 合并 .env（不破坏用户已有内容）
5. 调 client.workspace.ensure_seeded() + client.knowledge.ensure_seeded()
6. 提示完成 + 下一步

重复运行幂等且非破坏 —— 用户随时可重跑只为切换 provider。
"""
from __future__ import annotations

import getpass
import re
from dataclasses import dataclass
from pathlib import Path

import click
import yaml


# Provider 元信息：单一真相来源，新增 provider 在此加一条
@dataclass(frozen=True)
class ProviderInfo:
    key: str            # 内部标识（与 model.provider 对应；deepseek/qwen 等用 OpenAI 兼容时仍为 openai）
    label: str          # 显示名
    api_key_env: str | None  # 对应 .env 中的 KEY 名；ollama 为 None
    provider_field: str      # 写入 agent.yaml 的 model.provider 值
    default_model_id: str
    default_base_url: str | None  # OpenAI 兼容时必填，anthropic 用默认 None
    description: str


PROVIDERS: list[ProviderInfo] = [
    ProviderInfo(
        key="anthropic",
        label="Anthropic Claude",
        api_key_env="ANTHROPIC_API_KEY",
        provider_field="anthropic",
        default_model_id="claude-sonnet-4-5",
        default_base_url=None,
        description="官方 Claude API（原生接口）",
    ),
    ProviderInfo(
        key="openai",
        label="OpenAI",
        api_key_env="OPENAI_API_KEY",
        provider_field="openai",
        default_model_id="gpt-4o",
        default_base_url="https://api.openai.com/v1",
        description="GPT 系列",
    ),
    ProviderInfo(
        key="deepseek",
        label="DeepSeek",
        api_key_env="DEEPSEEK_API_KEY",
        provider_field="openai",
        default_model_id="deepseek-chat",
        default_base_url="https://api.deepseek.com/v1",
        description="OpenAI 兼容接口",
    ),
    ProviderInfo(
        key="qwen",
        label="Qwen (DashScope)",
        api_key_env="QWEN_API_KEY",
        provider_field="openai",
        default_model_id="qwen-max",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="阿里云 DashScope",
    ),
    ProviderInfo(
        key="moonshot",
        label="Moonshot",
        api_key_env="MOONSHOT_API_KEY",
        provider_field="openai",
        default_model_id="moonshot-v1-8k",
        default_base_url="https://api.moonshot.cn/v1",
        description="月之暗面",
    ),
    ProviderInfo(
        key="ollama",
        label="Ollama",
        api_key_env=None,
        provider_field="openai",
        default_model_id="llama3.2",
        default_base_url="http://localhost:11434/v1",
        description="本地推理（无需 KEY）",
    ),
]


def _scan_env_file(env_path: Path) -> dict[str, str]:
    """解析 .env，返回非空、非占位符的 KEY=value。

    占位符识别规则：以 sk-ant-... / sk-... / AIza... 等明显模板字符串开头。
    """
    if not env_path.exists():
        return {}
    placeholders = re.compile(r"^(sk-ant-\.\.\.|sk-\.\.\.|AIza\.\.\.|tvly-\.\.\.)$")
    found: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not value or placeholders.match(value):
            continue
        found[key] = value
    return found


def _provider_already_configured(p: ProviderInfo, env_keys: dict[str, str]) -> bool:
    if p.api_key_env is None:  # ollama
        return True
    return p.api_key_env in env_keys


def _select_provider(env_keys: dict[str, str]) -> ProviderInfo:
    """互动选 provider，已配置的标 ✓。"""
    click.echo("\n选择 LLM provider：")
    for i, p in enumerate(PROVIDERS, start=1):
        configured = _provider_already_configured(p, env_keys)
        marker = "✓" if configured else " "
        kenv = f"需 {p.api_key_env}" if p.api_key_env else "无需 KEY"
        click.echo(f"  {marker} [{i}] {p.label} — {p.description}（{kenv}）")
    click.echo("")
    choice = click.prompt(
        "请输入序号",
        type=click.IntRange(1, len(PROVIDERS)),
    )
    return PROVIDERS[choice - 1]


def _collect_api_key(p: ProviderInfo, env_keys: dict[str, str], no_prompt: bool) -> str | None:
    """获取选定 provider 的 KEY 值。已就位则返回 None（沿用现状），否则 prompt。

    返回 None 表示无需写 .env 变更（已配置或 ollama）。
    返回字符串表示需要写入 .env 的新值。
    """
    if p.api_key_env is None:  # ollama 不需要 KEY
        click.echo(f"\n{p.label} 无需 API KEY，跳过。")
        return None

    if p.api_key_env in env_keys:
        click.echo(f"\n检测到 {p.api_key_env} 已配置（.env 中），将沿用。")
        return None

    if no_prompt:
        click.echo(
            f"\n[--no-key-prompt] 跳过 {p.api_key_env} 输入，"
            "请在 sunday run 之前手动编辑 .env 填入。"
        )
        return ""  # 空字符串表示要写占位注释，不写真实值

    click.echo(f"\n请输入 {p.api_key_env}（隐藏输入；回车跳过留空）：")
    value = getpass.getpass(prompt="> ").strip()
    return value


def _update_agent_yaml(yaml_path: Path, p: ProviderInfo) -> None:
    """改写 agent.yaml 的 model 节（保留其他字段）。"""
    data: dict = {}
    if yaml_path.exists():
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded

    model = data.setdefault("model", {})
    model["provider"] = p.provider_field
    model["id"] = p.default_model_id
    if p.default_base_url is not None:
        model["base_url"] = p.default_base_url
    elif "base_url" in model:
        # anthropic 不需要 base_url，删除以免误用
        del model["base_url"]
    model["api_key_env"] = p.api_key_env  # ollama 时为 None

    yaml_path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _write_env_file(env_path: Path, p: ProviderInfo, key_value: str | None) -> bool:
    """合并写 .env：不破坏用户已有内容，仅追加 / 更新选定 provider 的 KEY。

    key_value: None=无需变更；""=占位注释；其他=实际值。
    返回是否实际写入。
    """
    if key_value is None or p.api_key_env is None:
        return False

    existing_lines: list[str] = []
    existing_keys: set[str] = set()
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            existing_lines.append(raw)
            stripped = raw.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                existing_keys.add(k)

    if p.api_key_env in existing_keys:
        # 已存在但被识别为空/占位 → 替换该行
        new_lines: list[str] = []
        for raw in existing_lines:
            stripped = raw.strip()
            if stripped.startswith(f"{p.api_key_env}="):
                if key_value:
                    new_lines.append(f"{p.api_key_env}={key_value}")
                else:
                    new_lines.append(f"# {p.api_key_env}=  # fill before sunday run")
            else:
                new_lines.append(raw)
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True

    # 追加新行（保留已有内容）
    appended = list(existing_lines)
    if appended and appended[-1].strip():
        appended.append("")
    appended.append(f"# {p.label} ({p.description})")
    if key_value:
        appended.append(f"{p.api_key_env}={key_value}")
    else:
        appended.append(f"# {p.api_key_env}=  # fill before sunday run")
    env_path.write_text("\n".join(appended) + "\n", encoding="utf-8")
    return True


async def _seed_workspace_and_memory(cfg) -> dict[str, list[str]]:
    """通过 MemoryClient 接口 seed L0/L1 模板。"""
    from sunday.bootstrap import build_memory_client, ensure_runtime_dirs

    client = build_memory_client(cfg, run_janitor=False)
    try:
        return await ensure_runtime_dirs(cfg, client)
    finally:
        await client.aclose()


def run_init(no_key_prompt: bool = False, project_root: Path | None = None) -> None:
    """sunday init 主流程。可被 CLI 命令或脚本直接调用。"""
    import asyncio

    project_root = project_root or Path.cwd()
    env_path = project_root / ".env"
    yaml_path = project_root / "configs" / "agent.yaml"

    click.echo("Sunday 首次部署向导\n" + "=" * 40)

    # 1. 扫描 .env
    env_keys = _scan_env_file(env_path)
    if env_keys:
        click.echo(f"\n检测到 .env 中已配置：{', '.join(sorted(env_keys.keys()))}")
    else:
        click.echo("\n.env 不存在或未配置任何 KEY。")

    # 2. 选 provider
    provider = _select_provider(env_keys)
    click.echo(f"\n已选定：{provider.label} ({provider.default_model_id})")

    # 3. 收 KEY
    key_value = _collect_api_key(provider, env_keys, no_key_prompt)

    # 4. 写 agent.yaml + .env
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    _update_agent_yaml(yaml_path, provider)
    click.echo(f"\n✓ 已更新 {yaml_path.relative_to(project_root)}")
    if _write_env_file(env_path, provider, key_value):
        click.echo(f"✓ 已更新 {env_path.relative_to(project_root)}")

    # 5. seed Memory（必须在 .env / yaml 写完后，因为 cfg 解析依赖它们）
    # 重新加载 settings 以反映刚才的 yaml 改动
    from sunday.config import Settings
    settings = Settings()
    seeded = asyncio.run(_seed_workspace_and_memory(settings.sunday))

    if seeded["workspace"]:
        click.echo(f"✓ 已 seed L0 模板到 workspace/：{', '.join(seeded['workspace'])}")
    if seeded["knowledge"]:
        click.echo(f"✓ 已 seed L1 模板到 memory/：{', '.join(seeded['knowledge'])}")
    if not seeded["workspace"] and not seeded["knowledge"]:
        click.echo("✓ Memory 模板已就位，无需 seed")

    # 6. 完成提示
    click.echo("\n" + "=" * 40)
    click.echo("初始化完成。下一步：")
    if provider.api_key_env and key_value == "":
        click.echo(f"  1. 编辑 {env_path.relative_to(project_root)}，填入 {provider.api_key_env}")
        click.echo("  2. 运行 sunday doctor 检查环境")
        click.echo("  3. 运行 sunday run \"测试任务\" 或 sunday tui")
    else:
        click.echo("  • 运行 sunday doctor 检查环境（可选）")
        click.echo("  • 运行 sunday run \"测试任务\" 或 sunday tui")
