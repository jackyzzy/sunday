"""Sunday 配置系统 — 使用 Pydantic Settings 统一加载"""
from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# 将 .env 注入 os.environ，使 get_api_key 中的 os.environ.get() 能读取到自定义变量
load_dotenv(override=False)


_PROVIDER_ENV_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}


def _lookup_api_key(provider: str, api_key_env: str | None) -> str:
    """从 os.environ 查找 API key，找不到时抛出 ValueError。"""
    if api_key_env:
        key = os.environ.get(api_key_env, "")
        if not key:
            raise ValueError(f"环境变量 '{api_key_env}' 未设置，请在 .env 中配置")
        return key
    env_var = _PROVIDER_ENV_MAP.get(provider, f"{provider.upper()}_API_KEY")
    key = os.environ.get(env_var, "")
    if not key:
        raise ValueError(
            f"未找到 provider '{provider}' 的 API key，"
            f"请在 .env 中设置或在 agent.yaml 中配置 model.api_key_env"
        )
    return key


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
        """从环境变量读取 API key，无需 Settings 对象。"""
        return _lookup_api_key(self.provider, self.api_key_env)


class ReasoningConfig(BaseModel):
    """推理与思考配置"""

    max_steps: int = 20                # 任务顶层步骤数上限（超出则截断）
    max_react_iteration: int = 40      # 单步内 ReAct 工具调用轮次上限
    max_replans_per_step: int = 10     # Team 内层子步骤重规划上限
    max_replans: int = 40              # 整任务外层重规划总上限（外层唯一限制）
    thinking_level: str = "medium"     # off | minimal | low | medium | high


class MemoryConfig(BaseModel):
    """记忆系统配置（不含路径字段，路径在 AgentConfig 中）"""

    consolidation_cron: str = "0 4 * * *"
    log_retention_days: int = 30
    l0_max_lines: int = 100
    history_max_chars: int = 2000   # 多轮对话历史单条消息最大字符数

    backend: Literal["local", "http"] = "local"
    """Memory 后端实现。当前仅 local（文件系统）；http 预留给未来远程 Memory 服务。"""


class ToolsConfig(BaseModel):
    """工具执行配置"""

    default_timeout: int = 60
    max_output_chars: int = 8192
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


class FactCheckConfig(BaseModel):
    """规划阶段事实核查子阶段配置（opt-in）。

    默认关闭：Plan 阶段保持纯粹（仅 LLM 推理），实时数据获取由 Execution 阶段
    通过 Step.requires_realtime_data 驱动。仅当用户对 L1/L2 跨会话记忆污染
    敏感、需要 Plan 时主动联网核实时才开启。
    """

    enabled: bool = False
    max_tool_calls: int = 2
    timeout_seconds: int = 10
    allowed_tools: list[str] = Field(default_factory=lambda: ["web_search", "read_file"])


class SubjectConsistencyConfig(BaseModel):
    """验证阶段主题一致性检查配置"""

    enabled: bool = True
    checker: str = "llm"  # llm | keyword | small_model（未来）


class FinalStepAnchorConfig(BaseModel):
    """最终步骤输出锚定到当前任务主题的配置"""

    enabled: bool = True
    intent_keywords: list[str] = Field(
        default_factory=lambda: [
            "整合", "总结", "汇总", "最终", "综合", "清单", "建议", "报告",
        ]
    )


class RealtimeHintsConfig(BaseModel):
    """实时数据需求识别配置（think 阶段 + 关键词聚合）。

    enabled=true 时 Planner 在 plan 前会跑一次轻量 think LLM 调用识别不确定
    断言，并结合任务文本关键词聚合为 hints，注入 plan prompt 让 LLM 为
    每个 step 标注 requires_realtime_data。
    """

    enabled: bool = True


class OfflineOutputLabelConfig(BaseModel):
    """Executor 未联网兜底打标配置。

    enabled=true 时，对 requires_realtime_data=True 的 step，若实际未成功
    调用 web_search/fetch_url，则自动给输出 prepend "⚠ 未联网" 标签。
    """

    enabled: bool = True


class ToolUsageAuditConfig(BaseModel):
    """Verifier 工具使用审计配置。

    enabled=true 时，对 requires_realtime_data=True 的 step：若既未成功
    联网也未带未联网标签 → Verifier 判 failed，触发 replan。
    """

    enabled: bool = True


class SynthesisQualityCheckConfig(BaseModel):
    """synthesis 步骤的深度质量检查配置（基于 verify_synthesis.md）。

    enabled=true 时，step_type=="synthesis" 的步骤验证使用 verify_synthesis.md
    做结构完整性 / 内容实质性 / 对比完备性 / 推荐合理性 / 细节充分性 / 自包含性
    六维审查，失败时触发 replan 重新生成。
    """

    enabled: bool = True


