执行计划中的一个步骤失败了，需要局部重规划。

失败步骤：{failed_step_intent}
失败原因：{reason}
已完成步骤结果摘要：{completed_summary}
已完成步骤 ID 清单（合法依赖来源）：{completed_step_ids}
原始任务目标：{goal}
剩余未完成步骤：{remaining_steps}

请重新规划从失败步骤开始的后续步骤，输出替代方案。

**重要约束（违反将导致执行失败）**：
1. 新步骤的 `depends_on` 只能引用：
   - 上述"已完成步骤 ID 清单"中的 ID，或
   - 本次新生成的步骤 ID（步骤之间允许内部依赖链）
2. 不得引用任何不在上述两种集合中的 ID（包括失败步骤本身的旧 ID）
3. 第一个新步骤通常只依赖"已完成步骤 ID"，因为前面没有其他新步骤可依赖
4. 如果原 plan 末尾本来有一个综合整合（synthesis）步骤，请在新步骤列表末尾保留一个 `step_type="synthesis"` 的整合步骤，并让它依赖最后一个新生成的实质步骤

以 JSON 格式输出替代步骤列表：
{{
  "steps": [
    {{
      "id": "step_X",
      "intent": "...",
      "expected_input": "...",
      "expected_output": "...",
      "success_criteria": "...",
      "depends_on": [],
      "step_type": "generic"
    }}
  ]
}}

只输出 JSON，不要任何额外说明。
