执行计划中的一个步骤失败了，需要局部重规划。

失败步骤：{failed_step_intent}
失败原因：{reason}
已完成步骤结果摘要：{completed_summary}
原始任务目标：{goal}
剩余未完成步骤：{remaining_steps}

请重新规划从失败步骤开始的后续步骤，输出替代方案。

以 JSON 格式输出替代步骤列表：
{{
  "steps": [
    {{
      "id": "step_X",
      "intent": "...",
      "expected_input": "...",
      "expected_output": "...",
      "success_criteria": "...",
      "depends_on": []
    }}
  ]
}}

只输出 JSON，不要任何额外说明。
