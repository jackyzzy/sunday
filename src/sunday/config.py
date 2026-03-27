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


class ModelConfig(BaseModel):
    """LLM 模型配置"""

    provider: str = "anthropic"
    id: str = "claude-opus-4-5"
    temperature: float = 0.2
    max_tokens: int = 8192
    base_url: str | None = None
    api_key_env: str | None = None  # 指定从哪个环境变量读取 API key，优先于 provider 默认映射


class ReasoningConfig(BaseModel):
    """推理与思考配置"""

    max_steps: int = 15
    thinking_level: str = "medium"  # off | minimal | low | medium | high
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


class AgentConfig(BaseModel):
    """Agent 运行配置"""

    name: str = "Sunday"
    workspace_dir: Path = Path("~/.sunday/workspace")
    sessions_dir: Path = Path("~/.sunday/sessions")

    def model_post_init(self, __context: Any) -> None:
        # 展开 ~ 路径
        self.workspace_dir = Path(os.path.expanduser(self.workspace_dir))
        self.sessions_dir = Path(os.path.expanduser(self.sessions_dir))


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


class Settings(BaseSettings):
    """应用级别设置，从 .env 读取密钥，从 YAML 读取配置"""

    # 环境变量中的 API keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # 配置文件路径
    sunday_config_file: str = "configs/agent.yaml"

    # 日志级别
    sunday_log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @cached_property
    def sunday(self) -> SundayConfig:
        """解析 YAML 配置文件，只执行一次"""
        config_path = Path(self.sunday_config_file)
        if not config_path.exists():
            return SundayConfig()

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