class QualityConfig(BaseModel):
    """质量控制开关：事实核查 / 主题一致性 / 最终步骤锚定 / 实时性识别 / 未联网打标 / 工具审计 / 综合质量"""

    fact_check: FactCheckConfig = Field(default_factory=FactCheckConfig)
    subject_consistency: SubjectConsistencyConfig = Field(
        default_factory=SubjectConsistencyConfig
    )
    final_step_anchor: FinalStepAnchorConfig = Field(
        default_factory=FinalStepAnchorConfig
    )
    realtime_hints: RealtimeHintsConfig = Field(default_factory=RealtimeHintsConfig)
    offline_output_label: OfflineOutputLabelConfig = Field(
        default_factory=OfflineOutputLabelConfig
    )
    tool_usage_audit: ToolUsageAuditConfig = Field(default_factory=ToolUsageAuditConfig)
    synthesis_quality_check: SynthesisQualityCheckConfig = Field(
        default_factory=SynthesisQualityCheckConfig
    )


class TuiConfig(BaseModel):
    """终端 TUI 客户端配置。

    所有项均可在 agent.yaml 的 `tui:` 节下覆盖；不写时取此默认值。
    """

    paste_fold_threshold: int = 4
    """超过 N 行的粘贴折叠为 [Pasted N lines #xxxxxx] 占位符；提交时还原原文。"""

    input_history_size: int = 200
    """输入历史最大条数（per-session，从已有 turns 派生，不另起独立存储）。"""

    enable_mouse: bool = False
    """是否开启 Textual 鼠标捕获。

    默认 False：让终端模拟器接管鼠标 → 拖拽=终端原生选区高亮、右键=终端原生菜单/粘贴。
    Sunday TUI 全部 widget（ChatLog/StatusBar/Input）均无需鼠标点击交互，
    禁用鼠标对功能零影响。仅在某些罕见终端 mouse=False 异常时改回 True。
    """


class NodeConfig(BaseModel):
    """单个执行节点的专属配置（可选，未配置则使用全局默认）。

    在 agent.yaml 的 nodes 节中以 step.id 为 key 配置，ReactAgent 创建 Team/SimpleNode
    时优先读取此配置；未配置则根据 step.is_simple 选择默认节点类型。
    """

    type: str = "auto"  # auto | team | simple；auto 时由 step.is_simple 决定
    extra_skills: list[str] = Field(default_factory=list)  # 在基础工具集上叠加的技能名
    model: "ModelConfig | None" = None  # 节点专属模型配置（保留扩展位）
    executor_prompt: str | None = None  # 覆盖该节点 executor 使用的 prompt 名（不含 .md）


class AgentConfig(BaseModel):
    """Agent 运行配置"""

    name: str = "Sunday"
    workspace_dir: Path = Path("~/.sunday/workspace")   # L0：用户静态配置
    memory_dir: Path = Path("~/.sunday/memory")         # L1+L2：AI 维护的动态记忆
    sessions_dir: Path = Path("~/.sunday/sessions")     # L3：会话详细记录（reports 子目录在此下）
    log_dir: Path = Path("~/.sunday/logs")

    def model_post_init(self, __context: Any) -> None:
        # 展开 ~ 路径
        self.workspace_dir = Path(os.path.expanduser(self.workspace_dir))
        self.memory_dir = Path(os.path.expanduser(self.memory_dir))
        self.sessions_dir = Path(os.path.expanduser(self.sessions_dir))
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
    quality: QualityConfig = Field(default_factory=QualityConfig)
    tui: TuiConfig = Field(default_factory=TuiConfig)
    tasks: dict[str, TaskConfig] = Field(default_factory=dict)
    nodes: dict[str, NodeConfig] = Field(default_factory=dict)  # step.id → 节点专属配置

    @property
    def configs_dir(self) -> Path:
        """返回 configs 目录路径，用于定位 prompts/、templates/ 等子目录。"""
        return _resolve_configs_dir()

    def load_prompt(self, name: str) -> str:
        """从 configs/prompts/{name}.md 加载 prompt 模板。

        name 是文件名（不含 .md 后缀），例如 'plan'、'verify' 等。
        文件不存在时抛出 FileNotFoundError。
        """
        prompt_path = self.configs_dir / "prompts" / f"{name}.md"
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
        """获取指定 provider 的 API key，找不到时抛出 ValueError。"""
        return _lookup_api_key(provider, api_key_env)


settings = Settings()
