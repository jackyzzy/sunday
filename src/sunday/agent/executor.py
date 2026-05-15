"""Phase 2：Executor — ReAct 执行循环"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, NamedTuple, Protocol

from sunday.agent.llm_client import LLMClient
from sunday.agent.models import (
    AgentState,
    ReactIteration,
    Step,
    StepResult,
    StepStatus,
)

if TYPE_CHECKING:
    from sunday.agent.utils import EmitCallable
    from sunday.config import ModelConfig, SundayConfig

logger = logging.getLogger(__name__)


class MaxStepsError(Exception):
    """ReAct 循环超出最大步骤数"""


class RepetitionError(Exception):
    """连续重复相同工具调用"""


class ToolRegistryProtocol(Protocol):
    """Executor 依赖的工具注册表接口（避免循环 import）"""

    def get_schemas(self) -> list[dict]: ...
    async def execute(self, tool_name: str, arguments: dict, session_id: str) -> str: ...


class _LastToolCall(NamedTuple):
    name: str
    arguments_str: str


class Executor:
    """负责 ReAct 执行循环。执行阶段 temperature=0。"""

    def __init__(
        self,
        config: "SundayConfig",
        tool_registry: ToolRegistryProtocol | None = None,
        executor_prompt_override: str | None = None,
        emit: "EmitCallable | None" = None,
    ) -> None:
        from sunday.agent.prompt_resolver import PromptResolver
        from sunday.agent.utils import noop_emit

        self.config = config
        self.tool_registry = tool_registry
        self._executor_prompt_override = executor_prompt_override
        self._resolver = PromptResolver(config)
        self.emit = emit or noop_emit

    def _get_system_prompt(self, step_type: str = "generic") -> str:
        """按 step_type 路由 prompt：

        1. executor_prompt_override 静态覆盖（构造时注入，绕过 task_type 路由）
        2. 否则 PromptResolver 按 (role="executor", task_type=step_type) 加载：
           - generic → executor_system.md（默认）
           - research/analysis/synthesis → executor_{type}.md（找不到 raise）
        """
        if self._executor_prompt_override:
            return self.config.load_prompt(self._executor_prompt_override)
        return self._resolver.resolve("executor", step_type)

    async def run(self, step: Step, state: AgentState) -> StepResult:
        """执行单个步骤，返回 StepResult。网络错误转为 FAILED 结果，不向上传播。"""
        try:
            return await self._run_inner(step, state)
        except Exception as e:
            import httpx
            from sunday.agent.providers.base import LLMAPIError
            if isinstance(e, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
                msg = f"网络连接失败：{type(e).__name__}（请检查网络或代理配置）"
            elif isinstance(e, httpx.RemoteProtocolError):
                msg = f"服务端连接中断：{e}（可能是上下文过长或服务端超时，可重试）"
            elif isinstance(e, LLMAPIError):
                msg = f"LLM API 错误（步骤标记失败，可触发 replan）：{e}"
            else:
                raise
            logger.error("步骤 %s LLM 调用失败：%s", step.id, msg)
            return StepResult(
                step_id=step.id,
                status=StepStatus.FAILED,
                output=msg,
            )

    async def _run_inner(self, step: Step, state: AgentState) -> StepResult:
        """实际执行逻辑（由 run() 包裹以统一捕获网络异常）。"""
        from sunday.agent.providers import get_provider

        model_cfg: ModelConfig = self.config.model
        max_steps = self.config.reasoning.max_react_iterations_per_substep
        provider = get_provider(model_cfg.provider)

        system = self._get_system_prompt(step.step_type).format(
            intent=step.intent,
            expected_output=step.expected_output,
            success_criteria=step.success_criteria,
        )
        # 最终步骤主题锚定：若 step 命中关键词，在 system prompt 末尾追加锚定段
        anchor = self._build_anchor(step, state)
        if anchor:
            system = f"{system}\n\n{anchor}"
        # 时效性约束：requires_realtime_data=true 的步骤必须联网
        realtime_notice = self._build_realtime_notice(step)
        if realtime_notice:
            system = f"{system}\n\n{realtime_notice}"
        messages = [{"role": "user", "content": step.intent}]
        tools = self.tool_registry.get_schemas() if self.tool_registry else []
        iterations: list[ReactIteration] = []
        last_tool_call: _LastToolCall | None = None

        for i in range(max_steps):
            # 最后一次机会：禁用工具，强制模型整合已有信息直接输出
            if i == max_steps - 1:
                messages.append({
                    "role": "user",
                    "content": "你已用完工具调用次数。请根据以上收集到的信息，直接输出最终结果，不要再调用工具。",
                })
                response = await self._call_llm(system, messages, [], model_cfg)
                logger.warning("步骤 %s 达到最大迭代次数 %d，已强制收尾输出", step.id, max_steps)
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.DONE,
                    output=self._apply_offline_label(response.text, iterations, step),
                    react_iterations=iterations,
                )

            response = await self._call_llm(system, messages, tools, model_cfg)

            # 模型判断任务完成
            if response.finish_reason in ("stop", "end_turn") or not response.tool_call:
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.DONE,
                    output=self._apply_offline_label(response.text, iterations, step),
                    react_iterations=iterations,
                )

            # 有工具调用
            tc = response.tool_call
            current_call = _LastToolCall(tc.name, tc.arguments)
            if current_call == last_tool_call:
                raise RepetitionError(f"连续重复调用工具 {tc.name}，参数：{tc.arguments}")
            last_tool_call = current_call

            # 执行工具（arguments 可能为格式错误的 JSON，容错处理）
            try:
                arguments = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                # JSON 截断时不静默降级——给模型明确的错误观察值，让它知道要拆分内容
                logger.warning(
                    "工具 %s 参数 JSON 解析失败（内容可能因 max_tokens 截断）。原文前 300 字：%s",
                    tc.name, tc.arguments[:300],
                )
                observation = (
                    f"[工具参数 JSON 解析失败] 调用 {tc.name!r} 时参数 JSON 格式不完整，"
                    f"可能因输出超出 max_tokens 被截断。"
                    f"请将文件内容拆分为多次调用：先用 write_file 写少量内容，再用 append_file 追加剩余部分；"
                    f"每次写入不超过 1500 字。"
                )
                iterations.append(ReactIteration(
                    iteration=i,
                    tool_name=tc.name,
                    tool_input={},
                    observation=observation,
                ))
                messages.extend(provider.build_tool_result_messages(response, observation))
                continue

            # 取第一个参数值作为预览（如搜索词、文件名等）
            args_preview = next(iter(arguments.values()), "") if arguments else ""
            if not isinstance(args_preview, str):
                args_preview = str(args_preview)
            args_preview = args_preview[:60]

            await self.emit(state.session_id, "tool_start", {
                "step_id": step.id,
                "tool": tc.name,
                "args_preview": args_preview,
                "iteration": i,
            })

            if self.tool_registry:
                observation = await self.tool_registry.execute(
                    tc.name, arguments, state.session_id
                )
            else:
                observation = f"[工具 {tc.name} 不可用]"

            await self.emit(state.session_id, "tool_end", {
                "step_id": step.id,
                "tool": tc.name,
                "iteration": i,
                "result_preview": (observation or "")[:80],
            })

            iterations.append(ReactIteration(
                iteration=i,
                tool_name=tc.name,
                tool_input=arguments,
                observation=observation,
            ))

            # 委托 provider 构造正确的 tool result 消息格式
            messages.extend(provider.build_tool_result_messages(response, observation))

    # ── 时效性约束（Change 5：Executor 防御层）─────────────────────────────

    OFFLINE_LABEL = "> ⚠ 本节未联网验证，基于训练数据整理，时效性未确认。\n\n"
    """未联网兜底标签；幂等：已含此前缀的输出不重复打。"""

    _SEARCH_TOOLS = ("web_search", "fetch_url")

    def _build_realtime_notice(self, step: Step) -> str:
        """为 requires_realtime_data=True 的 step 生成 system prompt 注入文本。"""
        if not step.requires_realtime_data:
            return ""
        return (
            "## 时效性约束（本步骤已被规划标记为 requires_realtime_data）\n"
            "- 必须至少调用一次 web_search 或 fetch_url 获取最新信息\n"
            "- 如工具持续失败（返回以 `[错误]` 开头的字符串），仍须完成输出，"
            "但输出**首行**必须为：\n"
            f"  `{self.OFFLINE_LABEL.rstrip()}`\n"
            "- 禁止使用'据公开报道'、'综合公开信息'、'行业媒体综合整理'等暗示"
            "实际检索的措辞，除非本 step 真的成功调用过联网工具"
        )

    def _apply_offline_label(
        self, output: str, iterations: list[ReactIteration], step: Step,
    ) -> str:
        """代码兜底：未联网且无标签的 realtime 步骤强制 prepend 标签。

        触发条件（同时满足）：
        - config.quality.offline_output_label.enabled
        - step.requires_realtime_data
        - 本 step 没有任何成功的联网工具调用（observation 不以 `[错误]` 开头）
        - 输出首行尚未含此标签（幂等保证）
        """
        cfg = self.config.quality.offline_output_label
        if not cfg.enabled or not step.requires_realtime_data:
            return output
        if any(self._is_search_success(it) for it in iterations):
            return output
        if output.lstrip().startswith("> ⚠ 本节未联网验证"):
            return output
        return self.OFFLINE_LABEL + output

    @classmethod
    def _is_search_success(cls, it: ReactIteration) -> bool:
        if it.tool_name not in cls._SEARCH_TOOLS:
            return False
        obs = it.observation or ""
        return not obs.lstrip().startswith("[错误]")

    def _build_anchor(self, step: Step, state: AgentState) -> str:
        """为"最终汇总/整合"类步骤生成主题锚定段，防止输出被跨会话旧话题带偏。

        仅当 step.intent 命中 config.quality.final_step_anchor.intent_keywords 时生效。
        """
        cfg = self.config.quality.final_step_anchor
        if not cfg.enabled:
            return ""
        intent = step.intent or ""
        if not any(kw in intent for kw in cfg.intent_keywords):
            return ""

        parts: list[str] = []
        if state.task and state.task.strip():
            parts.append(f"任务主题：{state.task.strip()}")
        if state.plan and state.plan.goal and state.plan.goal.strip():
            parts.append(f"规划目标：{state.plan.goal.strip()}")
        if state.session_thread and state.session_thread.key_entities:
            parts.append(
                f"关键实体：{'、'.join(state.session_thread.key_entities)}"
            )
        if not parts:
            return ""

        anchor_lines = "\n".join(f"- {p}" for p in parts)
        return (
            "【主题锚定】当前步骤涉及整合/汇总/最终输出，请严格围绕以下主题展开，"
            "不得偏离转向其他无关话题：\n"
            f"{anchor_lines}\n"
            "若系统提示中的跨会话记忆出现其他话题，不得将其作为本次输出的主干内容。"
        )

    # ── 内部 LLM 调用（执行阶段 temperature=0） ────────────────────────────

    async def _call_llm(
        self, system: str, messages: list, tools: list, model_cfg: "ModelConfig"
    ):
        return await LLMClient.call(
            model_cfg, messages,
            system=system,
            tools=tools or None,
            temperature=0,
        )
