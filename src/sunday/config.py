"""Sunday 配置系统 — 使用 Pydantic Settings 统一加载"""
from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# 将 .env 注入 os.environ，使 get_api_key 中的 os.environ.get() 能读取到自定义变量
load_dotenv(override=False)


def _resolve_configs_dir() -> Path:
    """定位 configs 目录。优先读 SUNDAY_CONFIGS_DIR 环境变量，否则从包位置推导。

    SUNDAY_CONFIGS_DIR 支持：
    - 绝对路径：直接使用
    - 相对路径：相对于当前工作目录（cwd）解析
    - 未设置：从包位置推导（src/sunday/config.py 上溯 3 级为项目根）
    """
    env = os.environ.get("SUNDAY_CONFIGS_DIR")
    if env:
        p = Path(env)
        return p if p.is_absolute() else Path.cwd() / p
    # 包位置推导：src/sunday/config.py → 上溯 3 级为项目根
    return Path(__file__).parent.parent.parent / "configs"


class ModelConfig(BaseModel):
    """LLM 模型配置"""

    provider: str = "anthropic"
    id: str = "claude-opus-4-5"
    temperature: float = 0.2
    max_tokens: int = 8192
    base_url: str | None = None
    api_key_env: str | None = None  # 指定从哪个环境变量读取 API key，优先于 provider 默认映射

    def get_api_key(self) -> str:
        """从环境变量读取 API key，无需 Settings 对象。

        优先使用 api_key_env 指定的环境变量名（来自 agent.yaml model.api_key_env），
        未指定时按 provider 名称回退到默认映射。
        """
        if self.api_key_env:
            key = os.environ.get(self.api_key_env, "")
            if not key:
                raise ValueError(f"环境变量 '{self.api_key_env}' 未设置，请在 .env 中配置")
            return key
        provider_env = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "google": "GOOGLE_API_KEY",
        }
        env_var = provider_env.get(self.provider, f"{self.provider.upper()}_API_KEY")
        key = os.environ.get(env_var, "")
        if not key:
            raise ValueError(
                f"未找到 provider '{self.provider}' 的 API key，"
                f"请在 .env 中设置或在 agent.yaml 中配置 model.api_key_env"
            )
        return key


class ReasoningConfig(BaseModel):
    """推理与思考配置"""

    max_steps: int = 10                # 任务顶层步骤数上限（超出则截断）
    max_react_iteration: int = 5       # 单步内 ReAct 工具调用轮次上限
    max_replans_per_step: int = 3      # Team 内层子步骤重规划上限
    max_replans: int = 5               # 整任务外层重规划总上限（外层唯一限制）
    thinking_level: str = "medium"     # off | minimal | low | medium | high
    thinking_budget_tokens: int = 4096


class MemoryConfig(BaseModel):
    """记忆系统配置（不含路径字段，路径在 AgentConfig 中）"""

    consolidation_cron: str = "0 4 * * *"
    log_retention_days: int = 30
    l0_max_lines: int = 100


class ToolsConfig(BaseModel):
    """工具执行配置"""

    default_timeout: int = 30
    max_output_chars: int = 4096
    sandbox_mode: bool = True
    allow_list: list[str] = Field(default_factory=list)
    deny_list: list[str] = Field(default_factory=lambda: ["rm -rf", "dd if="])


class MCPServerConfig(BaseModel):
    """单个 MCP 服务器配置"""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class MCPConfig(BaseModel):
    """MCP 协议配置"""

    servers: list[MCPServerConfig] = Field(default_factory=list)


class SkillsConfig(BaseModel):
    """技能包配置"""

    extra_dirs: list[str] = Field(default_factory=list)


class NodeConfig(BaseModel):
    """单个执行节点的专属配置（可选，未配置则使用全局默认）。

    在 agent.yaml 的 nodes 节中以 step.id 为 key 配置，ReactAgent 创建 Team/SimpleNode
    时优先读取此配置；未配置则根据 step.is_simple 选择默认节点类型。
    """

    type: str = "auto"  # auto | team | simple；auto 时由 step.is_simple 决定
    extra_skills: list[str] = Field(default_factory=list)  # 在基础工具集上叠加的技能名
    # TODO(phase-future): 支持单个节点使用独立模型配置，覆盖全局 model 节
    model: "ModelConfig | None" = None


