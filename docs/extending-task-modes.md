# 扩展 task_type（任务模式）

Sunday 的 Executor / Verifier 按 **task-mode** 切分 prompt（research / analysis / synthesis / generic）。这个矩阵由 [`src/sunday/agent/prompt_resolver.py`](../src/sunday/agent/prompt_resolver.py) 单点解析，所有 role 共用同一份 fallback / loud-fail 逻辑。

加新 task-mode（例：`coding`）需要 **3 步，0 行 Python 代码改动**（除 enum）。

---

## 当前 prompt 矩阵

| Role | generic | research | analysis | synthesis |
|------|---------|----------|----------|-----------|
| Executor | `executor_system.md` | `executor_research.md` | `executor_analysis.md` | `executor_synthesis.md` |
| Verifier | `verify.md` | `verify_research.md` | `verify_analysis.md` | `verify_synthesis.md` |

加新 task-mode 时**必须同时补齐 Executor + Verifier 两侧**，否则 PromptResolver 会在该 task-mode 第一次被使用时 raise ValueError（loud fail，行为可预期）。

---

## 第 1 步：扩展 `Step.step_type` enum

编辑 [`src/sunday/agent/models.py`](../src/sunday/agent/models.py) 的 `StepType`：

```python
StepType = Literal["research", "analysis", "synthesis", "generic", "coding"]  # 加 coding
```

这是唯一的代码改动 —— Pydantic 的 Literal enum 是数据模型契约，加值需显式声明。

---

## 第 2 步：放两个 prompt 文件

按命名约定创建两个文件：

```
configs/prompts/executor_coding.md
configs/prompts/verify_coding.md
```

**模板风格参考**：
- Executor 的 prompt 描述"如何执行此类任务"（执行原则、规范、诚信约束）
- Verifier 的 prompt 描述"如何判断此类任务的输出合格"（多维度检查 + JSON 输出格式）

可直接复制最相近的现有 prompt（如 `executor_analysis.md`）作为骨架后定制。

---

## 第 3 步：更新 `plan.md` schema 说明

编辑 [`configs/prompts/plan.md`](../configs/prompts/plan.md) 中 step_type 的 enum 说明清单，让 Planner LLM 知道有这个新选项可选：

```markdown
**step_type**（**每步必填**）：步骤任务模式，必须从以下 enum 中选一个：
- `research`：搜索、调研、数据收集
- `analysis`：对比、评分、综合分析已有数据
- `synthesis`：把多个步骤的产出整合为最终交付物
- `coding`：代码生成、调试         ← 新加这一行
- `generic`：无特殊任务模式，走默认 executor 提示
```

同样更新 [`configs/prompts/team_plan.md`](../configs/prompts/team_plan.md) 的相应清单。

---

## 完整流程：自检清单

新加 task_type 后，建议运行以下检查：

```bash
# 1. step_type enum 校验
uv run pytest tests/unit/test_planner_step_type.py -v

# 2. PromptResolver 命中正确文件
uv run python -c "
from sunday.agent.prompt_resolver import PromptResolver
from sunday.config import settings
r = PromptResolver(settings.sunday)
print(r.resolve('executor', 'coding')[:80])  # 应输出 executor_coding.md 开头
print(r.resolve('verify', 'coding')[:80])    # 应输出 verify_coding.md 开头
"

# 3. 全量回归
uv run pytest tests/ -q
```

---

## 常见错误

### 1. 只加了 Executor 没加 Verifier

```
ValueError: role='verify' task_type='coding' 要求 verify_coding.md 存在，但加载失败...
```

→ 立即创建 `verify_coding.md`。这是 loud fail 的设计意图（不允许"半个 task-mode"）。

### 2. 忘了改 `plan.md` 的 enum 说明

Planner LLM 不知道 `coding` 可选 → 一直输出 `generic`，专项 prompt 永远不被触发。
→ 编辑 `plan.md` 的 step_type 清单部分。

### 3. 想加新 role（不是 task_type）

如想加 `synthesizer` 这种新 role（与 `executor` / `verify` 平级），编辑 [`prompt_resolver.py`](../src/sunday/agent/prompt_resolver.py) 的 `_ROLE_DEFAULTS` dict：

```python
_ROLE_DEFAULTS: dict[str, str] = {
    "executor": "executor_system",
    "verify": "verify",
    "synthesizer": "synthesizer_default",  # 加这一条
}
```

加 role 是单点改动，PromptResolver 自动支持二维查找；之后 `synthesizer_research.md` / `synthesizer_analysis.md` 等都按命名约定自动命中。

---

## 设计决策（FAQ）

**Q: 为什么 generic 是合法默认值，而不是"忘填了 fallback"？**
A: 显式优于隐式。`step_type=generic` 表示 Planner LLM **主动声明**这个步骤无特殊任务模式；如果 LLM 漏填，Pydantic 会用默认值 `generic` —— 行为可预期。Loud fail 只发生在指定了非 generic 但 prompt 文件缺失的情况。

**Q: 为什么 task_type 不在 yaml 配置里，而是写死在 Literal enum 里？**
A: `step_type` 是数据模型字段，需要 Pydantic 校验 + IDE 类型提示。yaml 配置只在"运行时可调"的边界上使用（如 `quality.synthesis_quality_check.enabled`），结构契约用 enum。

**Q: 我能同时关闭某个 task_type 的深度检查吗？**
A: 当前只 synthesis 有这个闸门（`config.quality.synthesis_quality_check.enabled`）。其他 task-mode 直接走对应 prompt，没有"关闭深度检查"的概念。如果未来需要，按 `quality.<type>_quality_check.enabled` 模式扩展即可，PromptResolver 不感知。
