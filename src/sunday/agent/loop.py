"""loop.py — 向后兼容转发层。

实现已迁移至 react_agent.py（ReactAgent 类）。
本文件仅保留以下符号的重新导出，供旧代码和测试 patch 路径继续使用：

    - AgentLoop = ReactAgent        （向后兼容别名）
    - Team / SimpleNode             （供 patch("sunday.agent.loop.Team") 等使用）

新代码请直接从 sunday.agent.react_agent 导入 ReactAgent。
"""
from sunday.agent.react_agent import ReactAgent  # noqa: F401
from sunday.agent.simple import SimpleNode  # noqa: F401  供 patch("sunday.agent.loop.SimpleNode")
from sunday.agent.team import Team  # noqa: F401  供 patch("sunday.agent.loop.Team")

# 向后兼容别名：旧代码和测试可继续使用 AgentLoop
AgentLoop = ReactAgent

__all__ = ["ReactAgent", "AgentLoop", "Team", "SimpleNode"]