class AgentConfig(BaseModel):
    """Agent 运行配置"""

    name: str = "Sunday"
    workspace_dir: Path = Path("~/.sunday/workspace")
    sessions_dir: Path = Path("~/.sunday/sessions")
    report_dir: Path = Path("~/.sunday/reports")
    log_dir: Path = Path("~/.sunday/logs")

    def model_post_init(self, __context: Any) -> None:
        # 展开 ~ 路径
        self.workspace_dir = Path(os.path.expanduser(self.workspace_dir))
        self.sessions_dir = Path(os.path.expanduser(self.sessions_dir))
        self.report_dir = Path(os.path.expanduser(self.report_dir))
        self.log_dir = Path(os.path.expanduser(self.log_dir))


class TaskConfig(BaseModel):
    """单个任务配置"""

    description: str = ""
    steps: list[str] = Field(default_factory=list)


class SundayConfig(BaseModel):
    """Sunday 顶层配置，对应 agent.yaml 全部字段"""

    agent: AgentConfig = Field(default_factory=AgentConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    reasoning: ReasoningConfig = Field(default_factory=ReasoningConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    tasks: dict[str, TaskConfig] = Field(default_factory=dict)
    nodes: dict[str, NodeConfig] = Field(default_factory=dict)  # step.id → 节点专属配置

    def load_prompt(self, name: str) -> str:
        """从 configs/prompts/{name}.md 加载 prompt 模板。

        name 是文件名（不含 .md 后缀），例如 'plan'、'verify' 等。
        文件不存在时抛出 FileNotFoundError。
        """
        prompt_path = _resolve_configs_dir() / "prompts" / f"{name}.md"
        if not prompt_path.exists():
            raise FileNotFoundError(
                f"Prompt 文件未找到：{prompt_path}\n"
                f"请确认 configs/prompts/{name}.md 存在。"
            )
        return prompt_path.read_text(encoding="utf-8")


class Settings(BaseSettings):
    """应用级别设置，从 .env 读取密钥，从 YAML 读取配置"""

    # 环境变量中的 API keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # 日志级别
    sunday_log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @cached_property
    def sunday(self) -> SundayConfig:
        """解析 agent.yaml 配置文件，只执行一次。

        配置文件路径：SUNDAY_CONFIGS_DIR 指定目录（支持绝对/相对路径），
        未设置时从包位置推导 configs/agent.yaml。
        """
        config_path = _resolve_configs_dir() / "agent.yaml"

        if not config_path.exists():
            raise FileNotFoundError(
                f"配置文件未找到：{config_path}\n"
                f"请确认 configs/agent.yaml 存在，"
                f"或通过 SUNDAY_CONFIGS_DIR 环境变量指定配置目录（支持绝对/相对路径）。"
            )

        with config_path.open(encoding="utf-8") as f:
            raw: dict = yaml.safe_load(f) or {}

        return SundayConfig(**raw)

    def get_api_key(self, provider: str, api_key_env: str | None = None) -> str:
        """获取指定 provider 的 API key，找不到时抛出 ValueError。

        优先使用 api_key_env 指定的环境变量名（来自 agent.yaml model.api_key_env），
        未指定时回退到按 provider 名称的默认映射。
        """
        if api_key_env:
            key = os.environ.get(api_key_env, "")
            if not key:
                raise ValueError(f"环境变量 '{api_key_env}' 未设置，请在 .env 中配置")
            return key
        key_map = {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "google": self.google_api_key,
        }
        key = key_map.get(provider, "")
        if not key:
            raise ValueError(
                f"未找到 provider '{provider}' 的 API key，"
                f"请在 .env 中设置 {provider.upper()}_API_KEY 或在 agent.yaml 中配置 model.api_key_env"
            )
        return key


settings = Settings()
