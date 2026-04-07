Team 内的一个子步骤执行失败，需要对该子步骤及其后续子步骤进行局部重规划。

父步骤意图：{parent_step_intent}
失败的子步骤：{failed_sub_step_intent}
失败原因：{reason}
已完成子步骤摘要：{completed_sub_summary}
剩余未执行子步骤：{remaining_sub_steps}

请重新规划从失败子步骤开始的后续子步骤，输出替代方案。

要求：
- 步骤数量尽量少（1~3 个），能一步完成的不拆成两步
- 每步必须是可直接执行的工具调用或直接输出，不能是人机交互操作
- success_criteria 基于输出内容自动验证，不应要求交互
- 步骤 id 格式为 "{parent_step_id}.R1"、"{parent_step_id}.R2" 等（R 代表重规划）

以 JSON 格式输出，不要任何额外说明：
{{
  "steps": [
    {{
      "id": "{parent_step_id}.R1",
      "intent": "这步要做什么",
      "expected_input": "需要什么输入",
      "expected_output": "输出是什么",
      "success_criteria": "输出满足什么条件算成功",
      "depends_on": []
    }}
  ]
}}
